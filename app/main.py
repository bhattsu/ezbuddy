import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from app.api.dependencies.rate_limit import limiter
from app.api.routes import all_routes
from app.config.settings import get_settings
from app.observability.logging_config import configure_logging, request_id_ctx
from app.services.aim_factory import shutdown_rds, startup_rds

_settings = get_settings()
configure_logging(log_level=_settings.LOG_LEVEL, log_format=_settings.LOG_FORMAT)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and graceful shutdown drain."""
    app.state.request_count = 0
    app.state.draining = False
    await startup_rds(app)
    yield
    app.state.draining = True
    drain_seconds = get_settings().SHUTDOWN_DRAIN_SECONDS
    deadline = asyncio.get_event_loop().time() + drain_seconds
    while getattr(app.state, "request_count", 0) > 0 and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.5)
    logger.info("Shutdown drain complete")
    await shutdown_rds(app)


class RequestIDMiddleware:
    """ASGI middleware: attach X-Request-ID for HTTP only (does not break WebSockets)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        request_id = headers.get("x-request-id") or str(uuid.uuid4())
        token = request_id_ctx.set(request_id)

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                raw_headers = list(message.get("headers") or [])
                raw_headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": raw_headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            request_id_ctx.reset(token)


class DrainMiddleware:
    """ASGI middleware: 503 while draining; skip WebSocket upgrades."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        app_state = scope.get("app")
        state = getattr(app_state, "state", None) if app_state else None
        if state is not None and getattr(state, "draining", False):
            response = JSONResponse(
                status_code=503,
                content={"detail": "Server is shutting down"},
                headers={"Retry-After": "30"},
            )
            await response(scope, receive, send)
            return

        if state is not None:
            state.request_count = getattr(state, "request_count", 0) + 1
        try:
            await self.app(scope, receive, send)
        finally:
            if state is not None:
                state.request_count = max(0, getattr(state, "request_count", 1) - 1)


app = FastAPI(
    title="US Legal Pro filing chat",
    description="WebSocket filing assistant, document analysis, and court-form questions",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
# CORS first (outermost added last in Starlette — add CORS last so it wraps others)
app.add_middleware(DrainMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unexpected server error: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Unexpected server error"},
    )


@app.options("/{path:path}")
async def preflight_handler():
    return JSONResponse(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS, PATCH, DELETE",
            "Access-Control-Allow-Headers": "*",
        },
    )


app.include_router(all_routes)


def custom_openapi():
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    from app.api.endpoints.template_ingest import patch_template_ingest_openapi

    patch_template_ingest_openapi(schema)
    return schema


app.openapi = custom_openapi

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
