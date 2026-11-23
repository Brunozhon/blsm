from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import shutil
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from ..adapters.base import ModPackageMetadata, ServerAdapter
from ..db import Database

API = "https://api.modrinth.com/v2"
USER_AGENT = "blsm/0.1.0 (self-hosted game server manager)"
SAFE_JAR = re.compile(r"^[A-Za-z0-9_.+()-]+\.jar$")
SAFE_ID = re.compile(r"^[0-9A-Za-z_-]+$")
MAX_CANDIDATES_PER_PROJECT = 40
MAX_SOLVER_STATES = 2_000
MAX_MOD_BYTES = 256 * 1024 * 1024
PLAN_TTL_SECONDS = 10 * 60
MAX_CACHED_PLANS = 8
MAX_PLAN_CACHE_BYTES = 256 * 1024 * 1024


class ModResolutionError(RuntimeError):
    pass


@dataclass(slots=True)
class Candidate:
    project_id: str
    project_title: str
    version_id: str
    version_number: str
    version_type: str
    published: str
    filename: str
    content: bytes
    packages: tuple[ModPackageMetadata, ...]
    required_projects: tuple[tuple[str, str | None], ...]
    incompatible_projects: tuple[tuple[str, str | None], ...]


@dataclass(slots=True)
class PlanBundle:
    created_at: float
    server_id: str
    snapshot: tuple[tuple[str, int, int], ...]
    installed_fingerprint: tuple[tuple[str, str, str], ...]
    records: list[dict[str, str]]
    changes: list[dict[str, Any]]
    downloads: dict[str, bytes]
    public: dict[str, Any]


@dataclass(slots=True)
class ResolutionContext:
    client: httpx.AsyncClient
    server: dict[str, Any]
    installed: dict[str, dict[str, Any]]
    current: dict[str, Candidate]
    fixed: list[Candidate]
    target_project_id: str
    target_title: str
    selected_version_id: str | None
    adapter: ServerAdapter
    runtime_versions: dict[str, str]
    version_lists: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    projects: dict[str, dict[str, Any]] = field(default_factory=dict)
    candidates: dict[str, Candidate] = field(default_factory=dict)
    allow_existing_changes: bool = False
    states: int = 0
    best_issues: list[str] = field(default_factory=list)


