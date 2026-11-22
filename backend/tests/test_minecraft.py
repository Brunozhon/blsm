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
