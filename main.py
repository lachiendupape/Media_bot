import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

import os
import json
import logging
import threading
import hmac
import time
import uuid
from collections import OrderedDict
from datetime import timedelta
from functools import wraps
import requests
from flask import Flask, g, request, jsonify, session, redirect, url_for, render_template
from llm import chat_with_llm
from api.radarr import credit_cache
import plex_auth
import config
from observability import (
    append_jsonl,
    clear_request_id,
    configure_logging,
    hash_user_identifier,
    init_observability,
    redact_sensitive_fields,
    set_request_id,
)

configure_logging(config.LOG_LEVEL)
init_observability(
    service_name=config.OBSERVABILITY_SERVICE_NAME,
    environment=os.getenv('FLASK_ENV', 'development'),
    sentry_dsn=config.SENTRY_DSN,
    otlp_endpoint=config.OTEL_EXPORTER_OTLP_ENDPOINT,
)

log = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
)
# Set Secure flag when served behind HTTPS proxy
if os.getenv('FLASK_ENV') == 'production':
    app.config['SESSION_COOKIE_SECURE'] = True

BOT_API_KEY = os.getenv('BOT_API_KEY')
_RECENT_REQUESTS = OrderedDict()
_RECENT_REQUEST_LIMIT = 200


def _remember_request_context(request_id, payload):
    _RECENT_REQUESTS[request_id] = payload
    _RECENT_REQUESTS.move_to_end(request_id)
    while len(_RECENT_REQUESTS) > _RECENT_REQUEST_LIMIT:
        _RECENT_REQUESTS.popitem(last=False)


def _auth_mode():
    if session.get('plex_user'):
        return 'session'
    if request.headers.get('X-Api-Key'):
        return 'api_key'
    return 'anonymous'


def _categorize_issue(description):
    """Categorize issue type based on description keywords."""
    desc_lower = description.lower()
    if any(word in desc_lower for word in ['crash', 'error', 'failing', 'broken', 'doesn\'t work', 'not working']):
        return 'bug'
    elif any(word in desc_lower for word in ['feature', 'add', 'implement', 'would like', 'suggest', 'request']):
        return 'enhancement'
    return 'issue'


def _generate_auto_labels(description, debug_context):
    """Generate labels based on issue description and context."""
    labels = set()
    desc_lower = description.lower()
    
    # Categorize by type
    category = _categorize_issue(description)
    labels.add(category)
    
    # Domain detection
    if any(word in desc_lower for word in ['search', 'query', 'find', 'look']):
        labels.add('search')
    if any(word in desc_lower for word in ['chat', 'response', 'llm', 'model', 'ai']):
        labels.add('chat')
    if any(word in desc_lower for word in ['ui', 'interface', 'button', 'modal', 'display']):
        labels.add('ui')
    if any(word in desc_lower for word in ['performance', 'slow', 'hanging', 'timeout', 'latency']):
        labels.add('performance')
    if any(word in desc_lower for word in ['security', 'auth', 'permission', 'access']):
        labels.add('security')
    
    # Add user-configured labels if present
    if config.GITHUB_ISSUE_LABELS:
        labels.update(config.GITHUB_ISSUE_LABELS.split(','))
    
    return list(labels)


def _build_github_issue_payload(report, debug_context):
    description = report['description']
    expected = report['expected'] or None
    
    # Generate smart title
    category = _categorize_issue(description)
    title_text = description[:60].strip()
    issue_title = f"[{category.upper()}] {title_text}"
    
    lines = [
        "## Description",
        description,
    ]
    
    if expected:
        lines.extend([
            "",
            "## Expected Behavior",
            expected,
        ])
    
    last_chat = debug_context.get('last_chat') or {}
    if last_chat and (last_chat.get('user_message') or last_chat.get('response_text')):
        lines.append("")
        lines.append("## Context")
        if last_chat.get('user_message'):
            lines.extend([
                "**Last Prompt:**",
                last_chat.get('user_message'),
            ])
        if last_chat.get('response_text'):
            lines.extend([
                "",
                "**Last Response:**",
                last_chat.get('response_text'),
            ])
    
    # Metadata section
    lines.extend([
        "",
        "## Technical Details",
        f"- Request ID: `{report['request_id']}`",
        f"- App Version: `{config.APP_VERSION}`",
        f"- Auth Mode: `{debug_context.get('auth_mode', 'unknown')}`",
        f"- Timestamp: `{report.get('timestamp', 'unknown')}`",
    ])
    
    telemetry = (last_chat.get('telemetry') or {})
    if telemetry:
        lines.extend([
            "",
            "## Telemetry",
            "```json",
            json.dumps(redact_sensitive_fields(telemetry), indent=2, ensure_ascii=True),
            "```",
        ])
    
    # Generate auto-labels
    auto_labels = _generate_auto_labels(description, debug_context)
    
    return {
        'title': issue_title,
        'body': "\n".join(lines),
        'labels': auto_labels,
    }


