from __future__ import annotations

import asyncio
import re
import shutil
import uuid
from pathlib import Path
from typing import Annotated, Any, NoReturn

import httpx
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import FileResponse, Response

from .adapters.base import AdapterCapability
from .auth import hash_password, require_admin, verify_password
from .schemas import (
    BackupPolicyUpdate,
    ConsoleCommand,
    FileContentUpdate,
    FileCreate,
    FileRename,
    GameRulesUpdate,
    ModInstall,
    ModpackBuild,
    ModPlanApply,
    PasswordPayload,
    PlayerAccess,
    ServerCreate,
    ServerDelete,
    ServerSettingsUpdate,
    ServerUpdate,
    WhitelistSetting,
)
from .services.backups import BackupBusy, BackupError
from .services.files import FileConflict, FileManagerError, FileTooLarge
from .services.mod_resolution import ModResolutionError
from .services.modrinth import PACK_MIME

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


def _require_no_backup_operation(request: Request, server_id: str) -> None:
    operation = request.app.state.backups.operation(server_id)
    if operation:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot modify the server while {operation.replace('_', ' ')} is in progress",
        )


def _public_server(request: Request, server: dict[str, Any]) -> dict[str, Any]:
    adapter = request.app.state.adapters[server["adapter"]]
    adapter_config = adapter.public_config(Path(server["path"]), server["config"])
    config = {key: value for key, value in adapter_config.items() if key != "rcon_password"}
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


@router.get("/adapters/{adapter_key}/versions", dependencies=[Depends(require_admin)])
async def adapter_versions(request: Request, adapter_key: str) -> list[str]:
    adapter = request.app.state.adapters.get(adapter_key)
    if adapter is None:
        raise HTTPException(status_code=404, detail="Server adapter not found")
    try:
        return await adapter.available_versions()
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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


@router.patch("/servers/{server_id}", dependencies=[Depends(require_admin)])
async def update_server(
    request: Request, server_id: str, payload: ServerUpdate
) -> dict[str, Any]:
    server = _server_or_404(request, server_id)
    _require_no_backup_operation(request, server_id)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Server name cannot be blank")
    adapter = request.app.state.adapters[server["adapter"]]
    if payload.config and AdapterCapability.MANAGEMENT not in adapter.capabilities:
        raise HTTPException(
            status_code=404,
            detail="This server does not expose management settings",
        )
    if payload.config and request.app.state.processes.status(server) == "running":
        raise HTTPException(status_code=409, detail="Stop the server before changing it")
    request.app.state.processes.block_server_operations(server_id)
    try:
        if payload.config:
            async with adapter.configuration_transaction(
                Path(server["path"]), server["config"], payload.config
            ) as config:
                request.app.state.db.update_server_details(server_id, name, config)
        else:
            request.app.state.db.update_server_details(
                server_id, name, server["config"]
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        request.app.state.processes.unblock_server_operations(server_id)
    return _public_server(request, _server_or_404(request, server_id))


@router.delete("/servers/{server_id}", dependencies=[Depends(require_admin)])
async def delete_server(
    request: Request, server_id: str, payload: ServerDelete
) -> dict[str, bool]:
    server = _server_or_404(request, server_id)
    _require_no_backup_operation(request, server_id)
    if payload.confirmation != server["name"]:
        raise HTTPException(status_code=400, detail="Type the exact server name to confirm")
    if request.app.state.processes.status(server) == "running":
        raise HTTPException(status_code=409, detail="Stop the server before deleting it")
    servers_dir = request.app.state.settings.servers_dir.resolve()
    server_path = Path(server["path"])
    if server_path.is_symlink() or server_path.resolve().parent != servers_dir:
        raise HTTPException(status_code=409, detail="Refusing to delete an unsafe server path")

    request.app.state.processes.block_server_operations(server_id)
    try:
        await request.app.state.backups.purge_server(server_id)
        if server_path.exists():
            shutil.rmtree(server_path)
        request.app.state.db.delete_server(server_id)
    except BackupError as exc:
        _raise_backup_error(exc)
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not completely delete the server: {exc}"
        ) from exc
    finally:
        request.app.state.processes.unblock_server_operations(server_id)
    return {"deleted": True}


