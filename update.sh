#!/usr/bin/env bash
set -Eeuo pipefail

umask 027

APP_ROOT="${BLSM_INSTALL_DIR:-/opt/blsm}"
DATA_DIR="${BLSM_DATA_DIR:-/var/lib/blsm}"
CONFIG_DIR="${BLSM_CONFIG_DIR:-/etc/blsm}"
BACKUP_ROOT="${BLSM_BACKUP_DIR:-/var/backups/blsm}"
BACKUP_RETENTION="${BLSM_BACKUP_RETENTION:-5}"
SERVICE_NAME="blsm"
SERVICE_USER="blsm"
SERVICE_GROUP="blsm"
REPOSITORY_URL="${BLSM_REPOSITORY_URL:-}"
REQUESTED_VERSION=""
FORCE=0
CHECK_ONLY=0
STAGING_DIR=""
PREVIOUS_RELEASE=""
ACTIVATED=0
SERVICE_STOPPED=0
SUCCESS=0
NEW_RELEASE_DIR=""
SERVER_STATE_FILE=""

log() { printf '[BLSM update] %s\n' "$*"; }
warn() { printf '[BLSM update] Warning: %s\n' "$*" >&2; }
die() { printf '[BLSM update] Error: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: sudo blsm-update [options]

Options:
  --repo URL       Save and use this Git repository for releases.
  --version TAG    Install a specific release tag instead of the latest.
  --check          Report the available release without changing anything.
  --force          Reinstall even when the selected release is current.
  --help           Show this help.

Backups are stored under /var/backups/blsm. Application data remains under
/var/lib/blsm and is never replaced by an update.
EOF
}

activate_release() {
  local release_dir="$1"
  local temporary_link="$APP_ROOT/.current-new"
  rm -f -- "$temporary_link"
  ln -s "$release_dir" "$temporary_link"
  mv -Tf "$temporary_link" "$APP_ROOT/current"
}

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
    && gpg --batch --quiet --show-keys "$temporary_dir/keyring.gpg" >/dev/null \
    && install -m 0644 -o root -g root "$temporary_dir/keyring.gpg" "$staged_key" \
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

java_major() {
  java -XshowSettings:properties -version 2>&1 \
    | awk -F= '/java[.]specification[.]version/ {gsub(/[[:space:]]/, "", $2); print $2; exit}'
}

ensure_java() {
  local major=0 java_25="" os_codename="" source_line=""
  if command -v java >/dev/null 2>&1; then
    major="$(java_major)"
  fi
  if [[ "$major" =~ ^[0-9]+$ ]] && (( major >= 25 )); then
    log "Using Java $major"
    return
  fi

  log 'Upgrading the game-server runtime to Java 25'
  apt-get update
  if apt-cache show openjdk-25-jre-headless >/dev/null 2>&1; then
    apt-get install -y --no-install-recommends openjdk-25-jre-headless
  else
    [[ -r /etc/os-release ]] || die 'Unable to identify this operating system'
    # shellcheck disable=SC1091
    . /etc/os-release
    os_codename="${VERSION_CODENAME:-}"
    [[ -n "$os_codename" ]] || die 'Unable to determine the Ubuntu release codename'
    install_apt_signing_key \
      https://packages.adoptium.net/artifactory/api/gpg/key/public \
      /usr/share/keyrings/adoptium.gpg Adoptium
    printf -v source_line \
      'deb [signed-by=/usr/share/keyrings/adoptium.gpg] https://packages.adoptium.net/artifactory/deb %s main' \
      "$os_codename"
    install_apt_source_list /etc/apt/sources.list.d/adoptium.list "$source_line"
    apt-get update
    apt-get install -y --no-install-recommends temurin-25-jre
  fi

  major="$(java_major)"
  if [[ ! "$major" =~ ^[0-9]+$ ]] || (( major < 25 )); then
    java_25="$(
      update-alternatives --list java \
        | grep -E '/(java|temurin)-25[^/]*/bin/java$' \
        | head -n 1 \
        || true
    )"
    [[ -n "$java_25" ]] || die 'Java 25 was installed but could not be selected'
    update-alternatives --set java "$java_25"
  fi
  major="$(java_major)"
  [[ "$major" =~ ^[0-9]+$ ]] && (( major >= 25 )) \
    || die 'Java 25 or newer is required for current Minecraft releases'
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

