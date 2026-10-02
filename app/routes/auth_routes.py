import logging

from authlib.integrations.starlette_client import OAuthError
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth import oauth, safe_next
from app.config import settings
from app.db import get_db
from app.models import User
from app.services.mail import render_email, send_email
from app.templating import flash, templates

log = logging.getLogger(__name__)

router = APIRouter()

# Keys that belong to the signed-in user; cleared on logout.
USER_KEYS = ("user_id", "email", "name", "avatar_url")


def upsert_user(db: Session, email: str, name: str | None, avatar_url: str | None) -> tuple[User, bool]:
    """Insert or update by email. Returns (user, created). Safe if two logins race."""
    new_id = db.scalar(
        insert(User)
        .values(email=email, name=name, avatar_url=avatar_url)
        .on_conflict_do_nothing(index_elements=[User.email])
        .returning(User.id)
    )
    user = db.scalar(select(User).where(User.email == email))
    if new_id is None:
        user.name = name or user.name
        user.avatar_url = avatar_url or user.avatar_url
    db.commit()
    return user, new_id is not None


@router.get("/login")
def login_page(request: Request):
    if "user_id" in request.session:
        return RedirectResponse(safe_next(request.session.pop("next", None)), status_code=303)
    if "next" in request.query_params:
        request.session["next"] = safe_next(request.query_params["next"])
    return templates.TemplateResponse(request, "auth/login.html", {})


@router.get("/auth/google")
async def login_google(request: Request):
    # Must match an "Authorized redirect URI" on the Google OAuth client exactly.
    return await oauth.google.authorize_redirect(request, f"{settings.base_url}/auth/callback")


@router.get("/auth/callback")
async def auth_callback(
    request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as exc:
        log.warning("Google sign-in failed: %s", exc)
        flash(request, "Google sign-in failed. Please try again.", "danger")
        return RedirectResponse("/login", status_code=303)

    info = token.get("userinfo") or {}
    email = (info.get("email") or "").strip().lower()
    if not email or not info.get("email_verified"):
        flash(request, "Your Google account email is not verified.", "danger")
        return RedirectResponse("/login", status_code=303)

    user, created = upsert_user(db, email, info.get("name"), info.get("picture"))

    request.session.update(
        user_id=user.id, email=user.email, name=user.name, avatar_url=user.avatar_url
    )
    if created:
        background_tasks.add_task(
            send_email,
            user.email,
            f"Welcome to {settings.mail_from_name}",
            render_email("welcome.html", name=user.name or user.email),
            to_name=user.name,
        )
        flash(request, "Welcome! Your account has been created.", "success")
    return RedirectResponse(safe_next(request.session.pop("next", None)), status_code=303)


@router.post("/logout")
def logout(request: Request):
    for key in USER_KEYS + ("next",):
        request.session.pop(key, None)
    flash(request, "You have been signed out.", "info")
    return RedirectResponse("/login", status_code=303)
