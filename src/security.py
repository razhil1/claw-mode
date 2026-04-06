"""
NEXUS IDE — Security Module
=============================
CSRF protection, security headers, rate limiting, and input sanitization.
"""

import secrets
import hashlib
import time
import re
import logging
from functools import wraps
from collections import defaultdict

from flask import request, session, jsonify, g, abort

logger = logging.getLogger(__name__)

_RATE_LIMITS: dict[str, list[float]] = defaultdict(list)
_CSRF_TOKENS: dict[str, float] = {}

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "X-XSS-Protection": "1; mode=block",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def apply_security_headers(response):
    for header, value in SECURITY_HEADERS.items():
        response.headers[header] = value
    return response


def generate_csrf_token() -> str:
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def validate_csrf_token() -> bool:
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return True
    if request.path.startswith("/api/telegram/"):
        return True
    if request.path.startswith("/api/chat/"):
        return True
    token = (
        request.headers.get("X-CSRF-Token")
        or request.form.get("csrf_token")
        or (request.get_json(silent=True) or {}).get("csrf_token")
    )
    if not token:
        return True
    return token == session.get("csrf_token")


def get_client_ip() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "127.0.0.1"


def rate_limit(max_requests: int = 60, window_seconds: int = 60):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            ip = get_client_ip()
            now = time.time()
            key = f"{f.__name__}:{ip}"
            timestamps = _RATE_LIMITS[key]
            timestamps[:] = [t for t in timestamps if now - t < window_seconds]
            if len(timestamps) >= max_requests:
                return jsonify({
                    "error": "Rate limit exceeded. Please try again later.",
                    "retry_after": window_seconds,
                }), 429
            timestamps.append(now)
            return f(*args, **kwargs)
        return wrapper
    return decorator


def sanitize_path(path: str) -> str:
    path = path.replace("\x00", "")
    path = re.sub(r"\.\.[\\/]", "", path)
    path = re.sub(r"[\x00-\x1f\x7f]", "", path)
    return path.strip()


def sanitize_input(text: str, max_length: int = 50000) -> str:
    if not text:
        return ""
    text = text[:max_length]
    text = text.replace("\x00", "")
    return text


def validate_json_payload(required_fields: list[str] = None):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            data = request.get_json(silent=True)
            if data is None:
                return jsonify({"error": "Invalid JSON payload"}), 400
            if required_fields:
                missing = [field for field in required_fields if field not in data]
                if missing:
                    return jsonify({
                        "error": f"Missing required fields: {', '.join(missing)}"
                    }), 400
            g.json_data = data
            return f(*args, **kwargs)
        return wrapper
    return decorator


def init_security(app):
    app.jinja_env.globals["csrf_token"] = generate_csrf_token

    @app.after_request
    def add_security_headers(response):
        return apply_security_headers(response)

    @app.before_request
    def check_csrf():
        if not validate_csrf_token():
            abort(403)

    logger.info("Security middleware initialized")
