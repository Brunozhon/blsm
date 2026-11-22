import json
from pathlib import Path
from typing import Any

from blsm import maintenance


class FakeDatabase:
    def __init__(self, servers: list[dict[str, Any]]):
        self.servers = {server["id"]: server for server in servers}

    def list_servers(self) -> list[dict[str, Any]]:
        return list(self.servers.values())

    def get_server(self, server_id: str) -> dict[str, Any] | None:
        return self.servers.get(server_id)


class FakeProcesses:
    def __init__(self):
        self.running = {"online"}
        self.stopped: list[str] = []
        self.started: list[str] = []

    def status(self, server: dict[str, Any]) -> str:
        return "running" if server["id"] in self.running else "stopped"

    async def stop(self, server: dict[str, Any]) -> None:
        self.running.discard(server["id"])
        self.stopped.append(server["id"])

    async def start(self, server: dict[str, Any]) -> None:
        self.running.add(server["id"])
        self.started.append(server["id"])


async def test_update_maintenance_restarts_only_previously_running_servers(
    tmp_path: Path, monkeypatch: Any
) -> None:
    servers = [
        {"id": "online", "name": "Online"},
        {"id": "offline", "name": "Offline"},
    ]
    database = FakeDatabase(servers)
    processes = FakeProcesses()
    monkeypatch.setattr(
        maintenance, "_services", lambda: (database, {}, processes)
    )
    state_file = tmp_path / "update-state.json"

    assert await maintenance.stop_for_update(state_file) == 0
    assert processes.stopped == ["online"]
    assert json.loads(state_file.read_text()) == {"server_ids": ["online"]}

    assert await maintenance.resume_after_update(state_file) == 0
    assert processes.started == ["online"]
    assert "offline" not in processes.running