@router.post("/servers/{server_id}/install", dependencies=[Depends(require_admin)])
async def install_server(request: Request, server_id: str) -> dict[str, Any]:
    server = _server_or_404(request, server_id)
    _require_no_backup_operation(request, server_id)
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


def _capability_adapter(
    request: Request,
    server: dict[str, Any],
    capability: AdapterCapability,
    detail: str,
) -> Any:
    adapter = request.app.state.adapters[server["adapter"]]
    if capability not in adapter.capabilities:
        raise HTTPException(status_code=404, detail=detail)
    return adapter


@router.get("/servers/{server_id}/game-rules", dependencies=[Depends(require_admin)])
async def game_rules(request: Request, server_id: str) -> dict[str, Any]:
    server = _server_or_404(request, server_id)
    adapter = _capability_adapter(
        request,
        server,
        AdapterCapability.GAME_RULES,
        "This server does not expose game rules",
    )
    running = request.app.state.processes.status(server) == "running"
    try:
        rules = await adapter.game_rules(Path(server["path"]), server["config"], running)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"rules": rules, "online": running}


@router.put("/servers/{server_id}/game-rules", dependencies=[Depends(require_admin)])
async def update_game_rules(
    request: Request, server_id: str, payload: GameRulesUpdate
) -> dict[str, Any]:
    server = _server_or_404(request, server_id)
    adapter = _capability_adapter(
        request,
        server,
        AdapterCapability.GAME_RULES,
        "This server does not expose game rules",
    )
    if request.app.state.processes.status(server) != "running":
        raise HTTPException(status_code=409, detail="Start the server to change game rules")
    try:
        rules = await adapter.update_game_rules(
            Path(server["path"]), server["config"], payload.values
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"rules": rules, "online": True}


def _settings_adapter(request: Request, server: dict[str, Any]) -> Any:
    return _capability_adapter(
        request,
        server,
        AdapterCapability.SETTINGS,
        "This server does not expose editable settings",
    )


def _settings_view(adapter: Any, server: dict[str, Any]) -> dict[str, Any]:
    try:
        settings = adapter.server_settings(Path(server["path"]), server["config"])
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"settings": settings, "restart_required": True}


@router.get("/servers/{server_id}/settings", dependencies=[Depends(require_admin)])
async def server_settings(request: Request, server_id: str) -> dict[str, Any]:
    server = _server_or_404(request, server_id)
    return _settings_view(_settings_adapter(request, server), server)


