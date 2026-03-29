import sys
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

import os
import threading
import hmac
from functools import wraps
from flask import Flask, request, jsonify, session, redirect, url_for, render_template
from dotenv import load_dotenv
from llm import chat_with_llm
from api.radarr import credit_cache
import plex_auth
from config import FLASK_SECRET_KEY

load_dotenv()
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

BOT_API_KEY = os.getenv('BOT_API_KEY')


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
    return render_template('chat.html', user=session['plex_user'])

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
    try:
        print(f"Received message: {user_message}", flush=True)
        response_text = chat_with_llm(user_message)
        print(f"Bot response: {response_text[:200]}", flush=True)
        return jsonify({"response": response_text})
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"Endpoint Error Traceback: {error_trace}")
        return jsonify({"error": error_trace}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "running",
        "service": "Media Bot LLM",
        "actor_cache": "ready" if credit_cache.ready else "building"
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
