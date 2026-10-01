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


if __name__ == "__main__":
    # Start with `python -m app.main` locally and on any host.
    import os

    import uvicorn

    uvicorn.run(
        "app.main:app",
        # Hosts like Render must reach the app from outside the container.
        host="0.0.0.0" if settings.is_production else "127.0.0.1",
        # Render/Railway/Fly assign the port via $PORT.
        port=int(os.getenv("PORT", "8000")),
        # Trust the host's proxy so the app knows requests arrived over https.
        proxy_headers=True,
        forwarded_allow_ips="*",
        reload=not settings.is_production,
    )
