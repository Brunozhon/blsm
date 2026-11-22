from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

import httpx

from . import rcon
from .base import AdapterCapability, ServerAdapter

FABRIC_META = "https://meta.fabricmc.net/v2"


class MinecraftFabricAdapter(ServerAdapter):
    key = "minecraft-fabric"
    display_name = "Minecraft Java · Fabric"
    capabilities = frozenset(
        {
            AdapterCapability.CONSOLE,
            AdapterCapability.MODS,
            AdapterCapability.PLAYERS,
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

    async def send_command(self, path: Path, config: dict[str, Any], command: str) -> str:
        return await rcon.command(
            "127.0.0.1",
            int(config["rcon_port"]),
            str(config["rcon_password"]),
            command,
        )

    async def graceful_stop(self, path: Path, config: dict[str, Any]) -> None:
        await self.send_command(path, config, "stop")

    def is_installed(self, path: Path) -> bool:
        return (path / "fabric-server-launch.jar").is_file()

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
        path.write_text(contents, encoding="utf-8")
