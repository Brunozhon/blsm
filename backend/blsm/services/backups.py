from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import shutil
import tarfile
import uuid
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from ..db import Database
from .processes import ProcessManager

DEFAULT_POLICY: dict[str, Any] = {
    "enabled": False,
    "interval_hours": 24,
    "retention_count": 7,
    "max_total_mb": 40960,
    "next_run_at": None,
    "last_run_at": None,
    "last_error": None,
}
MANIFEST_NAME = ".blsm-backup.json"
TRANSIENT_NAMES = {MANIFEST_NAME, "blsm-console.log", "session.lock"}


class BackupError(RuntimeError):
    pass


class BackupBusy(BackupError):
    pass


class _HashingWriter:
    def __init__(self, handle: BinaryIO):
        self.handle = handle
        self.digest = hashlib.sha256()

    def write(self, data: bytes) -> int:
        self.digest.update(data)
        return self.handle.write(data)

    def flush(self) -> None:
        self.handle.flush()


class BackupService:
    def __init__(
        self,
        db: Database,
        processes: ProcessManager,
        backups_dir: Path,
        servers_dir: Path,
        *,
        scheduler_interval: float = 60,
        reserve_bytes: int = 1024 * 1024 * 1024,
    ):
        self.db = db
        self.processes = processes
        self.backups_dir = backups_dir.resolve()
        self.servers_dir = servers_dir.resolve()
        self.scheduler_interval = scheduler_interval
        self.reserve_bytes = reserve_bytes
        self._locks: dict[str, asyncio.Lock] = {}
        self._operations: dict[str, str] = {}
        self._scheduler_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        self.backups_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._recover_restore_journals()
        self._remove_partial_archives()
        self.db.mark_interrupted_backups()
        await self._recover_pending_restarts()
        self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def shutdown(self) -> None:
        self._stop_event.set()
        if self._scheduler_task is not None:
            await self._scheduler_task
            self._scheduler_task = None

    def policy(self, server_id: str) -> dict[str, Any]:
        stored = self.db.get_backup_policy(server_id)
        if stored is None:
            return {"server_id": server_id, **DEFAULT_POLICY}
        return {
            **stored,
            "enabled": bool(stored["enabled"]),
        }

    def configure_policy(self, server_id: str, values: dict[str, Any]) -> dict[str, Any]:
        current = self.policy(server_id)
        now = datetime.now(timezone.utc)
        enabled = bool(values["enabled"])
        policy = {
            **current,
            **values,
            "server_id": server_id,
            "next_run_at": (
                (now + timedelta(hours=int(values["interval_hours"]))).isoformat()
                if enabled
                else None
            ),
        }
        self.db.set_backup_policy(server_id, policy)
        return self.policy(server_id)

    def operation(self, server_id: str) -> str | None:
        return self._operations.get(server_id)

    async def manual_backup(self, server: dict[str, Any]) -> dict[str, Any]:
        return await self._exclusive(server["id"], "backup", self._manual_backup(server))

    async def scheduled_backup(self, server: dict[str, Any]) -> dict[str, Any]:
        return await self._exclusive(
            server["id"], "scheduled_backup", self._scheduled_backup(server)
        )

    async def restore(self, server: dict[str, Any], backup_id: str) -> dict[str, Any]:
        return await self._exclusive(
            server["id"], "restore", self._restore(server, backup_id)
        )

    async def delete(self, server_id: str, backup_id: str) -> None:
        await self._exclusive(server_id, "delete", self._delete(server_id, backup_id))

    async def purge_server(self, server_id: str) -> None:
        """Remove only backup data rooted in this BLSM server's own directory."""
        await self._exclusive(
            server_id, "delete_server", self._purge_server(server_id)
        )

    def download_path(self, server_id: str, backup_id: str) -> tuple[Path, dict[str, Any]]:
        record = self._complete_record(server_id, backup_id)
        path = self._record_path(record)
        if not path.is_file():
            raise BackupError("Backup archive is missing")
        return path, record

    async def _exclusive(self, server_id: str, operation: str, awaitable: Any) -> Any:
        lock = self._locks.setdefault(server_id, asyncio.Lock())
        if lock.locked():
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise BackupBusy("Another backup or restore operation is already in progress")
        await lock.acquire()
        self._operations[server_id] = operation
        self.processes.block_server_operations(server_id)
        try:
            return await awaitable
        finally:
            self.processes.unblock_server_operations(server_id)
            self._operations.pop(server_id, None)
            lock.release()

    async def _manual_backup(self, server: dict[str, Any]) -> dict[str, Any]:
        current = self._current_server(server["id"])
        if self.processes.status(current) != "stopped":
            raise BackupError("The server must be fully stopped before a manual backup")
        record = self._prepare_record(current, "manual", restart_after=False)
        return await self._write_record(current, record)

    async def _scheduled_backup(self, server: dict[str, Any]) -> dict[str, Any]:
        current = self._current_server(server["id"])
        status = self.processes.status(current)
        if status not in {"running", "stopped"}:
            raise BackupError("Scheduled backup requires an installed, healthy server")
        was_running = status == "running"
        record = self._prepare_record(current, "scheduled", restart_after=was_running)
        try:
            if was_running:
                adapter = self.processes.adapters[current["adapter"]]
                await adapter.prepare_backup_shutdown(Path(current["path"]), current["config"])
                await self.processes.stop(
                    current, timeout=60, force=False, allow_blocked=True
                )
            stopped = self._current_server(current["id"])
            if self.processes.status(stopped) != "stopped":
                raise BackupError("The server did not reach a fully stopped state")
            result = await self._write_record(stopped, record)
        except Exception as exc:
            if self.db.get_backup(current["id"], record["id"]):
                self.db.update_backup_record(
                    record["id"], status="failed", error=str(exc), completed_at=self._now()
                )
            raise
        finally:
            if was_running:
                latest = self._current_server(current["id"])
                if self.processes.status(latest) == "stopped":
                    try:
                        await self.processes.start(latest, allow_blocked=True)
                    except Exception as exc:
                        raise BackupError(
                            f"Backup finished, but the server could not restart: {exc}"
                        ) from exc
                if self.processes.status(self._current_server(current["id"])) == "running":
                    self.db.clear_backup_restart(current["id"])
        return result

    async def _write_record(
        self,
        server: dict[str, Any],
        record: dict[str, Any],
        protected_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        policy = self.policy(server["id"])
        protected = {record["id"], *(protected_ids or set())}
        await self._cleanup_retention(server["id"], policy, protected)
        manifest = {
            "format": 1,
            "server_id": server["id"],
            "adapter": server["adapter"],
            "created_at": record["created_at"],
            "config": server["config"],
            "installed_mods": self.db.list_mods(server["id"]),
        }
        self.db.update_backup_record(record["id"], status="creating")
        try:
            result = await asyncio.to_thread(
                self._create_archive,
                Path(server["path"]),
                self._record_path(record),
                manifest,
            )
        except asyncio.CancelledError:
            self.db.update_backup_record(
                record["id"], status="failed", error="Backup was interrupted"
            )
            raise
        except Exception as exc:
            self._record_path(record).with_suffix(".partial").unlink(missing_ok=True)
            self.db.update_backup_record(
                record["id"], status="failed", error=str(exc), completed_at=self._now()
            )
            raise BackupError(str(exc)) from exc
        self.db.update_backup_record(
            record["id"],
            status="complete",
            completed_at=self._now(),
            size_bytes=result["size_bytes"],
            source_bytes=result["source_bytes"],
            sha256=result["sha256"],
            error=None,
        )
        await self._cleanup_retention(server["id"], policy, protected)
        completed = self.db.get_backup(server["id"], record["id"])
        assert completed is not None
        return completed

    def _create_archive(
        self, source: Path, destination: Path, manifest: dict[str, Any]
    ) -> dict[str, Any]:
        if not source.is_dir():
            raise BackupError("Server files are missing")
        source_bytes = self._source_size(source)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        available = shutil.disk_usage(destination.parent).free
        if available < source_bytes + self.reserve_bytes:
            required_gb = (source_bytes + self.reserve_bytes) / 1024**3
            raise BackupError(
                f"Insufficient disk space; at least {required_gb:.1f} GB free is required"
            )
        partial = destination.with_suffix(".partial")
        partial.unlink(missing_ok=True)
        try:
            with partial.open("xb") as raw:
                writer = _HashingWriter(raw)
                with tarfile.open(
                    fileobj=writer,
                    mode="w|gz",
                    compresslevel=3,
                    format=tarfile.PAX_FORMAT,
                    dereference=True,
                ) as archive:
                    manifest_bytes = json.dumps(manifest, separators=(",", ":")).encode()
                    info = tarfile.TarInfo(MANIFEST_NAME)
                    info.size = len(manifest_bytes)
                    info.mode = 0o600
                    info.mtime = int(datetime.now(timezone.utc).timestamp())
                    archive.addfile(info, io.BytesIO(manifest_bytes))
                    for path in self._archive_entries(source):
                        archive.add(
                            path,
                            arcname=path.relative_to(source).as_posix(),
                            recursive=False,
                        )
                writer.flush()
                os.fsync(raw.fileno())
                digest = writer.digest.hexdigest()
            partial.chmod(0o600)
            os.replace(partial, destination)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        return {
            "size_bytes": destination.stat().st_size,
            "source_bytes": source_bytes,
            "sha256": digest,
        }

    async def _restore(
        self, server: dict[str, Any], backup_id: str
    ) -> dict[str, Any]:
        current = self._current_server(server["id"])
        if self.processes.status(current) != "stopped":
            raise BackupError("The server must be fully stopped before restoration")
        source_record = self._complete_record(current["id"], backup_id)
        archive = self._record_path(source_record)
        if not archive.is_file():
            raise BackupError("Backup archive is missing")
        await asyncio.to_thread(
            self._verify_archive, archive, str(source_record.get("sha256") or "")
        )

        safety = self._prepare_record(current, "pre_restore", restart_after=False)
        await self._write_record(current, safety, {backup_id})
        original_config = current["config"]
        original_mods = self.db.list_mods(current["id"])
        try:
            swap = await asyncio.to_thread(self._extract_and_swap, current, archive)
            manifest = swap["manifest"]
            self.db.restore_server_metadata(
                current["id"], manifest["config"], manifest["installed_mods"]
            )
            cleanup_complete = await asyncio.to_thread(self._commit_swap, swap)
        except Exception as exc:
            self.db.restore_server_metadata(current["id"], original_config, original_mods)
            if "swap" in locals():
                await asyncio.to_thread(self._rollback_swap, swap)
            raise BackupError(f"Restore failed; the original server was preserved: {exc}") from exc
        await self._cleanup_retention(
            current["id"], self.policy(current["id"]), {backup_id, safety["id"]}
        )
        return {
            "restored": source_record,
            "safety_backup": self.db.get_backup(current["id"], safety["id"]),
            "cleanup_pending": not cleanup_complete,
        }

    def _extract_and_swap(
        self, server: dict[str, Any], archive_path: Path
    ) -> dict[str, Any]:
        server_path = Path(server["path"]).resolve()
        if self.servers_dir not in server_path.parents:
            raise BackupError("Server path is outside the managed server directory")
        suffix = uuid.uuid4().hex[:10]
        staging = self.servers_dir / f".{server['id']}-restore-{suffix}"
        old_path = self.servers_dir / f".{server['id']}-previous-{suffix}"
        marker = self.backups_dir / f"restore-{server['id']}.json"
        if marker.exists():
            raise BackupError("A previous restore requires recovery before continuing")
        swap = {
            "server_id": server["id"],
            "server_path": str(server_path),
            "staging": str(staging),
            "old_path": str(old_path),
            "marker": str(marker),
            "stage": "extracting",
        }
        staging.mkdir(mode=0o700)
        self._write_journal(marker, swap)
        try:
            manifest, extracted_bytes = self._extract_archive(archive_path, staging, server)
            available = shutil.disk_usage(staging).free
            if available < self.reserve_bytes:
                raise BackupError("Restoration would leave less than 1 GB of free disk space")
            swap.update(
                {"stage": "extracted", "manifest": manifest, "extracted_bytes": extracted_bytes}
            )
            self._write_journal(
                marker, {key: value for key, value in swap.items() if key != "manifest"}
            )
            server_path.rename(old_path)
            swap["stage"] = "current_moved"
            self._write_journal(
                marker, {key: value for key, value in swap.items() if key != "manifest"}
            )
            staging.rename(server_path)
            swap["stage"] = "swapped"
            self._write_journal(
                marker, {key: value for key, value in swap.items() if key != "manifest"}
            )
            return swap
        except Exception:
            self._rollback_swap(swap)
            raise

    def _extract_archive(
        self, archive_path: Path, staging: Path, server: dict[str, Any]
    ) -> tuple[dict[str, Any], int]:
        extracted_bytes = 0
        manifest: dict[str, Any] | None = None
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
            total_size = sum(member.size for member in members if member.isfile())
            if shutil.disk_usage(staging).free < total_size + self.reserve_bytes:
                raise BackupError("Insufficient disk space to stage this restoration safely")
            for member in members:
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                    raise BackupError("Backup contains an unsafe path")
                if member.name == MANIFEST_NAME:
                    source = archive.extractfile(member)
                    if source is None or member.size > 1024 * 1024:
                        raise BackupError("Backup manifest is invalid")
                    manifest = json.loads(source.read())
                    continue
                if not (member.isdir() or member.isfile()):
                    raise BackupError("Backup contains unsupported links or special files")
                target = staging.joinpath(*relative.parts)
                if staging not in target.resolve().parents:
                    raise BackupError("Backup path escapes the restore directory")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                source = archive.extractfile(member)
                if source is None:
                    raise BackupError("Backup contains an unreadable file")
                with target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                target.chmod(member.mode & 0o700 or 0o600)
                extracted_bytes += member.size
        return self._validate_manifest(manifest, server), extracted_bytes

    @staticmethod
    def _validate_manifest(
        manifest: Any, server: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(manifest, dict) or manifest.get("format") != 1:
            raise BackupError("Backup manifest is missing or unsupported")
        if (
            manifest.get("server_id") != server["id"]
            or manifest.get("adapter") != server["adapter"]
        ):
            raise BackupError("Backup belongs to a different server or adapter")
        config = manifest.get("config")
        mods = manifest.get("installed_mods")
        if not isinstance(config, dict) or not isinstance(mods, list):
            raise BackupError("Backup manifest configuration is invalid")
        required = {"project_id", "version_id", "title", "filename"}
        if any(not isinstance(mod, dict) or not required <= mod.keys() for mod in mods):
            raise BackupError("Backup manifest mod inventory is invalid")
        manifest["installed_mods"] = [
            {key: str(mod[key]) for key in required} for mod in mods
        ]
        return manifest

    def _rollback_swap(self, swap: dict[str, Any]) -> None:
        server_path = Path(swap["server_path"])
        staging = Path(swap["staging"])
        old_path = Path(swap["old_path"])
        if old_path.exists():
            if server_path.exists():
                shutil.rmtree(server_path)
            old_path.rename(server_path)
        if staging.exists():
            shutil.rmtree(staging)
        Path(swap["marker"]).unlink(missing_ok=True)

    def _commit_swap(self, swap: dict[str, Any]) -> bool:
        swap["stage"] = "committed"
        self._write_journal(
            Path(swap["marker"]),
            {key: value for key, value in swap.items() if key != "manifest"},
        )
        old_path = Path(swap["old_path"])
        if old_path.exists():
            try:
                shutil.rmtree(old_path)
            except OSError:
                return False
        Path(swap["marker"]).unlink(missing_ok=True)
        return True

    async def _delete(self, server_id: str, backup_id: str) -> None:
        record = self.db.get_backup(server_id, backup_id)
        if record is None:
            raise BackupError("Backup not found")
        self._record_path(record).unlink(missing_ok=True)
        self.db.delete_backup_record(server_id, backup_id)

    async def _purge_server(self, server_id: str) -> None:
        directory = self.backups_dir / server_id
        if directory.parent.resolve() != self.backups_dir:
            raise BackupError("Refusing to delete an unsafe backup path")
        if directory.is_symlink():
            directory.unlink()
        elif directory.exists():
            shutil.rmtree(directory)
        marker = self.backups_dir / f"restore-{server_id}.json"
        marker.unlink(missing_ok=True)

    async def _cleanup_retention(
        self, server_id: str, policy: dict[str, Any], protected: set[str]
    ) -> None:
        records = [
            item for item in self.db.list_backups(server_id) if item["status"] == "complete"
        ]
        total = sum(int(item["size_bytes"]) for item in records)
        limit = int(policy["max_total_mb"]) * 1024 * 1024
        keep_count = int(policy["retention_count"])
        remaining = len(records)
        for record in reversed(records):
            if remaining <= keep_count and total <= limit:
                break
            if record["id"] in protected:
                continue
            self._record_path(record).unlink(missing_ok=True)
            self.db.delete_backup_record(server_id, record["id"])
            total -= int(record["size_bytes"])
            remaining -= 1

    async def _scheduler_loop(self) -> None:
        while not self._stop_event.is_set():
            await self._recover_pending_restarts()
            for policy in self.db.list_due_backup_policies(self._now()):
                server_id = str(policy["server_id"])
                error: str | None = None
                server = self.db.get_server(server_id)
                try:
                    if server is None:
                        raise BackupError("Server no longer exists")
                    await self.scheduled_backup(server)
                except Exception as exc:
                    error = str(exc)
                latest = self.policy(server_id)
                latest.update(
                    {
                        "last_run_at": self._now(),
                        "last_error": error,
                        "next_run_at": (
                            datetime.now(timezone.utc)
                            + timedelta(hours=int(latest["interval_hours"]))
                        ).isoformat(),
                    }
                )
                self.db.set_backup_policy(server_id, latest)
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.scheduler_interval
                )

    async def _recover_pending_restarts(self) -> None:
        for server_id in self.db.list_backup_restart_pending():
            server = self.db.get_server(server_id)
            if server is None:
                self.db.clear_backup_restart(server_id)
                continue
            status = self.processes.status(server)
            if status == "running":
                self.db.clear_backup_restart(server_id)
            elif status == "stopped":
                try:
                    await self.processes.start(server)
                except Exception:
                    continue
                self.db.clear_backup_restart(server_id)

    def _recover_restore_journals(self) -> None:
        for marker in self.backups_dir.glob("restore-*.json"):
            try:
                swap = json.loads(marker.read_text(encoding="utf-8"))
                server = self.db.get_server(str(swap.get("server_id", "")))
                if server is None or Path(server["path"]).resolve() != Path(
                    swap["server_path"]
                ).resolve():
                    continue
                staging = Path(swap["staging"]).resolve()
                old_path = Path(swap["old_path"]).resolve()
                if staging.parent != self.servers_dir or old_path.parent != self.servers_dir:
                    continue
                swap["marker"] = str(marker)
                if swap.get("stage") == "committed":
                    self._commit_swap(swap)
                else:
                    self._rollback_swap(swap)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue

    def _remove_partial_archives(self) -> None:
        for partial in self.backups_dir.glob("*/*.partial"):
            partial.unlink(missing_ok=True)

    def _prepare_record(
        self, server: dict[str, Any], kind: str, *, restart_after: bool
    ) -> dict[str, Any]:
        created_at = self._now()
        backup_id = uuid.uuid4().hex[:16]
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        record = {
            "id": backup_id,
            "server_id": server["id"],
            "filename": f"{timestamp}-{kind}-{backup_id}.tar.gz",
            "kind": kind,
            "status": "preparing",
            "created_at": created_at,
            "restart_after": restart_after,
        }
        self.db.create_backup_record(record)
        return record

    def _complete_record(self, server_id: str, backup_id: str) -> dict[str, Any]:
        record = self.db.get_backup(server_id, backup_id)
        if record is None:
            raise BackupError("Backup not found")
        if record["status"] != "complete":
            raise BackupError("Backup is not complete")
        return record

    def _record_path(self, record: dict[str, Any]) -> Path:
        filename = str(record["filename"])
        if Path(filename).name != filename:
            raise BackupError("Backup filename is invalid")
        directory = (self.backups_dir / str(record["server_id"])).resolve()
        if directory.parent != self.backups_dir:
            raise BackupError("Backup directory is invalid")
        return directory / filename

    def _current_server(self, server_id: str) -> dict[str, Any]:
        server = self.db.get_server(server_id)
        if server is None:
            raise BackupError("Server no longer exists")
        return server

    @staticmethod
    def _archive_entries(source: Path) -> Any:
        for root, directories, files in os.walk(source):
            root_path = Path(root)
            directories[:] = sorted(
                name for name in directories if not (root_path / name).is_symlink()
            )
            if root_path != source:
                yield root_path
            for name in sorted(files):
                path = root_path / name
                if name in TRANSIENT_NAMES or path.is_symlink():
                    continue
                yield path

    @classmethod
    def _source_size(cls, source: Path) -> int:
        total = 0
        for path in cls._archive_entries(source):
            if path.is_file():
                total += path.stat().st_size
        return total

    @staticmethod
    def _write_journal(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value), encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)

    @staticmethod
    def _verify_archive(path: Path, expected_sha256: str) -> None:
        if len(expected_sha256) != 64:
            raise BackupError("Backup checksum is missing")
        digest = hashlib.sha256()
        with path.open("rb") as archive:
            while chunk := archive.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != expected_sha256:
            raise BackupError("Backup checksum verification failed")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
