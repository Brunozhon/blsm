#!/usr/bin/env bash
set -Eeuo pipefail

umask 027

APP_ROOT="${BLSM_INSTALL_DIR:-/opt/blsm}"
DATA_DIR="${BLSM_DATA_DIR:-/var/lib/blsm}"
CONFIG_DIR="${BLSM_CONFIG_DIR:-/etc/blsm}"
DEFAULT_REPOSITORY_URL="https://github.com/Brunozhon/blsm"
SERVICE_NAME="blsm"
SERVICE_USER="blsm"
SERVICE_GROUP="blsm"
HOST="0.0.0.0"
PORT="8080"
REPOSITORY_URL="${BLSM_REPOSITORY_URL:-$DEFAULT_REPOSITORY_URL}"
SCRIPT_PATH="${BASH_SOURCE[0]:-}"
if [[ -n "$SCRIPT_PATH" && -f "$SCRIPT_PATH" ]]; then
  SOURCE_DIR="$(cd -- "$(dirname -- "$SCRIPT_PATH")" && pwd -P)"
else
  SOURCE_DIR="$PWD"
fi
SOURCE_TEMP=""
STAGING_DIR=""

log() { printf '[BLSM] %s\n' "$*"; }
warn() { printf '[BLSM] Warning: %s\n' "$*" >&2; }
die() { printf '[BLSM] Error: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: sudo ./install.sh [options]

Options:
  --repo URL       Repository to install and use for updates.
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
  if [[ -n "$SOURCE_TEMP" && -d "$SOURCE_TEMP" ]]; then
    rm -rf -- "$SOURCE_TEMP"
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
if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  [[ "${ID:-}" == "ubuntu" ]] || die 'This installer currently supports Ubuntu Server only'
else
  die 'Unable to identify this operating system'
fi

install_apt_signing_key() {
  local url="$1"
  local destination="$2"
  local label="$3"
  local temporary_dir staged_key

  [[ ! -L "$destination" ]] || die "Refusing symbolic-link APT keyring: $destination"
  install -d -m 0755 -o root -g root "$(dirname -- "$destination")"
  temporary_dir="$(mktemp -d /tmp/blsm-apt-key.XXXXXX)" \
    || die "Could not create temporary storage for the $label APT key"
  if ! staged_key="$(mktemp "${destination}.new.XXXXXX")"; then
    rm -rf -- "$temporary_dir"
    die "Could not stage the $label APT keyring"
  fi

  if curl --fail --silent --show-error --location \
      "$url" --output "$temporary_dir/key.asc" \
    && gpg --batch --yes --dearmor \
      --output "$temporary_dir/keyring.gpg" "$temporary_dir/key.asc" \
    && gpg --batch --quiet --show-keys \
      "$temporary_dir/keyring.gpg" >/dev/null \
    && install -m 0644 -o root -g root \
      "$temporary_dir/keyring.gpg" "$staged_key" \
    && mv -fT -- "$staged_key" "$destination"; then
    rm -rf -- "$temporary_dir"
    return 0
  fi

  rm -rf -- "$temporary_dir"
  rm -f -- "$staged_key"
  die "Could not install the $label APT signing key"
}

install_apt_source_list() {
  local destination="$1"
  local source_line="$2"
  local staged_source

  [[ ! -L "$destination" ]] || die "Refusing symbolic-link APT source: $destination"
  staged_source="$(mktemp "${destination}.new.XXXXXX")" \
    || die "Could not stage APT source configuration at $destination"
  if printf '%s\n' "$source_line" > "$staged_source" \
    && chown root:root "$staged_source" \
    && chmod 0644 "$staged_source" \
    && mv -fT -- "$staged_source" "$destination"; then
    return 0
  fi
  rm -f -- "$staged_source"
  die "Could not install APT source configuration at $destination"
}

repair_existing_apt_configuration() {
  local path
  local managed_files=(
    /usr/share/keyrings/nodesource.gpg
    /usr/share/keyrings/adoptium.gpg
    /etc/apt/sources.list.d/nodesource.list
    /etc/apt/sources.list.d/adoptium.list
  )
  for path in "${managed_files[@]}"; do
    if [[ -f "$path" && ! -L "$path" ]]; then
      chown root:root "$path"
      chmod 0644 "$path"
    fi
  done

  # A failed earlier run may have left a repository enabled with an unreadable
  # or incomplete keyring. Refresh it before the first apt-get update.
  if command -v curl >/dev/null 2>&1 && command -v gpg >/dev/null 2>&1; then
    if [[ -f /etc/apt/sources.list.d/nodesource.list ]] \
      && grep -Fq 'https://deb.nodesource.com/node_20.x' \
        /etc/apt/sources.list.d/nodesource.list; then
      log 'Repairing the existing NodeSource APT keyring'
      install_apt_signing_key \
        https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
        /usr/share/keyrings/nodesource.gpg NodeSource
    fi
    if [[ -f /etc/apt/sources.list.d/adoptium.list ]] \
      && grep -Fq 'https://packages.adoptium.net/artifactory/deb' \
        /etc/apt/sources.list.d/adoptium.list; then
      log 'Repairing the existing Adoptium APT keyring'
      install_apt_signing_key \
        https://packages.adoptium.net/artifactory/api/gpg/key/public \
        /usr/share/keyrings/adoptium.gpg Adoptium
    fi
  fi
}

install_system_dependencies() {
  log 'Installing system dependencies'
  export DEBIAN_FRONTEND=noninteractive
  repair_existing_apt_configuration
  apt-get update
  apt-get install -y --no-install-recommends \
    ca-certificates curl git gnupg \
    python3 python3-venv rsync util-linux
}

prepare_source() {
  if [[ -f "$SOURCE_DIR/pyproject.toml" && -f "$SOURCE_DIR/frontend/package.json" ]]; then
    log "Using the local BLSM source at $SOURCE_DIR"
    return
  fi

  log "Cloning BLSM from $REPOSITORY_URL"
  SOURCE_TEMP="$(mktemp -d /tmp/blsm-install.XXXXXX)"
  local remote_tags release_ref
  remote_tags="$(git ls-remote --tags --refs -- "$REPOSITORY_URL")"
  release_ref="$(
    printf '%s\n' "$remote_tags" \
      | awk '{sub("refs/tags/", "", $2); print $2}' \
      | { grep -E '^v?[0-9]+([.][0-9]+){1,3}$' || true; } \
      | sort -V \
      | tail -n 1
  )"
  if [[ -n "$release_ref" ]]; then
    log "Installing release $release_ref"
    git clone --depth 1 --branch "$release_ref" -- \
      "$REPOSITORY_URL" "$SOURCE_TEMP/source"
  else
    log 'No versioned release tags found; installing the default branch'
    git clone --depth 1 -- "$REPOSITORY_URL" "$SOURCE_TEMP/source"
  fi
  SOURCE_DIR="$SOURCE_TEMP/source"
  [[ -f "$SOURCE_DIR/pyproject.toml" && -f "$SOURCE_DIR/frontend/package.json" ]] \
    || die 'The cloned repository is not a complete BLSM source release'
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
    local source_line
    install_apt_signing_key \
      https://packages.adoptium.net/artifactory/api/gpg/key/public \
      /usr/share/keyrings/adoptium.gpg Adoptium
    printf -v source_line \
      'deb [signed-by=/usr/share/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb %s main' \
      "$VERSION_CODENAME"
    install_apt_source_list /etc/apt/sources.list.d/adoptium.list "$source_line"
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
  local node_major=0 npm_version="" installed_node_package="" nodesource_version=""
  if command -v node >/dev/null 2>&1; then
    node_major="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"
  fi
  if command -v npm >/dev/null 2>&1; then
    npm_version="$(npm --version 2>/dev/null || true)"
  fi
  if [[ "$node_major" =~ ^[0-9]+$ ]] && (( node_major >= 20 )) \
    && [[ -n "$npm_version" ]]; then
    log "Using Node.js $(node --version) with npm $npm_version"
    return
  fi

  if [[ "$node_major" =~ ^[0-9]+$ ]] && (( node_major >= 20 )); then
    warn "Node.js $(node --version) is installed, but npm is missing or unusable"
    installed_node_package="$(
      dpkg-query -W -f='${Version}' nodejs 2>/dev/null || true
    )"
    if [[ "$installed_node_package" != *nodesource* ]]; then
      log 'Installing the matching Ubuntu npm package'
      if apt-get install -y --no-install-recommends npm; then
        hash -r
        if command -v node >/dev/null 2>&1 && command -v npm >/dev/null 2>&1; then
          node_major="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"
          npm_version="$(npm --version 2>/dev/null || true)"
          if [[ "$node_major" =~ ^[0-9]+$ ]] && (( node_major >= 20 )) \
            && [[ -n "$npm_version" ]]; then
            log "Using Node.js $(node --version) with npm $npm_version"
            return
          fi
        fi
        warn 'Ubuntu installed npm, but the resulting Node.js toolchain is not compatible'
      else
        warn 'Ubuntu could not install a compatible npm package; trying NodeSource'
      fi
    fi
  fi
  log 'Installing Node.js 20 build toolchain'
  install_apt_signing_key \
    https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
    /usr/share/keyrings/nodesource.gpg NodeSource
  install_apt_source_list /etc/apt/sources.list.d/nodesource.list \
    'deb [signed-by=/usr/share/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main'
  apt-get update
  nodesource_version="$(
    apt-cache madison nodejs \
      | awk '$3 ~ /nodesource/ {print $3; exit}'
  )"
  [[ -n "$nodesource_version" ]] \
    || die 'The NodeSource repository did not provide a Node.js 20 package'
  log "Installing NodeSource nodejs $nodesource_version (includes npm)"
  apt-get install -y --reinstall --allow-downgrades --no-install-recommends \
    "nodejs=$nodesource_version"
  hash -r
  command -v node >/dev/null 2>&1 || die 'The Node.js executable could not be installed'
  node_major="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"
  [[ "$node_major" =~ ^[0-9]+$ ]] && (( node_major >= 20 )) \
    || die 'Node.js 20 or newer could not be installed'
  npm_version=""
  if command -v npm >/dev/null 2>&1; then
    npm_version="$(npm --version 2>/dev/null || true)"
  fi
  [[ -n "$npm_version" ]] || die 'npm could not be installed with Node.js'
  log "Using Node.js $(node --version) with npm $npm_version"
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
  command -v npm >/dev/null 2>&1 \
    || die 'npm is required to build BLSM; rerun the installer to repair Node.js'
  npm --version >/dev/null 2>&1 \
    || die 'npm is installed but unusable; rerun the installer to repair Node.js'
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

