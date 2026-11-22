from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from blsm.adapters.base import AdapterCapability, ServerAdapter
from blsm.db import Database
from blsm.services import backups as backup_module
from blsm.services.backups import BackupBusy, BackupError, BackupService
from blsm.services.processes import ProcessError


@pytest.fixture(autouse=True)
def run_backup_workers_inline(monkeypatch: Any) -> None:
    """The test sandbox blocks the event-loop wakeup FD used by asyncio.to_thread."""

    async def run_sync(function: Any, *args: Any) -> Any:
        return function(*args)

    monkeypatch.setattr(backup_module.asyncio, "to_thread", run_sync)


class BackupAdapter(ServerAdapter):
    key = "backup-test"
    display_name = "Backup test"
    capabilities = frozenset({AdapterCapability.BACKUPS})

    def __init__(self) -> None:
        self.prepared = 0

    async def install(self, path: Path, config: dict[str, Any]) -> dict[str, Any]:
        return config

    def launch_command(self, path: Path, config: dict[str, Any]) -> list[str]:
        return []

    async def send_command(self, path: Path, config: dict[str, Any], command: str) -> str:
        return ""

    async def graceful_stop(self, path: Path, config: dict[str, Any]) -> None:
        return None

    def is_installed(self, path: Path) -> bool:
        return path.is_dir()

    async def prepare_backup_shutdown(
        self, path: Path, config: dict[str, Any]
    ) -> None:
        self.prepared += 1


class FakeProcesses:
    def __init__(self, adapter: BackupAdapter, *, running: bool = False):
        self.adapters = {adapter.key: adapter}
        self.running = running
        self.stop_calls: list[tuple[float, bool]] = []
        self.start_calls = 0
        self.fail_stop = False
        self.blocked: set[str] = set()

    def block_server_operations(self, server_id: str) -> None:
        self.blocked.add(server_id)

    def unblock_server_operations(self, server_id: str) -> None:
        self.blocked.discard(server_id)

    def status(self, server: dict[str, Any]) -> str:
        return "running" if self.running else "stopped"

    async def stop(
        self,
        server: dict[str, Any],
        timeout: float = 30,
        *,
        force: bool = True,
        allow_blocked: bool = False,
    ) -> None:
        self.stop_calls.append((timeout, force))
        if self.fail_stop:
            raise ProcessError("graceful shutdown failed")
        self.running = False

    async def start(
        self, server: dict[str, Any], *, allow_blocked: bool = False
    ) -> None:
        self.start_calls += 1
        self.running = True


def create_backup_runtime(
    tmp_path: Path, *, running: bool = False
) -> tuple[Database, dict[str, Any], BackupAdapter, FakeProcesses, BackupService]:
    data_dir = tmp_path / "data"
    servers_dir = data_dir / "servers"
    server_path = servers_dir / "server-1"
    server_path.mkdir(parents=True)
    (server_path / "world").mkdir()
    (server_path / "world" / "level.dat").write_bytes(b"original world")
    (server_path / "mods").mkdir()
    (server_path / "mods" / "example.jar").write_bytes(b"original mod")
    (server_path / "server.properties").write_text("motd=Original\n", encoding="utf-8")
    (server_path / "blsm-console.log").write_text("transient log", encoding="utf-8")

    db = Database(data_dir / "blsm.db")
    db.initialize()
    server = db.create_server(
        {
            "id": "server-1",
            "name": "Test server",
            "adapter": BackupAdapter.key,
            "path": str(server_path),
            "config": {"game_version": "26.3", "memory_mb": 2048},
        }
    )
    db.upsert_mod(
        server["id"],
        {
            "project_id": "example",
            "version_id": "version-1",
            "title": "Example",
            "filename": "example.jar",
        },
    )
    adapter = BackupAdapter()
    processes = FakeProcesses(adapter, running=running)
    service = BackupService(
        db,
        processes,
        data_dir / "backups",
        servers_dir,
        scheduler_interval=0.01,
        reserve_bytes=0,
    )
    return db, server, adapter, processes, service


async def test_manual_backup_and_safe_restore_round_trip(tmp_path: Path) -> None:
    db, server, _, _, service = create_backup_runtime(tmp_path)

    backup = await asyncio.wait_for(service.manual_backup(server), timeout=3)
    archive_path, _ = service.download_path(server["id"], backup["id"])
    assert backup["status"] == "complete"
    assert backup["sha256"]
    with tarfile.open(archive_path, "r:gz") as archive:
        names = archive.getnames()
    assert ".blsm-backup.json" in names
    assert "world/level.dat" in names
    assert "mods/example.jar" in names
    assert "server.properties" in names
    assert "blsm-console.log" not in names

    server_path = Path(server["path"])
    (server_path / "world" / "level.dat").write_bytes(b"changed world")
    (server_path / "mods" / "example.jar").write_bytes(b"changed mod")
    (server_path / "server.properties").write_text("motd=Changed\n", encoding="utf-8")
    db.update_server_config(server["id"], {"game_version": "different"})
    db.remove_mod(server["id"], "example")
    service.configure_policy(
        server["id"],
        {
            "enabled": False,
            "interval_hours": 24,
            "retention_count": 1,
            "max_total_mb": 1024,
        },
    )

    result = await asyncio.wait_for(service.restore(server, backup["id"]), timeout=3)

    assert (server_path / "world" / "level.dat").read_bytes() == b"original world"
    assert (server_path / "mods" / "example.jar").read_bytes() == b"original mod"
    assert (server_path / "server.properties").read_text() == "motd=Original\n"
    restored_server = db.get_server(server["id"])
    assert restored_server is not None
    assert restored_server["config"]["game_version"] == "26.3"
    assert db.list_mods(server["id"])[0]["project_id"] == "example"
    assert result["safety_backup"]["kind"] == "pre_restore"
    assert not list((tmp_path / "data" / "backups").glob("restore-*.json"))


