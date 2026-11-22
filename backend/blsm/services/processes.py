from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..adapters.base import ServerAdapter
from ..db import Database


class ProcessError(RuntimeError):
    pass


@dataclass(slots=True)
class OwnedProcess:
    process: subprocess.Popen[Any]
    server_id: str
    log_path: Path
    startup_complete: bool = False
    finalized: bool = False


class ProcessManager:
    def __init__(
        self,
        db: Database,
        adapters: dict[str, ServerAdapter],
        *,
        poll_interval: float = 0.25,
        startup_grace: float = 1.0,
    ):
        self.db = db
        self.adapters = adapters
        self._locks: dict[str, asyncio.Lock] = {}
        self._cpu_samples: dict[int, tuple[int, float]] = {}
        self._owned_processes: dict[int, OwnedProcess] = {}
        self._watch_tasks: set[asyncio.Task[None]] = set()
        self._blocked_servers: set[str] = set()
        self._poll_interval = poll_interval
        self._startup_grace = startup_grace

    def _lock(self, server_id: str) -> asyncio.Lock:
        return self._locks.setdefault(server_id, asyncio.Lock())

    def _pid_alive(self, pid: int | None) -> bool:
        if not pid:
            return False
        owned = self._owned_processes.get(pid)
        if owned is not None:
            return owned.process.poll() is None
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
        if pid or server.get("exit_code") is not None:
            return "crashed"
        path = Path(server["path"])
        adapter = self.adapters[server["adapter"]]
        return "stopped" if adapter.is_installed(path) else "not_installed"

    def block_server_operations(self, server_id: str) -> None:
        self._blocked_servers.add(server_id)

    def unblock_server_operations(self, server_id: str) -> None:
        self._blocked_servers.discard(server_id)

    def _require_unblocked(self, server_id: str, allow_blocked: bool) -> None:
        if not allow_blocked and server_id in self._blocked_servers:
            raise ProcessError("A backup or restore operation is in progress")

    async def start(
        self, server: dict[str, Any], *, allow_blocked: bool = False
    ) -> None:
        self._require_unblocked(server["id"], allow_blocked)
        async with self._lock(server["id"]):
            self._require_unblocked(server["id"], allow_blocked)
            if self._pid_alive(server.get("pid")):
                raise ProcessError("Server is already running")
            adapter = self.adapters[server["adapter"]]
            path = Path(server["path"])
            if not adapter.is_installed(path):
                raise ProcessError("Install the server before starting it")
            adapter.validate_environment(path, server["config"])
            log_path = path / "blsm-console.log"
            try:
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
            except OSError as exc:
                self.db.update_server_runtime(server["id"], None, None, -1)
                self._append_supervisor_log(log_path, f"Could not start server: {exc}")
                raise ProcessError(f"Could not start the server process: {exc}") from exc

            started_at = datetime.now(timezone.utc).isoformat()
            owned = OwnedProcess(process, server["id"], log_path)
            self._owned_processes[process.pid] = owned
            try:
                self.db.update_server_runtime(server["id"], process.pid, started_at)
            except Exception as exc:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                while process.poll() is None:
                    await asyncio.sleep(self._poll_interval)
                self._owned_processes.pop(process.pid, None)
                self._append_supervisor_log(
                    log_path, f"Could not persist process {process.pid}: {exc}"
                )
                raise ProcessError("Could not record the new server process") from exc

            watch_task = asyncio.create_task(self._watch_process(process.pid))
            self._watch_tasks.add(watch_task)
            watch_task.add_done_callback(self._watch_tasks.discard)

            deadline = asyncio.get_running_loop().time() + self._startup_grace
            while asyncio.get_running_loop().time() < deadline:
                if process.poll() is not None:
                    self._finalize_process(process.pid)
                    raise ProcessError(
                        f"The server process exited during startup with code "
                        f"{process.returncode}. Review {log_path.name} for details."
                    )
                await asyncio.sleep(
                    min(self._poll_interval, max(0, deadline - asyncio.get_running_loop().time()))
                )
            owned.startup_complete = True

    async def stop(
        self,
        server: dict[str, Any],
        timeout: float = 30,
        *,
        force: bool = True,
        allow_blocked: bool = False,
    ) -> None:
        self._require_unblocked(server["id"], allow_blocked)
        async with self._lock(server["id"]):
            self._require_unblocked(server["id"], allow_blocked)
            pid = server.get("pid")
            if not self._pid_alive(pid):
                self.db.update_server_runtime(server["id"], None, None)
                return
            adapter = self.adapters[server["adapter"]]
            try:
                await adapter.graceful_stop(Path(server["path"]), server["config"])
            except Exception as exc:
                if not force:
                    raise ProcessError(
                        "The server did not accept a graceful shutdown request"
                    ) from exc
                os.killpg(int(pid), signal.SIGTERM)
            deadline = asyncio.get_running_loop().time() + timeout
            while self._pid_alive(pid) and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(0.25)
            if self._pid_alive(pid):
                if not force:
                    raise ProcessError(
                        "The server did not stop before the backup shutdown timeout"
                    )
                os.killpg(int(pid), signal.SIGKILL)
                kill_deadline = asyncio.get_running_loop().time() + 5
                while (
                    self._pid_alive(pid)
                    and asyncio.get_running_loop().time() < kill_deadline
                ):
                    await asyncio.sleep(self._poll_interval)
            self._finalize_process(int(pid))
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

    async def shutdown(self) -> None:
        """Stop supervising children without stopping the independent game processes."""
        tasks = list(self._watch_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._watch_tasks.clear()
        self._owned_processes.clear()

    async def _watch_process(self, pid: int) -> None:
        owned = self._owned_processes[pid]
        try:
            while owned.process.poll() is None:
                await asyncio.sleep(self._poll_interval)
            self._finalize_process(pid)
        finally:
            if self._owned_processes.get(pid) is owned:
                self._owned_processes.pop(pid, None)

    def _finalize_process(self, pid: int) -> None:
        owned = self._owned_processes.get(pid)
        if owned is None or owned.finalized or owned.process.returncode is None:
            return
        return_code = int(owned.process.returncode)
        recorded_code = return_code if owned.startup_complete or return_code != 0 else -1
        try:
            self.db.record_server_exit(owned.server_id, pid, recorded_code)
        except Exception as exc:
            self._append_supervisor_log(
                owned.log_path, f"Could not persist exit for process {pid}: {exc}"
            )
        owned.finalized = True
        self._append_supervisor_log(
            owned.log_path, f"Server process {pid} exited with code {return_code}"
        )

    @staticmethod
    def _append_supervisor_log(path: Path, message: str) -> None:
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            with path.open("ab", buffering=0) as handle:
                handle.write(f"\n[BLSM {timestamp}] {message}\n".encode())
        except OSError:
            pass

