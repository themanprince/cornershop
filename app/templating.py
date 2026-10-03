import mimetypes
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.services.cart import item_count

BASE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=BASE_DIR / "templates")


SHOP_TIMEZONE = ZoneInfo("Africa/Lagos")

# Label and Bootstrap colour for each order status.
STATUS_BADGES = {
    "pending": ("Payment not completed", "secondary"),
    "paid": ("Paid", "primary"),
    "shipped": ("Shipped", "info"),
    "delivered": ("Delivered", "success"),
    "cancelled": ("Cancelled", "danger"),
}


def naira(kobo: int | None) -> str:
    return f"₦{(kobo or 0) / 100:,.2f}"


def local_datetime(value: datetime | None) -> str:
    """'2 Oct 2026, 14:05' in Lagos time."""
    if value is None:
        return ""
    local = value.astimezone(SHOP_TIMEZONE)
    return f"{local.day} {local:%b %Y, %H:%M}"


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


def cart_count(request: Request) -> int:
    """The navbar's cart badge. Counted at render time, so it reflects changes made earlier in the request."""
    db = getattr(request.state, "db", None)
    user_id = request.session.get("user_id")
    if db is None or user_id is None:
        return 0
    return item_count(db, user_id)


templates.env.filters["naira"] = naira
templates.env.filters["local_datetime"] = local_datetime
templates.env.globals["STATUS_BADGES"] = STATUS_BADGES
templates.env.tests["video"] = is_video
templates.env.globals["pop_flashes"] = pop_flashes
templates.env.globals["current_user"] = current_user
templates.env.globals["cart_count"] = cart_count
templates.env.globals["support_email"] = settings.support_email
templates.env.globals["google_site_verification"] = settings.google_site_verification