async def test_scheduled_backup_stops_gracefully_and_restarts(tmp_path: Path) -> None:
    _, server, adapter, processes, service = create_backup_runtime(tmp_path, running=True)

    backup = await service.scheduled_backup(server)

    assert backup["status"] == "complete"
    assert adapter.prepared == 1
    assert processes.stop_calls == [(60, False)]
    assert processes.start_calls == 1
    assert processes.running is True


async def test_scheduled_backup_aborts_when_graceful_shutdown_fails(
    tmp_path: Path,
) -> None:
    db, server, _, processes, service = create_backup_runtime(tmp_path, running=True)
    processes.fail_stop = True

    with pytest.raises(ProcessError, match="graceful shutdown failed"):
        await service.scheduled_backup(server)

    assert processes.start_calls == 0
    assert processes.running is True
    records = db.list_backups(server["id"])
    assert records[0]["status"] == "failed"
    assert "graceful shutdown failed" in records[0]["error"]


async def test_backup_rejects_low_disk_space_without_partial_archive(
    tmp_path: Path, monkeypatch: Any
) -> None:
    db, server, _, _, service = create_backup_runtime(tmp_path)
    service.reserve_bytes = 1024
    monkeypatch.setattr(
        backup_module.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(total=1024, used=1023, free=1),
    )

    with pytest.raises(BackupError, match="Insufficient disk space"):
        await service.manual_backup(server)

    record = db.list_backups(server["id"])[0]
    assert record["status"] == "failed"
    assert not list((tmp_path / "data" / "backups").glob("**/*.partial"))
    assert not list((tmp_path / "data" / "backups").glob("**/*.tar.gz"))


async def test_overlapping_backup_operations_are_rejected(tmp_path: Path) -> None:
    _, server, _, _, service = create_backup_runtime(tmp_path)
    lock = service._locks.setdefault(server["id"], asyncio.Lock())
    await lock.acquire()
    try:
        with pytest.raises(BackupBusy, match="already in progress"):
            await service.manual_backup(server)
    finally:
        lock.release()


async def test_retention_removes_oldest_archives(tmp_path: Path) -> None:
    db, server, _, _, service = create_backup_runtime(tmp_path)
    service.configure_policy(
        server["id"],
        {
            "enabled": False,
            "interval_hours": 24,
            "retention_count": 2,
            "max_total_mb": 1024,
        },
    )

    for index in range(3):
        (Path(server["path"]) / "world" / "level.dat").write_text(str(index))
        await service.manual_backup(server)

    complete = [item for item in db.list_backups(server["id"]) if item["status"] == "complete"]
    assert len(complete) == 2
    assert len(list((tmp_path / "data" / "backups").glob("**/*.tar.gz"))) == 2


async def test_interrupted_scheduled_backup_recovers_restart_intent(tmp_path: Path) -> None:
    db, server, _, processes, service = create_backup_runtime(tmp_path)
    record = service._prepare_record(server, "scheduled", restart_after=True)
    db.update_backup_record(record["id"], status="creating")

    await service.start()
    await service.shutdown()

    assert processes.start_calls == 1
    interrupted = db.get_backup(server["id"], record["id"])
    assert interrupted is not None
    assert interrupted["status"] == "failed"
    assert interrupted["restart_after"] == 0


async def test_restore_rejects_archive_path_traversal(tmp_path: Path) -> None:
    _, server, _, _, service = create_backup_runtime(tmp_path)
    record = await service.manual_backup(server)
    archive_path, _ = service.download_path(server["id"], record["id"])
    manifest = {
        "format": 1,
        "server_id": server["id"],
        "adapter": server["adapter"],
        "config": server["config"],
        "installed_mods": [],
    }
    with tarfile.open(archive_path, "w:gz") as archive:
        manifest_bytes = json.dumps(manifest).encode()
        manifest_info = tarfile.TarInfo(".blsm-backup.json")
        manifest_info.size = len(manifest_bytes)
        archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
        unsafe = tarfile.TarInfo("../escaped.txt")
        unsafe.size = 6
        archive.addfile(unsafe, io.BytesIO(b"unsafe"))
    service.db.update_backup_record(
        record["id"], sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest()
    )

    with pytest.raises(BackupError, match="original server was preserved"):
        await service.restore(server, record["id"])

    assert not (tmp_path / "data" / "servers" / "escaped.txt").exists()
    assert (Path(server["path"]) / "world" / "level.dat").read_bytes() == b"original world"


async def test_restore_rejects_corrupted_archive_before_safety_backup(tmp_path: Path) -> None:
    db, server, _, _, service = create_backup_runtime(tmp_path)
    record = await service.manual_backup(server)
    archive_path, _ = service.download_path(server["id"], record["id"])
    with archive_path.open("ab") as archive:
        archive.write(b"corrupted")

    with pytest.raises(BackupError, match="checksum verification failed"):
        await service.restore(server, record["id"])

    assert len(db.list_backups(server["id"])) == 1
    assert (Path(server["path"]) / "world" / "level.dat").read_bytes() == b"original world"


async def test_delete_removes_archive_and_history(tmp_path: Path) -> None:
    db, server, _, _, service = create_backup_runtime(tmp_path)
    record = await service.manual_backup(server)
    archive_path, _ = service.download_path(server["id"], record["id"])

    await service.delete(server["id"], record["id"])

    assert not archive_path.exists()
    assert db.get_backup(server["id"], record["id"]) is None
