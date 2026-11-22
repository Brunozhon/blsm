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

    @staticmethod
    def _server_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["config"] = json.loads(result["config"])
        return result
