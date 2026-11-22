from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import httpx

from . import rcon
from .base import AdapterCapability, ServerAdapter
from .minecraft_properties import PROPERTY_METADATA, SECRET_PROPERTIES

FABRIC_META = "https://meta.fabricmc.net/v2"


class MinecraftFabricAdapter(ServerAdapter):
    key = "minecraft-fabric"
    display_name = "Minecraft Java · Fabric"
    capabilities = frozenset(
        {
            AdapterCapability.CONSOLE,
            AdapterCapability.MODS,
            AdapterCapability.PLAYERS,
            AdapterCapability.WHITELIST,
            AdapterCapability.OPERATORS,
            AdapterCapability.SETTINGS,
            AdapterCapability.GAME_RULES,
            AdapterCapability.FILES,
            AdapterCapability.BACKUPS,
        }
    )

    async def install(self, path: Path, config: dict[str, Any]) -> dict[str, Any]:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        game_version = str(config.get("game_version", "1.21.4"))
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            loader = config.get("loader_version") or await self._latest_version(
                client, f"{FABRIC_META}/versions/loader/{game_version}"
            )
            installer = config.get("installer_version") or await self._latest_version(
                client, f"{FABRIC_META}/versions/installer"
            )
            jar_url = (
                f"{FABRIC_META}/versions/loader/{game_version}/{loader}/{installer}/server/jar"
            )
            response = await client.get(jar_url)
            response.raise_for_status()
            if len(response.content) < 100_000:
                raise RuntimeError("Fabric installer returned an unexpectedly small file")
            (path / "fabric-server-launch.jar").write_bytes(response.content)

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
        }

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
        installed = int(version_match.group(1))
        if installed < required:
            raise RuntimeError(
                f"Minecraft {game_version} requires Java {required} or newer; "
                f"BLSM found Java {installed}. Rerun the current BLSM installer "
                "to upgrade the managed Java runtime."
            )

    async def send_command(self, path: Path, config: dict[str, Any], command: str) -> str:
        return await rcon.command(
            "127.0.0.1",
            int(config["rcon_port"]),
            str(config["rcon_password"]),
            command,
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
