from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import pytest
from blsm.adapters import minecraft
from blsm.adapters.minecraft import MinecraftFabricAdapter
from blsm.adapters.minecraft_game_rules import (
    GAME_RULE_METADATA_VERSION,
    MINECRAFT_GAME_RULES,
)
from blsm.adapters.minecraft_properties import (
    MINECRAFT_26_3_PROPERTY_NAMES,
    PROPERTY_METADATA,
    PROPERTY_METADATA_VERSION,
)


def test_java_requirements_follow_minecraft_versions() -> None:
    required = MinecraftFabricAdapter.required_java_major

    assert required("26.3") == 25
    assert required("26.1-pre-1") == 25
    assert required("1.21.4") == 21
    assert required("1.20.5") == 21
    assert required("1.20.4") == 17


def test_property_descriptions_cover_minecraft_26_3() -> None:
    assert PROPERTY_METADATA_VERSION == "26.3"
    assert PROPERTY_METADATA.keys() >= MINECRAFT_26_3_PROPERTY_NAMES
    assert all(
        PROPERTY_METADATA[key].get("description")
        for key in MINECRAFT_26_3_PROPERTY_NAMES
    )
    assert "Obsolete in 26.3" in PROPERTY_METADATA["pvp"]["description"]


def test_game_rule_catalog_covers_minecraft_26_3() -> None:
    assert GAME_RULE_METADATA_VERSION == "26.3"
    assert len(MINECRAFT_GAME_RULES) == 59
    assert all(rule["description"] for rule in MINECRAFT_GAME_RULES.values())
    assert MINECRAFT_GAME_RULES["minecraft:random_tick_speed"]["type"] == "integer"
    assert MINECRAFT_GAME_RULES["minecraft:pvp"]["type"] == "boolean"


def test_rejects_java_runtime_that_is_too_old(monkeypatch: Any) -> None:
    monkeypatch.setattr(minecraft.shutil, "which", lambda executable: "/usr/bin/java")
    monkeypatch.setattr(
        minecraft.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(
            args[0], 0, "", "java.specification.version = 21\n"
        ),
    )

    with pytest.raises(RuntimeError, match=r"Minecraft 26\.3 requires Java 25"):
        MinecraftFabricAdapter().validate_environment(
            Path("/server"), {"game_version": "26.3"}
        )


def test_reads_and_updates_all_server_properties_without_exposing_secrets(
    tmp_path: Path,
) -> None:
    properties_path = tmp_path / "server.properties"
    properties_path.write_text(
        "# Minecraft server properties\n"
        "allow-flight=false\n"
        "difficulty=normal\n"
        "rcon.password=do-not-expose\n"
        "server-port=25565\n"
        "future-setting=preserved\n",
        encoding="utf-8",
    )
    adapter = MinecraftFabricAdapter()

    settings = {item["key"]: item for item in adapter.server_settings(tmp_path, {})}
    assert settings["allow-flight"]["type"] == "boolean"
    assert settings["allow-flight"]["description"]
    assert settings["future-setting"]["type"] == "string"
    assert settings["future-setting"]["value"] == "preserved"
    assert settings["rcon.password"]["value"] == ""
    assert settings["rcon.password"]["configured"] is True

    config = adapter.update_server_settings(
        tmp_path,
        {"server_port": 25565, "rcon_password": "do-not-expose"},
        {
            "allow-flight": "true",
            "future-setting": "updated",
            "rcon.password": "new-secret",
            "server-port": "25570",
        },
    )
    contents = properties_path.read_text(encoding="utf-8")
    assert "allow-flight=true" in contents
    assert "future-setting=updated" in contents
    assert "difficulty=normal" in contents
    assert config["server_port"] == 25570
    assert config["rcon_password"] == "new-secret"
    assert properties_path.stat().st_mode & 0o777 == 0o600


def test_rejects_invalid_property_values_and_symlinks(tmp_path: Path) -> None:
    adapter = MinecraftFabricAdapter()
    properties_path = tmp_path / "server.properties"
    properties_path.write_text("allow-flight=false\n", encoding="utf-8")

    with pytest.raises(ValueError, match="true or false"):
        adapter.update_server_settings(tmp_path, {}, {"allow-flight": "sometimes"})
    with pytest.raises(ValueError, match="whole number"):
        adapter.update_server_settings(tmp_path, {}, {"server-port": "minecraft"})
    with pytest.raises(ValueError, match="Invalid setting name"):
        adapter.update_server_settings(tmp_path, {}, {"unsafe setting": "value"})

    properties_path.unlink()
    external = tmp_path / "external.properties"
    external.write_text("motd=external\n", encoding="utf-8")
    properties_path.symlink_to(external)
    with pytest.raises(RuntimeError, match="symlinked"):
        adapter.server_settings(tmp_path, {})


