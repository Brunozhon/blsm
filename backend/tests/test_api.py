import json
from pathlib import Path
from typing import Any

from blsm.config import Settings
from blsm.main import create_app


async def request_json(
    app: Any,
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any]]:
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
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
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


async def test_first_run_auth_and_server_creation(tmp_path: Path) -> None:
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
        assert "rcon_password" not in body["config"]