@router.put("/servers/{server_id}/settings", dependencies=[Depends(require_admin)])
async def update_server_settings(
    request: Request, server_id: str, payload: ServerSettingsUpdate
) -> dict[str, Any]:
    server = _server_or_404(request, server_id)
    _require_no_backup_operation(request, server_id)
    adapter = _settings_adapter(request, server)
    if request.app.state.processes.status(server) == "running":
        raise HTTPException(status_code=409, detail="Stop the server before changing settings")
    try:
        config = adapter.update_server_settings(
            Path(server["path"]), server["config"], payload.values
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.app.state.db.update_server_config(server_id, config)
    updated = _server_or_404(request, server_id)
    return _settings_view(adapter, updated)


def _file_server(request: Request, server_id: str) -> tuple[dict[str, Any], Path]:
    server = _server_or_404(request, server_id)
    _capability_adapter(
        request,
        server,
        AdapterCapability.FILES,
        "This server does not expose managed files",
    )
    root = Path(server["path"])
    managed_root = request.app.state.settings.servers_dir.resolve()
    if root.is_symlink() or root.resolve().parent != managed_root:
        raise HTTPException(status_code=409, detail="The managed server path is unsafe")
    return server, root


def _raise_file_error(exc: FileManagerError) -> NoReturn:
    if isinstance(exc, FileTooLarge):
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    if isinstance(exc, FileConflict):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/servers/{server_id}/files", dependencies=[Depends(require_admin)])
async def server_files(
    request: Request,
    server_id: str,
    path: str = Query(default=".", max_length=1024),
) -> dict[str, object]:
    _, root = _file_server(request, server_id)
    try:
        return request.app.state.files.list_directory(root, path)
    except FileManagerError as exc:
        _raise_file_error(exc)


@router.get("/servers/{server_id}/files/content", dependencies=[Depends(require_admin)])
async def server_file_content(
    request: Request,
    server_id: str,
    path: str = Query(min_length=1, max_length=1024),
) -> dict[str, object]:
    server, root = _file_server(request, server_id)
    try:
        result = request.app.state.files.read_text(root, path)
    except FileManagerError as exc:
        _raise_file_error(exc)
    result["server_running"] = request.app.state.processes.status(server) == "running"
    return result


@router.put("/servers/{server_id}/files/content", dependencies=[Depends(require_admin)])
async def update_server_file(
    request: Request,
    server_id: str,
    payload: FileContentUpdate,
    path: str = Query(min_length=1, max_length=1024),
) -> dict[str, object]:
    server, root = _file_server(request, server_id)
    _require_no_backup_operation(request, server_id)
    try:
        result = request.app.state.files.write_text(
            root,
            path,
            payload.content,
            payload.revision,
        )
    except FileManagerError as exc:
        _raise_file_error(exc)
    result["server_running"] = request.app.state.processes.status(server) == "running"
    return result


@router.post(
    "/servers/{server_id}/files", dependencies=[Depends(require_admin)], status_code=201
)
async def create_server_file(
    request: Request, server_id: str, payload: FileCreate
) -> dict[str, object]:
    _, root = _file_server(request, server_id)
    _require_no_backup_operation(request, server_id)
    try:
        return request.app.state.files.create(root, payload.path, payload.kind)
    except FileManagerError as exc:
        _raise_file_error(exc)


@router.put("/servers/{server_id}/files/rename", dependencies=[Depends(require_admin)])
async def rename_server_file(
    request: Request, server_id: str, payload: FileRename
) -> dict[str, str]:
    _, root = _file_server(request, server_id)
    _require_no_backup_operation(request, server_id)
    try:
        return request.app.state.files.rename(root, payload.path, payload.new_name)
    except FileManagerError as exc:
        _raise_file_error(exc)


@router.delete("/servers/{server_id}/files", dependencies=[Depends(require_admin)])
async def delete_server_file(
    request: Request,
    server_id: str,
    path: str = Query(min_length=1, max_length=1024),
) -> dict[str, bool]:
    _, root = _file_server(request, server_id)
    _require_no_backup_operation(request, server_id)
    try:
        await asyncio.to_thread(request.app.state.files.delete, root, path)
    except FileManagerError as exc:
        _raise_file_error(exc)
    return {"deleted": True}


@router.post("/servers/{server_id}/files/upload", dependencies=[Depends(require_admin)])
async def upload_server_file(
    request: Request,
    server_id: str,
    upload: Annotated[UploadFile, File()],
    path: Annotated[str, Form(max_length=1024)] = ".",
    overwrite: Annotated[bool, Form()] = False,
) -> dict[str, object]:
    _, root = _file_server(request, server_id)
    _require_no_backup_operation(request, server_id)
    filename = upload.filename or ""
    try:
        return await asyncio.to_thread(
            request.app.state.files.upload,
            root,
            path,
            filename,
            upload.file,
            overwrite,
        )
    except FileManagerError as exc:
        _raise_file_error(exc)
    finally:
        await upload.close()


@router.get(
    "/servers/{server_id}/files/download", dependencies=[Depends(require_admin)]
)
async def download_server_file(
    request: Request,
    server_id: str,
    path: str = Query(min_length=1, max_length=1024),
) -> FileResponse:
    _, root = _file_server(request, server_id)
    try:
        download = request.app.state.files.download_path(root, path)
    except FileManagerError as exc:
        _raise_file_error(exc)
    return FileResponse(download, filename=download.name)


def _backup_server(request: Request, server_id: str) -> dict[str, Any]:
    server = _server_or_404(request, server_id)
    _capability_adapter(
        request,
        server,
        AdapterCapability.BACKUPS,
        "This server does not support managed backups",
    )
    return server


def _raise_backup_error(exc: BackupError) -> NoReturn:
    detail = str(exc)
    if isinstance(exc, BackupBusy):
        raise HTTPException(status_code=409, detail=detail) from exc
    if "disk space" in detail.lower():
        raise HTTPException(status_code=507, detail=detail) from exc
    raise HTTPException(status_code=409, detail=detail) from exc


@router.get("/servers/{server_id}/backups", dependencies=[Depends(require_admin)])
async def backups(request: Request, server_id: str) -> dict[str, Any]:
    _backup_server(request, server_id)
    return {
        "backups": request.app.state.db.list_backups(server_id),
        "policy": request.app.state.backups.policy(server_id),
        "operation": request.app.state.backups.operation(server_id),
    }


@router.post(
    "/servers/{server_id}/backups",
    dependencies=[Depends(require_admin)],
    status_code=201,
)
async def create_backup(request: Request, server_id: str) -> dict[str, Any]:
    server = _backup_server(request, server_id)
    try:
        return await request.app.state.backups.manual_backup(server)
    except BackupError as exc:
        _raise_backup_error(exc)


@router.put("/servers/{server_id}/backup-policy", dependencies=[Depends(require_admin)])
async def update_backup_policy(
    request: Request, server_id: str, payload: BackupPolicyUpdate
) -> dict[str, Any]:
    _backup_server(request, server_id)
    return request.app.state.backups.configure_policy(server_id, payload.model_dump())


@router.get(
    "/servers/{server_id}/backups/{backup_id}/download",
    dependencies=[Depends(require_admin)],
)
async def download_backup(request: Request, server_id: str, backup_id: str) -> FileResponse:
    _backup_server(request, server_id)
    try:
        path, record = request.app.state.backups.download_path(server_id, backup_id)
    except BackupError as exc:
        _raise_backup_error(exc)
    return FileResponse(
        path,
        media_type="application/gzip",
        filename=str(record["filename"]),
    )


@router.post(
    "/servers/{server_id}/backups/{backup_id}/restore",
    dependencies=[Depends(require_admin)],
)
async def restore_backup(
    request: Request, server_id: str, backup_id: str
) -> dict[str, Any]:
    server = _backup_server(request, server_id)
    try:
        return await request.app.state.backups.restore(server, backup_id)
    except BackupError as exc:
        _raise_backup_error(exc)


@router.delete(
    "/servers/{server_id}/backups/{backup_id}",
    dependencies=[Depends(require_admin)],
)
async def delete_backup(
    request: Request, server_id: str, backup_id: str
) -> dict[str, bool]:
    _backup_server(request, server_id)
    try:
        await request.app.state.backups.delete(server_id, backup_id)
    except BackupError as exc:
        _raise_backup_error(exc)
    return {"deleted": True}


def _player_adapter(
    request: Request,
    server: dict[str, Any],
    capability: AdapterCapability | None = None,
) -> Any:
    adapter = request.app.state.adapters[server["adapter"]]
    player_capabilities = {
        AdapterCapability.PLAYERS,
        AdapterCapability.WHITELIST,
        AdapterCapability.OPERATORS,
    }
    supported = capability in adapter.capabilities if capability else bool(
        player_capabilities & adapter.capabilities
    )
    if not supported:
        raise HTTPException(
            status_code=404, detail="This server does not support player management"
        )
    return adapter


def _require_running(request: Request, server: dict[str, Any]) -> None:
    if request.app.state.processes.status(server) != "running":
        raise HTTPException(status_code=409, detail="Start the server to manage players")


@router.get("/servers/{server_id}/players", dependencies=[Depends(require_admin)])
async def player_state(request: Request, server_id: str) -> dict[str, Any]:
    server = _server_or_404(request, server_id)
    adapter = _player_adapter(request, server)
    running = request.app.state.processes.status(server) == "running"
    try:
        return await adapter.player_state(Path(server["path"]), server["config"], running)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/servers/{server_id}/players/whitelist", dependencies=[Depends(require_admin)])
async def set_whitelist_member(
    request: Request, server_id: str, payload: PlayerAccess
) -> dict[str, str]:
    server = _server_or_404(request, server_id)
    adapter = _player_adapter(request, server, AdapterCapability.WHITELIST)
    _require_running(request, server)
    try:
        response = await adapter.set_whitelist_member(
            Path(server["path"]), server["config"], payload.name, payload.enabled
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"response": response}


@router.post("/servers/{server_id}/players/operators", dependencies=[Depends(require_admin)])
async def set_operator(
    request: Request, server_id: str, payload: PlayerAccess
) -> dict[str, str]:
    server = _server_or_404(request, server_id)
    adapter = _player_adapter(request, server, AdapterCapability.OPERATORS)
    _require_running(request, server)
    try:
        response = await adapter.set_operator(
            Path(server["path"]), server["config"], payload.name, payload.enabled
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"response": response}


@router.put(
    "/servers/{server_id}/players/whitelist/enabled",
    dependencies=[Depends(require_admin)],
)
async def set_whitelist_enabled(
    request: Request, server_id: str, payload: WhitelistSetting
) -> dict[str, str]:
    server = _server_or_404(request, server_id)
    adapter = _player_adapter(request, server, AdapterCapability.WHITELIST)
    _require_running(request, server)
    try:
        response = await adapter.set_whitelist_enabled(
            Path(server["path"]), server["config"], payload.enabled
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"response": response}


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


@router.get(
    "/servers/{server_id}/mods/{project_id}/versions",
    dependencies=[Depends(require_admin)],
)
async def mod_versions(
    request: Request, server_id: str, project_id: str
) -> list[dict[str, Any]]:
    server = _server_or_404(request, server_id)
    try:
        return await request.app.state.modrinth.versions(server, project_id)
    except ModResolutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Modrinth is currently unavailable") from exc


@router.post("/servers/{server_id}/mods/plan", dependencies=[Depends(require_admin)])
async def plan_mod_change(
    request: Request, server_id: str, payload: ModInstall
) -> dict[str, Any]:
    server = _server_or_404(request, server_id)
    _require_no_backup_operation(request, server_id)
    try:
        return await request.app.state.modrinth.plan(
            server, payload.project_id, payload.title, payload.version_id
        )
    except ModResolutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Modrinth is currently unavailable") from exc


@router.post("/servers/{server_id}/mods/apply", dependencies=[Depends(require_admin)])
async def apply_mod_change(
    request: Request, server_id: str, payload: ModPlanApply
) -> dict[str, Any]:
    _server_or_404(request, server_id)
    _require_no_backup_operation(request, server_id)
    processes = request.app.state.processes
    processes.block_server_operations(server_id)
    try:
        refreshed = _server_or_404(request, server_id)
        if processes.status(refreshed) == "running":
            raise HTTPException(
                status_code=409,
                detail="Stop the game server before applying mod changes",
            )
        return await request.app.state.modrinth.apply_plan(refreshed, payload.plan_id)
    except ModResolutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    finally:
        processes.unblock_server_operations(server_id)


@router.get("/servers/{server_id}/modpack", dependencies=[Depends(require_admin)])
async def modpack_preview(request: Request, server_id: str) -> dict[str, Any]:
    server = _server_or_404(request, server_id)
    _capability_adapter(
        request,
        server,
        AdapterCapability.MODPACKS,
        "This server adapter does not support client modpacks",
    )
    try:
        return await request.app.state.modrinth.modpack_preview(server)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Modrinth is currently unavailable") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/servers/{server_id}/modpack", dependencies=[Depends(require_admin)])
