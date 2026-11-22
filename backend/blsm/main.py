from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .adapters import MinecraftFabricAdapter
from .api import console_socket, router
from .auth import SessionManager, configure_admin
from .config import Settings, read_or_create_secret
from .db import Database
from .services.backups import BackupService
from .services.modrinth import ModrinthService
from .services.processes import ProcessManager


class SecurityHeadersMiddleware:
    """Small pure-ASGI middleware that avoids buffering streaming responses."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(
                    [
                        (b"x-content-type-options", b"nosniff"),
                        (b"x-frame-options", b"DENY"),
                        (b"referrer-policy", b"no-referrer"),
                        (
                            b"permissions-policy",
                            b"camera=(), microphone=(), geolocation=()",
                        ),
                    ]
                )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


def find_frontend_dist(settings: Settings) -> Path | None:
    """Locate the built dashboard in production and source checkouts."""
    candidates = [settings.frontend_dir] if settings.frontend_dir else []
    candidates.extend(
        [
            Path.cwd() / "frontend" / "dist",
            Path(__file__).resolve().parents[2] / "frontend" / "dist",
        ]
    )
    for candidate in candidates:
        resolved = candidate.resolve()
        if (resolved / "index.html").is_file():
            return resolved
    return None


def create_app(settings: Settings | None = None) -> FastAPI:
    current_settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        current_settings.ensure_directories()
        db = Database(current_settings.database_path)
        db.initialize()
        configure_admin(db, current_settings.admin_password)
        adapters = {adapter.key: adapter for adapter in [MinecraftFabricAdapter()]}
        app.state.settings = current_settings
        app.state.db = db
        app.state.adapters = adapters
        app.state.sessions = SessionManager(read_or_create_secret(current_settings.data_dir))
        app.state.processes = ProcessManager(db, adapters)
        app.state.modrinth = ModrinthService(db)
        app.state.backups = BackupService(
            db,
            app.state.processes,
            current_settings.backups_dir,
            current_settings.servers_dir,
        )
        await app.state.backups.start()
        try:
            yield
        finally:
            await app.state.backups.shutdown()
            await app.state.processes.shutdown()

    app = FastAPI(
        title="Bruno's Little Server Manager",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(current_settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.add_middleware(SecurityHeadersMiddleware)

    app.include_router(router)

    @app.websocket("/ws/servers/{server_id}/console")
    async def websocket_console(websocket: WebSocket, server_id: str) -> None:
        await console_socket(websocket, server_id)

    frontend_dist = find_frontend_dist(current_settings)
    if frontend_dist is not None:
        assets = frontend_dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            requested = (frontend_dist / path).resolve()
            if frontend_dist in requested.parents and requested.is_file():
                return FileResponse(requested)
            return FileResponse(frontend_dist / "index.html")

    return app


app = create_app()

