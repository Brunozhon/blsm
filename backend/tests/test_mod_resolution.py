import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from blsm.adapters.fabric_metadata import inspect_fabric_jar
from blsm.adapters.minecraft import MinecraftFabricAdapter
from blsm.db import Database
from blsm.services import mod_resolution as resolution_module
from blsm.services.mod_resolution import (
    ModResolutionError,
    ModResolver,
    validate_candidates,
    version_matches,
)

ADAPTER = MinecraftFabricAdapter()
ADAPTER._loader_runtime_cache[("26.3", "0.18.2")] = {}


def fabric_jar(
    document: dict[str, Any],
    nested_documents: dict[str, dict[str, Any]] | None = None,
) -> bytes:
    document = dict(document)
    if nested_documents:
        document["jars"] = [{"file": path} for path in nested_documents]
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("fabric.mod.json", json.dumps(document))
        for path, nested_document in (nested_documents or {}).items():
            archive.writestr(path, fabric_jar(nested_document))
    return output.getvalue()


BUNDLED_LIBRARIES = {
    "META-INF/jars/apollib.jar": {
        "schemaVersion": 1,
        "id": "apollib",
        "version": "1.2.4",
        "name": "ApolloLib",
    },
    "META-INF/jars/json5-java.jar": {
        "schemaVersion": 1,
        "id": "de_marhali_json5-java",
        "version": "3.0.0",
        "name": "JSON5 Java",
    },
}


TERRALITH_JAR = fabric_jar(
    {
        "schemaVersion": 1,
        "id": "terralith",
        "version": "2.6.5",
        "name": "Terralith",
        "depends": {"fabricloader": ">=0.17", "minecraft": "~26.3"},
    }
)
TECTONIC_BETA_JAR = fabric_jar(
    {
        "schemaVersion": 1,
        "id": "tectonic",
        "version": "3.1.0+beta2",
        "name": "Tectonic",
        "depends": {
            "fabricloader": ">=0.17",
            "minecraft": "~26.3",
            "lithostitched": ">=2.0.6",
        },
        "breaks": {"terralith": "*"},
    }
)
TECTONIC_STABLE_JAR = fabric_jar(
    {
        "schemaVersion": 1,
        "id": "tectonic",
        "version": "3.0.32",
        "name": "Tectonic",
        "depends": {
            "fabricloader": ">=0.17",
            "minecraft": "~26.3",
            "lithostitched": ">=2.0.6",
            "apollib": ">=1.1.6",
        },
    },
    BUNDLED_LIBRARIES,
)
LITHOSTITCHED_JAR = fabric_jar(
    {
        "schemaVersion": 1,
        "id": "lithostitched",
        "version": "2.0.6",
        "name": "Lithostitched",
        "depends": {"fabricloader": ">=0.17", "minecraft": "~26.3"},
    },
    BUNDLED_LIBRARIES,
)
LITHIUM_JAR = fabric_jar(
    {
        "schemaVersion": 1,
        "id": "lithium",
        "version": "0.26.4+mc26.3",
        "name": "Lithium",
        "depends": {
            "fabricloader": ">=0.19.5",
            "minecraft": "~26.3",
            "mixinextras": ">=0.5.5",
        },
    }
)


def version(
    version_id: str,
    project_id: str,
    number: str,
    channel: str,
    published: str,
    filename: str,
    content: bytes,
) -> dict[str, Any]:
    return {
        "id": version_id,
        "project_id": project_id,
        "name": number,
        "version_number": number,
        "version_type": channel,
        "date_published": published,
        "game_versions": ["26.3"],
        "loaders": ["fabric"],
        "dependencies": [],
        "files": [
            {
                "primary": True,
                "filename": filename,
                "url": f"https://cdn.modrinth.com/{filename}",
                "size": len(content),
                "hashes": {"sha512": hashlib.sha512(content).hexdigest()},
            }
        ],
    }


TERRALITH_VERSION = version(
    "terralith-v",
    "terralith-project",
    "2.6.5+26.3",
    "release",
    "2026-09-27T20:41:09Z",
    "terralith.jar",
    TERRALITH_JAR,
)
TECTONIC_BETA = version(
    "tectonic-beta2",
    "tectonic-project",
    "3.1.0+beta2-fabric-26.3",
    "beta",
    "2026-11-07T01:44:14Z",
    "tectonic-beta2.jar",
    TECTONIC_BETA_JAR,
)
TECTONIC_STABLE = version(
    "tectonic-stable",
    "tectonic-project",
    "3.0.32-fabric-26.3",
    "release",
    "2026-10-25T22:32:55Z",
    "tectonic-stable.jar",
    TECTONIC_STABLE_JAR,
)
TECTONIC_INCOMPATIBLE_RELEASE = version(
    "tectonic-incompatible-release",
    "tectonic-project",
    "3.0.33-fabric-26.3",
    "release",
    "2026-11-10T01:00:00Z",
    "tectonic-incompatible.jar",
    TECTONIC_BETA_JAR,
)
LITHOSTITCHED_VERSION = version(
    "lithostitched-v",
    "lithostitched-project",
    "2.0.6-fabric-26.3",
    "release",
    "2026-10-20T12:00:00Z",
    "lithostitched.jar",
    LITHOSTITCHED_JAR,
)
for tectonic_version in (TECTONIC_BETA, TECTONIC_STABLE):
    tectonic_version["dependencies"] = [
        {
            "project_id": "lithostitched-project",
            "version_id": None,
            "dependency_type": "required",
        }
    ]
TECTONIC_INCOMPATIBLE_RELEASE["dependencies"] = TECTONIC_STABLE["dependencies"]


class FakeResponse:
    def __init__(self, value: Any = None, content: bytes = b""):
        self.value = value
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self) -> Any:
        return self.value


class FakeClient:
    def __init__(self, *_args: Any, **_kwargs: Any):
        pass

    async def __aenter__(self) -> "FakeClient":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def get(self, path: str, params: dict[str, str] | None = None) -> FakeResponse:
        if path == "/project/tectonic-project":
            return FakeResponse({"id": "tectonic-project", "title": "Tectonic"})
        if path == "/project/terralith-project":
            return FakeResponse({"id": "terralith-project", "title": "Terralith"})
        if path == "/project/lithostitched-project":
            return FakeResponse(
                {"id": "lithostitched-project", "title": "Lithostitched"}
            )
        if path == "/project/tectonic-project/version":
            assert params == {"loaders": '["fabric"]', "game_versions": '["26.3"]'}
            return FakeResponse([TECTONIC_BETA, TECTONIC_STABLE])
        if path == "/project/terralith-project/version":
            return FakeResponse([TERRALITH_VERSION])
        if path == "/project/lithostitched-project/version":
            return FakeResponse([LITHOSTITCHED_VERSION])
        if path == "/version/terralith-v":
            return FakeResponse(TERRALITH_VERSION)
        if path == "/version/tectonic-beta2":
            return FakeResponse(TECTONIC_BETA)
        if path == "https://cdn.modrinth.com/tectonic-beta2.jar":
            return FakeResponse(content=TECTONIC_BETA_JAR)
        if path == "https://cdn.modrinth.com/tectonic-stable.jar":
            return FakeResponse(content=TECTONIC_STABLE_JAR)
        if path == "https://cdn.modrinth.com/lithostitched.jar":
            return FakeResponse(content=LITHOSTITCHED_JAR)
        raise AssertionError(f"Unexpected request: {path}")


class OlderFallbackClient(FakeClient):
    async def get(self, path: str, params: dict[str, str] | None = None) -> FakeResponse:
        if path == "/project/tectonic-project/version":
            return FakeResponse([TECTONIC_INCOMPATIBLE_RELEASE, TECTONIC_STABLE])
        if path == "https://cdn.modrinth.com/tectonic-incompatible.jar":
            return FakeResponse(content=TECTONIC_BETA_JAR)
        return await super().get(path, params)


def runtime(tmp_path: Path) -> tuple[Database, dict[str, Any], ModResolver]:
    db = Database(tmp_path / "blsm.db")
    db.initialize()
    server_path = tmp_path / "server"
    mods_dir = server_path / "mods"
    mods_dir.mkdir(parents=True)
    (mods_dir / "terralith.jar").write_bytes(TERRALITH_JAR)
    server = db.create_server(
        {
            "id": "server-1",
            "name": "Resolver Test",
            "adapter": "minecraft-fabric",
            "path": str(server_path),
            "config": {"game_version": "26.3", "loader_version": "0.18.2"},
        }
    )
    db.upsert_mod(
        server["id"],
        {
            "project_id": "terralith-project",
            "version_id": "terralith-v",
            "title": "Terralith",
            "filename": "terralith.jar",
        },
    )
    return db, server, ModResolver(db, {ADAPTER.key: ADAPTER})


def test_fabric_version_ranges_and_tectonic_breaks_metadata() -> None:
    assert version_matches("26.3", "~26.3")
    assert version_matches("0.18.2", ">=0.17")
    assert not version_matches("26.4", "~26.3")
    tectonic = resolution_module.local_candidate("tectonic.jar", TECTONIC_BETA_JAR, ADAPTER)
    terralith = resolution_module.local_candidate("terralith.jar", TERRALITH_JAR, ADAPTER)

    issues = validate_candidates(
        [tectonic, terralith],
        {"minecraft": "26.3", "fabricloader": "0.18.2"},
        complete=True,
    )

    assert inspect_fabric_jar(TECTONIC_BETA_JAR, "tectonic.jar")[0].breaks == {
        "terralith": "*"
    }
    assert any("Tectonic 3.1.0+beta2 breaks Terralith 2.6.5" in issue for issue in issues)


def test_duplicate_nested_libraries_are_resolved_as_alternatives() -> None:
    tectonic = resolution_module.local_candidate(
        "tectonic.jar", TECTONIC_STABLE_JAR, ADAPTER
    )
    lithostitched = resolution_module.local_candidate(
        "lithostitched.jar", LITHOSTITCHED_JAR, ADAPTER
    )

    issues = validate_candidates(
        [tectonic, lithostitched],
        {"minecraft": "26.3", "fabricloader": "0.18.2"},
        complete=True,
    )

    assert issues == []
    nested_ids = {
        package.package_id for package in tectonic.packages if package.nested
    }
    assert nested_ids == {"apollib", "de_marhali_json5-java"}


def test_lithium_accepts_mixinextras_supplied_by_fabric_loader() -> None:
    lithium = resolution_module.local_candidate("lithium.jar", LITHIUM_JAR, ADAPTER)

    missing = validate_candidates(
        [lithium],
        {"minecraft": "26.3", "fabricloader": "0.19.5", "java": "25"},
        complete=True,
    )
    resolved = validate_candidates(
        [lithium],
        {
            "minecraft": "26.3",
            "fabricloader": "0.19.5",
            "mixinextras": "0.5.5",
            "java": "25",
        },
        complete=True,
    )

    assert any("requires missing package mixinextras >=0.5.5" in issue for issue in missing)
    assert resolved == []


@pytest.mark.asyncio
async def test_resolver_prefers_stable_and_rejects_explicit_tectonic_beta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(resolution_module.httpx, "AsyncClient", FakeClient)
    db, server, resolver = runtime(tmp_path)

    available = await resolver.versions(server, "tectonic-project")
    assert [item["id"] for item in available] == ["tectonic-stable", "tectonic-beta2"]

    plan = await resolver.create_plan(server, "tectonic-project", "Tectonic", None)
    assert plan["selected_version_id"] == "tectonic-stable"
    assert {change["project_id"] for change in plan["changes"]} == {
        "tectonic-project",
        "lithostitched-project",
    }
    tectonic_change = next(
        change for change in plan["changes"] if change["project_id"] == "tectonic-project"
    )
    assert tectonic_change["to_version"] == "3.0.32-fabric-26.3"

    with pytest.raises(ModResolutionError) as caught:
        await resolver.create_plan(
            server, "tectonic-project", "Tectonic", "tectonic-beta2"
        )
    message = str(caught.value)
    assert "Tectonic 3.1.0+beta2 breaks Terralith 2.6.5" in message
    assert "Try another version" in message

    applied = await resolver.apply_plan(server, plan["plan_id"])
    assert applied["applied"] is True
    assert {item["project_id"] for item in db.list_mods(server["id"])} == {
        "lithostitched-project",
        "tectonic-project",
        "terralith-project",
    }
    assert (Path(server["path"]) / "mods" / "tectonic-stable.jar").is_file()
    assert (Path(server["path"]) / "mods" / "lithostitched.jar").is_file()


@pytest.mark.asyncio
async def test_mod_transaction_applies_and_rolls_back_on_database_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(resolution_module.httpx, "AsyncClient", FakeClient)
    db, server, resolver = runtime(tmp_path)
    plan = await resolver.create_plan(server, "tectonic-project", "Tectonic", None)
    original_replace = db.replace_mods
    calls = 0

    def fail_once(server_id: str, mods: list[dict[str, str]]) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated database failure")
        original_replace(server_id, mods)

    monkeypatch.setattr(db, "replace_mods", fail_once)
    with pytest.raises(ModResolutionError, match="rolled back"):
        await resolver.apply_plan(server, plan["plan_id"])

    mods_dir = Path(server["path"]) / "mods"
    assert (mods_dir / "terralith.jar").read_bytes() == TERRALITH_JAR
    assert not (mods_dir / "tectonic-stable.jar").exists()
    assert not (mods_dir / "lithostitched.jar").exists()
    assert [item["project_id"] for item in db.list_mods(server["id"])] == [
        "terralith-project"
    ]
    assert not list(mods_dir.glob(".blsm-mod-transaction-*"))


@pytest.mark.asyncio
async def test_resolver_searches_older_release_when_newest_release_conflicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(resolution_module.httpx, "AsyncClient", OlderFallbackClient)
    _, server, resolver = runtime(tmp_path)

    plan = await resolver.create_plan(server, "tectonic-project", "Tectonic", None)

    assert plan["selected_version_id"] == "tectonic-stable"


@pytest.mark.asyncio
async def test_explicit_older_version_is_presented_as_an_approved_downgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(resolution_module.httpx, "AsyncClient", FakeClient)
    db, server, resolver = runtime(tmp_path)
    mods_dir = Path(server["path"]) / "mods"
    (mods_dir / "tectonic-beta2.jar").write_bytes(TECTONIC_BETA_JAR)
    db.upsert_mod(
        server["id"],
        {
            "project_id": "tectonic-project",
            "version_id": "tectonic-beta2",
            "title": "Tectonic",
            "filename": "tectonic-beta2.jar",
        },
    )

    plan = await resolver.create_plan(
        server, "tectonic-project", "Tectonic", "tectonic-stable"
    )

    tectonic = next(
        change for change in plan["changes"] if change["project_id"] == "tectonic-project"
    )
    assert tectonic["direction"] == "downgrade"
    assert tectonic["from_version"] == "3.1.0+beta2-fabric-26.3"
    assert tectonic["to_version"] == "3.0.32-fabric-26.3"
    assert plan["requires_confirmation"] is True
