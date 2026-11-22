# Bruno's Little Server Manager

BLSM (pronounced “Blossom”) is a lightweight, LAN-first game-server control panel. The current MVP manages Minecraft Java Edition with Fabric while keeping game-specific behavior behind a reusable adapter interface.

## What works

- Secure one-time administrator setup, scrypt password storage, signed 12-hour sessions, and origin-restricted browser access
- Fabric server installation, EULA gate, configurable memory/version, and start/stop/restart controls
- Detached Minecraft process groups: the game keeps running if the BLSM web process restarts
- Crash detection, PID/memory/CPU/uptime telemetry, disk-backed live console, and RCON command input
- Modrinth search filtered to the server's Minecraft and Fabric versions
- Verified mod install, update, and removal (downloads are checked against Modrinth SHA-512 hashes)
- Capability-driven React UI and a modular `ServerAdapter` backend boundary

Backups, file browsing, game-rule editing, player management, and map integration are represented in the architecture but are not part of this first MVP. Inventory editing is intentionally excluded.

## Requirements

- Ubuntu Server 22.04 or newer
- Root or `sudo` access for installation
- Outbound HTTPS access while installing and updating
- About 250 MB for BLSM itself, plus memory and disk for each game server

The installer supplies Python, Node.js 20, Java 21, and the other required system packages.

## Production installation

Run the installer directly from GitHub:

```bash
curl -fsSL https://raw.githubusercontent.com/Brunozhon/blsm/main/install.sh | sudo bash
```

The script installs Git first and automatically clones `https://github.com/Brunozhon/blsm`. To install from an existing checkout instead, run its local copy:

```bash
git clone https://github.com/Brunozhon/blsm
cd blsm
sudo ./install.sh
```

The script is safe to rerun. It repairs permissions on BLSM-configured APT keyrings, preserves existing configuration and data, creates a new application release, and rolls back the application symlink if the health check fails. It automatically:

- Installs Python, Node.js 20, Java 21, Git, rsync, and supporting packages
- Builds the React dashboard and creates an isolated Python environment
- Creates the locked-down `blsm` service account
- Stores application releases under `/opt/blsm/releases`
- Stores all persistent state, worlds, mods, and SQLite data under `/var/lib/blsm`
- Writes configuration to `/etc/blsm/blsm.env`
- Enables and starts the hardened `blsm.service` systemd unit

Open `http://SERVER_LAN_IP:8080` and create the administrator password. No manual frontend or backend startup is required.

Installer options:

```text
--repo URL       Alternate repository to install and use for updates
--host ADDRESS   Dashboard listen address (default 0.0.0.0)
--port PORT      Dashboard port (default 8080)
```

### Updating

After installation, update BLSM with one command:

```bash
sudo blsm-update
```

The updater finds the newest semantic release tag. If the repository has no release tags, it follows its default branch by commit. It stages and builds the candidate before downtime, records which game servers are online, stops the BLSM web service, gracefully stops all managed servers, and creates a fully offline backup before switching releases. After the updated dashboard passes its health check, the updater starts the same game servers again.

The application symlink changes atomically. BLSM must pass `/api/health` within 30 seconds or the updater switches back to the previous application release and restarts it. Previously running game servers are restarted after either a successful update or an automatic rollback.

Useful update commands:

```bash
# Show the selected release without changing the installation
sudo blsm-update --check

# Install a particular release
sudo blsm-update --version v0.2.0

# Configure or replace the saved repository URL
sudo blsm-update --repo https://github.com/Brunozhon/blsm
```

Backups are stored in `/var/backups/blsm/<UTC timestamp>/` and contain:

- `config/`: the complete `/etc/blsm` configuration
- `data/`: SQLite state, server configurations, worlds, mods, logs, and other server files
- `manifest.txt`: the old and new release identifiers

Unchanged files are hard-linked to the previous snapshot to reduce disk usage. Five snapshots are retained by default; set `BLSM_BACKUP_RETENTION` when invoking the updater to change that number. Updates never replace or delete `/var/lib/blsm`.

To restore data, stop BLSM and all managed game servers first, then use `rsync` from the selected snapshot. Keep the original data directory until the restored dashboard and servers have been verified.

### Service operation

```bash
sudo systemctl status blsm
sudo journalctl -u blsm -f
sudo systemctl restart blsm
```

Minecraft runs in a separate OS session. Restarting the BLSM web service does not stop a running game server. Stop game servers from the dashboard before shutting down the machine.

### Uninstalling

Uninstalling is destructive: it permanently removes BLSM and every server it manages, including all worlds, mods, backups, configuration, users, logs, and SQLite data. Download and run the uninstaller from an interactive terminal:

```bash
curl -fsSL https://raw.githubusercontent.com/Brunozhon/blsm/main/uninstall.sh | sudo bash
```

You must type `UNINSTALL BLSM` exactly before any changes are made. A checkout can run `sudo ./uninstall.sh`; an installed release can run `sudo /opt/blsm/current/uninstall.sh`. The default behavior is always a complete uninstall; there is no separate `--purge` option.

For an installation that used custom locations, pass the same `BLSM_INSTALL_DIR`, `BLSM_DATA_DIR`, `BLSM_CONFIG_DIR`, or `BLSM_BACKUP_DIR` environment overrides through `sudo`. The script displays every target before confirmation, stops managed servers gracefully, and refuses to delete unsafe, overlapping, symbolic-link, or unverified paths. Shared packages such as Java, Node.js, Python, Git, and system journal files are retained because other software may use them.

## Development

For development, run the API and Vite dev server separately:

```bash
# Terminal 1
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
python -m blsm

# Terminal 2
cd frontend
npm install
npm run dev
```

Vite listens on port 5173 and proxies API/WebSocket traffic to port 8080.

## Configuration

Copy `.env.example` into your service environment. BLSM reads:

| Variable | Default | Purpose |
| --- | --- | --- |
| `BLSM_DATA_DIR` | `./data` | SQLite database and isolated server directories; production uses `/var/lib/blsm` |
| `BLSM_HOST` | `0.0.0.0` | Web bind address |
| `BLSM_PORT` | `8080` | Web port |
| `BLSM_ALLOWED_ORIGINS` | localhost Vite origins | Comma-separated development browser origins |
| `BLSM_ADMIN_PASSWORD` | unset | Optional first-run bootstrap password (12+ chars) |
| `BLSM_FRONTEND_DIR` | auto-detected | Built dashboard directory; production configures the active release automatically |

For LAN use, access the production build from FastAPI on port 8080. Do not expose BLSM directly to the public internet. If remote access is needed, use a private VPN such as WireGuard or Tailscale and firewall port 8080 to the trusted interface.

## Verify

```bash
. .venv/bin/activate
pytest
ruff check backend

cd frontend
npm run typecheck
npm run build
```

## Project layout

```text
backend/blsm/adapters/   capability contract and game adapters
backend/blsm/services/   process supervision and Modrinth workflows
backend/blsm/api.py      authenticated HTTP and WebSocket API
frontend/src/            React application and Tailwind styling
data/                    runtime state (ignored by Git)
```

See [AGENTS.md](AGENTS.md) for contribution conventions and priorities.
