"""Public pages: anyone can view these without signing in (Google verification needs them)."""

from fastapi import APIRouter, Request

from app.templating import templates

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