secure_release_permissions() {
  local release_dir="$1"
  local resolved_release releases_root
  resolved_release="$(readlink -f -- "$release_dir")"
  releases_root="$(readlink -f -- "$APP_ROOT/releases")"
  [[ -n "$resolved_release" && "$resolved_release" == "$releases_root"/* ]] \
    || die "Refusing to change permissions outside $releases_root: $release_dir"
  chown -R root:"$SERVICE_GROUP" "$resolved_release"
  chmod -R u=rwX,g=rX,o= "$resolved_release"
}

verify_release_access() {
  local release_dir="$1"
  runuser -u "$SERVICE_USER" -- test -x "$release_dir" \
    && runuser -u "$SERVICE_USER" -- test -x "$release_dir/.venv/bin/python" \
    && runuser -u "$SERVICE_USER" -- test -r "$release_dir/frontend/dist/index.html" \
    || die "The $SERVICE_USER service account cannot access the built release"
}

health_check() {
  local attempt
  for attempt in {1..30}; do
    if curl --fail --silent --max-time 2 \
      "http://127.0.0.1:$PORT/api/health" | grep -q '"status":"ok"'; then
      return 0
    fi
    if systemctl is-failed --quiet "$SERVICE_NAME.service" 2>/dev/null; then
      return 1
    fi
    if (( attempt % 5 == 0 )); then
      log "Waiting for BLSM to become healthy (${attempt}/30 seconds)"
    fi
    sleep 1
  done
  return 1
}

report_service_failure() {
  printf '\n[BLSM] systemd service status:\n' >&2
  systemctl status "$SERVICE_NAME.service" --no-pager --full >&2 || true
  printf '\n[BLSM] recent service log:\n' >&2
  journalctl -u "$SERVICE_NAME.service" --no-pager --output=short-precise -n 50 >&2 \
    || true
}

install_system_dependencies
prepare_source
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
if [[ -n "$previous_release" && -d "$previous_release" ]]; then
  log 'Repairing access to the existing BLSM release'
  secure_release_permissions "$previous_release"
fi

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
secure_release_permissions "$STAGING_DIR"
verify_release_access "$STAGING_DIR"
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
  report_service_failure
  if [[ -n "$previous_release" && -d "$previous_release" ]]; then
    warn 'Rolling back to the previous application release'
    activate_release "$previous_release"
    systemctl restart "$SERVICE_NAME.service" || true
  else
    systemctl stop "$SERVICE_NAME.service" || true
  fi
  die 'Installation failed; see the service log above'
fi

log 'Installation complete'
log "Open http://<this-server>:$PORT and create the administrator account"
log 'Future updates: sudo blsm-update'

