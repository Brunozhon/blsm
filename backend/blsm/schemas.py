from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator


class PasswordPayload(BaseModel):
    password: str = Field(min_length=12, max_length=256)


class ServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    adapter: str = "minecraft-fabric"
    game_version: str = Field(default="1.21.4", pattern=r"^[0-9A-Za-z.+_-]+$")
    memory_mb: int = Field(default=4096, ge=1024, le=14336)
    server_port: int = Field(default=25565, ge=1024, le=65535)
    rcon_port: int = Field(default=25575, ge=1024, le=65535)
    accept_eula: bool = False


class ConsoleCommand(BaseModel):
    command: str = Field(min_length=1, max_length=512)


class PlayerAccess(BaseModel):
    name: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9_]+$")
    enabled: bool


class WhitelistSetting(BaseModel):
    enabled: bool


class ServerSettingsUpdate(BaseModel):
    values: dict[str, str]

    @field_validator("values")
    @classmethod
    def validate_values(cls, values: dict[str, str]) -> dict[str, str]:
        if not values:
            raise ValueError("At least one setting must be provided")
        if len(values) > 256:
            raise ValueError("Too many settings in one update")
        for key, value in values.items():
            if not 1 <= len(key) <= 128 or not all(
                character.isalnum() or character in "._-" for character in key
            ):
                raise ValueError(f"Invalid setting name: {key}")
            if len(value) > 8192 or any(character in value for character in "\r\n\x00"):
                raise ValueError(f"Invalid value for setting: {key}")
        return values


class GameRulesUpdate(BaseModel):
    values: dict[str, StrictBool | StrictInt]

    @field_validator("values")
    @classmethod
    def validate_values(cls, values: dict[str, bool | int]) -> dict[str, bool | int]:
        if not values:
            raise ValueError("At least one game rule must be provided")
        if len(values) > 128:
            raise ValueError("Too many game rules in one update")
        for key in values:
            if not 1 <= len(key) <= 128 or not all(
                character.isalnum() or character in "._:-" for character in key
            ):
                raise ValueError(f"Invalid game rule name: {key}")
        return values


class BackupPolicyUpdate(BaseModel):
    enabled: bool = False
    interval_hours: int = Field(default=24, ge=1, le=168)
    retention_count: int = Field(default=7, ge=1, le=100)
    max_total_mb: int = Field(default=40960, ge=1024, le=204800)


class ModInstall(BaseModel):
    project_id: str = Field(min_length=1, max_length=128)
    title: str | None = Field(default=None, max_length=256)


class ServerView(BaseModel):
    id: str
    name: str
    adapter: str
    path: str
    status: str
    installed: bool
    capabilities: list[str]
    config: dict[str, Any]

