import base64
import json
import logging
import mimetypes
import uuid

import httpx
from fastapi import UploadFile

from app.config import settings

log = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024
ALLOWED_PREFIXES = ("image/", "video/")


class UploadError(ValueError):
    """Shown to the admin on the product form, so messages say what to fix."""


KEY_HELP = (
    "SUPABASE_SERVICE_ROLE_KEY must be a secret key: in Supabase go to Project Settings → API Keys "
    "and copy the secret key (sb_secret_…) or the legacy service_role key."
)


def _jwt_role(key: str) -> str | None:
    """The `role` claim of a legacy Supabase JWT key, without verifying it."""
    try:
        payload = key.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("role")
    except (IndexError, ValueError, AttributeError):
        return None


def key_problem(key: str) -> str | None:
    """Explain why `key` can't upload to Storage, or None if it looks like a secret key."""
    if key.startswith("sb_publishable_"):
        return f"The storage key is a publishable key, which can't upload files. {KEY_HELP}"
    if key.startswith("eyJ"):
        role = _jwt_role(key)
        if role and role != "service_role":
            return f"The storage key is the “{role}” key, which can't upload files. {KEY_HELP}"
    return None


def _supabase_message(body_text: str) -> str:
    try:
        body = json.loads(body_text)
    except ValueError:
        return body_text.strip()[:200]
    if not isinstance(body, dict):
        return body_text.strip()[:200]
    return " ".join(str(body[k]) for k in ("error", "message") if body.get(k)) or body_text[:200]


def explain_failure(status_code: int, body_text: str, key: str) -> str:
    """Turn a Supabase Storage error response into a message saying which setting to fix."""
    message = _supabase_message(body_text)
    lowered = message.lower()
    if "bucket not found" in lowered:
        return (
            f"Supabase has no storage bucket named “{settings.supabase_bucket}”. Bucket names are "
            "case-sensitive: set SUPABASE_BUCKET to the exact name shown in Supabase → Storage."
        )
    if any(word in lowered for word in ("row-level security", "unauthorized", "jwt", "api key", "signature")) \
            or status_code in (401, 403):
        return key_problem(key) or f"Supabase rejected the storage key ({message}). {KEY_HELP}"
    if status_code == 413 or "maximum allowed size" in lowered or "too large" in lowered:
        return (
            "This file is bigger than the bucket's size limit. Raise the limit in Supabase → Storage → "
            "bucket settings, or upload a smaller file."
        )
    if "mime" in lowered:
        return (
            "The bucket doesn't accept this file type. In Supabase → Storage → bucket settings, clear "
            "Allowed MIME types or add image/* and video/*."
        )
    if status_code == 404:
        return (
            "Supabase Storage wasn't found at SUPABASE_URL. It should look like "
            "https://<project-ref>.supabase.co with nothing after it."
        )
    return f"Supabase Storage refused the upload (HTTP {status_code}: {message})."


def _auth_headers() -> dict[str, str]:
    key = settings.supabase_service_role_key
    headers = {"apikey": key}
    # Legacy service_role keys are JWTs and go in Authorization too. New
    # sb_secret_ keys are not JWTs; the gateway only accepts them via apikey.
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _object_url(filename: str) -> str:
    return f"{settings.supabase_url}/storage/v1/object/{settings.supabase_bucket}/{filename}"


def public_url(filename: str) -> str:
    return f"{settings.supabase_url}/storage/v1/object/public/{settings.supabase_bucket}/{filename}"


async def upload_image(file: UploadFile) -> str:
    """Upload to Supabase Storage and return the public URL. Never touches local disk."""
    content_type = (file.content_type or "").lower()
    if not content_type.startswith(ALLOWED_PREFIXES):
        raise UploadError("Only image or video files are allowed.")

    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise UploadError("File is larger than 5 MB.")
    if not data:
        raise UploadError("File is empty.")

    ext = (mimetypes.guess_extension(content_type) or "").lstrip(".") or "bin"
    filename = f"{uuid.uuid4().hex}.{ext}"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                _object_url(filename),
                content=data,
                headers={**_auth_headers(), "Content-Type": content_type},
            )
    except httpx.HTTPError as exc:
        log.error("Supabase upload to %s failed: %s", settings.supabase_url, exc)
        raise UploadError(
            f"Couldn't reach Supabase Storage at {settings.supabase_url}. Check SUPABASE_URL "
            "(https://<project-ref>.supabase.co) and that the Supabase project isn't paused."
        ) from exc
    if resp.status_code >= 300:
        log.error("Supabase upload failed: %s %s", resp.status_code, resp.text)
        raise UploadError(explain_failure(resp.status_code, resp.text, settings.supabase_service_role_key))
    return public_url(filename)


async def delete_image(url: str | None) -> None:
    """Best-effort delete of an object previously returned by upload_image. Never raises."""
    if not url:
        return
    prefix = public_url("")
    if not url.startswith(prefix):
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(_object_url(url[len(prefix):]), headers=_auth_headers())
        if resp.status_code >= 300:
            log.warning("Supabase delete failed for %s: %s %s", url, resp.status_code, resp.text)
    except Exception:
        log.exception("Supabase delete failed for %s", url)


# Flag a wrong key in the logs at startup, before anyone tries an upload.
if _problem := key_problem(settings.supabase_service_role_key):
    log.error("Supabase Storage uploads will fail: %s", _problem)
