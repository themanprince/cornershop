from authlib.integrations.starlette_client import OAuth
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings
from app.templating import current_user

oauth = OAuth()
oauth.register(
    name="google",
    client_id=settings.google_client_id,
    client_secret=settings.google_client_secret,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

# Everything else requires a signed-in user.
PUBLIC_PATHS = {
    "/",
    "/privacy",
    "/terms",
    "/login",
    "/auth/google",
    "/auth/callback",
    "/health",
    # Paystack's servers call this; it is authenticated by its HMAC signature instead.
    "/payments/webhook",
    "/manifest.json",
    "/sw.js",
}
PUBLIC_PREFIXES = ("/static/",)


def safe_next(value: str | None, default: str = "/shop") -> str:
    """Only allow same-site relative paths, so `next` can't be an open redirect."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return default
    return value


class RequireLoginMiddleware(BaseHTTPMiddleware):
    """Redirects anonymous visitors to /login. Must sit inside SessionMiddleware."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if (
            path in PUBLIC_PATHS
            or path.startswith(PUBLIC_PREFIXES)
            or "user_id" in request.session
        ):
            return await call_next(request)
        # Only remember real page visits. Browsers also fetch things like /favicon.ico
        # in the background, and those must not become the post-sign-in destination.
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            target = path + (f"?{request.url.query}" if request.url.query else "")
            request.session["next"] = safe_next(target)
        return RedirectResponse("/login", status_code=303)


def require_user(request: Request) -> dict:
    user = current_user(request)
    if user is None:
        # The middleware normally catches this first.
        raise HTTPException(status_code=401)
    return user


def require_admin(request: Request) -> dict:
    user = require_user(request)
    if not user["is_admin"]:
        raise HTTPException(status_code=403)
    return user
