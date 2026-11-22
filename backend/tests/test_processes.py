from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from blsm.adapters.base import ServerAdapter
from blsm.db import Database
from blsm.services import processes as process_module
from blsm.services.processes import ProcessError, ProcessManager


class ProcessAdapter(ServerAdapter):
    key = "process-test"
    display_name = "Process test"
    capabilities = frozenset()

    def __init__(self, command: list[str]):
        self.command = command

    async def install(self, path: Path, config: dict[str, Any]) -> dict[str, Any]:
        return config

    def launch_command(self, path: Path, config: dict[str, Any]) -> list[str]:
        return self.command

    async def send_command(self, path: Path, config: dict[str, Any], command: str) -> str:
        return ""

    async def graceful_stop(self, path: Path, config: dict[str, Any]) -> None:
        raise RuntimeError("Use the process-group fallback")

    def is_installed(self, path: Path) -> bool:
        return True


def create_runtime(
    tmp_path: Path, command: list[str]
) -> tuple[Database, dict[str, Any], ProcessManager]:
    database = Database(tmp_path / "blsm.db")
    database.initialize()
    server_path = tmp_path / "server"
    server_path.mkdir()
    server = database.create_server(
        {
            "id": "server-1",
            "name": "Test server",
            "adapter": ProcessAdapter.key,
            "path": str(server_path),
            "config": {"memory_mb": 1024},
        }
    )
    adapter = ProcessAdapter(command)
    manager = ProcessManager(
        database,
        {adapter.key: adapter},
        poll_interval=0.01,
        startup_grace=0.05,
    )
    return database, server, manager


async def wait_until(predicate: Any, timeout: float = 2) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("Condition was not reached before timeout")
        await asyncio.sleep(0.01)


def capture_spawned_pids(monkeypatch: Any) -> list[int]:
    spawned: list[int] = []
    real_popen = subprocess.Popen

    def tracked_popen(*args: Any, **kwargs: Any) -> subprocess.Popen[Any]:
        process = real_popen(*args, **kwargs)
        spawned.append(process.pid)
        return process

    monkeypatch.setattr(process_module.subprocess, "Popen", tracked_popen)
    return spawned


def assert_reaped(pid: int) -> None:
    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


async def test_startup_failure_is_reaped_and_recorded(
    tmp_path: Path, monkeypatch: Any
) -> None:
    database, server, manager = create_runtime(
        tmp_path,
        [sys.executable, "-c", "import sys; print('startup failed'); sys.exit(7)"],
    )
    spawned = capture_spawned_pids(monkeypatch)

    with pytest.raises(ProcessError, match="exited during startup with code 7"):
        await manager.start(server)

    failed = database.get_server(server["id"])
    assert failed is not None
    assert failed["pid"] is None
    assert failed["exit_code"] == 7
    assert manager.status(failed) == "crashed"
    assert_reaped(spawned[0])
    log = (Path(server["path"]) / "blsm-console.log").read_text()
    assert "startup failed" in log
    assert "exited with code 7" in log
    await manager.shutdown()


async def test_crash_after_startup_is_reaped(tmp_path: Path, monkeypatch: Any) -> None:
    database, server, manager = create_runtime(
        tmp_path,
        [sys.executable, "-c", "import time, sys; time.sleep(.15); sys.exit(9)"],
    )
    spawned = capture_spawned_pids(monkeypatch)

    await manager.start(server)
    await wait_until(
        lambda: (current := database.get_server(server["id"])) is not None
        and current["exit_code"] == 9
    )

    crashed = database.get_server(server["id"])
    assert crashed is not None
    assert manager.status(crashed) == "crashed"
    assert_reaped(spawned[0])
    await manager.shutdown()


async def test_restart_reaps_old_process_and_tracks_new_one(
    tmp_path: Path, monkeypatch: Any
) -> None:
    database, server, manager = create_runtime(
        tmp_path, [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    spawned = capture_spawned_pids(monkeypatch)

    await manager.start(server)
    await manager.restart(database.get_server(server["id"]) or server)

    assert len(spawned) == 2
    assert_reaped(spawned[0])
    restarted = database.get_server(server["id"])
    assert restarted is not None
    assert restarted["pid"] == spawned[1]
    assert manager.status(restarted) == "running"

    await manager.stop(restarted, timeout=0.1)
    assert_reaped(spawned[1])
    stopped = database.get_server(server["id"])
    assert stopped is not None
    assert manager.status(stopped) == "stopped"
    await manager.shutdown()


async def test_manager_shutdown_preserves_independent_process(
    tmp_path: Path, monkeypatch: Any
) -> None:
    database, server, manager = create_runtime(
        tmp_path, [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    spawned = capture_spawned_pids(monkeypatch)

    await manager.start(server)
    pid = spawned[0]
    try:
        await manager.shutdown()
        os.kill(pid, 0)
        running = database.get_server(server["id"])
        assert running is not None
        assert running["pid"] == pid
        assert manager.status(running) == "running"
    finally:
        os.killpg(pid, signal.SIGTERM)
        os.waitpid(pid, 0)


async def test_backup_guard_blocks_lifecycle_actions(tmp_path: Path) -> None:
    _, server, manager = create_runtime(
        tmp_path, [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    manager.block_server_operations(server["id"])

    with pytest.raises(ProcessError, match="backup or restore"):
        await manager.start(server)

    manager.unblock_server_operations(server["id"])
    await manager.shutdown()
