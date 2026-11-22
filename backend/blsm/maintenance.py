from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from .adapters import MinecraftFabricAdapter
from .config import Settings
from .db import Database
from .services.processes import ProcessManager


def _write_state(path: Path, server_ids: list[str]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps({"server_ids": server_ids}), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def _services() -> tuple[Database, dict[str, Any], ProcessManager]:
    settings = Settings.from_env()
    settings.ensure_directories()
    db = Database(settings.database_path)
    db.initialize()
    adapters = {adapter.key: adapter for adapter in [MinecraftFabricAdapter()]}
    return db, adapters, ProcessManager(db, adapters)


async def stop_for_update(state_file: Path) -> int:
    db, _, processes = _services()
    running = [
        server for server in db.list_servers() if processes.status(server) == "running"
    ]
    _write_state(state_file, [server["id"] for server in running])
    if not running:
        return 0

    results = await asyncio.gather(
        *(processes.stop(server) for server in running), return_exceptions=True
    )
    failed = False
    for server, result in zip(running, results, strict=True):
        if isinstance(result, BaseException):
            failed = True
            print(f"Could not stop {server['name']}: {result}", file=sys.stderr)
    return 1 if failed else 0


async def resume_after_update(state_file: Path) -> int:
    if not state_file.exists():
        return 0
    db, _, processes = _services()
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        server_ids = [str(value) for value in state.get("server_ids", [])]
    except (OSError, ValueError, TypeError) as exc:
        print(f"Could not read update state: {exc}", file=sys.stderr)
        return 1

    servers = [server for server_id in server_ids if (server := db.get_server(server_id))]
    to_start = [server for server in servers if processes.status(server) != "running"]
    results = await asyncio.gather(
        *(processes.start(server) for server in to_start), return_exceptions=True
    )
    failed = False
    for server, result in zip(to_start, results, strict=True):
        if isinstance(result, BaseException):
            failed = True
            print(f"Could not restart {server['name']}: {result}", file=sys.stderr)
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="BLSM update maintenance helper")
    parser.add_argument("command", choices=("stop-for-update", "resume-after-update"))
    parser.add_argument("--state-file", required=True, type=Path)
    args = parser.parse_args()
    operation = (
        stop_for_update if args.command == "stop-for-update" else resume_after_update
    )
    raise SystemExit(asyncio.run(operation(args.state_file)))


if __name__ == "__main__":
    main()

