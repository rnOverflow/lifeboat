"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import func, select, text

from app.api.routers import admin, auth, copilot, dr, ops, runs
from app.clock import Clock
from app.config import Settings, get_settings
from app.db import Database
from app.engine.runtime import Runtime
from app.models import Role, User
from app.observability import HTTP_REQUESTS, REGISTRY, setup_tracing
from app.security import LoginRateLimiter, hash_password

log = logging.getLogger("lifeboat")


def bootstrap_users(rt: Runtime) -> list[str]:
    """Create initial users from LIFEBOAT_BOOTSTRAP_*_PASSWORD env vars when the users table is empty."""
    st = rt.settings
    wanted = {
        "admin": (Role.ADMIN, st.bootstrap_admin_password),
        "operator": (Role.OPERATOR, st.bootstrap_operator_password),
        "approver": (Role.APPROVER, st.bootstrap_approver_password),
        "viewer": (Role.VIEWER, st.bootstrap_viewer_password),
    }
    created: list[str] = []
    with rt.db.session() as s:
        if s.scalar(select(func.count()).select_from(User)):
            return created
        for name, (role, pw) in wanted.items():
            if pw:
                s.add(
                    User(
                        username=name,
                        password_hash=hash_password(pw),
                        role=role,
                        is_active=True,
                        created_at=rt.clock.now(),
                    )
                )
                created.append(name)
    if not created:
        log.warning("no users exist and no LIFEBOAT_BOOTSTRAP_*_PASSWORD set - nobody can log in")
    return created


def create_app(settings: Settings | None = None, clock: Clock | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    db = db or Database(settings.database_url)
    rt = Runtime(settings, db, clock or Clock())

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # noqa: ANN202
        if settings.env != "prod":
            db.create_all()  # prod uses `alembic upgrade head`
        rt.bootstrap()
        bootstrap_users(rt)
        rt.start()
        yield
        await rt.stop()

    app = FastAPI(
        title="Lifeboat DR Platform",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.runtime = rt
    app.state.login_limiter = LoginRateLimiter()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def headers_and_metrics(request: Request, call_next):  # noqa: ANN001, ANN202
        response = await call_next(request)
        route = request.scope.get("route")
        HTTP_REQUESTS.labels(request.method, getattr(route, "path", "unmatched"), str(response.status_code)).inc()
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    for r in (auth.router, dr.router, runs.router, ops.router, admin.router, copilot.router):
        app.include_router(r, prefix="/api/v1")

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz() -> Response:
        try:
            with db.engine.connect() as c:
                c.execute(text("SELECT 1"))
        except Exception:  # noqa: BLE001
            return JSONResponse({"status": "db unavailable"}, status_code=503)
        if rt.is_stale():
            return JSONResponse({"status": "engine stalled"}, status_code=503)
        return JSONResponse({"status": "ready"})

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

    setup_tracing(app, settings.otel_exporter_otlp_endpoint)
    return app


def app_factory() -> FastAPI:  # uvicorn --factory app.main:app_factory
    return create_app()
