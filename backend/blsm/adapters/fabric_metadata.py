from __future__ import annotations

import io
import json
import zipfile
from pathlib import PurePosixPath
from typing import Any

from .base import ModPackageMetadata

MAX_METADATA_BYTES = 1_000_000
MAX_NESTED_JAR_BYTES = 32 * 1024 * 1024


def inspect_fabric_jar(
    content: bytes, filename: str, *, depth: int = 0
) -> tuple[ModPackageMetadata, ...]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            info = archive.getinfo("fabric.mod.json")
            if info.file_size > MAX_METADATA_BYTES:
                raise ValueError(f"fabric.mod.json is too large in {filename}")
            document = json.loads(archive.read(info))
            documents = document if isinstance(document, list) else [document]
            packages: list[ModPackageMetadata] = []
            nested_paths: list[str] = []
            for item in documents:
                if not isinstance(item, dict):
                    raise ValueError(f"Invalid fabric.mod.json in {filename}")
                package_id = str(item.get("id") or "")
                version = str(item.get("version") or "")
                if not package_id or not version:
                    raise ValueError(f"Incomplete fabric.mod.json in {filename}")
                packages.append(
                    ModPackageMetadata(
                        package_id=package_id,
                        version=version,
                        name=str(item.get("name") or package_id),
                        depends=_dependency_map(item.get("depends")),
                        breaks=_dependency_map(item.get("breaks")),
                        conflicts=_dependency_map(item.get("conflicts")),
                        nested=depth > 0,
                    )
                )
                if depth < 2:
                    for nested in item.get("jars") or []:
                        if isinstance(nested, dict) and isinstance(nested.get("file"), str):
                            nested_paths.append(nested["file"])
            for nested_path in nested_paths:
                pure = PurePosixPath(nested_path)
                if pure.is_absolute() or ".." in pure.parts:
                    raise ValueError(f"Unsafe nested jar path in {filename}")
                nested_info = archive.getinfo(nested_path)
                if nested_info.file_size > MAX_NESTED_JAR_BYTES:
                    raise ValueError(f"Nested jar is too large in {filename}")
                packages.extend(
                    inspect_fabric_jar(
                        archive.read(nested_info), f"{filename}!/{nested_path}", depth=depth + 1
                    )
                )
            return tuple(packages)
    except (KeyError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot inspect fabric.mod.json in {filename}") from exc


def _dependency_map(value: Any) -> dict[str, str | list[str]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str | list[str]] = {}
    for key, requirement in value.items():
        if isinstance(requirement, str) or (
            isinstance(requirement, list)
            and requirement
            and all(isinstance(item, str) for item in requirement)
        ):
            result[str(key)] = requirement
    return result
