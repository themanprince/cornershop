import mimetypes
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.services.cart import item_count

BASE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=BASE_DIR / "templates")


def naira(kobo: int | None) -> str:
    return f"₦{(kobo or 0) / 100:,.2f}"


def is_video(url: str | None) -> bool:
    mime, _ = mimetypes.guess_type(url or "")
    return bool(mime and mime.startswith("video/"))


def flash(request: Request, message: str, category: str = "info") -> None:
    request.session.setdefault("_flashes", []).append([category, message])


def pop_flashes(request: Request) -> list:
    return request.session.pop("_flashes", [])


def current_user(request: Request) -> dict | None:
    """The signed-in user from the session, or None. Admin status is computed live."""
    if "user_id" not in request.session:
        return None
    email = request.session.get("email", "")
    return {
        "id": request.session["user_id"],
        "email": email,
        "name": request.session.get("name") or email,
        "avatar_url": request.session.get("avatar_url"),
        "is_admin": email.lower() in settings.admin_emails,
    }


templates.env.filters["naira"] = naira
templates.env.tests["video"] = is_video
templates.env.globals["pop_flashes"] = pop_flashes
templates.env.globals["current_user"] = current_user
templates.env.globals["cart_count"] = lambda request: item_count(request.session)
templates.env.globals["support_email"] = settings.support_email
templates.env.globals["google_site_verification"] = settings.google_site_verification
