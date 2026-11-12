"""FastAPI application: wires the store, notifier, car runtime, API routes and the SPA.

Run with ``uvicorn psacc.main:app``. Tests build their own app with ``create_app(db_url, gateway_factory)``.
"""

import dataclasses
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from . import setup_flow, store
from .api import GatewayFactory, init_state, pt_detail, start_car, stop_car
from .api import auth as auth_api
from .api import automation as automation_api
from .api import control as control_api
from .api import setup as setup_api
from .api import status as status_api
from .fake_gateway import FakeGateway, default_snapshot
from .auth import LoginRateLimiter, require_login
from .notify import DiscordNotifier, QueuedNotifier

logger = logging.getLogger(__name__)

_login_required = Depends(require_login)

# CSRF guard: every state-changing /api request must carry this header. A cross-site page cannot add a custom
# header without a CORS preflight, and the API grants no CORS, so forged form posts/fetches are refused.
CSRF_HEADER = "X-PSACC"
CSRF_VALUE = "1"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class SpaStaticFiles(StaticFiles):
    """Static files with the SPA fallback: unknown non-API paths serve ``index.html``."""

    async def get_response(self, path: str, scope):
        if scope["path"] == "/api" or scope["path"].startswith("/api/"):
            raise HTTPException(404, "Não encontrado")  # never shadow API routes with the SPA
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:  # what StaticFiles raises
            if exc.status_code != 404:
                raise
        return await super().get_response("index.html", scope)


def _default_gateway_factory():
    """The real PSA gateway, or an in-memory fake car when PSACC_FAKE=1 (UI development without a PSA account)."""
    if os.environ.get("PSACC_FAKE") != "1":
        return setup_flow.load_gateway
    logger.warning("PSACC_FAKE=1: using a FAKE in-memory car, no PSA account is used. Never enable in production.")
    snap = dataclasses.replace(
        default_snapshot("FAKEVIN0000000000"),
        soc=62.0,
        range_km=118.0,
        plugged=True,
        latitude=37.7412,
        longitude=-25.6756,
    )
    gateway = FakeGateway(snap, apply_effect=True)
    return lambda: gateway


def create_app(db_url: str | None = None, gateway_factory: GatewayFactory | None = None) -> FastAPI:
    store.init_db(db_url)  # also creates the config row with the session secret, before any request
    discord = DiscordNotifier(store.get_config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        notifier = QueuedNotifier(discord)
        init_state(app, gateway_factory or _default_gateway_factory(), notifier)
        start_car(app)
        try:
            yield
        finally:
            stop_car(app)
            notifier.stop()

    app = FastAPI(title="PSACC", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.discord = discord
    app.state.login_limiter = LoginRateLimiter()
    app.add_middleware(
        SessionMiddleware, secret_key=store.get_config().session_secret, same_site="lax", https_only=False
    )

    @app.middleware("http")
    async def csrf_guard(request: Request, call_next):
        path = request.url.path
        if (
            request.method not in _SAFE_METHODS
            and (path == "/api" or path.startswith("/api/"))
            and request.headers.get(CSRF_HEADER) != CSRF_VALUE
        ):
            return JSONResponse(
                status_code=403, content={"detail": f"Pedido recusado: falta o cabeçalho {CSRF_HEADER}"}
            )
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": pt_detail(list(exc.errors()))})

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "fake": os.environ.get("PSACC_FAKE") == "1"}

    app.include_router(auth_api.router)
    for module in (status_api, automation_api, control_api, setup_api):
        app.include_router(module.router, dependencies=[_login_required])

    static_dir = os.environ.get("PSACC_STATIC_DIR")
    if static_dir and Path(static_dir).is_dir():
        app.mount("/", SpaStaticFiles(directory=static_dir, html=True), name="spa")
    else:
        logger.info("PSACC_STATIC_DIR not set or missing, not serving the SPA")
    return app


def __getattr__(name: str):
    # ``psacc.main:app`` is built on first access, so importing this module (tests) never opens the real DB
    if name == "app":
        globals()["app"] = application = create_app()
        return application
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
