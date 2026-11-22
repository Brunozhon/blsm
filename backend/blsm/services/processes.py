from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..adapters.base import ServerAdapter
from ..db import Database


class ProcessError(RuntimeError):
    pass


class ProcessManager:
    def __init__(self, db: Database, adapters: dict[str, ServerAdapter]):
        self.db = db
        self.adapters = adapters
        self._locks: dict[str, asyncio.Lock] = {}
        self._cpu_samples: dict[int, tuple[int, float]] = {}

    def _lock(self, server_id: str) -> asyncio.Lock:
        return self._locks.setdefault(server_id, asyncio.Lock())

    @staticmethod
    def _pid_alive(pid: int | None) -> bool:
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            stat = Path(f"/proc/{pid}/stat")
            return not (stat.exists() and stat.read_text().split()[2] == "Z")
        except (OSError, PermissionError, IndexError):
            return False

    def status(self, server: dict[str, Any]) -> str:
        pid = server.get("pid")
        if self._pid_alive(pid):
            return "running"
        if pid:
            return "crashed"
        path = Path(server["path"])
        adapter = self.adapters[server["adapter"]]
        return "stopped" if adapter.is_installed(path) else "not_installed"

    async def start(self, server: dict[str, Any]) -> None:
        async with self._lock(server["id"]):
            if self._pid_alive(server.get("pid")):
                raise ProcessError("Server is already running")
            adapter = self.adapters[server["adapter"]]
            path = Path(server["path"])
            if not adapter.is_installed(path):
                raise ProcessError("Install the server before starting it")
            if shutil.which("java") is None:
                raise ProcessError("Java is not installed or is not available on PATH")
            log_path = path / "blsm-console.log"
            log_handle = log_path.open("ab", buffering=0)
            try:
                process = subprocess.Popen(
                    adapter.launch_command(path, server["config"]),
                    cwd=path,
                    stdin=subprocess.DEVNULL,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
            finally:
                log_handle.close()
            started_at = datetime.now(timezone.utc).isoformat()
            self.db.update_server_runtime(server["id"], process.pid, started_at)

    async def stop(self, server: dict[str, Any], timeout: float = 30) -> None:
        async with self._lock(server["id"]):
            pid = server.get("pid")
            if not self._pid_alive(pid):
                self.db.update_server_runtime(server["id"], None, None)
                return
            adapter = self.adapters[server["adapter"]]
            try:
                await adapter.graceful_stop(Path(server["path"]), server["config"])
            except Exception:
                os.killpg(int(pid), signal.SIGTERM)
            deadline = asyncio.get_running_loop().time() + timeout
            while self._pid_alive(pid) and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.25)
            if self._pid_alive(pid):
                os.killpg(int(pid), signal.SIGKILL)
            self.db.update_server_runtime(server["id"], None, None)

    async def restart(self, server: dict[str, Any]) -> None:
        await self.stop(server)
        latest = self.db.get_server(server["id"])
        if latest is None:
            raise ProcessError("Server no longer exists")
        await self.start(latest)

    def stats(self, server: dict[str, Any]) -> dict[str, Any]:
        pid = server.get("pid")
        if not self._pid_alive(pid):
            return {"memory_mb": 0, "cpu_percent": 0, "uptime_seconds": 0}
        memory_kb = 0
        cpu_percent: float | None = None
        try:
            for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    memory_kb = int(line.split()[1])
                    break
        except (OSError, ValueError):
            pass
        try:
            stat_fields = Path(f"/proc/{pid}/stat").read_text().split()
            process_ticks = int(stat_fields[13]) + int(stat_fields[14])
            now = time.monotonic()
            previous = self._cpu_samples.get(int(pid))
            self._cpu_samples[int(pid)] = (process_ticks, now)
            if previous and now > previous[1]:
                ticks_per_second = os.sysconf("SC_CLK_TCK")
                elapsed = now - previous[1]
                cpu_seconds = (process_ticks - previous[0]) / ticks_per_second
                cpu_percent = round(
                    max(0, cpu_seconds / elapsed * 100),
                    1,
                )
        except (OSError, ValueError, IndexError):
            pass
        uptime = 0
        if server.get("started_at"):
            try:
                started = datetime.fromisoformat(server["started_at"])
                uptime = max(0, int((datetime.now(timezone.utc) - started).total_seconds()))
            except ValueError:
                pass
        return {
            "memory_mb": round(memory_kb / 1024, 1),
            "memory_limit_mb": int(server["config"].get("memory_mb", 0)),
            "cpu_percent": cpu_percent,
            "uptime_seconds": uptime,
            "pid": pid,
        }

    @staticmethod
    def tail(path: Path, max_bytes: int = 128_000) -> tuple[int, str]:
        if not path.exists():
            return 0, ""
        size = path.stat().st_size
        with path.open("rb") as handle:
            handle.seek(max(0, size - max_bytes))
            data = handle.read()
        return size, data.decode("utf-8", errors="replace")

