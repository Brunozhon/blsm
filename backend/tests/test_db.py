import sqlite3
from pathlib import Path

from blsm.db import Database


def test_server_and_mod_persistence(tmp_path: Path) -> None:
    db = Database(tmp_path / "blsm.db")
    db.initialize()
    server = db.create_server(
        {
            "id": "server-1",
            "name": "Test",
            "adapter": "minecraft-fabric",
            "path": str(tmp_path / "server"),
            "config": {"game_version": "1.21.4"},
        }
    )
    assert server["config"]["game_version"] == "1.21.4"
    db.upsert_mod(
        "server-1",
        {
            "project_id": "fabric-api",
            "version_id": "v1",
            "title": "Fabric API",
            "filename": "fabric-api.jar",
        },
    )
    assert db.list_mods("server-1")[0]["filename"] == "fabric-api.jar"
    removed = db.remove_mod("server-1", "fabric-api")
    assert removed is not None
    assert db.list_mods("server-1") == []


def test_existing_database_adds_process_exit_metadata(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE servers ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, adapter TEXT NOT NULL, "
            "path TEXT NOT NULL UNIQUE, config TEXT NOT NULL DEFAULT '{}', "
            "pid INTEGER, started_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )

    db = Database(database_path)
    db.initialize()

    with db.connect() as connection:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(servers)").fetchall()
        }
    assert "exit_code" in columns

