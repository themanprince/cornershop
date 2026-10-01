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
    pass


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

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            _object_url(filename),
            content=data,
            headers={**_auth_headers(), "Content-Type": content_type},
        )
    if resp.status_code >= 300:
        log.error("Supabase upload failed: %s %s", resp.status_code, resp.text)
        raise UploadError("Image upload failed. Please try again.")
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
