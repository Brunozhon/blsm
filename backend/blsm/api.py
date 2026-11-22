from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
)

from .auth import hash_password, require_admin, verify_password
from .schemas import ConsoleCommand, ModInstall, PasswordPayload, ServerCreate

router = APIRouter(prefix="/api")


def _services(request: Request) -> tuple[Any, Any, Any, Any]:
    return (
        request.app.state.db,
        request.app.state.processes,
        request.app.state.adapters,
        request.app.state.modrinth,
    )


def _server_or_404(request: Request, server_id: str) -> dict[str, Any]:
    server = request.app.state.db.get_server(server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


def _public_server(request: Request, server: dict[str, Any]) -> dict[str, Any]:
    adapter = request.app.state.adapters[server["adapter"]]
    config = {key: value for key, value in server["config"].items() if key != "rcon_password"}
    return {
        "id": server["id"],
        "name": server["name"],
        "adapter": server["adapter"],
        "status": request.app.state.processes.status(server),
        "installed": adapter.is_installed(Path(server["path"])),
        "capabilities": sorted(adapter.capabilities),
        "config": config,
    }


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/auth/status")
async def auth_status(request: Request) -> dict[str, bool]:
    return {"configured": request.app.state.db.get_setting("admin_password") is not None}


@router.post("/auth/setup")
async def auth_setup(request: Request, payload: PasswordPayload) -> dict[str, str]:
    db = request.app.state.db
    if db.get_setting("admin_password") is not None:
        raise HTTPException(status_code=409, detail="Administrator account is already configured")
    db.set_setting("admin_password", hash_password(payload.password))
    return {"token": request.app.state.sessions.create()}


@router.post("/auth/login")
async def auth_login(request: Request, payload: PasswordPayload) -> dict[str, str]:
    encoded = request.app.state.db.get_setting("admin_password")
    if encoded is None or not verify_password(payload.password, encoded):
        await asyncio.sleep(0.35)
        raise HTTPException(status_code=401, detail="Invalid password")
    return {"token": request.app.state.sessions.create()}


@router.get("/auth/socket-token", dependencies=[Depends(require_admin)])
async def socket_token(request: Request) -> dict[str, str]:
    return {
        "token": request.app.state.sessions.create(
            lifetime_seconds=60, purpose="websocket"
        )
    }


@router.get("/adapters", dependencies=[Depends(require_admin)])
async def adapters(request: Request) -> list[dict[str, Any]]:
    return [adapter.public_metadata() for adapter in request.app.state.adapters.values()]


@router.get("/servers", dependencies=[Depends(require_admin)])
async def list_servers(request: Request) -> list[dict[str, Any]]:
    return [_public_server(request, server) for server in request.app.state.db.list_servers()]


@router.post("/servers", dependencies=[Depends(require_admin)], status_code=201)
async def create_server(request: Request, payload: ServerCreate) -> dict[str, Any]:
    db, _, adapters_map, _ = _services(request)
    if payload.adapter not in adapters_map:
        raise HTTPException(status_code=400, detail="Unknown server adapter")
    slug = re.sub(r"[^a-z0-9]+", "-", payload.name.lower()).strip("-") or "server"
    server_id = uuid.uuid4().hex[:12]
    path = (request.app.state.settings.servers_dir / f"{slug}-{server_id[:6]}").resolve()
    if request.app.state.settings.servers_dir not in path.parents:
        raise HTTPException(status_code=400, detail="Invalid server name")
    path.mkdir(parents=True, mode=0o700)
    server = db.create_server(
        {
            "id": server_id,
            "name": payload.name.strip(),
            "adapter": payload.adapter,
            "path": str(path),
            "config": payload.model_dump(exclude={"name", "adapter"}),
        }
    )
    return _public_server(request, server)


@router.get("/servers/{server_id}", dependencies=[Depends(require_admin)])
async def get_server(request: Request, server_id: str) -> dict[str, Any]:
    return _public_server(request, _server_or_404(request, server_id))


@router.post("/servers/{server_id}/install", dependencies=[Depends(require_admin)])
async def install_server(request: Request, server_id: str) -> dict[str, Any]:
    server = _server_or_404(request, server_id)
    if request.app.state.processes.status(server) == "running":
        raise HTTPException(status_code=409, detail="Stop the server before installing")
    if not server["config"].get("accept_eula"):
        raise HTTPException(status_code=400, detail="The Minecraft EULA must be accepted first")
    adapter = request.app.state.adapters[server["adapter"]]
    try:
        config = await adapter.install(Path(server["path"]), server["config"])
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    request.app.state.db.update_server_config(server_id, config)
    return _public_server(request, _server_or_404(request, server_id))


async def _lifecycle(request: Request, server_id: str, action: str) -> dict[str, Any]:
    server = _server_or_404(request, server_id)
    try:
        await getattr(request.app.state.processes, action)(server)
    except (RuntimeError, OSError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _public_server(request, _server_or_404(request, server_id))


@router.post("/servers/{server_id}/start", dependencies=[Depends(require_admin)])
async def start_server(request: Request, server_id: str) -> dict[str, Any]:
    return await _lifecycle(request, server_id, "start")


@router.post("/servers/{server_id}/stop", dependencies=[Depends(require_admin)])
async def stop_server(request: Request, server_id: str) -> dict[str, Any]:
    return await _lifecycle(request, server_id, "stop")


@router.post("/servers/{server_id}/restart", dependencies=[Depends(require_admin)])
async def restart_server(request: Request, server_id: str) -> dict[str, Any]:
    return await _lifecycle(request, server_id, "restart")


@router.get("/servers/{server_id}/stats", dependencies=[Depends(require_admin)])
async def server_stats(request: Request, server_id: str) -> dict[str, Any]:
    return request.app.state.processes.stats(_server_or_404(request, server_id))


@router.post("/servers/{server_id}/console", dependencies=[Depends(require_admin)])
async def console_command(
    request: Request, server_id: str, payload: ConsoleCommand
) -> dict[str, str]:
    if any(char in payload.command for char in "\r\n\x00"):
        raise HTTPException(status_code=400, detail="Console commands must be a single line")
    server = _server_or_404(request, server_id)
    if request.app.state.processes.status(server) != "running":
        raise HTTPException(status_code=409, detail="Server is not running")
    try:
        response = await request.app.state.adapters[server["adapter"]].send_command(
            Path(server["path"]), server["config"], payload.command
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"response": response}


@router.get("/servers/{server_id}/mods/search", dependencies=[Depends(require_admin)])
async def search_mods(
    request: Request, server_id: str, q: str = Query(default="", max_length=128)
) -> list[dict[str, Any]]:
    server = _server_or_404(request, server_id)
    try:
        return await request.app.state.modrinth.search(q, server["config"]["game_version"])
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Modrinth is currently unavailable") from exc


@router.get("/servers/{server_id}/mods", dependencies=[Depends(require_admin)])
async def list_mods(request: Request, server_id: str) -> list[dict[str, Any]]:
    _server_or_404(request, server_id)
    return request.app.state.db.list_mods(server_id)


@router.post("/servers/{server_id}/mods", dependencies=[Depends(require_admin)])
async def install_mod(
    request: Request, server_id: str, payload: ModInstall
) -> dict[str, Any]:
    server = _server_or_404(request, server_id)
    try:
        return await request.app.state.modrinth.install(server, payload.project_id, payload.title)
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.delete("/servers/{server_id}/mods/{project_id}", dependencies=[Depends(require_admin)])
async def remove_mod(request: Request, server_id: str, project_id: str) -> dict[str, bool]:
    server = _server_or_404(request, server_id)
    try:
        request.app.state.modrinth.remove(server, project_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"removed": True}


@router.post(
    "/servers/{server_id}/mods/{project_id}/update", dependencies=[Depends(require_admin)]
)
async def update_mod(request: Request, server_id: str, project_id: str) -> dict[str, Any]:
    server = _server_or_404(request, server_id)
    installed = next(
        (
            mod
            for mod in request.app.state.db.list_mods(server_id)
            if mod["project_id"] == project_id
        ),
        None,
    )
    if installed is None:
        raise HTTPException(status_code=404, detail="This mod is not installed")
    try:
        return await request.app.state.modrinth.install(server, project_id, installed["title"])
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


async def console_socket(websocket: WebSocket, server_id: str) -> None:
    token = websocket.query_params.get("token", "")
    sessions = websocket.app.state.sessions
    if sessions.verify(token, purpose="websocket") is None:
        await websocket.close(code=4401)
        return
    server = websocket.app.state.db.get_server(server_id)
    if server is None:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    log_path = Path(server["path"]) / "blsm-console.log"
    position, initial = websocket.app.state.processes.tail(log_path)
    if initial:
        await websocket.send_json({"type": "history", "data": initial})
    try:
        while True:
            await asyncio.sleep(0.35)
            if not log_path.exists():
                continue
            size = log_path.stat().st_size
            if size < position:
                position = 0
            if size > position:
                with log_path.open("rb") as handle:
                    handle.seek(position)
                    data = handle.read(min(size - position, 64_000))
                    position = handle.tell()
                await websocket.send_json(
                    {"type": "append", "data": data.decode("utf-8", errors="replace")}
                )
    except (WebSocketDisconnect, RuntimeError):
        return
