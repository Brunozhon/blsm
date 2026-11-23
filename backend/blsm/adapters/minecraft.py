from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx

from . import rcon
from .base import AdapterCapability, ModPackageMetadata, ServerAdapter
from .fabric_metadata import inspect_fabric_jar
from .minecraft_game_rules import MINECRAFT_GAME_RULES
from .minecraft_properties import PROPERTY_METADATA, SECRET_PROPERTIES

FABRIC_META = "https://meta.fabricmc.net/v2"
MAX_LOADER_LIBRARY_BYTES = 32 * 1024 * 1024


class MinecraftFabricAdapter(ServerAdapter):
    key = "minecraft-fabric"
    display_name = "Minecraft Java · Fabric"
    capabilities = frozenset(
        {
            AdapterCapability.CONSOLE,
            AdapterCapability.MODS,
            AdapterCapability.MODPACKS,
            AdapterCapability.PLAYERS,
            AdapterCapability.WHITELIST,
            AdapterCapability.OPERATORS,
            AdapterCapability.SETTINGS,
            AdapterCapability.GAME_RULES,
            AdapterCapability.FILES,
            AdapterCapability.BACKUPS,
            AdapterCapability.MANAGEMENT,
        }
    )

    def __init__(self) -> None:
        self._loader_runtime_cache: dict[tuple[str, str], dict[str, str]] = {}

    async def install(self, path: Path, config: dict[str, Any]) -> dict[str, Any]:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        game_version = str(config.get("game_version", "1.21.4"))
        launcher, loader, installer = await self._download_launcher(
            game_version,
            str(config["loader_version"]) if config.get("loader_version") else None,
            str(config["installer_version"]) if config.get("installer_version") else None,
        )
        (path / "fabric-server-launch.jar").write_bytes(launcher)

        rcon_password = str(config.get("rcon_password") or secrets.token_urlsafe(24))
        rcon_port = int(config.get("rcon_port", 25575))
        server_port = int(config.get("server_port", 25565))
        properties = path / "server.properties"
        existing = self._read_properties(properties)
        existing.update(
            {
                "server-port": str(server_port),
                "enable-rcon": "true",
                "rcon.port": str(rcon_port),
                "rcon.password": rcon_password,
                "broadcast-rcon-to-ops": "false",
                "motd": str(config.get("motd", "Managed by BLSM")),
            }
        )
        world_seed = str(config.get("world_seed", "")).strip()
        if world_seed:
            existing["level-seed"] = world_seed
        self._write_properties(properties, existing)
        if config.get("accept_eula"):
            (path / "eula.txt").write_text("eula=true\n", encoding="utf-8")
        (path / "mods").mkdir(exist_ok=True)
        return {
            **config,
            "game_version": game_version,
            "loader_version": loader,
            "installer_version": installer,
            "rcon_password": rcon_password,
            "rcon_port": rcon_port,
            "server_port": server_port,
            "memory_mb": max(1024, int(config.get("memory_mb", 4096))),
            "world_seed": world_seed,
        }

    async def _download_launcher(
        self,
        game_version: str,
        loader_version: str | None = None,
        installer_version: str | None = None,
    ) -> tuple[bytes, str, str]:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            loader = loader_version or await self._latest_version(
                client, f"{FABRIC_META}/versions/loader/{game_version}"
            )
            installer = installer_version or await self._latest_version(
                client, f"{FABRIC_META}/versions/installer"
            )
            response = await client.get(
                f"{FABRIC_META}/versions/loader/{game_version}/{loader}/{installer}/server/jar"
            )
            response.raise_for_status()
            if len(response.content) < 100_000:
                raise RuntimeError("Fabric installer returned an unexpectedly small file")
        return response.content, loader, installer

    def public_config(self, path: Path, config: dict[str, Any]) -> dict[str, Any]:
        result = dict(config)
        properties_path = path / "server.properties"
        properties: dict[str, str] = {}
        if properties_path.is_file() and not properties_path.is_symlink():
            properties = self._read_properties(properties_path)
        result["world_seed"] = properties.get(
            "level-seed", str(config.get("world_seed", ""))
        )
        result["world_seed_locked"] = self._world_generated(path, properties)
        return result

    @asynccontextmanager
    async def configuration_transaction(
        self, path: Path, config: dict[str, Any], values: dict[str, str | int]
    ) -> AsyncIterator[dict[str, Any]]:
        unknown = values.keys() - {"memory_mb", "world_seed", "game_version"}
        if unknown:
            raise ValueError(
                f"Unsupported server configuration: {', '.join(sorted(unknown))}"
            )
        normalized = dict(config)
        properties_path = path / "server.properties"
        properties_changed = False
        properties = self._read_properties(properties_path)
        original_properties = (
            properties_path.read_bytes()
            if properties_path.is_file() and not properties_path.is_symlink()
            else None
        )
        if properties_path.is_symlink():
            raise RuntimeError("Refusing to manage a symlinked server.properties file")

        if "memory_mb" in values:
            memory = values["memory_mb"]
            if type(memory) is not int or not 1024 <= memory <= 14336:
                raise ValueError("Memory allocation must be between 1024 and 14336 MB")
            normalized["memory_mb"] = memory

        if "world_seed" in values:
            seed = values["world_seed"]
            if not isinstance(seed, str):
                raise ValueError("World seed must be text or a whole number")
            seed = seed.strip()
            if len(seed) > 128 or any(character in seed for character in "\r\n\x00"):
                raise ValueError("World seed is invalid")
            current_seed = properties.get("level-seed", str(config.get("world_seed", "")))
            if seed != current_seed and self._world_generated(path, properties):
                raise ValueError(
                    "The world has already been generated; changing level-seed would not "
                    "replace its stored seed. Restore or create a different world instead."
                )
            properties["level-seed"] = seed
            normalized["world_seed"] = seed
            properties_changed = seed != current_seed

        staging: Path | None = None
        launcher_backup: Path | None = None
        launcher_path = path / "fabric-server-launch.jar"
        if "game_version" in values:
            target = values["game_version"]
            if not isinstance(target, str) or re.fullmatch(r"[0-9A-Za-z.+_-]+", target) is None:
                raise ValueError("Game version is invalid")
            current = str(config.get("game_version", ""))
            if target != current:
                if self._version_key(target) <= self._version_key(current):
                    raise ValueError("Select a newer Minecraft version")
                available = await self.available_versions()
                if target not in available:
                    raise ValueError(
                        "The selected Minecraft version is not a stable Fabric release"
                    )
                upgraded = {**normalized, "game_version": target}
                upgraded.pop("loader_version", None)
                upgraded.pop("installer_version", None)
                self.validate_environment(path, upgraded)
                if self.is_installed(path):
                    launcher, loader, installer = await self._download_launcher(target)
                    descriptor, staging_name = tempfile.mkstemp(
                        prefix=".fabric-upgrade.", suffix=".jar", dir=path
                    )
                    staging = Path(staging_name)
                    with os.fdopen(descriptor, "wb") as handle:
                        handle.write(launcher)
                        handle.flush()
                        os.fsync(handle.fileno())
                    staging.chmod(0o600)
                    upgraded["loader_version"] = loader
                    upgraded["installer_version"] = installer
                normalized = upgraded

        applied_launcher = False
        success = False
        try:
            if properties_changed:
                self._write_properties(properties_path, properties)
            if staging is not None:
                if launcher_path.is_symlink():
                    raise RuntimeError("Refusing to replace a symlinked Fabric launcher")
                descriptor, backup_name = tempfile.mkstemp(
                    prefix=".fabric-launcher-backup.", suffix=".jar", dir=path
                )
                os.close(descriptor)
                launcher_backup = Path(backup_name)
                launcher_backup.unlink()
                if launcher_path.is_file():
                    launcher_path.replace(launcher_backup)
                staging.replace(launcher_path)
                applied_launcher = True
            yield normalized
            success = True
        finally:
            if not success:
                if applied_launcher:
                    launcher_path.unlink(missing_ok=True)
                if launcher_backup is not None and launcher_backup.is_file():
                    launcher_backup.replace(launcher_path)
                if properties_changed:
                    if original_properties is None:
                        properties_path.unlink(missing_ok=True)
                    else:
                        properties_path.write_bytes(original_properties)
            if staging is not None:
                staging.unlink(missing_ok=True)
            if launcher_backup is not None:
                launcher_backup.unlink(missing_ok=True)

    @staticmethod
    def _version_key(value: str) -> tuple[int, ...]:
        parts = re.match(r"^(\d+(?:\.\d+)*)", value)
        return tuple(int(part) for part in parts.group(1).split(".")) if parts else (0,)

    @staticmethod
    def _world_generated(path: Path, properties: dict[str, str]) -> bool:
        root = path.resolve()
        world = (root / properties.get("level-name", "world")).resolve()
        if world == root or root not in world.parents:
            return True
        return (world / "level.dat").is_file()

    def mod_loader(self, config: dict[str, Any]) -> str:
        del config
        return "fabric"

    async def mod_runtime_versions(
        self, path: Path, config: dict[str, Any]
    ) -> dict[str, str]:
        del path
        versions = {
            "minecraft": str(config.get("game_version", "")),
            "fabricloader": str(config.get("loader_version", "")),
            "java": str(self._installed_java_major()),
        }
        game_version = versions["minecraft"]
        loader_version = versions["fabricloader"]
        if game_version and loader_version:
            versions.update(
                await self._loader_runtime_packages(game_version, loader_version)
            )
        return versions

    async def _loader_runtime_packages(
        self, game_version: str, loader_version: str
    ) -> dict[str, str]:
        cache_key = (game_version, loader_version)
        cached = self._loader_runtime_cache.get(cache_key)
        if cached is not None:
            return dict(cached)
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response = await client.get(
                f"{FABRIC_META}/versions/loader/{game_version}/{loader_version}"
            )
            response.raise_for_status()
            metadata = response.json()
            if not isinstance(metadata, dict):
                raise RuntimeError("Fabric returned invalid loader runtime metadata")
            launcher_metadata = metadata.get("launcherMeta")
            library_groups = (
                launcher_metadata.get("libraries")
                if isinstance(launcher_metadata, dict)
                else None
            )
            libraries = (
                library_groups.get("development")
                if isinstance(library_groups, dict)
                else None
            )
            if not isinstance(libraries, list):
                raise RuntimeError("Fabric returned invalid loader library metadata")
            packages: dict[str, str] = {}
            for library in libraries:
                if not isinstance(library, dict):
                    raise RuntimeError("Fabric returned invalid loader library metadata")
                coordinate = str(library.get("name", ""))
                parts = coordinate.split(":")
                if len(parts) != 3 or any(
                    re.fullmatch(r"[A-Za-z0-9_.+-]+", part) is None for part in parts
                ):
                    raise RuntimeError("Fabric returned an unsafe loader library coordinate")
                base_url = str(library.get("url", ""))
                if base_url.rstrip("/") != "https://maven.fabricmc.net":
                    raise RuntimeError("Fabric returned an untrusted loader library URL")
                group, artifact, version = parts
                try:
                    size = int(library.get("size", 0))
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        "Fabric returned unsafe loader library metadata"
                    ) from exc
                expected = str(library.get("sha512", ""))
                if (
                    size <= 0
                    or size > MAX_LOADER_LIBRARY_BYTES
                    or re.fullmatch(r"[0-9a-f]{128}", expected) is None
                ):
                    raise RuntimeError("Fabric returned unsafe loader library metadata")
                artifact_url = (
                    f"{base_url.rstrip('/')}/{group.replace('.', '/')}/{artifact}/"
                    f"{version}/{artifact}-{version}.jar"
                )
                artifact_response = await client.get(artifact_url)
                artifact_response.raise_for_status()
                content = artifact_response.content
                if len(content) != size or hashlib.sha512(content).hexdigest() != expected:
                    raise RuntimeError("A Fabric Loader runtime library failed validation")
                try:
                    inspected = inspect_fabric_jar(content, f"{artifact}-{version}.jar")
                except ValueError as exc:
                    raise RuntimeError(
                        "Could not inspect a Fabric Loader runtime library"
                    ) from exc
                for package in inspected:
                    packages[package.package_id] = package.version
        self._loader_runtime_cache[cache_key] = packages
        return dict(packages)

    def inspect_mod_package(
        self, content: bytes, filename: str
    ) -> tuple[ModPackageMetadata, ...]:
        return inspect_fabric_jar(content, filename)

    async def available_versions(self) -> list[str]:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            response = await client.get(f"{FABRIC_META}/versions/game")
            response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("Fabric returned an invalid game version list")
        versions = list(
            dict.fromkeys(
                str(item["version"])
                for item in payload
                if isinstance(item, dict)
                and item.get("stable") is True
                and isinstance(item.get("version"), str)
                and re.fullmatch(r"[0-9A-Za-z.+_-]+", item["version"])
            )
        )
        if not versions:
            raise RuntimeError("Fabric did not return any stable game versions")
        return versions

    @staticmethod
    async def _latest_version(client: httpx.AsyncClient, url: str) -> str:
        response = await client.get(url)
        response.raise_for_status()
        values = response.json()
        if not values:
            raise RuntimeError("No compatible Fabric version was found")
        entry = next((value for value in values if value.get("stable", True)), values[0])
        if "loader" in entry:
            return str(entry["loader"]["version"])
        return str(entry["version"])

    def launch_command(self, path: Path, config: dict[str, Any]) -> list[str]:
        memory = max(1024, int(config.get("memory_mb", 4096)))
        return [
            "java",
            f"-Xms{min(1024, memory)}M",
            f"-Xmx{memory}M",
            "-jar",
            "fabric-server-launch.jar",
            "nogui",
        ]

    @staticmethod
    def required_java_major(game_version: str) -> int:
        """Return the minimum Java runtime for supported Minecraft releases."""
        match = re.match(r"^(\d+)(?:\.(\d+))?(?:\.(\d+))?", game_version)
        if match is None:
            return 25
        major = int(match.group(1))
        minor = int(match.group(2) or 0)
        patch = int(match.group(3) or 0)
        if major >= 26:
            return 25
        if major == 1 and (minor > 20 or (minor == 20 and patch >= 5)):
            return 21
        if major == 1 and minor >= 18:
            return 17
        if major == 1 and minor == 17:
            return 16
        return 8

    def validate_environment(self, path: Path, config: dict[str, Any]) -> None:
        del path
        game_version = str(config.get("game_version", "1.21.4"))
        required = self.required_java_major(game_version)
        java = shutil.which("java")
        if java is None:
            raise RuntimeError(
                f"Minecraft {game_version} requires Java {required} or newer, "
                "but Java is not installed"
            )
        installed = self._installed_java_major()
        if installed < required:
            raise RuntimeError(
                f"Minecraft {game_version} requires Java {required} or newer; "
                f"BLSM found Java {installed}. Rerun the current BLSM installer "
                "to upgrade the managed Java runtime."
            )

    @staticmethod
    def _installed_java_major() -> int:
        java = shutil.which("java")
        if java is None:
            return 0
        try:
            result = subprocess.run(
                [java, "-XshowSettings:properties", "-version"],
                capture_output=True,
                check=False,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise RuntimeError(f"Could not inspect the Java runtime at {java}") from exc
        output = f"{result.stdout}\n{result.stderr}"
        version_match = re.search(r"java\.specification\.version\s*=\s*(?:1\.)?(\d+)", output)
        if result.returncode != 0 or version_match is None:
            raise RuntimeError(f"Could not determine the Java version at {java}")
        return int(version_match.group(1))

    async def send_command(self, path: Path, config: dict[str, Any], command: str) -> str:
        return await rcon.command(
            "127.0.0.1",
            int(config["rcon_port"]),
            str(config["rcon_password"]),
            command,
        )

    async def send_commands(
        self, path: Path, config: dict[str, Any], commands: list[str]
    ) -> list[str]:
        del path
        return await rcon.commands(
            "127.0.0.1",
            int(config["rcon_port"]),
            str(config["rcon_password"]),
            commands,
        )

    async def player_state(
        self, path: Path, config: dict[str, Any], query_online: bool
    ) -> dict[str, Any]:
        properties = self._read_properties(path / "server.properties")
        maximum = int(properties.get("max-players", "20"))
        online: list[str] = []
        if query_online:
            response = await self.send_command(path, config, "list")
            online, reported_maximum = self._parse_online_players(response)
            if reported_maximum is not None:
                maximum = reported_maximum
        return {
            "online": online,
            "online_count": len(online),
            "max_players": maximum,
            "whitelist_enabled": properties.get("white-list", "false").lower() == "true",
            "whitelisted": self._read_player_names(path / "whitelist.json"),
            "operators": self._read_player_names(path / "ops.json"),
        }

    async def set_whitelist_member(
        self, path: Path, config: dict[str, Any], name: str, enabled: bool
    ) -> str:
        safe_name = self._validate_player_name(name)
        action = "add" if enabled else "remove"
        return await self.send_command(path, config, f"whitelist {action} {safe_name}")

    async def set_operator(
        self, path: Path, config: dict[str, Any], name: str, enabled: bool
    ) -> str:
        safe_name = self._validate_player_name(name)
        command = "op" if enabled else "deop"
        return await self.send_command(path, config, f"{command} {safe_name}")

    async def set_whitelist_enabled(
        self, path: Path, config: dict[str, Any], enabled: bool
    ) -> str:
        response = await self.send_command(
            path, config, f"whitelist {'on' if enabled else 'off'}"
        )
        properties_path = path / "server.properties"
        properties = self._read_properties(properties_path)
        properties["white-list"] = "true" if enabled else "false"
        self._write_properties(properties_path, properties)
        return response

    async def graceful_stop(self, path: Path, config: dict[str, Any]) -> None:
        await self.send_command(path, config, "stop")

    def server_settings(
        self, path: Path, config: dict[str, Any]
    ) -> list[dict[str, Any]]:
        del config
        properties_path = self._properties_path(path)
        properties = self._read_properties(properties_path)
        result: list[dict[str, Any]] = []
        for key, value in sorted(properties.items()):
            metadata = PROPERTY_METADATA.get(key, {})
            secret = key in SECRET_PROPERTIES or metadata.get("type") == "secret"
            result.append(
                {
                    "key": key,
                    "value": "" if secret else value,
                    "configured": bool(value) if secret else True,
                    "type": metadata.get("type", "string"),
                    "description": metadata.get("description"),
                    "category": metadata.get("category", "Other"),
                    "options": metadata.get("options"),
                    "min": metadata.get("min"),
                    "max": metadata.get("max"),
                    "secret": secret,
                }
            )
        return result

    def update_server_settings(
        self, path: Path, config: dict[str, Any], values: dict[str, str]
    ) -> dict[str, Any]:
        properties_path = self._properties_path(path)
        properties = self._read_properties(properties_path)
        for key, value in values.items():
            self._validate_property(key, value)
            properties[key] = value
        self._write_properties(properties_path, properties)

        normalized = dict(config)
        integer_config = {"server-port": "server_port", "rcon.port": "rcon_port"}
        for property_name, config_name in integer_config.items():
            if property_name in values:
                normalized[config_name] = int(values[property_name])
        if "rcon.password" in values:
            normalized["rcon_password"] = values["rcon.password"]
        return normalized

    async def game_rules(
        self, path: Path, config: dict[str, Any], query_values: bool
    ) -> list[dict[str, Any]]:
        result = [
            {"key": key, **metadata, "value": None, "available": False}
            for key, metadata in MINECRAFT_GAME_RULES.items()
        ]
        if not query_values:
            return result
        responses = await self.send_commands(
            path,
            config,
            [f"gamerule {rule['key']}" for rule in result],
        )
        for rule, response in zip(result, responses, strict=True):
            value = self._parse_game_rule_value(response, rule["type"])
            if value is not None:
                rule["value"] = value
                rule["available"] = True
        return result

    async def update_game_rules(
        self, path: Path, config: dict[str, Any], values: dict[str, bool | int]
    ) -> list[dict[str, Any]]:
        commands: list[str] = []
        for key, value in values.items():
            metadata = MINECRAFT_GAME_RULES.get(key)
            if metadata is None:
                raise ValueError(f"Unknown Minecraft 26.3 game rule: {key}")
            self._validate_game_rule(key, metadata, value)
            serialized = str(value).lower() if isinstance(value, bool) else str(value)
            commands.append(f"gamerule {key} {serialized}")
        responses = await self.send_commands(path, config, commands)
        for response in responses:
            if self._parse_game_rule_value(response, None) is None:
                raise RuntimeError(f"Minecraft rejected a game rule update: {response}")
        return await self.game_rules(path, config, True)

    def is_installed(self, path: Path) -> bool:
        return (path / "fabric-server-launch.jar").is_file()

    @staticmethod
    def _properties_path(path: Path) -> Path:
        properties_path = path / "server.properties"
        if properties_path.is_symlink():
            raise RuntimeError("Refusing to manage a symlinked server.properties file")
        if not properties_path.is_file():
            raise RuntimeError("Install the server before editing server.properties")
        return properties_path

    @staticmethod
    def _validate_property(key: str, value: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", key):
            raise ValueError(f"Invalid setting name: {key}")
        if len(value) > 8192 or any(character in value for character in "\r\n\x00"):
            raise ValueError(f"Invalid value for setting: {key}")
        metadata = PROPERTY_METADATA.get(key)
        if metadata is None:
            return
        kind = metadata.get("type")
        if kind == "boolean" and value not in {"true", "false"}:
            raise ValueError(f"{key} must be true or false")
        options = metadata.get("options")
        if options is not None and value not in options:
            raise ValueError(f"{key} must be one of: {', '.join(options)}")
        if kind == "integer":
            try:
                number = int(value)
            except ValueError as exc:
                raise ValueError(f"{key} must be a whole number") from exc
            if metadata.get("min") is not None and number < metadata["min"]:
                raise ValueError(f"{key} must be at least {metadata['min']}")
            if metadata.get("max") is not None and number > metadata["max"]:
                raise ValueError(f"{key} must be at most {metadata['max']}")

    @staticmethod
    def _parse_game_rule_value(response: str, kind: str | None) -> bool | int | None:
        cleaned = re.sub(r"§.", "", response).strip()
        matches = re.findall(r"(?<![\w-])(true|false|-?\d+)(?![\w-])", cleaned)
        if not matches:
            return None
        value = matches[-1]
        if kind == "boolean" or (kind is None and value in {"true", "false"}):
            return value == "true"
        try:
            return int(value)
        except ValueError:
            return None

    @staticmethod
    def _validate_game_rule(
        key: str, metadata: dict[str, Any], value: bool | int
    ) -> None:
        if metadata["type"] == "boolean":
            if type(value) is not bool:
                raise ValueError(f"{key} must be true or false")
            return
        if type(value) is not int:
            raise ValueError(f"{key} must be a whole number")
        if value < metadata["min"]:
            raise ValueError(f"{key} must be at least {metadata['min']}")
        if metadata.get("max") is not None and value > metadata["max"]:
            raise ValueError(f"{key} must be at most {metadata['max']}")

    @staticmethod
    def _validate_player_name(name: str) -> str:
        if re.fullmatch(r"[A-Za-z0-9_]{1,16}", name) is None:
            raise RuntimeError("Player names may only contain letters, numbers, and underscores")
        return name

    @staticmethod
    def _read_player_names(path: Path) -> list[str]:
        if not path.exists():
            return []
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read {path.name}") from exc
        if not isinstance(values, list):
            raise RuntimeError(f"Invalid player data in {path.name}")
        names = {
            entry["name"]
            for entry in values
            if isinstance(entry, dict)
            and isinstance(entry.get("name"), str)
            and entry["name"]
        }
        return sorted(names, key=str.casefold)

    @staticmethod
    def _parse_online_players(response: str) -> tuple[list[str], int | None]:
        cleaned = re.sub(r"§.", "", response).strip()
        match = re.search(
            r"(\d+)\s+of a max of\s+(\d+)\s+players online\s*:?\s*(.*)$",
            cleaned,
            re.IGNORECASE,
        )
        if match is None:
            raise RuntimeError("The server returned an unrecognized player list")
        count = int(match.group(1))
        maximum = int(match.group(2))
        names = [name.strip() for name in match.group(3).split(",") if name.strip()]
        if len(names) != count:
            raise RuntimeError("The server returned an incomplete player list")
        return names, maximum

    @staticmethod
    def _read_properties(path: Path) -> dict[str, str]:
        result: dict[str, str] = {}
        if not path.exists():
            return result
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                result[key] = value
        return result

    @staticmethod
    def _write_properties(path: Path, properties: dict[str, str]) -> None:
        contents = "# Managed by BLSM\n" + "".join(
            f"{key}={value}\n" for key, value in sorted(properties.items())
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".server.properties.", dir=path.parent, text=True
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(contents)
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.chmod(0o600)
            temporary_path.replace(path)
        finally:
            temporary_path.unlink(missing_ok=True)
