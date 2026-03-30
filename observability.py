import contextlib
import contextvars
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone

try:
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration
except ImportError:  # pragma: no cover - optional dependency
    sentry_sdk = None
    FlaskIntegration = None

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
except ImportError:  # pragma: no cover - optional dependency
    trace = None
    Resource = None
    TracerProvider = None
    BatchSpanProcessor = None
    OTLPSpanExporter = None

_REQUEST_ID = contextvars.ContextVar("request_id", default="-")
_STANDARD_LOG_ATTRS = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info", "thread",
    "threadName", "taskName",
}
_TRACER = None


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
        }

        for key, value in record.__dict__.items():
            if key.startswith("_") or key in _STANDARD_LOG_ATTRS:
                continue
            payload[key] = _json_safe(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        return json.dumps(payload, ensure_ascii=True)


def _json_safe(value):
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def configure_logging(level="INFO"):
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def set_request_id(request_id):
    _REQUEST_ID.set(request_id or "-")


def clear_request_id():
    _REQUEST_ID.set("-")


def get_request_id():
    return _REQUEST_ID.get()


def redact_sensitive_fields(data):
    if not isinstance(data, dict):
        return data

    redacted = {}
    for key, value in data.items():
        key_lower = str(key).lower()
        if any(token in key_lower for token in ("key", "token", "secret", "password", "pass", "cookie", "authorization")):
            redacted[key] = "[REDACTED]"
        elif isinstance(value, dict):
            redacted[key] = redact_sensitive_fields(value)
        elif isinstance(value, list):
            redacted[key] = [redact_sensitive_fields(item) if isinstance(item, dict) else _json_safe(item) for item in value]
        else:
            redacted[key] = _json_safe(value)
    return redacted


def hash_user_identifier(user_info):
    if not user_info:
        return None
    username = user_info.get("username", "")
    if not username:
        return None
    return hashlib.sha256(username.encode("utf-8")).hexdigest()[:16]


def append_jsonl(file_path, payload):
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    with open(file_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def init_observability(service_name, environment=None, sentry_dsn=None, otlp_endpoint=None):
    global _TRACER

    if sentry_dsn and sentry_sdk and FlaskIntegration:
        sentry_sdk.init(
            dsn=sentry_dsn,
            environment=environment,
            integrations=[FlaskIntegration()],
            traces_sample_rate=0.0,
            send_default_pii=False,
        )

    if otlp_endpoint and trace and Resource and TracerProvider and BatchSpanProcessor and OTLPSpanExporter:
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
        trace.set_tracer_provider(provider)
        _TRACER = trace.get_tracer(service_name)
    elif trace:
        _TRACER = trace.get_tracer(service_name)


@contextlib.contextmanager
def start_span(name, attributes=None):
    if _TRACER is None:
        yield None
        return

    with _TRACER.start_as_current_span(name) as span:
        span.set_attribute("request.id", get_request_id())
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, str(value))
        yield span
