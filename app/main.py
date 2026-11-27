"""App factory. HTTP layer only: wires routers, middleware and error handlers."""
from fastapi import FastAPI

from app.api.errors import register_error_handlers
from app.api.middleware import BodySizeLimitMiddleware
from app.api.routes import admin, alerts, costs, health, images, jobs, posts, suggestions
from app.config import get_settings
from app.logging_config import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="AI Image Understanding & Content Matching Engine",
        version="0.1.0",
        description="Vision tagging, semantic matching and a mismatch guard. FlyRank Backend capstone.",
    )
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_body_bytes)
    register_error_handlers(app)
    for module in (health, jobs, images, posts, suggestions, admin, costs, alerts):
        app.include_router(module.router)
    return app


app = create_app()
