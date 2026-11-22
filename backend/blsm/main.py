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
        yield

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
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.add_middleware(SecurityHeadersMiddleware)

    app.include_router(router)

    @app.websocket("/ws/servers/{server_id}/console")
    async def websocket_console(websocket: WebSocket, server_id: str) -> None:
        await console_socket(websocket, server_id)

    frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend_dist.is_dir():
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

