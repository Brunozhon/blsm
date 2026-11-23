from io import BytesIO
from pathlib import Path

import pytest
from blsm.services.files import FileConflict, FileManagerError, ServerFileManager


def test_file_manager_crud_and_revision_protection(tmp_path: Path) -> None:
    manager = ServerFileManager()
    root = tmp_path / "server"
    root.mkdir()

    manager.create(root, "config", "directory")
    manager.create(root, "config/server.properties", "file")
    initial = manager.read_text(root, "config/server.properties")
    saved = manager.write_text(
        root,
        "config/server.properties",
        "motd=BLSM\n",
        str(initial["revision"]),
    )

    assert saved["language"] == "properties"
    assert saved["configuration"] is True
    assert saved["content"] == "motd=BLSM\n"
    listing = manager.list_directory(root, "config")
    assert listing["entries"] == [
        {
            "name": "server.properties",
            "path": "config/server.properties",
            "kind": "file",
            "size": 10,
            "modified_at": listing["entries"][0]["modified_at"],
            "editable": True,
            "language": "properties",
            "configuration": True,
        }
    ]

    (root / "config/server.properties").write_text("motd=external\n", encoding="utf-8")
    with pytest.raises(FileConflict, match="changed on disk"):
        manager.write_text(
            root,
            "config/server.properties",
            "motd=stale\n",
            str(saved["revision"]),
        )

    renamed = manager.rename(root, "config/server.properties", "blsm.properties")
    assert renamed == {"path": "config/blsm.properties"}
    manager.delete(root, "config")
    assert not (root / "config").exists()


def test_file_manager_blocks_traversal_and_symlink_access(tmp_path: Path) -> None:
    manager = ServerFileManager()
    root = tmp_path / "server"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    (root / "escape").symlink_to(outside, target_is_directory=True)

    for unsafe in ("../outside/secret.txt", "/etc/passwd", "escape/secret.txt", "..\\secret"):
        with pytest.raises(FileManagerError):
            manager.read_text(root, unsafe)

    entries = manager.list_directory(root, ".")["entries"]
    assert entries[0]["kind"] == "symlink"
    manager.delete(root, "escape")
    assert outside.is_dir()
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "secret"


def test_upload_is_atomic_and_requires_explicit_overwrite(tmp_path: Path) -> None:
    manager = ServerFileManager()
    root = tmp_path / "server"
    root.mkdir()

    uploaded = manager.upload(root, ".", "config.json5", BytesIO(b"{value: true}\n"))
    assert uploaded == {"path": "config.json5", "size": 14}
    assert not list(root.glob(".blsm-upload-*"))
    with pytest.raises(FileConflict, match="already exists"):
        manager.upload(root, ".", "config.json5", BytesIO(b"replacement"))

    manager.upload(root, ".", "config.json5", BytesIO(b"replacement"), overwrite=True)
    assert (root / "config.json5").read_bytes() == b"replacement"
