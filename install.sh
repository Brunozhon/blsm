#!/usr/bin/env bash
set -Eeuo pipefail

umask 027

APP_ROOT="${BLSM_INSTALL_DIR:-/opt/blsm}"
DATA_DIR="${BLSM_DATA_DIR:-/var/lib/blsm}"
CONFIG_DIR="${BLSM_CONFIG_DIR:-/etc/blsm}"
SERVICE_NAME="blsm"
SERVICE_USER="blsm"
SERVICE_GROUP="blsm"
HOST="0.0.0.0"
PORT="8080"
REPOSITORY_URL="${BLSM_REPOSITORY_URL:-}"
SOURCE_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
STAGING_DIR=""

log() { printf '[BLSM] %s\n' "$*"; }
warn() { printf '[BLSM] Warning: %s\n' "$*" >&2; }
die() { printf '[BLSM] Error: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: sudo ./install.sh [options]

Options:
  --repo URL       Git repository used by future one-command updates.
  --host ADDRESS   Listen address (default: 0.0.0.0).
  --port PORT      Dashboard port (default: 8080).
  --help           Show this help.

Environment overrides:
  BLSM_INSTALL_DIR, BLSM_DATA_DIR, BLSM_CONFIG_DIR, BLSM_REPOSITORY_URL
EOF
}