resume_stopped_servers() {
  if [[ -z "$SERVER_STATE_FILE" || ! -f "$SERVER_STATE_FILE" ]]; then
    return 0
  fi
  if runuser -u "$SERVICE_USER" -- \
    env BLSM_DATA_DIR="$DATA_DIR" \
    "$APP_ROOT/current/.venv/bin/python" -m blsm.maintenance \
    resume-after-update --state-file "$SERVER_STATE_FILE"; then
    rm -f -- "$SERVER_STATE_FILE"
    SERVER_STATE_FILE=""
    return 0
  fi
  return 1
}

on_exit() {
  local status=$?
  trap - EXIT
  if [[ $SUCCESS -ne 1 ]]; then
    if [[ $ACTIVATED -eq 1 && -n "$PREVIOUS_RELEASE" && -d "$PREVIOUS_RELEASE" ]]; then
      warn 'Update failed; restoring the previous application release'
      systemctl stop "$SERVICE_NAME.service" >/dev/null 2>&1 || true
      activate_release "$PREVIOUS_RELEASE" || true
    fi
    if ! resume_stopped_servers; then
      warn 'Could not restart all previously running game servers automatically'
    fi
    if [[ $SERVICE_STOPPED -eq 1 ]]; then
      systemctl start "$SERVICE_NAME.service" >/dev/null 2>&1 || true
    fi
  fi
  if [[ -n "$STAGING_DIR" && -d "$STAGING_DIR" ]]; then
    rm -rf -- "$STAGING_DIR"
  fi
  if [[ $SUCCESS -ne 1 && -n "$NEW_RELEASE_DIR" && -d "$NEW_RELEASE_DIR" ]]; then
    rm -rf -- "$NEW_RELEASE_DIR"
  fi
  exit "$status"
}
trap on_exit EXIT

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      [[ $# -ge 2 ]] || die '--repo requires a URL'
      REPOSITORY_URL="$2"
      shift 2
      ;;
    --version)
      [[ $# -ge 2 ]] || die '--version requires a tag'
      REQUESTED_VERSION="$2"
      shift 2
      ;;
    --check)
      CHECK_ONLY=1
      shift
      ;;
    --force)
      FORCE=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *) die "Unknown option: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || die 'Run this script as root (for example: sudo blsm-update)'
