"""Public pages: anyone can view these without signing in (Google verification needs them)."""

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from app.templating import BASE_DIR, templates

router = APIRouter()

# Update when the policy text changes.
POLICIES_LAST_UPDATED = "2 October 2026"


@router.get("/")
def landing(request: Request):
    return templates.TemplateResponse(request, "pages/landing.html", {})


@router.get("/privacy")
def privacy(request: Request):
    return templates.TemplateResponse(
        request, "pages/privacy.html", {"last_updated": POLICIES_LAST_UPDATED}
    )


@router.get("/terms")
def terms(request: Request):
    return templates.TemplateResponse(
        request, "pages/terms.html", {"last_updated": POLICIES_LAST_UPDATED}
    )


@router.get("/manifest.json", include_in_schema=False)
def manifest():
    return FileResponse(
        BASE_DIR / "static" / "manifest.json",
        media_type="application/manifest+json",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(
        BASE_DIR / "static" / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )

