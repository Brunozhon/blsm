from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ..adapters.base import ServerAdapter
from ..db import Database
from .mod_resolution import ModResolver

API = "https://api.modrinth.com/v2"
USER_AGENT = "blsm/0.1.0 (self-hosted game server manager)"
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9_.+()-]+\.jar$")
SAFE_PACK_COMPONENT = re.compile(r"[^A-Za-z0-9._+-]+")
PACK_MIME = "application/x-modrinth-modpack+zip"
VALID_SIDES = frozenset({"required", "optional", "unsupported"})
MAX_PACK_PROJECTS = 256


class ModrinthService:
    def __init__(self, db: Database, adapters: dict[str, ServerAdapter] | None = None):
        self.db = db
        self.resolver = ModResolver(db, adapters or {})

    async def versions(
        self, server: dict[str, Any], project_id: str
    ) -> list[dict[str, Any]]:
        return await self.resolver.versions(server, project_id)

    async def plan(
        self,
        server: dict[str, Any],
        project_id: str,
        title: str | None,
        version_id: str | None,
    ) -> dict[str, Any]:
        return await self.resolver.create_plan(server, project_id, title, version_id)

    async def apply_plan(
        self, server: dict[str, Any], plan_id: str
    ) -> dict[str, Any]:
        return await self.resolver.apply_plan(server, plan_id)

    async def search(self, query: str, game_version: str, limit: int = 20) -> list[dict[str, Any]]:
        facets = f'[["project_type:mod"],["categories:fabric"],["versions:{game_version}"]]'
        async with httpx.AsyncClient(
            base_url=API, headers={"User-Agent": USER_AGENT}, timeout=15
        ) as client:
            response = await client.get(
                "/search",
                params={"query": query, "facets": facets, "limit": min(limit, 50)},
            )
            response.raise_for_status()
            hits = response.json().get("hits", [])
        return [
            {
                "project_id": item["project_id"],
                "slug": item["slug"],
                "title": item["title"],
                "description": item["description"],
                "icon_url": item.get("icon_url"),
                "downloads": item.get("downloads", 0),
                "author": item.get("author", "Unknown"),
            }
            for item in hits
        ]

    def remove(self, server: dict[str, Any], project_id: str) -> None:
        record = self.db.remove_mod(server["id"], project_id)
        if not record:
            raise RuntimeError("This mod is not installed")
        path = Path(server["path"]) / "mods" / record["filename"]
        if path.is_file():
            path.unlink()

    async def modpack_preview(self, server: dict[str, Any]) -> dict[str, Any]:
        """Resolve installed projects and their required client dependencies."""
        resolved, warnings = await self._resolve_modpack(server)
        return {
            "game_version": str(server["config"].get("game_version", "")),
            "loader_version": server["config"].get("loader_version"),
            "mods": [self._public_pack_mod(item) for item in resolved.values()],
            "warnings": warnings,
        }

    async def build_modpack(
        self,
        server: dict[str, Any],
        name: str,
        version_id: str,
        selected_project_ids: list[str],
    ) -> tuple[bytes, str]:
        """Build a small Modrinth pack index without copying any server jars."""
        resolved, _ = await self._resolve_modpack(server)
        selected = set(selected_project_ids)
        unknown = selected.difference(resolved)
        if unknown:
            raise RuntimeError("The mod selection is stale; refresh the modpack list")
        if not selected:
            raise RuntimeError("Select at least one client mod")

        # Required dependency closure is enforced again server-side so a crafted
        # client request cannot emit a known-incomplete pack.
        pending = list(selected)
        while pending:
            project_id = pending.pop()
            item = resolved[project_id]
            if not item["eligible"] and not item["required_by"]:
                raise RuntimeError(f"{item['title']} is marked as server-only by Modrinth")
            for dependency_id in item["required_dependencies"]:
                if dependency_id not in selected:
                    selected.add(dependency_id)
                    pending.append(dependency_id)

        loader_version = str(server["config"].get("loader_version") or "").strip()
        game_version = str(server["config"].get("game_version") or "").strip()
        if not loader_version:
            raise RuntimeError("Install the Fabric server before generating a modpack")

        files: list[dict[str, Any]] = []
        for project_id in sorted(selected, key=lambda value: resolved[value]["title"].lower()):
            item = resolved[project_id]
            file = item["_file"]
            filename = str(file.get("filename", ""))
            if not SAFE_FILENAME.fullmatch(filename):
                raise RuntimeError(f"Modrinth returned an unsafe filename for {item['title']}")
            url = str(file.get("url", ""))
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname != "cdn.modrinth.com":
                raise RuntimeError(f"Modrinth returned an unsafe download URL for {item['title']}")
            hashes = file.get("hashes", {})
            sha1 = str(hashes.get("sha1", ""))
            sha512 = str(hashes.get("sha512", ""))
            if not re.fullmatch(r"[0-9a-fA-F]{40}", sha1) or not re.fullmatch(
                r"[0-9a-fA-F]{128}", sha512
            ):
                raise RuntimeError(f"Modrinth did not provide complete hashes for {item['title']}")
            file_size = int(file.get("size", 0))
            if file_size <= 0:
                raise RuntimeError(f"Modrinth returned an invalid file size for {item['title']}")
            files.append(
                {
                    "path": f"mods/{filename}",
                    "hashes": {"sha1": sha1.lower(), "sha512": sha512.lower()},
                    "env": {"client": "required", "server": "unsupported"},
                    "downloads": [url],
                    "fileSize": file_size,
                }
            )

        index = {
            "formatVersion": 1,
            "game": "minecraft",
            "versionId": version_id,
            "name": name,
            "summary": f"Client pack for {server['name']}, generated by BLSM.",
            "files": files,
            "dependencies": {
                "minecraft": game_version,
                "fabric-loader": loader_version,
            },
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as pack:
            pack.writestr(
                "modrinth.index.json",
                json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8"),
            )
        safe_name = SAFE_PACK_COMPONENT.sub("-", name.strip()).strip(".-_") or "blsm-pack"
        safe_version = SAFE_PACK_COMPONENT.sub("-", version_id.strip()).strip(".-_") or "1.0.0"
        return archive.getvalue(), f"{safe_name}-{safe_version}.mrpack"

    async def _resolve_modpack(
        self, server: dict[str, Any]
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        installed = self.db.list_mods(server["id"])
        installed_by_project = {str(item["project_id"]): item for item in installed}
        game_version = str(server["config"].get("game_version", ""))
        warnings: list[str] = []
        resolved: dict[str, dict[str, Any]] = {}
        queued: set[str] = set(installed_by_project)
        pending: list[tuple[str, str | None, str, str | None]] = [
            (project_id, str(record["version_id"]), "installed", None)
            for project_id, record in installed_by_project.items()
        ]
        project_cache: dict[str, dict[str, Any]] = {}
        version_cache: dict[str, dict[str, Any]] = {}

        async with httpx.AsyncClient(
            base_url=API,
            headers={"User-Agent": USER_AGENT},
            timeout=20,
            follow_redirects=True,
        ) as client:
            while pending:
                project_id, requested_version, source, required_by = pending.pop(0)
                if project_id in resolved:
                    if required_by and required_by not in resolved[project_id]["required_by"]:
                        resolved[project_id]["required_by"].append(required_by)
                    continue
                if len(resolved) >= MAX_PACK_PROJECTS:
                    raise RuntimeError("The mod dependency graph exceeds 256 projects")

                project = await self._get_project(client, project_id, project_cache)
                version_id = requested_version
                installed_record = installed_by_project.get(project_id)
                if installed_record:
                    version_id = str(installed_record["version_id"])
                    source = "installed"
                version = (
                    await self._get_version(client, version_id, version_cache)
                    if version_id
                    else await self._compatible_version(
                        client, project_id, game_version, version_cache
                    )
                )
                files = version.get("files") or []
                if not files:
                    raise RuntimeError(f"Modrinth has no downloadable file for {project['title']}")
                primary = next((item for item in files if item.get("primary")), files[0])
                dependencies: list[str] = []
                dependency_requests: list[tuple[str, str | None]] = []
                for dependency in version.get("dependencies") or []:
                    if dependency.get("dependency_type") != "required":
                        continue
                    dependency_project = dependency.get("project_id")
                    dependency_version = dependency.get("version_id")
                    if not dependency_project and dependency_version:
                        dependency_data = await self._get_version(
                            client, str(dependency_version), version_cache
                        )
                        dependency_project = dependency_data.get("project_id")
                    if not dependency_project:
                        warnings.append(
                            f"{project['title']} has a required dependency "
                            "Modrinth could not identify."
                        )
                        continue
                    dependency_id = str(dependency_project)
                    dependencies.append(dependency_id)
                    dependency_requests.append(
                        (dependency_id, str(dependency_version) if dependency_version else None)
                    )

                client_side = self._side(project.get("client_side"))
                item = {
                    "project_id": project_id,
                    "version_id": str(version["id"]),
                    "version_number": str(version.get("version_number", version["id"])),
                    "title": str(project.get("title") or project_id),
                    "filename": str(primary.get("filename", "")),
                    "icon_url": project.get("icon_url"),
                    "client_side": client_side,
                    "source": source,
                    "required_by": [required_by] if required_by else [],
                    "required_dependencies": dependencies,
                    "eligible": client_side != "unsupported",
                    "default_enabled": False,
                    "_file": primary,
                }
                resolved[project_id] = item
                for dependency_id, dependency_version in dependency_requests:
                    if dependency_id in resolved:
                        if project_id not in resolved[dependency_id]["required_by"]:
                            resolved[dependency_id]["required_by"].append(project_id)
                    else:
                        if dependency_id not in queued:
                            queued.add(dependency_id)
                            pending.append(
                                (dependency_id, dependency_version, "dependency", project_id)
                            )
                        else:
                            # The project is queued by another parent. Preserve all
                            # reverse edges once that candidate has been resolved.
                            pending.append(
                                (dependency_id, dependency_version, "dependency", project_id)
                            )

        default_selected = {
            project_id
            for project_id, item in resolved.items()
            if item["source"] == "installed"
            and item["client_side"] in {"required", "optional"}
        }
        pending_defaults = list(default_selected)
        while pending_defaults:
            project_id = pending_defaults.pop()
            for dependency_id in resolved[project_id]["required_dependencies"]:
                if dependency_id not in default_selected:
                    default_selected.add(dependency_id)
                    pending_defaults.append(dependency_id)
        for project_id, item in resolved.items():
            item["required_by"].sort()
            item["required_dependencies"] = sorted(set(item["required_dependencies"]))
            item["eligible"] = item["eligible"] or bool(item["required_by"])
            item["default_enabled"] = project_id in default_selected

        tracked_names = {str(item["filename"]) for item in installed}
        mods_dir = Path(server["path"]) / "mods"
        if mods_dir.is_dir():
            untracked = sorted(
                path.name
                for path in mods_dir.iterdir()
                if path.is_file()
                and not path.is_symlink()
                and path.suffix.lower() == ".jar"
                and path.name not in tracked_names
            )
            if untracked:
                warnings.append(
                    f"{len(untracked)} manually installed jar(s) cannot be resolved through "
                    "Modrinth and will not be included."
                )

        resolved = dict(
            sorted(
                resolved.items(),
                key=lambda pair: (
                    not pair[1]["default_enabled"],
                    pair[1]["source"] != "installed",
                    pair[1]["title"].lower(),
                ),
            )
        )
        return resolved, warnings

    async def _get_project(
        self,
        client: httpx.AsyncClient,
        project_id: str,
        cache: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        if project_id not in cache:
            response = await client.get(f"/project/{project_id}")
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise RuntimeError("Modrinth returned invalid project metadata")
            cache[project_id] = value
        return cache[project_id]

    async def _get_version(
        self,
        client: httpx.AsyncClient,
        version_id: str,
        cache: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        if version_id not in cache:
            response = await client.get(f"/version/{version_id}")
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise RuntimeError("Modrinth returned invalid version metadata")
            cache[version_id] = value
        return cache[version_id]

    async def _compatible_version(
        self,
        client: httpx.AsyncClient,
        project_id: str,
        game_version: str,
        cache: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        response = await client.get(
            f"/project/{project_id}/version",
            params={"loaders": '["fabric"]', "game_versions": json.dumps([game_version])},
        )
        response.raise_for_status()
        values = response.json()
        if not isinstance(values, list) or not values:
            raise RuntimeError(
                f"A required dependency has no Fabric version for Minecraft {game_version}"
            )
        version = next(
            (item for item in values if item.get("version_type") == "release"), values[0]
        )
        cache[str(version["id"])] = version
        return version

    @staticmethod
    def _side(value: Any) -> str:
        side = str(value or "unknown").lower()
        return side if side in VALID_SIDES else "unknown"

    @staticmethod
    def _public_pack_mod(item: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in item.items() if not key.startswith("_")}

