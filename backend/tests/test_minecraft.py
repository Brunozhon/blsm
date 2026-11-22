from pathlib import Path
from subprocess import CompletedProcess
from typing import Any

import pytest
from blsm.adapters import minecraft
from blsm.adapters.minecraft import MinecraftFabricAdapter


def test_java_requirements_follow_minecraft_versions() -> None:
    required = MinecraftFabricAdapter.required_java_major

    assert required("26.3") == 25
    assert required("26.1-pre-1") == 25
    assert required("1.21.4") == 21
    assert required("1.20.5") == 21
    assert required("1.20.4") == 17


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
