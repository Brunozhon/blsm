from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class AdapterCapability(str, Enum):
    CONSOLE = "console"
    MODS = "mods"
    MODPACKS = "modpacks"
    PLAYERS = "players"
    WHITELIST = "whitelist"
    OPERATORS = "operators"
    SETTINGS = "settings"
    GAME_RULES = "game_rules"
    FILES = "files"
    BACKUPS = "backups"
    MANAGEMENT = "management"


@dataclass(frozen=True, slots=True)
class InstallProgress:
    phase: str
    detail: str
    percent: int


@dataclass(frozen=True, slots=True)
class ModPackageMetadata:
    package_id: str
    version: str
    name: str
    depends: dict[str, str | list[str]]
    breaks: dict[str, str | list[str]]
    conflicts: dict[str, str | list[str]]
    nested: bool = False


class ServerAdapter(ABC):
    key: str
    display_name: str
    capabilities: frozenset[AdapterCapability]

    @abstractmethod
    async def install(self, path: Path, config: dict[str, Any]) -> dict[str, Any]:
        """Install or update server files and return normalized configuration."""

    @abstractmethod
    def launch_command(self, path: Path, config: dict[str, Any]) -> list[str]:
        """Return a command without shell interpolation."""

    @abstractmethod
    async def send_command(self, path: Path, config: dict[str, Any], command: str) -> str:
        """Send a console command through a game-native remote protocol."""

    @abstractmethod
    async def graceful_stop(self, path: Path, config: dict[str, Any]) -> None:
        """Ask the game server to stop cleanly."""

    @abstractmethod
    def is_installed(self, path: Path) -> bool:
        """Whether enough server files exist to launch."""

    def mod_loader(self, config: dict[str, Any]) -> str:
        """Return the repository loader key for this adapter's packages."""
        raise NotImplementedError("This server adapter does not manage mods")

    def mod_runtime_versions(self, config: dict[str, Any]) -> dict[str, str]:
        """Return adapter-native runtime package versions used by dependency checks."""
        raise NotImplementedError("This server adapter does not manage mods")

    def inspect_mod_package(
        self, content: bytes, filename: str
    ) -> tuple[ModPackageMetadata, ...]:
        """Return normalized package-native dependency metadata."""
        raise NotImplementedError("This server adapter does not manage mods")

    def public_config(self, path: Path, config: dict[str, Any]) -> dict[str, Any]:
        """Return adapter-owned configuration safe for the dashboard."""
        del path
        return dict(config)

    def configuration_transaction(
        self, path: Path, config: dict[str, Any], values: dict[str, str | int]
    ) -> AbstractAsyncContextManager[dict[str, Any]]:
        """Stage an offline configuration change with rollback around persistence."""
        raise NotImplementedError("This server adapter does not expose management settings")

    def validate_environment(self, path: Path, config: dict[str, Any]) -> None:
        """Raise a useful error when host runtime requirements are not met."""
        return None

    async def available_versions(self) -> list[str]:
        """Return installable game versions, newest first."""
        return []

    async def player_state(
        self, path: Path, config: dict[str, Any], query_online: bool
    ) -> dict[str, Any]:
        """Return adapter-normalized player access and presence information."""
        raise NotImplementedError("This server adapter does not manage players")

    async def set_whitelist_member(
        self, path: Path, config: dict[str, Any], name: str, enabled: bool
    ) -> str:
        """Add or remove a player from the server whitelist."""
        raise NotImplementedError("This server adapter does not manage a whitelist")

    async def set_operator(
        self, path: Path, config: dict[str, Any], name: str, enabled: bool
    ) -> str:
        """Grant or revoke a player's operator role."""
        raise NotImplementedError("This server adapter does not manage operators")

    async def set_whitelist_enabled(
        self, path: Path, config: dict[str, Any], enabled: bool
    ) -> str:
        """Enable or disable whitelist enforcement."""
        raise NotImplementedError("This server adapter does not manage a whitelist")

    def server_settings(
        self, path: Path, config: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Return editable, adapter-described server settings."""
        raise NotImplementedError("This server adapter does not expose settings")

    def update_server_settings(
        self, path: Path, config: dict[str, Any], values: dict[str, str]
    ) -> dict[str, Any]:
        """Persist settings and return the normalized adapter configuration."""
        raise NotImplementedError("This server adapter does not expose settings")

    async def game_rules(
        self, path: Path, config: dict[str, Any], query_values: bool
    ) -> list[dict[str, Any]]:
        """Return adapter-described game rules and, when available, live values."""
        raise NotImplementedError("This server adapter does not expose game rules")

    async def update_game_rules(
        self, path: Path, config: dict[str, Any], values: dict[str, bool | int]
    ) -> list[dict[str, Any]]:
        """Apply game rules and return refreshed adapter-normalized state."""
        raise NotImplementedError("This server adapter does not expose game rules")

    async def prepare_backup_shutdown(
        self, path: Path, config: dict[str, Any]
    ) -> None:
        """Coordinate a scheduled shutdown before backup.

        A future game-side companion may override this hook to warn players and
        confirm readiness. Core backup code remains independent of that protocol.
        """
        return None

    def public_metadata(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "capabilities": sorted(self.capabilities),
        }


