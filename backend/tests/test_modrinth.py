import json
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest
from blsm.db import Database
from blsm.services import modrinth as modrinth_module
from blsm.services.modrinth import ModrinthService


class FakeResponse:
    def __init__(self, value: Any):
        self.value = value

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self.value


class FakeModrinthClient:
    projects = {
        "root": {
            "id": "root",
            "title": "Client Root",
            "client_side": "required",
            "server_side": "required",
        },
        "library": {
            "id": "library",
            "title": "Client Library",
            "client_side": "optional",
            "server_side": "required",
        },
    }
    versions = {
        "root-version": {
            "id": "root-version",
            "project_id": "root",
            "version_number": "2.0.0",
            "dependencies": [
                {
                    "project_id": "library",
                    "version_id": None,
                    "dependency_type": "required",
                }
            ],
            "files": [
                {
                    "primary": True,
                    "filename": "client-root.jar",
                    "url": "https://cdn.modrinth.com/data/root/versions/v/client-root.jar",
                    "size": 1234,
                    "hashes": {"sha1": "1" * 40, "sha512": "2" * 128},
                }
            ],
        },
        "library-version": {
            "id": "library-version",
            "project_id": "library",
            "version_number": "1.4.0",
            "version_type": "release",
            "dependencies": [],
            "files": [
                {
                    "primary": True,
                    "filename": "client-library.jar",
                    "url": "https://cdn.modrinth.com/data/library/versions/v/client-library.jar",
                    "size": 4321,
                    "hashes": {"sha1": "3" * 40, "sha512": "4" * 128},
                }
            ],
        },
    }

    def __init__(self, *_args: Any, **_kwargs: Any):
        pass

    async def __aenter__(self) -> "FakeModrinthClient":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def get(self, path: str, params: dict[str, str] | None = None) -> FakeResponse:
        if path.startswith("/project/") and path.endswith("/version"):
            project_id = path.split("/")[2]
            assert params == {"loaders": '["fabric"]', "game_versions": '["26.3"]'}
            return FakeResponse(
                [self.versions["library-version"]] if project_id == "library" else []
            )
        if path.startswith("/project/"):
            return FakeResponse(self.projects[path.removeprefix("/project/")])
        if path.startswith("/version/"):
            return FakeResponse(self.versions[path.removeprefix("/version/")])
        raise AssertionError(f"Unexpected Modrinth request: {path}")


def create_service(tmp_path: Path) -> tuple[ModrinthService, dict[str, Any]]:
    db = Database(tmp_path / "blsm.db")
    db.initialize()
    server_path = tmp_path / "server"
    (server_path / "mods").mkdir(parents=True)
    server = db.create_server(
        {
            "id": "server-1",
            "name": "Friends Server",
            "adapter": "minecraft-fabric",
            "path": str(server_path),
            "config": {
                "game_version": "26.3",
                "loader_version": "0.18.2",
            },
        }
    )
    db.upsert_mod(
        server["id"],
        {
            "project_id": "root",
            "version_id": "root-version",
            "title": "Client Root",
            "filename": "client-root.jar",
        },
    )
    (server_path / "mods" / "client-root.jar").write_bytes(b"tracked")
    (server_path / "mods" / "manual.jar").write_bytes(b"untracked")
    return ModrinthService(db), server


@pytest.mark.asyncio
async def test_modpack_resolves_required_dependencies_and_builds_standard_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(modrinth_module.httpx, "AsyncClient", FakeModrinthClient)
    service, server = create_service(tmp_path)

    preview = await service.modpack_preview(server)

    assert preview["game_version"] == "26.3"
    assert [mod["project_id"] for mod in preview["mods"]] == ["root", "library"]
    assert all(mod["default_enabled"] for mod in preview["mods"])
    assert preview["mods"][1]["source"] == "dependency"
    assert preview["mods"][1]["required_by"] == ["root"]
    assert "manually installed" in preview["warnings"][0]

    content, filename = await service.build_modpack(
        server, "Friends Client", "1.0.0", ["root"]
    )

    assert filename == "Friends-Client-1.0.0.mrpack"
    with zipfile.ZipFile(BytesIO(content)) as archive:
        assert archive.namelist() == ["modrinth.index.json"]
        index = json.loads(archive.read("modrinth.index.json"))
    assert index["formatVersion"] == 1
    assert index["dependencies"] == {
        "minecraft": "26.3",
        "fabric-loader": "0.18.2",
    }
    assert {item["path"] for item in index["files"]} == {
        "mods/client-root.jar",
        "mods/client-library.jar",
    }
    assert all(item["env"]["server"] == "unsupported" for item in index["files"])


@pytest.mark.asyncio
async def test_modpack_rejects_non_modrinth_download_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(modrinth_module.httpx, "AsyncClient", FakeModrinthClient)
    service, server = create_service(tmp_path)
    original = FakeModrinthClient.versions["root-version"]["files"][0]["url"]
    FakeModrinthClient.versions["root-version"]["files"][0]["url"] = (
        "https://example.com/client-root.jar"
    )
    try:
        with pytest.raises(RuntimeError, match="unsafe download URL"):
            await service.build_modpack(server, "Unsafe", "1.0.0", ["root"])
    finally:
        FakeModrinthClient.versions["root-version"]["files"][0]["url"] = original
