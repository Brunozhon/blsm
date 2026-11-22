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

