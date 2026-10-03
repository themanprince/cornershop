from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.auth import RequireLoginMiddleware
from app.config import settings
from app.db import get_db
from app.routes import admin, auth_routes, pages, payments_routes, shop
from app.templating import BASE_DIR, templates

ERROR_TITLES = {
    401: "Please sign in",
    403: "Access denied",
    404: "Page not found",
}


def share_db_with_templates(request: Request, db: Session = Depends(get_db)) -> None:
    """Lets base.html's cart badge reuse this request's database session."""
    request.state.db = db


def create_app() -> FastAPI:
    app = FastAPI(
        title="Cornershop",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(share_db_with_templates)],
    )
    # Middleware added last runs first, so the session is loaded before the login check.
    app.add_middleware(RequireLoginMiddleware)
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

    app.include_router(pages.router)
    app.include_router(auth_routes.router)
    app.include_router(shop.router)
    app.include_router(payments_routes.router)
    app.include_router(admin.router)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        response = templates.TemplateResponse(
            request,
            "error.html",
            {"status": exc.status_code, "title": ERROR_TITLES.get(exc.status_code, "Something went wrong")},
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )
        # The route's DB session was already closed when it raised; drawing the navbar's
        # cart badge reopened it, so close it again to hand the connection back to the pool.
        db = getattr(request.state, "db", None)
        if db is not None:
            db.close()
        return response

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