async def test_reads_player_state_and_uses_safe_console_commands(
    tmp_path: Path, monkeypatch: Any
) -> None:
    (tmp_path / "server.properties").write_text(
        "max-players=12\nwhite-list=true\n", encoding="utf-8"
    )
    (tmp_path / "whitelist.json").write_text(
        '[{"uuid":"one","name":"Alex"}]', encoding="utf-8"
    )
    (tmp_path / "ops.json").write_text(
        '[{"uuid":"two","name":"Steve","level":4}]', encoding="utf-8"
    )
    commands: list[str] = []

    async def command(path: Path, config: dict[str, Any], value: str) -> str:
        commands.append(value)
        if value == "list":
            return "There are 2 of a max of 12 players online: Alex, Steve"
        return "Player permissions updated"

    adapter = MinecraftFabricAdapter()
    monkeypatch.setattr(adapter, "send_command", command)

    state = await adapter.player_state(tmp_path, {}, True)
    assert state == {
        "online": ["Alex", "Steve"],
        "online_count": 2,
        "max_players": 12,
        "whitelist_enabled": True,
        "whitelisted": ["Alex"],
        "operators": ["Steve"],
    }
    await adapter.set_whitelist_member(tmp_path, {}, "Alex_2", False)
    await adapter.set_operator(tmp_path, {}, "Alex_2", True)
    await adapter.set_whitelist_enabled(tmp_path, {}, False)
    assert commands == [
        "list",
        "whitelist remove Alex_2",
        "op Alex_2",
        "whitelist off",
    ]
    assert "white-list=false" in (tmp_path / "server.properties").read_text()


async def test_rejects_unsafe_player_name_before_rcon(
    tmp_path: Path, monkeypatch: Any
) -> None:
    called = False

    async def command(path: Path, config: dict[str, Any], value: str) -> str:
        nonlocal called
        called = True
        return value

    adapter = MinecraftFabricAdapter()
    monkeypatch.setattr(adapter, "send_command", command)

    with pytest.raises(RuntimeError, match="letters, numbers, and underscores"):
        await adapter.set_operator(tmp_path, {}, "Alex stop", True)
    assert called is False


async def test_queries_and_updates_game_rules_over_batched_rcon(
    tmp_path: Path, monkeypatch: Any
) -> None:
    batches: list[list[str]] = []
    adapter = MinecraftFabricAdapter()

    async def commands(
        path: Path, config: dict[str, Any], values: list[str]
    ) -> list[str]:
        batches.append(values)
        responses: list[str] = []
        for command in values:
            key = command.split()[1]
            metadata = MINECRAFT_GAME_RULES[key]
            value = command.split()[2] if len(command.split()) == 3 else str(
                metadata["default"]
            ).lower()
            responses.append(f"Game rule {key} is currently set to {value}")
        return responses

    monkeypatch.setattr(adapter, "send_commands", commands)

    rules = await adapter.game_rules(tmp_path, {}, True)
    assert len(rules) == 59
    assert all(rule["available"] for rule in rules)
    pvp = next(rule for rule in rules if rule["key"] == "minecraft:pvp")
    assert pvp["value"] is True

    updated = await adapter.update_game_rules(
        tmp_path,
        {},
        {"minecraft:pvp": False, "minecraft:random_tick_speed": 6},
    )
    assert batches[-2] == [
        "gamerule minecraft:pvp false",
        "gamerule minecraft:random_tick_speed 6",
    ]
    assert len(batches[-1]) == 59
    assert len(updated) == 59


async def test_rejects_invalid_game_rule_updates(
    tmp_path: Path, monkeypatch: Any
) -> None:
    adapter = MinecraftFabricAdapter()
    called = False

    async def commands(
        path: Path, config: dict[str, Any], values: list[str]
    ) -> list[str]:
        nonlocal called
        called = True
        return values

    monkeypatch.setattr(adapter, "send_commands", commands)

    with pytest.raises(ValueError, match="Unknown Minecraft"):
        await adapter.update_game_rules(tmp_path, {}, {"minecraft:not_real": True})
    with pytest.raises(ValueError, match="true or false"):
        await adapter.update_game_rules(tmp_path, {}, {"minecraft:pvp": 1})
    with pytest.raises(ValueError, match="at least 0"):
        await adapter.update_game_rules(
            tmp_path, {}, {"minecraft:random_tick_speed": -1}
        )
    assert called is False