cleanup() {
  local status=$?
  if [[ -n "$STAGING_DIR" && -d "$STAGING_DIR" ]]; then
    rm -rf -- "$STAGING_DIR"
  fi
  exit "$status"
}
trap cleanup EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      [[ $# -ge 2 ]] || die '--repo requires a URL'
      REPOSITORY_URL="$2"
      shift 2
      ;;
    --host)
      [[ $# -ge 2 ]] || die '--host requires an address'
      HOST="$2"
      shift 2
      ;;
    --port)
      [[ $# -ge 2 ]] || die '--port requires a number'
      PORT="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *) die "Unknown option: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || die 'Run this script as root (for example: sudo ./install.sh)'
[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1 && PORT <= 65535 )) || die 'Port must be between 1 and 65535'
for managed_path in "$APP_ROOT" "$DATA_DIR" "$CONFIG_DIR"; do
  [[ "$managed_path" == /* && "$managed_path" != "/" ]] \
    || die "Managed directories must be absolute paths other than /: $managed_path"
done
[[ -f "$SOURCE_DIR/pyproject.toml" && -f "$SOURCE_DIR/frontend/package.json" ]] || die 'Run install.sh from a complete BLSM source release'

if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID:-}" == "ubuntu" ]] || die 'This installer currently supports Ubuntu Server only'
else
  die 'Unable to identify this operating system'
fi

if [[ -z "$REPOSITORY_URL" ]] && command -v git >/dev/null 2>&1; then
  REPOSITORY_URL="$(git -C "$SOURCE_DIR" remote get-url origin 2>/dev/null || true)"
fi

install_system_dependencies() {
  log 'Installing system dependencies'
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  apt-get install -y --no-install-recommends \
    ca-certificates curl git gnupg \
    python3 python3-venv rsync util-linux
}

java_major() {
  java -XshowSettings:properties -version 2>&1 \
    | awk -F= '/java[.]specification[.]version/ {gsub(/[[:space:]]/, "", $2); print $2; exit}'
}

ensure_java() {
  local major=0
  if command -v java >/dev/null 2>&1; then
    major="$(java_major)"
  fi
  if [[ "$major" =~ ^[0-9]+$ ]] && (( major >= 21 )); then
    log "Using Java $major"
    return
  fi

  if apt-cache show openjdk-21-jre-headless >/dev/null 2>&1; then
    log 'Installing OpenJDK 21'
    apt-get install -y --no-install-recommends openjdk-21-jre-headless
  else
    log 'Installing Eclipse Temurin Java 21'
    local key_file
    key_file="$(mktemp)"
    curl --fail --silent --show-error --location \
      https://packages.adoptium.net/artifactory/api/gpg/key/public \
      --output "$key_file"
    gpg --batch --yes --dearmor \
      --output /usr/share/keyrings/adoptium.gpg "$key_file"
    rm -f -- "$key_file"
    printf 'deb [signed-by=/usr/share/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb %s main\n' \
      "$VERSION_CODENAME" > /etc/apt/sources.list.d/adoptium.list
    apt-get update
    apt-get install -y --no-install-recommends temurin-21-jre
    if [[ "$(java_major)" -lt 21 ]]; then
      temurin_java="$(update-alternatives --list java | grep 'temurin-21' | head -n 1 || true)"
      [[ -n "$temurin_java" ]] || die 'Java 21 was installed but could not be selected'
      update-alternatives --set java "$temurin_java"
    fi
  fi
  major="$(java_major)"
  [[ "$major" =~ ^[0-9]+$ ]] && (( major >= 21 )) || die 'Java 21 or newer is required'
}

ensure_node() {
  local node_major=0
  if command -v node >/dev/null 2>&1; then
    node_major="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"
  fi
  if [[ "$node_major" =~ ^[0-9]+$ ]] && (( node_major >= 20 )); then
    log "Using Node.js $(node --version)"
    return
  fi

  log 'Installing Node.js 20 build toolchain'
  local key_file
  key_file="$(mktemp)"
  curl --fail --silent --show-error --location \
    https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    --output "$key_file"
  gpg --batch --yes --dearmor \
    --output /usr/share/keyrings/nodesource.gpg "$key_file"
  rm -f -- "$key_file"
  printf 'deb [signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main\n' \
    > /etc/apt/sources.list.d/nodesource.list
  apt-get update
  apt-get install -y --no-install-recommends nodejs
  node_major="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"
  (( node_major >= 20 )) || die 'Node.js 20 or newer could not be installed'
}

create_service_account() {
  if ! getent group "$SERVICE_GROUP" >/dev/null; then
    groupadd --system "$SERVICE_GROUP"
  fi
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --gid "$SERVICE_GROUP" --home-dir "$DATA_DIR" \
      --shell /usr/sbin/nologin "$SERVICE_USER"
  fi
  install -d -m 0755 -o root -g root "$APP_ROOT" "$APP_ROOT/releases"
  install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$DATA_DIR"
  install -d -m 0750 -o root -g "$SERVICE_GROUP" "$CONFIG_DIR"
}

write_configuration() {
  local env_file="$CONFIG_DIR/blsm.env"
  if [[ -f "$env_file" ]]; then
    log "Preserving existing configuration at $env_file"
  else
    cat > "$env_file" <<EOF
BLSM_DATA_DIR=$DATA_DIR
BLSM_HOST=$HOST
BLSM_PORT=$PORT
BLSM_ALLOWED_ORIGINS=
EOF
    chown root:"$SERVICE_GROUP" "$env_file"
    chmod 0640 "$env_file"
  fi

  local release_file="$CONFIG_DIR/release.conf"
  if [[ -n "$REPOSITORY_URL" ]]; then
    printf 'BLSM_REPOSITORY_URL=%q\n' "$REPOSITORY_URL" > "$release_file"
    chown root:"$SERVICE_GROUP" "$release_file"
    chmod 0640 "$release_file"
  elif [[ ! -f "$release_file" ]]; then
    printf 'BLSM_REPOSITORY_URL=\n' > "$release_file"
    chown root:"$SERVICE_GROUP" "$release_file"
    chmod 0640 "$release_file"
    warn 'No Git remote was found. Future updates require: blsm-update --repo <repository-url>'
  fi
}

build_release() {
  local release_dir="$1"
  log 'Installing frontend dependencies and building the dashboard'
  (
    cd "$release_dir/frontend"
    npm ci --no-audit --no-fund
    npm run build
    rm -rf -- node_modules
  )

  log 'Creating the isolated Python environment'
  python3 -m venv "$release_dir/.venv"
  "$release_dir/.venv/bin/python" -m pip install \
    --disable-pip-version-check --upgrade pip wheel
  "$release_dir/.venv/bin/python" -m pip install \
    --disable-pip-version-check "$release_dir"
}

write_systemd_service() {
  cat > "/etc/systemd/system/$SERVICE_NAME.service" <<EOF
[Unit]
Description=Bruno's Little Server Manager
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_GROUP
WorkingDirectory=$APP_ROOT/current
EnvironmentFile=$CONFIG_DIR/blsm.env
ExecStart=$APP_ROOT/current/.venv/bin/python -m blsm
Restart=on-failure
RestartSec=3
KillMode=process
TimeoutStopSec=20
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
EOF
  chmod 0644 "/etc/systemd/system/$SERVICE_NAME.service"
}

activate_release() {
  local release_dir="$1"
  local temporary_link="$APP_ROOT/.current-new"
  rm -f -- "$temporary_link"
  ln -s "$release_dir" "$temporary_link"
  mv -Tf "$temporary_link" "$APP_ROOT/current"
}

health_check() {
  local attempt
  for attempt in {1..30}; do
    if curl --fail --silent --show-error --max-time 2 \
      "http://127.0.0.1:$PORT/api/health" | grep -q '"status":"ok"'; then
      return 0
    fi
    sleep 1
  done
  return 1
}

install_system_dependencies
ensure_java
ensure_node
create_service_account
write_configuration

# A rerun preserves blsm.env, so use its configured port for the health check.
configured_port="$(sed -n 's/^BLSM_PORT=//p' "$CONFIG_DIR/blsm.env" | tail -n 1)"
PORT="${configured_port:-$PORT}"

release_id="$(date -u +%Y%m%dT%H%M%SZ)"
STAGING_DIR="$APP_ROOT/releases/.staging-$release_id-$$"
final_release="$APP_ROOT/releases/$release_id"
previous_release="$(readlink -f "$APP_ROOT/current" 2>/dev/null || true)"

log "Preparing release $release_id"
install -d -m 0755 "$STAGING_DIR"
rsync -a --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '.python-deps/' \
  --exclude '.pytest_cache/' \
  --exclude 'data/' \
  --exclude 'frontend/node_modules/' \
  --exclude 'frontend/dist/' \
  "$SOURCE_DIR/" "$STAGING_DIR/"
source_ref="$(git -C "$SOURCE_DIR" describe --tags --exact-match 2>/dev/null || true)"
if [[ -z "$source_ref" ]]; then
  source_commit="$(git -C "$SOURCE_DIR" rev-parse --short=12 HEAD 2>/dev/null || true)"
  source_ref="${source_commit:+git-$source_commit}"
fi
printf '%s\n' "${source_ref:-$release_id}" > "$STAGING_DIR/.blsm-release"
build_release "$STAGING_DIR"
chown -R root:root "$STAGING_DIR"
mv "$STAGING_DIR" "$final_release"
STAGING_DIR=""

write_systemd_service
activate_release "$final_release"
ln -sfn "$APP_ROOT/current/update.sh" /usr/local/sbin/blsm-update
systemctl daemon-reload
systemctl enable "$SERVICE_NAME.service" >/dev/null

log 'Starting BLSM'
if ! systemctl restart "$SERVICE_NAME.service" || ! health_check; then
  warn 'The new release did not become healthy'
  if [[ -n "$previous_release" && -d "$previous_release" ]]; then
    warn 'Rolling back to the previous application release'
    activate_release "$previous_release"
    systemctl restart "$SERVICE_NAME.service" || true
  else
    systemctl stop "$SERVICE_NAME.service" || true
  fi
  journalctl -u "$SERVICE_NAME.service" --no-pager -n 30 >&2 || true
  die 'Installation failed; see the service log above'
fi

log 'Installation complete'
log "Open http://<this-server>:$PORT and create the administrator account"
log 'Future updates: sudo blsm-update'

