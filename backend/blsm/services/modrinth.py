from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import httpx

from ..db import Database

API = "https://api.modrinth.com/v2"
USER_AGENT = "blsm/0.1.0 (self-hosted game server manager)"
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9_.+()-]+\.jar$")


class ModrinthService:
    def __init__(self, db: Database):
        self.db = db

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

    async def install(
        self, server: dict[str, Any], project_id: str, title: str | None = None
    ) -> dict[str, Any]:
        game_version = str(server["config"]["game_version"])
        async with httpx.AsyncClient(
            base_url=API,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
            follow_redirects=True,
        ) as client:
            project_response = await client.get(f"/project/{project_id}")
            project_response.raise_for_status()
            project = project_response.json()
            versions_response = await client.get(
                f"/project/{project_id}/version",
                params={"loaders": '["fabric"]', "game_versions": f'["{game_version}"]'},
            )
            versions_response.raise_for_status()
            versions = versions_response.json()
            if not versions:
                raise RuntimeError(f"No Fabric version supports Minecraft {game_version}")
            version = versions[0]
            file = next(
                (item for item in version["files"] if item.get("primary")),
                version["files"][0],
            )
            filename = str(file["filename"])
            if not SAFE_FILENAME.fullmatch(filename):
                raise RuntimeError("Modrinth returned an unsafe filename")
            download = await client.get(file["url"])
            download.raise_for_status()
            expected_hash = file.get("hashes", {}).get("sha512")
            if expected_hash and hashlib.sha512(download.content).hexdigest() != expected_hash:
                raise RuntimeError("Downloaded mod failed its SHA-512 integrity check")

        mods_dir = Path(server["path"]) / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        previous = next(
            (
                mod
                for mod in self.db.list_mods(server["id"])
                if mod["project_id"] == project["id"]
            ),
            None,
        )
        target = mods_dir / filename
        temporary = target.with_suffix(".jar.part")
        temporary.write_bytes(download.content)
        temporary.replace(target)
        if previous and previous["filename"] != filename:
            old = mods_dir / previous["filename"]
            if old.is_file():
                old.unlink()
        record = {
            "project_id": str(project["id"]),
            "version_id": str(version["id"]),
            "title": str(title or project["title"]),
            "filename": filename,
        }
        self.db.upsert_mod(server["id"], record)
        return record

    def remove(self, server: dict[str, Any], project_id: str) -> None:
        record = self.db.remove_mod(server["id"], project_id)
        if not record:
            raise RuntimeError("This mod is not installed")
        path = Path(server["path"]) / "mods" / record["filename"]
        if path.is_file():
            path.unlink()

