# BLSM contributor guide

## Architecture

- `backend/blsm` is a FastAPI application. Core code owns auth, persistence, process supervision, and capability discovery; game-specific behavior lives behind `ServerAdapter` implementations in `adapters/`.
- `frontend` is a React + TypeScript + Vite + Tailwind client. It renders navigation and actions from the server's advertised capabilities, never from adapter names.
- SQLite stores configuration, installed-mod inventory, and runtime metadata only. Game servers run as detached OS process groups and continue if BLSM restarts. While BLSM is their parent it must asynchronously reap exits without tying game lifetime to the web-app lifetime. Console output is tailed from disk; commands use the adapter's remote console protocol.
- Managed backups live outside server directories under `BLSM_DATA_DIR/backups`. Archives stream to partial files and become visible atomically; restores require validated staging, a safety backup, and an interruption-recovery journal. Scheduling stays in core while adapters may coordinate game-specific shutdown warnings through hooks.
- Mod changes use a resolve/review/apply transaction: combine repository metadata with adapter-native package metadata, validate the complete set, require explicit approval, and mutate files only while the game server is offline. Existing versions stay pinned unless the approved plan names their change; failed commits restore both files and database records.
- Runtime state belongs under `BLSM_DATA_DIR` (default `./data`) and must never be committed.
- Production uses immutable releases under `/opt/blsm/releases`, a `current` symlink, root-owned configuration in `/etc/blsm`, and persistent service-owned data in `/var/lib/blsm`.

## Design principles

- Dark, quiet, information-dense UI with neutral grays, restrained blue accents, 16px body text, and accessible focus/contrast.
- Prefer capability-driven UI, progressive disclosure, and explicit destructive confirmations.
- Keep the core adapter-neutral. If a feature exists only for one game, add it to that adapter and advertise its capability.

## Conventions

- Python: typed functions, small services, `pathlib`, stdlib where practical, and explicit error translation at API boundaries.
- TypeScript: strict mode, functional components, shared API types, and no unchecked `any`.
- APIs live under `/api`; WebSockets under `/ws`. Never place secrets in URLs except the short-lived WebSocket session token.
- Validate every filesystem path and external download before use. Do not expose arbitrary shell execution.
- Never leave a spawned process unwatched, and never make a watcher block BLSM shutdown or terminate a healthy game server during web-app restart.
- Serialize backup, restore, and game-process transitions per server. Never follow server-owned symlinks into unrelated files or restore unvalidated archive members.
- Treat repository compatibility as advisory and package-native metadata as authoritative. Never select prereleases ahead of stable versions by default or silently remove/downgrade installed packages.
- Keep `install.sh` and `update.sh` rerunnable, fail-fast, and rollback-safe. Never place worlds or user data inside an application release.

## Priorities

1. Safe lifecycle management and recoverable state.
2. Correct adapter isolation and capability discovery.
3. Reliable live console and low-overhead telemetry.
4. Compatible Modrinth workflows and clear errors.
5. Backups, file management, rules, and player tools; inventory editing is explicitly out of scope.