def _create_github_issue(report, debug_context):
    if not config.GITHUB_ISSUES_ENABLED:
        return None

    payload = _build_github_issue_payload(report, debug_context)
    response = requests.post(
        f"https://api.github.com/repos/{config.GITHUB_ISSUES_REPO}/issues",
        headers={
            'Accept': 'application/vnd.github+json',
            'Authorization': f"Bearer {config.GITHUB_ISSUES_TOKEN}",
            'X-GitHub-Api-Version': '2022-11-28',
        },
        json=payload,
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    return {
        'number': data.get('number'),
        'url': data.get('html_url'),
        'title': data.get('title'),
    }


@app.before_request
def assign_request_context():
    request_id = request.headers.get('X-Request-ID') or uuid.uuid4().hex[:12]
    g.request_id = request_id
    g.request_started = time.perf_counter()
    set_request_id(request_id)


@app.after_request
def finalize_request(response):
    request_id = getattr(g, 'request_id', '-')
    response.headers['X-Request-ID'] = request_id

    duration_ms = None
    if hasattr(g, 'request_started'):
        duration_ms = round((time.perf_counter() - g.request_started) * 1000, 2)

    log.info(
        'request.complete',
        extra={
            'request_path': request.path,
            'method': request.method,
            'status_code': response.status_code,
            'duration_ms': duration_ms,
            'auth_mode': _auth_mode(),
            'user_hash': hash_user_identifier(session.get('plex_user')),
        },
    )
    clear_request_id()
    return response


def require_auth(f):
    """Allow access via session (browser) OR X-Api-Key header (programmatic)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Session auth (browser)
        if session.get('plex_user'):
            return f(*args, **kwargs)
        # API key auth (programmatic)
        key = request.headers.get('X-Api-Key', '')
        if BOT_API_KEY and key and hmac.compare_digest(key, BOT_API_KEY):
            return f(*args, **kwargs)
        # Browser requests get redirected, API requests get 401
        if request.is_json:
            return jsonify({"error": "Unauthorized"}), 401
        return redirect(url_for('login_page'))
    return decorated


# --- Plex OAuth routes ---

@app.route('/auth/login')
def login_page():
    if session.get('plex_user'):
        return redirect(url_for('index'))
    return render_template('login.html', error=request.args.get('error'))


@app.route('/auth/start')
def auth_start():
    """Create a Plex PIN and redirect to Plex OAuth."""
    pin_id, pin_code = plex_auth.create_pin()
    session['plex_pin_id'] = pin_id
    forward_url = request.url_root.rstrip('/') + url_for('auth_callback')
    auth_url = plex_auth.build_auth_url(pin_code, forward_url)
    return redirect(auth_url)


@app.route('/auth/callback')
def auth_callback():
    """Plex redirects back here after user authenticates."""
    pin_id = session.pop('plex_pin_id', None)
    if not pin_id:
        return redirect(url_for('login_page', error='Auth session expired. Please try again.'))

    token = plex_auth.check_pin(pin_id)
    if not token:
        return redirect(url_for('login_page', error='Authentication failed. Please try again.'))

    user = plex_auth.get_plex_user(token)
    if not user:
        return redirect(url_for('login_page', error='Could not retrieve Plex user info.'))

    if not plex_auth.user_has_server_access(token):
        return redirect(url_for('login_page', error='Your Plex account does not have access to this server.'))

    session['plex_user'] = user
    session.permanent = True
    print(f"[Auth] Plex user '{user['username']}' signed in.", flush=True)
    return redirect(url_for('index'))


@app.route('/auth/logout')
def auth_logout():
    session.clear()
    return redirect(url_for('login_page'))


# --- Web UI ---

@app.route('/')
@require_auth
def index():
    return render_template(
        'chat.html',
        user=session['plex_user'],
        app_version=config.APP_VERSION,
        github_issue_enabled=config.GITHUB_ISSUES_ENABLED,
    )

@app.route('/chat', methods=['POST'])
@require_auth
def chat():
    """
    Endpoint for natural language media requests.
    Expects JSON payload: {"message": "string"}
    """
    data = request.json
    if not data or 'message' not in data:
        return jsonify({"error": "No message provided. Payload must be JSON with a 'message' key."}), 400
    
    user_message = data['message']
    if not isinstance(user_message, str) or len(user_message) > 1000:
        return jsonify({"error": "Message must be a string of 1000 characters or fewer."}), 400

    try:
        log.info(
            'chat.request',
            extra={
                'auth_mode': _auth_mode(),
                'user_hash': hash_user_identifier(session.get('plex_user')),
                'message_length': len(user_message),
            },
        )
        user_info = session.get('plex_user')
        chat_state = session.get('chat_state', {})
        llm_telemetry = {}
        response_text = chat_with_llm(
            user_message,
            user_info=user_info,
            state=chat_state,
            request_id=g.request_id,
            telemetry=llm_telemetry,
        )
        session['chat_state'] = chat_state
        session['last_request_id'] = g.request_id
        _remember_request_context(g.request_id, {
            'request_id': g.request_id,
            'user_message': user_message,
            'response_text': response_text,
            'telemetry': llm_telemetry,
            'app_version': config.APP_VERSION,
            'timestamp': int(time.time()),
        })
        log.info(
            'chat.response',
            extra={
                'auth_mode': _auth_mode(),
                'user_hash': hash_user_identifier(user_info),
                'model': llm_telemetry.get('model'),
                'tool_calls': llm_telemetry.get('tool_calls', []),
                'fallback_tool_parser': llm_telemetry.get('fallback_tool_parser', False),
                'response_length': len(response_text),
            },
        )
        return jsonify({"response": response_text, "request_id": g.request_id, "version": config.APP_VERSION})
    except Exception:
        log.exception("Error processing chat request")
        return jsonify({"error": "Something went wrong. Please try again.", "request_id": g.request_id}), 500


@app.route('/bug-report', methods=['POST'])
@require_auth
def bug_report():
    data = request.json or {}
    description = (data.get('description') or '').strip()
    expected = (data.get('expected') or '').strip()
    request_id = (data.get('request_id') or '').strip()
    include_debug_context = bool(data.get('include_debug_context', True))
    create_github_issue = bool(data.get('create_github_issue', False))

    if not description:
        return jsonify({'error': 'A short bug description is required.'}), 400
    if len(description) > 2000 or len(expected) > 1000:
        return jsonify({'error': 'Bug report fields are too long.'}), 400

    last_request_id = request_id or session.get('last_request_id')
    last_chat = _RECENT_REQUESTS.get(last_request_id, {})
    debug_context = {}
    if include_debug_context:
        debug_context = {
            'last_chat': {
                'request_id': last_chat.get('request_id'),
                'user_message': last_chat.get('user_message'),
                'response_text': last_chat.get('response_text'),
                'telemetry': redact_sensitive_fields(last_chat.get('telemetry', {})),
                'app_version': last_chat.get('app_version'),
                'timestamp': last_chat.get('timestamp'),
            },
            'auth_mode': _auth_mode(),
            'app_version': config.APP_VERSION,
        }

    report = {
        'submitted_at': int(time.time()),
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()),
        'request_id': last_request_id or last_chat.get('request_id') or g.request_id,
        'user_hash': hash_user_identifier(session.get('plex_user')),
        'description': description,
        'expected': expected,
        'include_debug_context': include_debug_context,
        'create_github_issue': create_github_issue,
        'debug_context': debug_context,
    }

    github_issue = None
    github_issue_error = None
    if create_github_issue:
        try:
            github_issue = _create_github_issue(report, debug_context)
            report['github_issue'] = github_issue
        except requests.RequestException as exc:
            github_issue_error = str(exc)
            report['github_issue_error'] = github_issue_error

    append_jsonl(config.BUG_REPORTS_FILE, report)
    log.warning(
        'bug_report.submitted',
        extra={
            'report_request_id': report['request_id'],
            'user_hash': report['user_hash'],
            'include_debug_context': include_debug_context,
            'github_issue_created': bool(github_issue),
            'github_issue_error': github_issue_error,
        },
    )
    return jsonify({
        'status': 'received',
        'request_id': report['request_id'],
        'github_issue': github_issue,
        'github_issue_enabled': config.GITHUB_ISSUES_ENABLED,
        'github_issue_error': github_issue_error,
    })

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "running",
        "service": "Media Bot LLM",
        "credit_cache": "ready" if credit_cache.ready else "building"
    })

@app.route('/cache/rebuild', methods=['POST'])
@require_auth
def rebuild_cache():
    """Trigger a cache rebuild in the background."""
    threading.Thread(target=credit_cache.build, daemon=True).start()
    return jsonify({"status": "rebuilding"})

if __name__ == '__main__':
    print("Starting Media Bot Flask Server...", flush=True)
    # Build actor credit cache in background
    threading.Thread(target=credit_cache.build, daemon=True).start()
    # Listen on all local IP addresses on port 5000 
    app.run(host='0.0.0.0', port=5000, debug=False)
