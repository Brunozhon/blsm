import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from blsm.config import Settings
from blsm.main import create_app
from fastapi.responses import FileResponse


async def request_json(
    app: Any,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
    parsed_path = urlsplit(path)
    payload = json.dumps(body).encode() if body is not None else b""
    request_headers = [(b"content-type", b"application/json")]
    request_headers.extend(
        (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
    )
    sent_request = False
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal sent_request
        if sent_request:
            return {"type": "http.disconnect"}
        sent_request = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": parsed_path.path,
            "raw_path": parsed_path.path.encode(),
            "query_string": parsed_path.query.encode(),
            "root_path": "",
            "headers": request_headers,
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        },
        receive,
        send,
    )
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return start["status"], json.loads(response_body)


async def test_serves_configured_frontend_and_spa_fallback(tmp_path: Path) -> None:
    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir()
    (frontend_dir / "index.html").write_text('<div id="root"></div>')
    settings = Settings(
        data_dir=tmp_path / "data",
        host="127.0.0.1",
        port=8080,
        allowed_origins=(),
        admin_password=None,
        frontend_dir=frontend_dir,
    )
    app = create_app(settings)
    spa_route = next(route for route in app.routes if route.path == "/{path:path}")

    root_response = await spa_route.endpoint("")
    fallback_response = await spa_route.endpoint("servers/example")

    assert isinstance(root_response, FileResponse)
    assert Path(root_response.path) == frontend_dir / "index.html"
    assert isinstance(fallback_response, FileResponse)
    assert Path(fallback_response.path) == frontend_dir / "index.html"