class ModResolver:
    def __init__(self, db: Database, adapters: dict[str, ServerAdapter]):
        self.db = db
        self.adapters = adapters
        self._plans: OrderedDict[str, PlanBundle] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, server_id: str) -> asyncio.Lock:
        return self._locks.setdefault(server_id, asyncio.Lock())

    async def versions(self, server: dict[str, Any], project_id: str) -> list[dict[str, Any]]:
        async with self._client() as client:
            project = await self._project(client, project_id, {})
            versions = await self._versions(client, server, project_id, {})
        return [
            {
                "id": str(version["id"]),
                "name": str(version.get("name") or version.get("version_number")),
                "version_number": str(version.get("version_number") or version["id"]),
                "version_type": str(version.get("version_type") or "release"),
                "date_published": version.get("date_published"),
                "project_title": str(project.get("title") or project_id),
                "recommended": index == 0,
            }
            for index, version in enumerate(versions)
        ]

    async def create_plan(
        self,
        server: dict[str, Any],
        project_id: str,
        title: str | None,
        version_id: str | None,
    ) -> dict[str, Any]:
        self._prune_plans()
        installed_records = self.db.list_mods(server["id"])
        installed = {str(item["project_id"]): item for item in installed_records}
        adapter = self.adapters.get(str(server["adapter"]))
        if adapter is None:
            raise ModResolutionError("This server adapter does not support mod resolution")
        async with self._client() as client:
            projects: dict[str, dict[str, Any]] = {}
            project = await self._project(client, project_id, projects)
            target_title = str(title or project.get("title") or project_id)
            current, fixed = await self._load_current_set(client, server, installed, projects)
            context = ResolutionContext(
                client=client,
                server=server,
                installed=installed,
                current=current,
                fixed=fixed,
                target_project_id=project_id,
                target_title=target_title,
                selected_version_id=version_id,
                adapter=adapter,
                runtime_versions=adapter.mod_runtime_versions(server["config"]),
                projects=projects,
            )
            selection = await self._solve(context)

        plan_id = secrets.token_urlsafe(24)
        bundle = self._make_plan(server, context, selection, plan_id)
        if sum(len(content) for content in bundle.downloads.values()) > MAX_PLAN_CACHE_BYTES:
            raise ModResolutionError("The resolved mod transaction is too large to stage safely")
        self._plans[plan_id] = bundle
        while len(self._plans) > MAX_CACHED_PLANS or sum(
            len(content)
            for cached in self._plans.values()
            for content in cached.downloads.values()
        ) > MAX_PLAN_CACHE_BYTES:
            self._plans.popitem(last=False)
        return bundle.public

    async def apply_plan(self, server: dict[str, Any], plan_id: str) -> dict[str, Any]:
        self._prune_plans()
        bundle = self._plans.get(plan_id)
        if bundle is None or bundle.server_id != server["id"]:
            raise ModResolutionError("This mod plan expired; resolve the change again")
        async with self._lock(server["id"]):
            if self._installed_fingerprint(server["id"]) != bundle.installed_fingerprint:
                raise ModResolutionError("Installed mod metadata changed; resolve the change again")
            if self._directory_snapshot(Path(server["path"]) / "mods") != bundle.snapshot:
                raise ModResolutionError("The mods directory changed; resolve the change again")
            self._apply_files(server, bundle, plan_id)
            self._plans.pop(plan_id, None)
            return {
                "applied": True,
                "changes": bundle.changes,
                "mods": self.db.list_mods(server["id"]),
            }

    async def _solve(self, context: ResolutionContext) -> dict[str, Candidate]:
        installed_projects = sorted(
            project_id
            for project_id in context.installed
            if project_id != context.target_project_id
        )
        initial_projects = [*installed_projects, context.target_project_id]
        result = await self._search(context, initial_projects, 0, {})
        if result is not None:
            return result
        # First keep every existing mod pinned and exhaust target/dependency
        # versions. Only then consider changing installed projects, placing the
        # mods named by the conflict nearest the target in the backtracking order.
        context.allow_existing_changes = True
        context.states = 0
        issue_text = " ".join(context.best_issues).lower()
        installed_projects.sort(
            key=lambda project_id: (
                context.current[project_id].project_title.lower() in issue_text,
                context.current[project_id].project_title.lower(),
            )
        )
        result = await self._search(
            context, [*installed_projects, context.target_project_id], 0, {}
        )
        if result is not None:
            return result
        details = "; ".join(context.best_issues[:4]) or "No compatible version set was found"
        raise ModResolutionError(
            f"No compatible mod combination exists: {details}. "
            "Try another version, update the named dependency, or explicitly remove the "
            "conflicting mod after making a backup."
        )

    async def _search(
        self,
        context: ResolutionContext,
        order: list[str],
        index: int,
        selection: dict[str, Candidate],
        exact_versions: dict[str, set[str]] | None = None,
    ) -> dict[str, Candidate] | None:
        context.states += 1
        if context.states > MAX_SOLVER_STATES:
            context.best_issues = ["Compatibility search exceeded its safe complexity limit"]
            return None
        exact_versions = exact_versions or {}
        if index < len(order):
            project_id = order[index]
            versions = await self._domain(context, project_id, exact_versions.get(project_id))
            for version in versions:
                candidate = await self._candidate(context, project_id, version)
                selection[project_id] = candidate
                issues = validate_candidates(
                    [*context.fixed, *selection.values()],
                    context.runtime_versions,
                    complete=False,
                )
                if not issues:
                    found = await self._search(
                        context, order, index + 1, selection, exact_versions
                    )
                    if found is not None:
                        return found
                else:
                    self._remember_issues(context, issues)
                selection.pop(project_id, None)
            return None

        dependency_versions: dict[str, set[str]] = {}
        for candidate in selection.values():
            for dependency_id, dependency_version in candidate.required_projects:
                if dependency_version:
                    dependency_versions.setdefault(dependency_id, set()).add(dependency_version)
                elif dependency_id not in dependency_versions:
                    dependency_versions[dependency_id] = set()
        for dependency_id, required in dependency_versions.items():
            selected = selection.get(dependency_id)
            if selected is not None:
                if required and selected.version_id not in required:
                    issues = [
                        f"{selected.project_title} {selected.version_number} does not match the "
                        f"exact Modrinth dependency {', '.join(sorted(required))}"
                    ]
                    self._remember_issues(context, issues)
                    return None
                continue
            extended = [*order, dependency_id]
            new_exact = {key: set(value) for key, value in exact_versions.items()}
            new_exact[dependency_id] = set(required)
            return await self._search(context, extended, index, selection, new_exact)

        issues = validate_candidates(
            [*context.fixed, *selection.values()],
            context.runtime_versions,
            complete=True,
        )
        if issues:
            self._remember_issues(context, issues)
            return None
        return dict(selection)

    async def _domain(
        self,
        context: ResolutionContext,
        project_id: str,
        exact_versions: set[str] | None,
    ) -> list[dict[str, Any]]:
        if exact_versions:
            if len(exact_versions) != 1:
                context.best_issues = [
                    f"Required dependencies demand different versions of project {project_id}"
                ]
                return []
            version = await self._version(context.client, next(iter(exact_versions)), {})
            if str(version.get("project_id")) != project_id:
                return []
            return [version] if self._version_matches_server(context.server, version) else []

        versions = await self._versions(
            context.client, context.server, project_id, context.version_lists
        )
        if project_id == context.target_project_id and context.selected_version_id:
            selected = next(
                (
                    version
                    for version in versions
                    if str(version["id"]) == context.selected_version_id
                ),
                None,
            )
            if selected is None:
                raise ModResolutionError(
                    "The selected version does not support this Minecraft version and loader"
                )
            return [selected]
        if project_id == context.target_project_id:
            return versions
        current = context.installed.get(project_id)
        if current:
            current_id = str(current["version_id"])
            current_version = next(
                (version for version in versions if str(version["id"]) == current_id), None
            )
            if current_version is None:
                current_version = await self._version(context.client, current_id, {})
            if not context.allow_existing_changes:
                return [current_version]
            return [current_version, *[v for v in versions if str(v["id"]) != current_id]]
        return versions

    async def _candidate(
        self,
        context: ResolutionContext,
        project_id: str,
        version: dict[str, Any],
    ) -> Candidate:
        version_id = str(version["id"])
        cached = context.candidates.get(version_id)
        if cached is not None:
            return cached
        project = await self._project(context.client, project_id, context.projects)
        files = version.get("files") or []
        if not files:
            raise ModResolutionError(f"{project.get('title', project_id)} has no downloadable jar")
        file = next((item for item in files if item.get("primary")), files[0])
        filename = str(file.get("filename", ""))
        if not SAFE_JAR.fullmatch(filename):
            raise ModResolutionError("Modrinth returned an unsafe mod filename")
        current = context.installed.get(project_id)
        current_path = Path(context.server["path"]) / "mods" / filename
        if current and str(current["version_id"]) == version_id and current_path.is_file():
            if current_path.is_symlink():
                raise ModResolutionError(f"Refusing to inspect symbolic link {filename}")
            content = current_path.read_bytes()
        else:
            url = str(file.get("url", ""))
            parsed = urlparse(url)
            if parsed.scheme != "https" or parsed.hostname != "cdn.modrinth.com":
                raise ModResolutionError("Modrinth returned an unsafe download URL")
            declared_size = int(file.get("size", 0))
            if declared_size <= 0 or declared_size > MAX_MOD_BYTES:
                raise ModResolutionError(f"{filename} has an unsafe declared size")
            response = await context.client.get(url)
            response.raise_for_status()
            content = response.content
            if len(content) != declared_size:
                raise ModResolutionError(f"{filename} did not match its declared size")
        expected = str((file.get("hashes") or {}).get("sha512", ""))
        if not expected or hashlib.sha512(content).hexdigest() != expected:
            raise ModResolutionError(f"{filename} failed its Modrinth SHA-512 check")
        candidate = candidate_from_bytes(
            project_id,
            str(project.get("title") or project_id),
            version,
            filename,
            content,
            context.adapter,
        )
        context.candidates[version_id] = candidate
        return candidate

    async def _load_current_set(
        self,
        client: httpx.AsyncClient,
        server: dict[str, Any],
        installed: dict[str, dict[str, Any]],
        projects: dict[str, dict[str, Any]],
    ) -> tuple[dict[str, Candidate], list[Candidate]]:
        current: dict[str, Candidate] = {}
        mods_dir = Path(server["path"]) / "mods"
        tracked = {str(record["filename"]): project_id for project_id, record in installed.items()}
        for project_id, record in installed.items():
            version = await self._version(client, str(record["version_id"]), {})
            project = await self._project(client, project_id, projects)
            path = mods_dir / str(record["filename"])
            if not path.is_file() or path.is_symlink():
                raise ModResolutionError(
                    f"Managed mod file {record['filename']} is missing or unsafe"
                )
            content = path.read_bytes()
            file = next(
                (
                    item
                    for item in version.get("files") or []
                    if str(item.get("filename")) == record["filename"]
                ),
                None,
            )
            expected = str((file or {}).get("hashes", {}).get("sha512", ""))
            if not expected or hashlib.sha512(content).hexdigest() != expected:
                raise ModResolutionError(
                    f"Managed mod {record['filename']} differs from its recorded Modrinth file"
                )
            current[project_id] = candidate_from_bytes(
                project_id,
                str(project.get("title") or record["title"]),
                version,
                str(record["filename"]),
                content,
                self.adapters[str(server["adapter"])],
            )

        fixed: list[Candidate] = []
        if mods_dir.is_dir():
            for path in sorted(mods_dir.iterdir()):
                if path.suffix.lower() != ".jar" or path.name in tracked:
                    continue
                if not path.is_file() or path.is_symlink():
                    raise ModResolutionError(f"Refusing to inspect unsafe mod path {path.name}")
                content = path.read_bytes()
                fixed.append(
                    local_candidate(
                        path.name, content, self.adapters[str(server["adapter"])]
                    )
                )
        return current, fixed

    def _make_plan(
        self,
        server: dict[str, Any],
        context: ResolutionContext,
        selection: dict[str, Candidate],
        plan_id: str,
    ) -> PlanBundle:
        records: list[dict[str, str]] = []
        changes: list[dict[str, Any]] = []
        downloads: dict[str, bytes] = {}
        required_by: dict[str, list[str]] = {}
        for candidate in selection.values():
            for dependency_id, _ in candidate.required_projects:
                required_by.setdefault(dependency_id, []).append(candidate.project_title)
        for project_id, candidate in sorted(
            selection.items(), key=lambda item: item[1].project_title.lower()
        ):
            record = {
                "project_id": project_id,
                "version_id": candidate.version_id,
                "title": candidate.project_title,
                "filename": candidate.filename,
            }
            records.append(record)
            previous = context.installed.get(project_id)
            if previous and str(previous["version_id"]) == candidate.version_id:
                continue
            action = "install" if previous is None else "change"
            current_candidate = context.current.get(project_id)
            direction = "install"
            if current_candidate is not None:
                direction = (
                    "downgrade"
                    if candidate.published < current_candidate.published
                    else "update"
                )
            reason = (
                "Requested by administrator"
                if project_id == context.target_project_id
                else f"Required by {', '.join(sorted(set(required_by.get(project_id, []))))}"
                if previous is None
                else "Compatibility adjustment requiring approval"
            )
            changes.append(
                {
                    "project_id": project_id,
                    "title": candidate.project_title,
                    "action": action,
                    "direction": direction,
                    "from_version_id": previous["version_id"] if previous else None,
                    "from_version": (
                        current_candidate.version_number if current_candidate else None
                    ),
                    "to_version_id": candidate.version_id,
                    "to_version": candidate.version_number,
                    "version_type": candidate.version_type,
                    "reason": reason,
                }
            )
            downloads[project_id] = candidate.content

        public = {
            "plan_id": plan_id,
            "project_id": context.target_project_id,
            "project_title": context.target_title,
            "selected_version_id": selection[context.target_project_id].version_id,
            "selected_version": selection[context.target_project_id].version_number,
            "changes": changes,
            "requires_confirmation": True,
            "server_must_be_stopped": True,
        }
        mods_dir = Path(server["path"]) / "mods"
        return PlanBundle(
            created_at=time.monotonic(),
            server_id=server["id"],
            snapshot=self._directory_snapshot(mods_dir),
            installed_fingerprint=self._installed_fingerprint(server["id"]),
            records=records,
            changes=changes,
            downloads=downloads,
            public=public,
        )

    def _apply_files(
        self, server: dict[str, Any], bundle: PlanBundle, plan_id: str
    ) -> None:
        mods_dir = Path(server["path"]) / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        transaction = mods_dir / f".blsm-mod-transaction-{plan_id[:12]}"
        incoming = transaction / "incoming"
        rollback = transaction / "rollback"
        incoming.mkdir(parents=True, mode=0o700)
        rollback.mkdir(mode=0o700)
        old_records = self.db.list_mods(server["id"])
        old_by_project = {str(item["project_id"]): item for item in old_records}
        new_by_project = {item["project_id"]: item for item in bundle.records}
        changed = set(bundle.downloads)
        new_names = [new_by_project[project_id]["filename"] for project_id in changed]
        if len(new_names) != len(set(new_names)):
            shutil.rmtree(transaction, ignore_errors=True)
            raise ModResolutionError("Resolved mods use duplicate filenames")
        managed_names = {
            str(item["filename"]): str(item["project_id"]) for item in old_records
        }
        for project_id in changed:
            filename = str(new_by_project[project_id]["filename"])
            owner = managed_names.get(filename)
            target = mods_dir / filename
            if target.exists() and owner not in {None, project_id}:
                shutil.rmtree(transaction, ignore_errors=True)
                raise ModResolutionError(f"{filename} is already owned by another managed mod")
            if target.exists() and owner is None:
                shutil.rmtree(transaction, ignore_errors=True)
                raise ModResolutionError(f"Refusing to overwrite untracked file {filename}")
            staged = incoming / filename
            staged.write_bytes(bundle.downloads[project_id])
            if hashlib.sha512(staged.read_bytes()).digest() != hashlib.sha512(
                bundle.downloads[project_id]
            ).digest():
                shutil.rmtree(transaction, ignore_errors=True)
                raise ModResolutionError(f"Failed to stage {filename} safely")

        moved_old: list[tuple[Path, Path]] = []
        moved_new: list[Path] = []
        try:
            for project_id in changed:
                previous = old_by_project.get(project_id)
                if previous:
                    old_path = mods_dir / str(previous["filename"])
                    if old_path.is_file() and not old_path.is_symlink():
                        saved = rollback / old_path.name
                        os.replace(old_path, saved)
                        moved_old.append((saved, old_path))
            for project_id in changed:
                filename = str(new_by_project[project_id]["filename"])
                target = mods_dir / filename
                os.replace(incoming / filename, target)
                moved_new.append(target)
            self.db.replace_mods(server["id"], bundle.records)
        except Exception as exc:
            for target in reversed(moved_new):
                if target.is_file() and not target.is_symlink():
                    target.unlink()
            for saved, destination in reversed(moved_old):
                if saved.is_file():
                    os.replace(saved, destination)
            self.db.replace_mods(server["id"], old_records)
            raise ModResolutionError(f"Mod transaction rolled back: {exc}") from exc
        finally:
            shutil.rmtree(transaction, ignore_errors=True)

    async def _versions(
        self,
        client: httpx.AsyncClient,
        server: dict[str, Any],
        project_id: str,
        cache: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        if project_id not in cache:
            game_version = str(server["config"].get("game_version", ""))
            adapter = self.adapters[str(server["adapter"])]
            loader = adapter.mod_loader(server["config"])
            response = await client.get(
                f"/project/{project_id}/version",
                params={
                    "loaders": json.dumps([loader]),
                    "game_versions": json.dumps([game_version]),
                },
            )
            response.raise_for_status()
            values = response.json()
            if not isinstance(values, list):
                raise ModResolutionError("Modrinth returned invalid version metadata")
            listed = [
                value
                for value in values
                if isinstance(value, dict) and self._version_matches_server(server, value)
            ]
            listed.sort(key=lambda value: str(value.get("date_published") or ""), reverse=True)
            listed.sort(key=self._version_sort_key)
            cache[project_id] = listed[:MAX_CANDIDATES_PER_PROJECT]
        if not cache[project_id]:
            raise ModResolutionError(
                f"No {adapter.mod_loader(server['config']).title()} version supports Minecraft "
                f"{server['config'].get('game_version')}"
            )
        return cache[project_id]

    async def _project(
        self,
        client: httpx.AsyncClient,
        project_id: str,
        cache: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        if not SAFE_ID.fullmatch(project_id):
            raise ModResolutionError("Modrinth returned an invalid project ID")
        if project_id not in cache:
            response = await client.get(f"/project/{project_id}")
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise ModResolutionError("Modrinth returned invalid project metadata")
            cache[project_id] = value
        return cache[project_id]

    async def _version(
        self,
        client: httpx.AsyncClient,
        version_id: str,
        cache: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        if not SAFE_ID.fullmatch(version_id):
            raise ModResolutionError("Modrinth returned an invalid version ID")
        if version_id not in cache:
            response = await client.get(f"/version/{version_id}")
            response.raise_for_status()
            value = response.json()
            if not isinstance(value, dict):
                raise ModResolutionError("Modrinth returned invalid version metadata")
            cache[version_id] = value
        return cache[version_id]

    @staticmethod
    def _version_sort_key(version: dict[str, Any]) -> int:
        channels = {"release": 0, "beta": 1, "alpha": 2}
        return channels.get(str(version.get("version_type")), 3)

    def _version_matches_server(
        self, server: dict[str, Any], version: dict[str, Any]
    ) -> bool:
        adapter = self.adapters[str(server["adapter"])]
        return adapter.mod_loader(server["config"]) in (version.get("loaders") or []) and str(
            server["config"].get("game_version", "")
        ) in (version.get("game_versions") or [])

    @staticmethod
    def _remember_issues(context: ResolutionContext, issues: list[str]) -> None:
        if not context.best_issues or len(issues) < len(context.best_issues):
            context.best_issues = issues

    @staticmethod
    def _directory_snapshot(mods_dir: Path) -> tuple[tuple[str, int, int], ...]:
        if not mods_dir.is_dir():
            return ()
        values: list[tuple[str, int, int]] = []
        for path in mods_dir.iterdir():
            if path.suffix.lower() != ".jar":
                continue
            if not path.is_file() or path.is_symlink():
                raise ModResolutionError(f"Unsafe entry in mods directory: {path.name}")
            stat = path.stat()
            values.append((path.name, stat.st_size, stat.st_mtime_ns))
        return tuple(sorted(values))

    def _installed_fingerprint(self, server_id: str) -> tuple[tuple[str, str, str], ...]:
        return tuple(
            sorted(
                (
                    str(item["project_id"]),
                    str(item["version_id"]),
                    str(item["filename"]),
                )
                for item in self.db.list_mods(server_id)
            )
        )

    def _prune_plans(self) -> None:
        now = time.monotonic()
        expired = [
            plan_id
            for plan_id, bundle in self._plans.items()
            if now - bundle.created_at > PLAN_TTL_SECONDS
        ]
        for plan_id in expired:
            self._plans.pop(plan_id, None)

    @staticmethod
    def _client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=API,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
            follow_redirects=True,
        )


def candidate_from_bytes(
    project_id: str,
    project_title: str,
    version: dict[str, Any],
    filename: str,
    content: bytes,
    adapter: ServerAdapter,
) -> Candidate:
    required: list[tuple[str, str | None]] = []
    incompatible: list[tuple[str, str | None]] = []
    for dependency in version.get("dependencies") or []:
        dependency_project = dependency.get("project_id")
        if not dependency_project:
            continue
        value = (str(dependency_project), dependency.get("version_id"))
        if dependency.get("dependency_type") == "required":
            required.append(value)
        elif dependency.get("dependency_type") == "incompatible":
            incompatible.append(value)
    try:
        packages = adapter.inspect_mod_package(content, filename)
    except ValueError as exc:
        raise ModResolutionError(str(exc)) from exc
    return Candidate(
        project_id=project_id,
        project_title=project_title,
        version_id=str(version["id"]),
        version_number=str(version.get("version_number") or version["id"]),
        version_type=str(version.get("version_type") or "release"),
        published=str(version.get("date_published") or ""),
        filename=filename,
        content=content,
        packages=packages,
        required_projects=tuple(required),
        incompatible_projects=tuple(incompatible),
    )


def local_candidate(filename: str, content: bytes, adapter: ServerAdapter) -> Candidate:
    try:
        packages = adapter.inspect_mod_package(content, filename)
    except ValueError as exc:
        raise ModResolutionError(str(exc)) from exc
    title = packages[0].name if packages else filename
    return Candidate(
        project_id=f"local:{filename}",
        project_title=title,
        version_id=f"local:{hashlib.sha256(content).hexdigest()}",
        version_number=packages[0].version if packages else "unknown",
        version_type="local",
        published="",
        filename=filename,
        content=content,
        packages=packages,
        required_projects=(),
        incompatible_projects=(),
    )


def validate_candidates(
    candidates: list[Candidate], builtins: dict[str, str], *, complete: bool
) -> list[str]:
    issues: list[str] = []
    by_project = {candidate.project_id: candidate for candidate in candidates}
    providers: dict[str, tuple[Candidate, ModPackageMetadata]] = {}
    for candidate in candidates:
        for package in candidate.packages:
            previous = providers.get(package.package_id)
            if previous and previous[0].project_id != candidate.project_id:
                issues.append(
                    f"{candidate.project_title} and {previous[0].project_title} both provide "
                    f"package ID {package.package_id}"
                )
            else:
                providers[package.package_id] = (candidate, package)

    for candidate in candidates:
        for project_id, version_id in candidate.incompatible_projects:
            other = by_project.get(project_id)
            if other and (version_id is None or other.version_id == version_id):
                issues.append(
                    f"{candidate.project_title} {candidate.version_number} is incompatible with "
                    f"{other.project_title} {other.version_number} according to Modrinth"
                )
        for package in candidate.packages:
            for dependency_id, requirement in package.depends.items():
                builtin = builtins.get(dependency_id)
                if builtin is not None:
                    if not version_matches(builtin, requirement):
                        issues.append(
                            f"{package.name} {package.version} requires {dependency_id} "
                            f"{_range_text(requirement)}, but the server has {builtin or 'unknown'}"
                        )
                    continue
                provider = providers.get(dependency_id)
                if provider is None:
                    if complete:
                        issues.append(
                            f"{package.name} {package.version} requires missing package "
                            f"{dependency_id} {_range_text(requirement)}"
                        )
                    continue
                if not version_matches(provider[1].version, requirement):
                    issues.append(
                        f"{package.name} {package.version} requires {provider[1].name} "
                        f"{_range_text(requirement)}, but {provider[1].version} is selected"
                    )
            for kind, declarations in (
                ("breaks", package.breaks),
                ("conflicts with", package.conflicts),
            ):
                for dependency_id, requirement in declarations.items():
                    provider = providers.get(dependency_id)
                    if provider and version_matches(provider[1].version, requirement):
                        issues.append(
                            f"{package.name} {package.version} {kind} {provider[1].name} "
                            f"{provider[1].version} ({_range_text(requirement)})"
                        )
    return list(dict.fromkeys(issues))


def version_matches(version: str, requirement: str | list[str]) -> bool:
    ranges = requirement if isinstance(requirement, list) else [requirement]
    return any(_range_matches(version, value) for value in ranges)


def _range_matches(version: str, requirement: str) -> bool:
    requirement = requirement.strip()
    if not requirement or requirement == "*":
        return True
    clauses = requirement.split()
    return all(_clause_matches(version, clause) for clause in clauses)


def _clause_matches(version: str, clause: str) -> bool:
    match = re.fullmatch(r"(>=|<=|>|<|=|\^|~)?(.+)", clause)
    if not match:
        return version == clause
    operator, expected = match.groups()
    if any(marker in expected for marker in (".x", ".X", ".*")):
        prefix = re.split(r"\.(?:x|X|\*)", expected, maxsplit=1)[0]
        return version == prefix or version.startswith(f"{prefix}.")
    if operator in {"^", "~"}:
        parsed = _semver(expected)
        actual = _semver(version)
        if parsed is None or actual is None:
            return version == expected
        lower = _compare_semver(actual, parsed) >= 0
        parts = list(parsed[0])
        while len(parts) < 3:
            parts.append(0)
        if operator == "~":
            upper = ((parts[0], parts[1] + 1, 0), "")
        elif parts[0] > 0:
            upper = ((parts[0] + 1, 0, 0), "")
        elif parts[1] > 0:
            upper = ((0, parts[1] + 1, 0), "")
        else:
            upper = ((0, 0, parts[2] + 1), "")
        return lower and _compare_semver(actual, upper) < 0
    comparison = _compare_versions(version, expected)
    return {
        None: comparison == 0,
        "=": comparison == 0,
        ">=": comparison >= 0,
        "<=": comparison <= 0,
        ">": comparison > 0,
        "<": comparison < 0,
    }.get(operator, False)


def _compare_versions(left: str, right: str) -> int:
    left_semver = _semver(left)
    right_semver = _semver(right)
    if left_semver is None or right_semver is None:
        return (left > right) - (left < right)
    return _compare_semver(left_semver, right_semver)


def _semver(value: str) -> tuple[tuple[int, ...], str] | None:
    core = value.split("+", 1)[0]
    main, separator, prerelease = core.partition("-")
    parts = main.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts), prerelease if separator else "~"


def _compare_semver(
    left: tuple[tuple[int, ...], str], right: tuple[tuple[int, ...], str]
) -> int:
    size = max(len(left[0]), len(right[0]))
    left_parts = (*left[0], *(0 for _ in range(size - len(left[0]))))
    right_parts = (*right[0], *(0 for _ in range(size - len(right[0]))))
    if left_parts != right_parts:
        return (left_parts > right_parts) - (left_parts < right_parts)
    if left[1] == right[1]:
        return 0
    if left[1] == "~":
        return 1
    if right[1] == "~":
        return -1
    return (left[1] > right[1]) - (left[1] < right[1])


def _range_text(value: str | list[str]) -> str:
    return " or ".join(value) if isinstance(value, list) else value
