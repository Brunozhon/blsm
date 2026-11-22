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
