import logging

import httpx

from app.config import settings
from app.templating import templates

log = logging.getLogger(__name__)

BREVO_SEND_URL = "https://api.brevo.com/v3/smtp/email"


def render_email(template_name: str, **context) -> str:
    return templates.get_template(f"emails/{template_name}").render(
        base_url=settings.base_url, **context
    )


async def send_email(to: str, subject: str, html: str, to_name: str | None = None) -> None:
    """Send via Brevo. Run through BackgroundTasks; logs and swallows every failure."""
    recipient = {"email": to}
    if to_name:
        recipient["name"] = to_name
    payload = {
        "sender": {"email": settings.mail_from_email, "name": settings.mail_from_name},
        "to": [recipient],
        "subject": subject,
        "htmlContent": html,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                BREVO_SEND_URL,
                json=payload,
                headers={"api-key": settings.brevo_api_key, "accept": "application/json"},
            )
        if resp.status_code >= 300:
            log.error("Brevo send to %s failed: %s %s", to, resp.status_code, resp.text)
    except Exception:
        log.exception("Brevo send to %s failed", to)
