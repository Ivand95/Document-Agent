import base64
import hashlib
import logging
import os
import secrets
import threading
import time
import uuid
from urllib.parse import urlparse

from jose import JWTError, jwt

from indexing_runner import NON_PROD_ENVS

log = logging.getLogger("security")

ALGORITHM = "HS256"
ISSUER = "document-agent"
AUDIENCE = "document-agent-ui"
DEFAULT_SECRET = "change-me-in-production"
MIN_SECRET_LENGTH = 32

ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "60"))
SESSION_MAX_HOURS = int(os.getenv("SESSION_MAX_HOURS", "12"))  # absolute limit, refreshing doesn't extend it
LOGIN_STATE_SECONDS = 600
MAX_PENDING_LOGINS = 1000
MAX_MESSAGE_CHARS = int(os.getenv("MAX_MESSAGE_CHARS", "4000"))


def is_production() -> bool:
    """Anything but an explicit development APP_ENV counts as production (fail-closed)."""
    return os.getenv("APP_ENV", "production").strip().lower() not in NON_PROD_ENVS


def jwt_secret() -> str:
    return os.getenv("JWT_SECRET_KEY", DEFAULT_SECRET)


def normalize_origin(value: str) -> str:
    """"https://app.example.com/path/" -> "https://app.example.com"; "*" stays as is."""
    value = value.strip()
    if value == "*":
        return value
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return value.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}"


def frontend_origins() -> list:
    """FRONTEND_URL accepts several origins separated by commas."""
    return [normalize_origin(v) for v in os.getenv("FRONTEND_URL", "").split(",") if v.strip()]


def origin_allowed(origin) -> bool:
    """Whether a browser Origin may talk to the API. Development without FRONTEND_URL allows any."""
    allowed = frontend_origins()
    if not allowed or "*" in allowed:
        return not is_production()
    if not origin:
        return not is_production()  # browsers always send it; only local tools don't
    return normalize_origin(origin) in allowed


def validate_settings():
    """Refuses to start in production with unsafe configuration."""
    if not is_production():
        if jwt_secret() == DEFAULT_SECRET:
            log.warning("JWT_SECRET_KEY is the development default; fine locally, never in production")
        return
    problems = []
    secret = jwt_secret()
    if secret == DEFAULT_SECRET or len(secret) < MIN_SECRET_LENGTH:
        problems.append(f"JWT_SECRET_KEY must be set to a random value of at least {MIN_SECRET_LENGTH} characters")
    origins = frontend_origins()
    if not origins or "*" in origins:
        problems.append("FRONTEND_URL must list the exact frontend origin(s), not be empty or '*'")
    if problems:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


# --- Session tokens ---
_revoked = {}  # jti -> exp. In memory: valid while there is a single API replica (tokens are short-lived)
_revoked_lock = threading.Lock()


def create_access_token(data: dict, session_start=None) -> str:
    """Short-lived token. `session_start` carries the original login time through refreshes."""
    now = int(time.time())
    session_start = session_start or now
    expires = min(now + ACCESS_TOKEN_MINUTES * 60, session_start + SESSION_MAX_HOURS * 3600)
    claims = {
        **data,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": expires,
        "session_start": session_start,
    }
    return jwt.encode(claims, jwt_secret(), algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """Verifies signature, issuer, audience, expiry and revocation. Raises JWTError."""
    claims = jwt.decode(
        token,
        jwt_secret(),
        algorithms=[ALGORITHM],
        audience=AUDIENCE,
        issuer=ISSUER,
        # python-jose only checks `aud` and `iss` when the token carries them
        options={"require_aud": True, "require_iss": True, "require_exp": True, "require_jti": True},
    )
    with _revoked_lock:
        if claims.get("jti") in _revoked:
            raise JWTError("Token revoked")
    return claims


def revoke_token(claims: dict):
    now = time.time()
    with _revoked_lock:
        for jti in [j for j, exp in _revoked.items() if exp < now]:
            del _revoked[jti]
        _revoked[claims["jti"]] = claims["exp"]


def refresh_token(claims: dict) -> str:
    """New token for the same session. Raises JWTError once the session reached its absolute limit."""
    session_start = claims.get("session_start", 0)
    if session_start + SESSION_MAX_HOURS * 3600 <= time.time():
        raise JWTError("Session expired")
    keep = {k: claims[k] for k in ("sub", "name", "department", "position") if k in claims}
    return create_access_token(keep, session_start=session_start)


# --- Login: state + PKCE ---
_pending_logins = {}  # state -> (code_verifier, opener origin, expires)
_pending_lock = threading.Lock()


def new_login(opener_origin) -> tuple:
    """Returns (state, code_challenge). The verifier and the opener origin stay on the server."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(32)
    now = time.time()
    with _pending_lock:
        for key in [k for k, v in _pending_logins.items() if v[2] < now]:
            del _pending_logins[key]
        if len(_pending_logins) >= MAX_PENDING_LOGINS:
            _pending_logins.pop(next(iter(_pending_logins)))
        _pending_logins[state] = (verifier, opener_origin, now + LOGIN_STATE_SECONDS)
    return state, challenge


def consume_login(state) -> tuple:
    """One-time use. Returns (code_verifier, opener origin), or (None, None) if unknown or expired."""
    with _pending_lock:
        entry = _pending_logins.pop(state or "", None)
    if entry is None or entry[2] < time.time():
        return None, None
    return entry[0], entry[1]
