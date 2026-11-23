from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS servers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    adapter TEXT NOT NULL,
    path TEXT NOT NULL UNIQUE,
    config TEXT NOT NULL DEFAULT '{}',
    pid INTEGER,
    started_at TEXT,
    exit_code INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS installed_mods (
    server_id TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    title TEXT NOT NULL,
    filename TEXT NOT NULL,
    installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (server_id, project_id)
);

CREATE TABLE IF NOT EXISTS backup_policies (
    server_id TEXT PRIMARY KEY REFERENCES servers(id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 0,
    interval_hours INTEGER NOT NULL DEFAULT 24,
    retention_count INTEGER NOT NULL DEFAULT 7,
    max_total_mb INTEGER NOT NULL DEFAULT 40960,
    next_run_at TEXT,
    last_run_at TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS backups (
    id TEXT PRIMARY KEY,
    server_id TEXT NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    filename TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    source_bytes INTEGER NOT NULL DEFAULT 0,
    sha256 TEXT,
    error TEXT,
    restart_after INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS backups_server_created
ON backups(server_id, created_at DESC);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(servers)").fetchall()
            }
            if "exit_code" not in columns:
                connection.execute("ALTER TABLE servers ADD COLUMN exit_code INTEGER")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = sqlite3.connect(self.path, timeout=10, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            try:
                yield connection
                connection.commit()
            finally:
                connection.close()

    def get_setting(self, key: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return str(row["value"]) if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO settings(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def list_servers(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM servers ORDER BY created_at").fetchall()
        return [self._server_row(row) for row in rows]

    def get_server(self, server_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM servers WHERE id = ?", (server_id,)).fetchone()
        return self._server_row(row) if row else None

    def create_server(self, server: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO servers(id, name, adapter, path, config) VALUES (?, ?, ?, ?, ?)",
                (
                    server["id"],
                    server["name"],
                    server["adapter"],
                    server["path"],
                    json.dumps(server.get("config", {})),
                ),
            )
        result = self.get_server(server["id"])
        assert result is not None
        return result

    def update_server_runtime(
        self,
        server_id: str,
        pid: int | None,
        started_at: str | None,
        exit_code: int | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE servers SET pid = ?, started_at = ?, exit_code = ? WHERE id = ?",
                (pid, started_at, exit_code, server_id),
            )

    def record_server_exit(self, server_id: str, pid: int, exit_code: int) -> None:
        """Record an exit only if this PID is still the server's active process."""
        with self.connect() as connection:
            connection.execute(
                "UPDATE servers SET pid = NULL, "
                "exit_code = CASE WHEN ? = 0 THEN NULL ELSE ? END, "
                "started_at = CASE WHEN ? = 0 THEN NULL ELSE started_at END "
                "WHERE id = ? AND pid = ?",
                (exit_code, exit_code, exit_code, server_id, pid),
            )

    def update_server_config(self, server_id: str, config: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE servers SET config = ? WHERE id = ?", (json.dumps(config), server_id)
            )

    def update_server_details(
        self, server_id: str, name: str, config: dict[str, Any]
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE servers SET name = ?, config = ? WHERE id = ?",
                (name, json.dumps(config), server_id),
            )

    def delete_server(self, server_id: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM servers WHERE id = ?", (server_id,))

    def list_mods(self, server_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT project_id, version_id, title, filename, installed_at "
                "FROM installed_mods WHERE server_id = ? ORDER BY title",
                (server_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_mod(self, server_id: str, mod: dict[str, str]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO installed_mods(server_id, project_id, version_id, title, filename) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(server_id, project_id) DO UPDATE SET "
                "version_id=excluded.version_id, title=excluded.title, filename=excluded.filename, "
                "installed_at=CURRENT_TIMESTAMP",
                (
                    server_id,
                    mod["project_id"],
                    mod["version_id"],
                    mod["title"],
                    mod["filename"],
                ),
            )

    def remove_mod(self, server_id: str, project_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM installed_mods WHERE server_id = ? AND project_id = ?",
                (server_id, project_id),
            ).fetchone()
            connection.execute(
                "DELETE FROM installed_mods WHERE server_id = ? AND project_id = ?",
                (server_id, project_id),
            )
        return dict(row) if row else None

    def replace_mods(self, server_id: str, mods: list[dict[str, str]]) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM installed_mods WHERE server_id = ?", (server_id,))
            connection.executemany(
                "INSERT INTO installed_mods(server_id, project_id, version_id, title, filename) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        server_id,
                        mod["project_id"],
                        mod["version_id"],
                        mod["title"],
                        mod["filename"],
                    )
                    for mod in mods
                ],
            )

    def restore_server_metadata(
        self, server_id: str, config: dict[str, Any], mods: list[dict[str, str]]
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE servers SET config = ? WHERE id = ?",
                (json.dumps(config), server_id),
            )
            connection.execute("DELETE FROM installed_mods WHERE server_id = ?", (server_id,))
            connection.executemany(
                "INSERT INTO installed_mods(server_id, project_id, version_id, title, filename) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        server_id,
                        mod["project_id"],
                        mod["version_id"],
                        mod["title"],
                        mod["filename"],
                    )
                    for mod in mods
                ],
            )

    def get_backup_policy(self, server_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM backup_policies WHERE server_id = ?", (server_id,)
            ).fetchone()
        return dict(row) if row else None

    def set_backup_policy(self, server_id: str, policy: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO backup_policies(server_id, enabled, interval_hours, "
                "retention_count, max_total_mb, next_run_at, last_run_at, last_error) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(server_id) DO UPDATE SET enabled=excluded.enabled, "
                "interval_hours=excluded.interval_hours, "
                "retention_count=excluded.retention_count, "
                "max_total_mb=excluded.max_total_mb, next_run_at=excluded.next_run_at, "
                "last_run_at=excluded.last_run_at, last_error=excluded.last_error",
                (
                    server_id,
                    int(bool(policy["enabled"])),
                    int(policy["interval_hours"]),
                    int(policy["retention_count"]),
                    int(policy["max_total_mb"]),
                    policy.get("next_run_at"),
                    policy.get("last_run_at"),
                    policy.get("last_error"),
                ),
            )

    def list_due_backup_policies(self, now: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM backup_policies WHERE enabled = 1 "
                "AND next_run_at IS NOT NULL AND next_run_at <= ? ORDER BY next_run_at",
                (now,),
            ).fetchall()
        return [dict(row) for row in rows]

    def create_backup_record(self, backup: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO backups(id, server_id, filename, kind, status, created_at, "
                "restart_after) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    backup["id"],
                    backup["server_id"],
                    backup["filename"],
                    backup["kind"],
                    backup["status"],
                    backup["created_at"],
                    int(bool(backup.get("restart_after"))),
                ),
            )

    def update_backup_record(self, backup_id: str, **values: Any) -> None:
        allowed = {
            "status",
            "completed_at",
            "size_bytes",
            "source_bytes",
            "sha256",
            "error",
            "restart_after",
        }
        invalid = values.keys() - allowed
        if invalid:
            raise ValueError(f"Invalid backup fields: {', '.join(sorted(invalid))}")
        if not values:
            return
        normalized = {
            key: int(bool(value)) if key == "restart_after" else value
            for key, value in values.items()
        }
        assignments = ", ".join(f"{key} = ?" for key in normalized)
        with self.connect() as connection:
            connection.execute(
                f"UPDATE backups SET {assignments} WHERE id = ?",  # noqa: S608
                (*normalized.values(), backup_id),
            )

    def get_backup(self, server_id: str, backup_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM backups WHERE server_id = ? AND id = ?",
                (server_id, backup_id),
            ).fetchone()
        return dict(row) if row else None

    def list_backups(self, server_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM backups WHERE server_id = ? ORDER BY created_at DESC",
                (server_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_backup_record(self, server_id: str, backup_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM backups WHERE server_id = ? AND id = ?",
                (server_id, backup_id),
            )

    def mark_interrupted_backups(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT server_id FROM backups WHERE restart_after = 1"
            ).fetchall()
            connection.execute(
                "UPDATE backups SET status = 'failed', "
                "error = 'Backup was interrupted before completion' "
                "WHERE status IN ('preparing', 'creating')"
            )
        return [str(row["server_id"]) for row in rows]

    def list_backup_restart_pending(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT server_id FROM backups WHERE restart_after = 1"
            ).fetchall()
        return [str(row["server_id"]) for row in rows]

    def clear_backup_restart(self, server_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE backups SET restart_after = 0 WHERE server_id = ?",
                (server_id,),
            )

    @staticmethod
    def _server_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["config"] = json.loads(result["config"])
        return result
