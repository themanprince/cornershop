from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.db import get_db

BASE_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["naira"] = lambda kobo: f"₦{(kobo or 0) / 100:,.2f}"


def flash(request: Request, message: str, category: str = "info") -> None:
    request.session.setdefault("_flashes", []).append([category, message])


def pop_flashes(request: Request) -> list:
    return request.session.pop("_flashes", [])


templates.env.globals["pop_flashes"] = pop_flashes


def create_app() -> FastAPI:
    app = FastAPI(title="Cornershop", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        same_site="lax",
        https_only=settings.is_production,
        max_age=60 * 60 * 24 * 14,
    )
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    @app.get("/health")
    def health(db: Session = Depends(get_db)):
        db.execute(text("select 1"))
        return JSONResponse({"status": "ok"})

    @app.get("/")
    def index(request: Request):
        # Replaced by the product grid in M1.
        return templates.TemplateResponse(request, "shop/index.html", {})

    return app


app = create_app()
