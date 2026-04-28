"""JWT-based admin auth.

Every request flows through `JWTAuthMiddleware` (wired in src/main.py), which:
  1. tries to read a JWT from `Authorization: Bearer <token>` or the
     `access_token` HttpOnly cookie,
  2. on success, sets `request.state.user = <subject>`,
  3. on protected paths without a valid token: redirects browsers to /login
     and returns 401 JSON for everything else.

Routes that need the username call `require_admin(request)`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

from src.config import get_settings

ACCESS_COOKIE = "access_token"
TOKEN_TYPE = "Bearer"

# Paths that bypass auth entirely. /docs, /redoc and /openapi.json are
# whitelisted here too: when enable_docs=false they 404 anyway because we
# don't register them with FastAPI; when true, anyone can read the schema
# (matches FastAPI's default behaviour).
_PUBLIC_EXACT = {
    "/login",
    "/logout",
    "/health",
    "/api/auth/login",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/docs/oauth2-redirect",
    "/favicon.ico",
}
_PUBLIC_PREFIXES = (
    "/static/",
    "/isapi/anpr/",  # Hikvision cameras push events here without auth.
)


def _is_public(path: str) -> bool:
    if path in _PUBLIC_EXACT:
        return True
    for p in _PUBLIC_PREFIXES:
        if path.startswith(p):
            return True
    return False


# ---------- token helpers ----------
def create_access_token(subject: str, expires_minutes: int | None = None) -> str:
    s = get_settings()
    minutes = expires_minutes if expires_minutes is not None else s.jwt_expire_minutes
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
        "typ": "access",
    }
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    s = get_settings()
    return jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])


def _extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
        if token:
            return token
    return request.cookies.get(ACCESS_COOKIE) or None


# ---------- middleware ----------
class JWTAuthMiddleware(BaseHTTPMiddleware):
    """Validate JWT on every request.

    Public paths (login, static, camera push, docs) are let through with
    `request.state.user = None`. Protected paths require a valid token —
    HTML GETs redirect to /login, everything else gets a 401 JSON body.
    """

    async def dispatch(self, request: Request, call_next):
        request.state.user = None

        token = _extract_token(request)
        if token:
            try:
                payload = decode_access_token(token)
                request.state.user = payload.get("sub")
            except jwt.PyJWTError:
                # leave user=None; protected routes will reject below.
                pass

        if _is_public(request.url.path):
            return await call_next(request)

        if not request.state.user:
            return _unauth_response(request)

        return await call_next(request)


def _unauth_response(request: Request):
    accepts_html = "text/html" in request.headers.get("accept", "")
    if request.method == "GET" and accepts_html:
        return RedirectResponse(url="/login", status_code=302)
    return JSONResponse({"detail": "login required"}, status_code=401)


# ---------- per-route helpers ----------
def require_admin(request: Request) -> str:
    """Return the authenticated username; raise 401 if absent.

    The middleware already enforces auth on protected paths, but this is
    still useful for endpoints that want the username string and as a
    second line of defence if the path list ever drifts.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="login required"
        )
    return user


def check_admin_credentials(username: str, password: str) -> bool:
    s = get_settings()
    return username == s.admin_username and password == s.admin_password