[[ "$BACKUP_RETENTION" =~ ^[0-9]+$ ]] && (( BACKUP_RETENTION >= 1 )) || die 'BLSM_BACKUP_RETENTION must be at least 1'
for managed_path in "$APP_ROOT" "$DATA_DIR" "$CONFIG_DIR" "$BACKUP_ROOT"; do
  [[ "$managed_path" == /* && "$managed_path" != "/" ]] \
    || die "Managed directories must be absolute paths other than /: $managed_path"
done
[[ -d "$APP_ROOT/releases" && -L "$APP_ROOT/current" ]] || die 'BLSM is not installed with install.sh'

if [[ -f "$CONFIG_DIR/release.conf" ]]; then
  # This file is created root-owned by install.sh.
  # shellcheck disable=SC1090
  . "$CONFIG_DIR/release.conf"
  REPOSITORY_URL="${REPOSITORY_URL:-${BLSM_REPOSITORY_URL:-}}"
fi
[[ -n "$REPOSITORY_URL" ]] || die 'No update repository is configured. Use --repo <repository-url> once.'

if [[ -f "$CONFIG_DIR/blsm.env" ]]; then
  # This file is created root-owned by install.sh.
  # shellcheck disable=SC1090
  . "$CONFIG_DIR/blsm.env"
  DATA_DIR="${BLSM_DATA_DIR:-$DATA_DIR}"
  if ! grep -q '^BLSM_FRONTEND_DIR=' "$CONFIG_DIR/blsm.env"; then
    printf 'BLSM_FRONTEND_DIR=%s/current/frontend/dist\n' "$APP_ROOT" \
      >> "$CONFIG_DIR/blsm.env"
  fi
  chown root:"$SERVICE_GROUP" "$CONFIG_DIR/blsm.env"
  chmod 0640 "$CONFIG_DIR/blsm.env"
fi

exec 9>"$APP_ROOT/update.lock"
flock -n 9 || die 'Another BLSM installation or update is already running'

git ls-remote -- "$REPOSITORY_URL" HEAD >/dev/null \
  || die 'The configured update repository is not reachable'
printf 'BLSM_REPOSITORY_URL=%q\n' "$REPOSITORY_URL" > "$CONFIG_DIR/release.conf"
chown root:"$SERVICE_GROUP" "$CONFIG_DIR/release.conf"
chmod 0640 "$CONFIG_DIR/release.conf"

if [[ -n "$REQUESTED_VERSION" ]]; then
  release_ref="$REQUESTED_VERSION"
  clone_ref="$REQUESTED_VERSION"
else
  log 'Checking the release repository'
  remote_tags="$(git ls-remote --tags --refs -- "$REPOSITORY_URL")"
  release_ref="$(
    printf '%s\n' "$remote_tags" \
      | awk '{sub("refs/tags/", "", $2); print $2}' \
      | { grep -E '^v?[0-9]+([.][0-9]+){1,3}$' || true; } \
      | sort -V \
      | tail -n 1
  )"
  if [[ -z "$release_ref" ]]; then
    head_commit="$(git ls-remote -- "$REPOSITORY_URL" HEAD | awk 'NR == 1 {print $1}')"
    [[ "$head_commit" =~ ^[0-9a-fA-F]{40}$ ]] || die 'Could not resolve the repository default branch'
    release_ref="git-${head_commit:0:12}"
    clone_ref="HEAD"
    warn 'No versioned release tags were found; using the repository default branch'
  else
    clone_ref="$release_ref"
  fi
fi

current_ref="$(cat "$APP_ROOT/current/.blsm-release" 2>/dev/null || true)"
log "Current release: ${current_ref:-unknown}"
log "Selected release: $release_ref"
if [[ $CHECK_ONLY -eq 1 ]]; then
  SUCCESS=1
  exit 0
fi
if [[ "$release_ref" == "$current_ref" && $FORCE -ne 1 ]]; then
  log 'BLSM is already up to date'
  SUCCESS=1
  exit 0
fi

ensure_java

command -v npm >/dev/null 2>&1 \
  || die 'npm is required to update BLSM; rerun the current install.sh to repair Node.js'
npm --version >/dev/null 2>&1 \
  || die 'npm is installed but unusable; rerun the current install.sh to repair Node.js'

release_id="$(date -u +%Y%m%dT%H%M%SZ)-${release_ref//[^0-9A-Za-z._-]/_}"
STAGING_DIR="$APP_ROOT/releases/.staging-$release_id-$$"
final_release="$APP_ROOT/releases/$release_id"
[[ ! -e "$final_release" ]] || die "Release directory already exists: $final_release"

log 'Fetching the selected release'
if [[ "$clone_ref" == "HEAD" ]]; then
  git clone --depth 1 -- "$REPOSITORY_URL" "$STAGING_DIR"
else
  git clone --depth 1 --branch "$clone_ref" -- "$REPOSITORY_URL" "$STAGING_DIR"
fi
[[ -f "$STAGING_DIR/install.sh" && -f "$STAGING_DIR/update.sh" ]] || die 'Fetched source is not a valid BLSM release'
[[ -f "$STAGING_DIR/pyproject.toml" && -f "$STAGING_DIR/frontend/package.json" ]] || die 'Fetched release is incomplete'
rm -rf -- "$STAGING_DIR/.git"
printf '%s\n' "$release_ref" > "$STAGING_DIR/.blsm-release"

log 'Building the dashboard'
(
  cd "$STAGING_DIR/frontend"
  npm ci --no-audit --no-fund
  npm run build
  rm -rf -- node_modules
)

log 'Building the isolated Python environment'
python3 -m venv "$STAGING_DIR/.venv"
"$STAGING_DIR/.venv/bin/python" -m pip install \
  --disable-pip-version-check --upgrade pip wheel
"$STAGING_DIR/.venv/bin/python" -m pip install \
  --disable-pip-version-check "$STAGING_DIR"
secure_release_permissions "$STAGING_DIR"
verify_release_access "$STAGING_DIR"

PREVIOUS_RELEASE="$(readlink -f "$APP_ROOT/current")"
secure_release_permissions "$PREVIOUS_RELEASE"
log 'Stopping the BLSM web service'
systemctl stop "$SERVICE_NAME.service"
SERVICE_STOPPED=1

install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_GROUP" /run/blsm
SERVER_STATE_FILE="/run/blsm/update-backup-$$.json"
install -m 0600 -o "$SERVICE_USER" -g "$SERVICE_GROUP" /dev/null "$SERVER_STATE_FILE"
log 'Gracefully stopping all running game servers'
if ! runuser -u "$SERVICE_USER" -- \
  env BLSM_DATA_DIR="$DATA_DIR" \
  "$APP_ROOT/current/.venv/bin/python" -m blsm.maintenance \
  stop-for-update --state-file "$SERVER_STATE_FILE"; then
  die 'One or more game servers could not be stopped safely'
fi

backup_id="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="$BACKUP_ROOT/$backup_id"
install -d -m 0700 -o root -g root "$BACKUP_ROOT"
while [[ -e "$backup_dir" ]]; do
  sleep 1
  backup_id="$(date -u +%Y%m%dT%H%M%SZ)"
  backup_dir="$BACKUP_ROOT/$backup_id"
done

link_options=()
latest_backup="$(
  find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' \
    | { grep -E '^[0-9]{8}T[0-9]{6}Z$' || true; } \
    | sort \
    | tail -n 1
)"
if [[ -n "$latest_backup" && -d "$BACKUP_ROOT/$latest_backup/data" ]]; then
  link_options=(--link-dest="$BACKUP_ROOT/$latest_backup/data")
fi

log "Backing up configuration and application data to $backup_dir"
install -d -m 0700 -o root -g root "$backup_dir"
rsync -aHAX --numeric-ids "$CONFIG_DIR/" "$backup_dir/config/"
rsync -aHAX --numeric-ids "${link_options[@]}" "$DATA_DIR/" "$backup_dir/data/"
cat > "$backup_dir/manifest.txt" <<EOF
created_utc=$backup_id
previous_release=$current_ref
new_release=$release_ref
data_directory=$DATA_DIR
EOF
chmod 0600 "$backup_dir/manifest.txt"

mv "$STAGING_DIR" "$final_release"
STAGING_DIR=""
NEW_RELEASE_DIR="$final_release"
activate_release "$final_release"
ACTIVATED=1
ln -sfn "$APP_ROOT/current/update.sh" /usr/local/sbin/blsm-update

log 'Starting the updated BLSM service'
systemctl daemon-reload
systemctl start "$SERVICE_NAME.service"

port="$(sed -n 's/^BLSM_PORT=//p' "$CONFIG_DIR/blsm.env" | tail -n 1)"
port="${port:-8080}"
healthy=0
for attempt in {1..30}; do
  if curl --fail --silent --max-time 2 \
      "http://127.0.0.1:$port/api/health" | grep -q '"status":"ok"' \
    && curl --fail --silent --max-time 2 \
      "http://127.0.0.1:$port/" | grep -q '<div id="root"></div>'; then
    healthy=1
    break
  fi
  if systemctl is-failed --quiet "$SERVICE_NAME.service" 2>/dev/null; then
    break
  fi
  if (( attempt % 5 == 0 )); then
    log "Waiting for BLSM to become healthy (${attempt}/30 seconds)"
  fi
  sleep 1
done

if [[ $healthy -ne 1 ]]; then
  printf '\n[BLSM update] systemd service status:\n' >&2
  systemctl status "$SERVICE_NAME.service" --no-pager --full >&2 || true
  printf '\n[BLSM update] recent service log:\n' >&2
  journalctl -u "$SERVICE_NAME.service" --no-pager --output=short-precise -n 50 >&2 \
    || true
  die 'The updated service failed its health check'
fi

log 'Restarting the game servers that were online before the update'
if ! resume_stopped_servers; then
  die 'BLSM was updated, but one or more game servers could not be restarted'
fi

mapfile -t old_backups < <(
  find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' \
    | { grep -E '^[0-9]{8}T[0-9]{6}Z$' || true; } \
    | sort -r \
    | tail -n "+$((BACKUP_RETENTION + 1))"
)
for old_backup in "${old_backups[@]}"; do
  [[ "$old_backup" =~ ^[0-9]{8}T[0-9]{6}Z$ ]] || continue
  rm -rf -- "$BACKUP_ROOT/$old_backup"
done

ACTIVATED=0
SERVICE_STOPPED=0
SUCCESS=1
NEW_RELEASE_DIR=""
log "Updated successfully to $release_ref"
log "Backup created at $backup_dir"