async def build_modpack(
    request: Request, server_id: str, payload: ModpackBuild
) -> Response:
    server = _server_or_404(request, server_id)
    _capability_adapter(
        request,
        server,
        AdapterCapability.MODPACKS,
        "This server adapter does not support client modpacks",
    )
    try:
        content, filename = await request.app.state.modrinth.build_modpack(
            server,
            payload.name,
            payload.version_id,
            payload.selected_project_ids,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Modrinth is currently unavailable") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type=PACK_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/servers/{server_id}/mods", dependencies=[Depends(require_admin)])
async def install_mod(
    request: Request, server_id: str, payload: ModInstall
) -> dict[str, Any]:
    server = _server_or_404(request, server_id)
    _require_no_backup_operation(request, server_id)
    try:
        return await request.app.state.modrinth.plan(
            server, payload.project_id, payload.title, payload.version_id
        )
    except ModResolutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Modrinth is currently unavailable") from exc


@router.delete("/servers/{server_id}/mods/{project_id}", dependencies=[Depends(require_admin)])
async def remove_mod(request: Request, server_id: str, project_id: str) -> dict[str, bool]:
    server = _server_or_404(request, server_id)
    _require_no_backup_operation(request, server_id)
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
    _require_no_backup_operation(request, server_id)
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
        return await request.app.state.modrinth.plan(
            server, project_id, installed["title"], None
        )
    except ModResolutionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Modrinth is currently unavailable") from exc


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