async def test_first_run_auth_and_server_creation(tmp_path: Path, monkeypatch: Any) -> None:
    settings = Settings(
        data_dir=tmp_path,
        host="127.0.0.1",
        port=8080,
        allowed_origins=("http://localhost:5173",),
        admin_password=None,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        assert await request_json(app, "GET", "/api/health") == (200, {"status": "ok"})
        assert await request_json(app, "GET", "/api/auth/status") == (
            200,
            {"configured": False},
        )
        setup_status, setup = await request_json(
            app,
            "POST",
            "/api/auth/setup",
            {"password": "a-secure-test-password"},
        )
        assert setup_status == 200

        async def available_versions() -> list[str]:
            return ["26.3", "1.21.11"]

        monkeypatch.setattr(
            app.state.adapters["minecraft-fabric"],
            "available_versions",
            available_versions,
        )
        assert await request_json(
            app,
            "GET",
            "/api/adapters/minecraft-fabric/versions",
            headers={"Authorization": f"Bearer {setup['token']}"},
        ) == (200, ["26.3", "1.21.11"])

        status, body = await request_json(
            app,
            "POST",
            "/api/servers",
            {
                "name": "Survival",
                "game_version": "1.21.4",
                "memory_mb": 2048,
                "accept_eula": True,
            },
            {"Authorization": f"Bearer {setup['token']}"},
        )
        assert status == 201
        assert body["status"] == "not_installed"
        assert "mods" in body["capabilities"]
        assert "settings" in body["capabilities"]
        assert "game_rules" in body["capabilities"]
        assert "backups" in body["capabilities"]
        assert "files" in body["capabilities"]
        assert "rcon_password" not in body["config"]

        backups_status, backups = await request_json(
            app,
            "GET",
            f"/api/servers/{body['id']}/backups",
            headers={"Authorization": f"Bearer {setup['token']}"},
        )
        assert backups_status == 200
        assert backups["backups"] == []
        assert backups["policy"]["retention_count"] == 7
        policy_status, policy = await request_json(
            app,
            "PUT",
            f"/api/servers/{body['id']}/backup-policy",
            {
                "enabled": True,
                "interval_hours": 12,
                "retention_count": 5,
                "max_total_mb": 20480,
            },
            {"Authorization": f"Bearer {setup['token']}"},
        )
        assert policy_status == 200
        assert policy["enabled"] is True
        assert policy["interval_hours"] == 12
        backup_status, backup_error = await request_json(
            app,
            "POST",
            f"/api/servers/{body['id']}/backups",
            headers={"Authorization": f"Bearer {setup['token']}"},
        )
        assert backup_status == 409
        assert "fully stopped" in backup_error["detail"]

        server_path = next((tmp_path / "servers").iterdir())
        create_file_status, created_file = await request_json(
            app,
            "POST",
            f"/api/servers/{body['id']}/files",
            {"path": "blsm.toml", "kind": "file"},
            {"Authorization": f"Bearer {setup['token']}"},
        )
        assert create_file_status == 201
        assert created_file == {"path": "blsm.toml", "kind": "file"}
        content_status, file_content = await request_json(
            app,
            "GET",
            f"/api/servers/{body['id']}/files/content?path=blsm.toml",
            headers={"Authorization": f"Bearer {setup['token']}"},
        )
        assert content_status == 200
        assert file_content["language"] == "toml"
        save_status, saved_file = await request_json(
            app,
            "PUT",
            f"/api/servers/{body['id']}/files/content?path=blsm.toml",
            {"content": "enabled = true\n", "revision": file_content["revision"]},
            {"Authorization": f"Bearer {setup['token']}"},
        )
        assert save_status == 200
        assert saved_file["content"] == "enabled = true\n"
        traversal_status, traversal = await request_json(
            app,
            "GET",
            f"/api/servers/{body['id']}/files/content?path=..%2Foutside.txt",
            headers={"Authorization": f"Bearer {setup['token']}"},
        )
        assert traversal_status == 400
        assert traversal["detail"] == "Invalid server-relative path"
        (server_path / "server.properties").write_text(
            "motd=Test server\nrcon.password=hidden\nserver-port=25565\n",
            encoding="utf-8",
        )
        settings_status, settings_body = await request_json(
            app,
            "GET",
            f"/api/servers/{body['id']}/settings",
            headers={"Authorization": f"Bearer {setup['token']}"},
        )
        assert settings_status == 200
        setting_values = {item["key"]: item for item in settings_body["settings"]}
        assert setting_values["motd"]["value"] == "Test server"
        assert setting_values["rcon.password"]["value"] == ""

        update_status, updated_settings = await request_json(
            app,
            "PUT",
            f"/api/servers/{body['id']}/settings",
            {"values": {"motd": "Updated", "server-port": "25570"}},
            {"Authorization": f"Bearer {setup['token']}"},
        )
        assert update_status == 200
        assert updated_settings["restart_required"] is True
        saved = (server_path / "server.properties").read_text(encoding="utf-8")
        assert "motd=Updated" in saved
        assert "server-port=25570" in saved
        refreshed = app.state.db.get_server(body["id"])
        assert refreshed is not None
        assert refreshed["config"]["server_port"] == 25570

        rules_status, rules = await request_json(
            app,
            "GET",
            f"/api/servers/{body['id']}/game-rules",
            headers={"Authorization": f"Bearer {setup['token']}"},
        )
        assert rules_status == 200
        assert rules["online"] is False
        assert len(rules["rules"]) == 59
        rule_update_status, rule_update = await request_json(
            app,
            "PUT",
            f"/api/servers/{body['id']}/game-rules",
            {"values": {"minecraft:pvp": False}},
            {"Authorization": f"Bearer {setup['token']}"},
        )
        assert rule_update_status == 409
        assert rule_update["detail"] == "Start the server to change game rules"
        player_status, players = await request_json(
            app,
            "GET",
            f"/api/servers/{body['id']}/players",
            headers={"Authorization": f"Bearer {setup['token']}"},
        )
        assert player_status == 200
        assert players["online"] == []
        assert players["whitelisted"] == []
        mutation_status, mutation = await request_json(
            app,
            "POST",
            f"/api/servers/{body['id']}/players/operators",
            {"name": "Alex", "enabled": True},
            {"Authorization": f"Bearer {setup['token']}"},
        )
        assert mutation_status == 409
        assert mutation["detail"] == "Start the server to manage players"

        update_status, updated_server = await request_json(
            app,
            "PATCH",
            f"/api/servers/{body['id']}",
            {
                "name": "Renamed Survival",
                "config": {
                    "memory_mb": 4096,
                    "world_seed": "BLSM test seed",
                    "game_version": "1.21.4",
                },
            },
            {"Authorization": f"Bearer {setup['token']}"},
        )
        assert update_status == 200
        assert updated_server["name"] == "Renamed Survival"
        assert updated_server["config"]["memory_mb"] == 4096
        assert updated_server["config"]["world_seed"] == "BLSM test seed"
        assert "level-seed=BLSM test seed" in (
            server_path / "server.properties"
        ).read_text(encoding="utf-8")

        wrong_delete_status, wrong_delete = await request_json(
            app,
            "DELETE",
            f"/api/servers/{body['id']}",
            {"confirmation": "wrong name"},
            {"Authorization": f"Bearer {setup['token']}"},
        )
        assert wrong_delete_status == 400
        assert "exact server name" in wrong_delete["detail"]

        backup_directory = tmp_path / "backups" / body["id"]
        backup_directory.mkdir()
        (backup_directory / "test.tar.gz").write_bytes(b"backup")
        delete_status, deleted = await request_json(
            app,
            "DELETE",
            f"/api/servers/{body['id']}",
            {"confirmation": "Renamed Survival"},
            {"Authorization": f"Bearer {setup['token']}"},
        )
        assert delete_status == 200
        assert deleted == {"deleted": True}
        assert app.state.db.get_server(body["id"]) is None
        assert not server_path.exists()
        assert not backup_directory.exists()

