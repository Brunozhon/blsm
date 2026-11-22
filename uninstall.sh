#!/usr/bin/env bash
set -uo pipefail

umask 077

DEFAULT_APP_ROOT="/opt/blsm"
DEFAULT_DATA_DIR="/var/lib/blsm"
DEFAULT_CONFIG_DIR="/etc/blsm"
DEFAULT_BACKUP_ROOT="/var/backups/blsm"
DEFAULT_LOG_DIR="/var/log/blsm"
DEFAULT_CACHE_DIR="/var/cache/blsm"
DEFAULT_RUNTIME_DIR="/run/blsm"

APP_ROOT="${BLSM_INSTALL_DIR:-$DEFAULT_APP_ROOT}"
DATA_DIR_WAS_SET="${BLSM_DATA_DIR+x}"
DATA_DIR="${BLSM_DATA_DIR:-$DEFAULT_DATA_DIR}"
CONFIG_DIR="${BLSM_CONFIG_DIR:-$DEFAULT_CONFIG_DIR}"
BACKUP_ROOT="${BLSM_BACKUP_DIR:-$DEFAULT_BACKUP_ROOT}"
LOG_DIR="$DEFAULT_LOG_DIR"
CACHE_DIR="$DEFAULT_CACHE_DIR"
RUNTIME_DIR="$DEFAULT_RUNTIME_DIR"

SERVICE_USER="blsm"
SERVICE_GROUP="blsm"
ERRORS=()
REMAINING=()
ACCOUNT_VERIFIED=0

log() { printf '[BLSM uninstall] %s\n' "$*"; }
warn() { printf '[BLSM uninstall] Warning: %s\n' "$*" >&2; }
die() { printf '[BLSM uninstall] Error: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: sudo ./uninstall.sh

Completely removes BLSM, its managed game servers, worlds, mods, backups,
configuration, database, logs, caches, service account, and systemd units.

There is no data-preserving mode and no --purge flag. The script always asks
you to type UNINSTALL BLSM before it makes changes.

Environment overrides for non-default installations:
  BLSM_INSTALL_DIR, BLSM_DATA_DIR, BLSM_CONFIG_DIR, BLSM_BACKUP_DIR
EOF
}

if [[ $# -gt 0 ]]; then
  case "$1" in
    --help|-h)
      usage
      exit 0
      ;;
    *) die "Unknown option: $1" ;;
  esac
fi

[[ $EUID -eq 0 ]] || die 'Run this script with sudo (for example: sudo ./uninstall.sh)'

read_configured_data_dir() {
  local env_file="$CONFIG_DIR/blsm.env"
  local value
  [[ -r "$env_file" ]] || return 1
  value="$(sed -n 's/^BLSM_DATA_DIR=//p' "$env_file" | tail -n 1)"
  value="${value%$'\r'}"
  [[ -n "$value" ]] || return 1
  printf '%s\n' "$value"
}

if [[ -z "$DATA_DIR_WAS_SET" ]]; then
  configured_data_dir="$(read_configured_data_dir || true)"
  if [[ -n "$configured_data_dir" ]]; then
    DATA_DIR="$configured_data_dir"
  fi
fi

canonical_path() {
  realpath -ms -- "$1" 2>/dev/null
}

path_is_safe() {
  local path="$1"
  local relative
  [[ "$path" == /* && "$path" != *$'\n'* && "$path" != *$'\r'* ]] || return 1
  case "$path" in
    /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/media|/mnt|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var|/var/backups|/var/cache|/var/lib|/var/log)
      return 1
      ;;
  esac
  relative="${path#/}"
  [[ "$relative" == */* ]]
}

normalize_target() {
  local variable_name="$1"
  local label="$2"
  local value normalized resolved
  # shellcheck disable=SC2178,SC2034
  local -n target="$variable_name"
  value="$target"
  [[ "$value" == /* ]] || die "$label path must be absolute: $value"
  normalized="$(canonical_path "$value")" || die "Could not resolve $label path: $value"
  path_is_safe "$normalized" || die "Refusing unsafe $label path: $value"
  resolved="$(readlink -m -- "$normalized" 2>/dev/null)" \
    || die "Could not inspect $label path: $value"
  [[ "$resolved" == "$normalized" ]] \
    || die "Refusing $label path containing a symbolic-link component: $value"
  target="$normalized"
}

normalize_target APP_ROOT 'application'
normalize_target DATA_DIR 'data'
normalize_target CONFIG_DIR 'configuration'
normalize_target BACKUP_ROOT 'backup'
normalize_target LOG_DIR 'log'
normalize_target CACHE_DIR 'cache'
normalize_target RUNTIME_DIR 'runtime'

roots=("$APP_ROOT" "$DATA_DIR" "$CONFIG_DIR" "$BACKUP_ROOT" "$LOG_DIR" "$CACHE_DIR")
for ((i = 0; i < ${#roots[@]}; i++)); do
  for ((j = i + 1; j < ${#roots[@]}; j++)); do
    if [[ "${roots[$i]}" == "${roots[$j]}" || "${roots[$i]}" == "${roots[$j]}"/* || "${roots[$j]}" == "${roots[$i]}"/* ]]; then
      die "Refusing overlapping managed paths: ${roots[$i]} and ${roots[$j]}"
    fi
  done
done

cat <<EOF

Bruno's Little Server Manager will be completely uninstalled.

THE FOLLOWING WILL BE PERMANENTLY DELETED:
  Application releases:       $APP_ROOT
  Configuration:              $CONFIG_DIR
  Database and managed data:  $DATA_DIR
  Update backups:             $BACKUP_ROOT
  BLSM logs:                  $LOG_DIR
  BLSM caches:                $CACHE_DIR
  Runtime state:              $RUNTIME_DIR
  Systemd units/drop-ins:     /etc/systemd/system/blsm.service
                              /etc/systemd/system/blsm.service.d
                              /etc/systemd/system/blsm-update.service
                              /etc/systemd/system/blsm-update.service.d
                              /etc/systemd/system/blsm-update-helper.service
                              /etc/systemd/system/blsm-update-helper.service.d
                              /etc/systemd/system/blsm-update.timer
                              /etc/systemd/system/blsm-update.timer.d
                              /etc/systemd/system/blsm-update.path
                              /etc/systemd/system/blsm-update.path.d
  Enabled-unit links:         /etc/systemd/system/multi-user.target.wants/blsm.service
                              /etc/systemd/system/multi-user.target.wants/blsm-update.service
                              /etc/systemd/system/multi-user.target.wants/blsm-update-helper.service
                              /etc/systemd/system/multi-user.target.wants/blsm-update.path
                              /etc/systemd/system/paths.target.wants/blsm-update.path
                              /etc/systemd/system/timers.target.wants/blsm-update.timer
  Command helpers:            /usr/local/sbin/blsm-update
                              /usr/local/sbin/blsm-uninstall
  Privilege configuration:   /etc/sudoers.d/blsm
                              /etc/polkit-1/rules.d/50-blsm.rules
  Service account and group:  $SERVICE_USER:$SERVICE_GROUP

WARNING: ALL BLSM-MANAGED MINECRAFT SERVERS, WORLDS, MODS, BACKUPS,
CONFIGURATION, USERS, AND APPLICATION DATA WILL BE PERMANENTLY DELETED.
THIS CANNOT BE UNDONE.
EOF

[[ -r /dev/tty && -w /dev/tty ]] \
  || die 'An interactive terminal is required for confirmation'
printf '\nType UNINSTALL BLSM to continue: ' > /dev/tty
IFS= read -r confirmation < /dev/tty || die 'Could not read confirmation'
printf '\n' > /dev/tty
[[ "$confirmation" == 'UNINSTALL BLSM' ]] || die 'Confirmation did not match; nothing was changed'

record_error() {
  ERRORS+=("$1")
  warn "$1"
}

record_remaining() {
  REMAINING+=("$1")
  warn "Not removed: $1"
}

account_is_dedicated() {
  local entry name password uid gid gecos home shell primary_group uid_min
  entry="$(getent passwd "$SERVICE_USER" 2>/dev/null)" || return 1
  IFS=: read -r name password uid gid gecos home shell <<< "$entry"
  primary_group="$(getent group "$gid" 2>/dev/null | cut -d: -f1)"
  uid_min="$(awk '$1 == "UID_MIN" {print $2; exit}' /etc/login.defs 2>/dev/null)"
  uid_min="${uid_min:-1000}"
  [[ "$name" == "$SERVICE_USER" && "$primary_group" == "$SERVICE_GROUP" ]] || return 1
  [[ "$(canonical_path "$home")" == "$DATA_DIR" ]] || return 1
  [[ "$shell" == /usr/sbin/nologin || "$shell" == /sbin/nologin || "$shell" == /bin/false ]] || return 1
  [[ "$uid" =~ ^[0-9]+$ && "$uid_min" =~ ^[0-9]+$ && "$uid" -lt "$uid_min" ]]
}

if account_is_dedicated; then
  ACCOUNT_VERIFIED=1
elif id "$SERVICE_USER" >/dev/null 2>&1; then
  warn "The $SERVICE_USER account does not match BLSM's dedicated service-account profile"
fi

if [[ -d "$APP_ROOT" && ! -L "$APP_ROOT" ]]; then
  exec 9>"$APP_ROOT/update.lock" || die "Could not open the BLSM update lock"
  flock -n 9 || die 'An installation or update is currently running; try again later'
fi

stop_systemd_units() {
  local unit
  local units=(
    blsm-update.timer
    blsm-update.path
    blsm-update-helper.service
    blsm-update.service
    blsm.service
  )
  command -v systemctl >/dev/null 2>&1 || return 0
  for unit in "${units[@]}"; do
    systemctl stop "$unit" >/dev/null 2>&1 || true
    systemctl disable "$unit" >/dev/null 2>&1 || true
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
      record_error "Could not stop $unit"
    fi
  done
}

service_user_pids() {
  id "$SERVICE_USER" >/dev/null 2>&1 || return 0
  ps -u "$SERVICE_USER" -o pid= 2>/dev/null | awk '$1 ~ /^[0-9]+$/ {print $1}'
}

managed_data_pids() {
  local process_dir process_cwd
  for process_dir in /proc/[0-9]*; do
    [[ -d "$process_dir" ]] || continue
    process_cwd="$(readlink -- "$process_dir/cwd" 2>/dev/null || true)"
    if [[ "$process_cwd" == "$DATA_DIR" || "$process_cwd" == "$DATA_DIR"/* ]]; then
      printf '%s\n' "${process_dir##*/}"
    fi
  done
}

remaining_managed_pids() {
  if [[ $ACCOUNT_VERIFIED -eq 1 ]]; then
    { service_user_pids; managed_data_pids; } | sort -un
  else
    managed_data_pids | sort -un
  fi
}

stop_managed_servers() {
  local state_file maintenance_python maintenance_failed=0 attempt
  local -a pids=()

  maintenance_python="$APP_ROOT/current/.venv/bin/python"
  if [[ -x "$maintenance_python" && -f "$DATA_DIR/blsm.db" && $ACCOUNT_VERIFIED -eq 1 ]]; then
    if ! install -d -m 0750 -o "$SERVICE_USER" -g "$SERVICE_GROUP" "$RUNTIME_DIR"; then
      record_error "Could not prepare $RUNTIME_DIR for graceful shutdown"
      return 1
    fi
    state_file="$(mktemp "$RUNTIME_DIR/uninstall.XXXXXX.json")" || {
      record_error 'Could not create the graceful-shutdown state file'
      return 1
    }
    if ! chown "$SERVICE_USER:$SERVICE_GROUP" "$state_file" || ! chmod 0600 "$state_file"; then
      rm -f -- "$state_file"
      record_error 'Could not secure the graceful-shutdown state file'
      return 1
    fi
    log 'Gracefully stopping managed game servers'
    if ! runuser -u "$SERVICE_USER" -- \
      env BLSM_DATA_DIR="$DATA_DIR" \
      "$maintenance_python" -m blsm.maintenance \
      stop-for-update --state-file "$state_file"; then
      maintenance_failed=1
      warn 'The adapter-aware game-server shutdown reported an error'
    fi
    rm -f -- "$state_file"
  fi

  mapfile -t pids < <(remaining_managed_pids)
  if [[ ${#pids[@]} -eq 0 ]]; then
    return 0
  fi

  warn "${#pids[@]} verified BLSM process(es) remain; sending SIGTERM"
  kill -TERM "${pids[@]}" 2>/dev/null || true
  for ((attempt = 0; attempt < 30; attempt++)); do
    mapfile -t pids < <(remaining_managed_pids)
    [[ ${#pids[@]} -eq 0 ]] && break
    sleep 1
  done
  mapfile -t pids < <(remaining_managed_pids)
  if [[ ${#pids[@]} -gt 0 ]]; then
    warn "${#pids[@]} process(es) did not stop gracefully; forcing them to exit"
    kill -KILL "${pids[@]}" 2>/dev/null || true
    sleep 1
    mapfile -t pids < <(remaining_managed_pids)
  fi
  if [[ ${#pids[@]} -gt 0 ]]; then
    record_error 'Managed processes are still running; no data was deleted'
    return 1
  fi
  if [[ $maintenance_failed -eq 1 ]]; then
    warn 'All remaining service-account processes were stopped by the bounded fallback'
  fi
  return 0
}

stop_systemd_units
if [[ ${#ERRORS[@]} -gt 0 ]]; then
  die 'One or more BLSM services are still active; no files were deleted'
fi
if ! stop_managed_servers; then
  printf '\n[BLSM uninstall] Uninstall stopped before deleting files.\n' >&2
  exit 1
fi

unit_file_is_blsm() {
  local path="$1"
  [[ -f "$path" ]] || return 1
  grep -Eq "Bruno's Little Server Manager|BLSM|/opt/blsm|/etc/blsm|blsm-update" "$path"
}

remove_unit_file() {
  local path="$1"
  [[ -e "$path" || -L "$path" ]] || return 0
  if unit_file_is_blsm "$path"; then
    rm -f -- "$path" || record_error "Could not remove $path"
  else
    record_remaining "$path (contents could not be verified as BLSM)"
  fi
}

helper_is_blsm() {
  local path="$1"
  local target
  [[ -e "$path" || -L "$path" ]] || return 1
  if [[ -L "$path" ]]; then
    target="$(readlink -- "$path")"
    [[ "$target" == "$APP_ROOT"/* || "$target" == /opt/blsm/* ]]
    return
  fi
  grep -Eq '\[BLSM update\]|Bruno.s Little Server Manager' "$path" 2>/dev/null
}

remove_helper() {
  local path="$1"
  [[ -e "$path" || -L "$path" ]] || return 0
  if helper_is_blsm "$path"; then
    rm -f -- "$path" || record_error "Could not remove $path"
  else
    record_remaining "$path (target or contents could not be verified as BLSM)"
  fi
}

for unit_path in \
  /etc/systemd/system/blsm.service \
  /etc/systemd/system/blsm-update.service \
  /etc/systemd/system/blsm-update-helper.service \
  /etc/systemd/system/blsm-update.timer \
  /etc/systemd/system/blsm-update.path; do
  remove_unit_file "$unit_path"
done

remove_unit_enablement_link() {
  local path="$1"
  local expected_unit="$2"
  local target
  [[ -e "$path" || -L "$path" ]] || return 0
  if [[ -L "$path" ]]; then
    target="$(readlink -- "$path")"
    if [[ "${target##*/}" == "$expected_unit" ]]; then
      rm -f -- "$path" || record_error "Could not remove $path"
      return 0
    fi
  fi
  record_remaining "$path (systemd enablement link could not be verified as BLSM)"
}

remove_unit_enablement_link \
  /etc/systemd/system/multi-user.target.wants/blsm.service blsm.service
remove_unit_enablement_link \
  /etc/systemd/system/multi-user.target.wants/blsm-update.service blsm-update.service
remove_unit_enablement_link \
  /etc/systemd/system/multi-user.target.wants/blsm-update-helper.service blsm-update-helper.service
remove_unit_enablement_link \
  /etc/systemd/system/multi-user.target.wants/blsm-update.path blsm-update.path
remove_unit_enablement_link \
  /etc/systemd/system/paths.target.wants/blsm-update.path blsm-update.path
remove_unit_enablement_link \
  /etc/systemd/system/timers.target.wants/blsm-update.timer blsm-update.timer

remove_unit_dropin() {
  local path="$1"
  [[ -e "$path" || -L "$path" ]] || return 0
  if [[ -L "$path" ]]; then
    record_remaining "$path (BLSM systemd drop-in path is a symbolic link)"
    return 0
  fi
  rm -rf --one-file-system -- "$path" || record_error "Could not remove $path"
  [[ ! -e "$path" ]] || record_remaining "$path"
}

for dropin_path in \
  /etc/systemd/system/blsm.service.d \
  /etc/systemd/system/blsm-update.service.d \
  /etc/systemd/system/blsm-update-helper.service.d \
  /etc/systemd/system/blsm-update.timer.d \
  /etc/systemd/system/blsm-update.path.d; do
  remove_unit_dropin "$dropin_path"
done
remove_helper /usr/local/sbin/blsm-update
remove_helper /usr/local/sbin/blsm-uninstall

remove_blsm_system_file() {
  local path="$1"
  [[ -e "$path" || -L "$path" ]] || return 0
  if [[ -f "$path" ]] && grep -Eqi 'blsm|Bruno.s Little Server Manager' "$path"; then
    rm -f -- "$path" || record_error "Could not remove $path"
  else
    record_remaining "$path (contents could not be verified as BLSM)"
  fi
}

remove_blsm_system_file /etc/sudoers.d/blsm
remove_blsm_system_file /etc/polkit-1/rules.d/50-blsm.rules

directory_is_blsm() {
  local path="$1"
  local kind="$2"
  local name lower_name header
  case "$kind:$path" in
    app:$DEFAULT_APP_ROOT|data:$DEFAULT_DATA_DIR|config:$DEFAULT_CONFIG_DIR|backup:$DEFAULT_BACKUP_ROOT|log:$DEFAULT_LOG_DIR|cache:$DEFAULT_CACHE_DIR|runtime:$DEFAULT_RUNTIME_DIR)
      return 0
      ;;
  esac
  [[ -f "$path/.blsm-managed" ]] && grep -qx 'BLSM_MANAGED_DIRECTORY' "$path/.blsm-managed" && return 0
  name="${path##*/}"
  lower_name="${name,,}"
  [[ "$lower_name" == *blsm* ]] || return 1
  case "$kind" in
    app)
      [[ -d "$path/releases" && ( -L "$path/current" || -f "$path/current/install.sh" ) ]]
      ;;
    data)
      [[ -f "$path/blsm.db" && -d "$path/servers" ]] || return 1
      header="$(head -c 15 "$path/blsm.db" 2>/dev/null)"
      [[ "$header" == 'SQLite format 3' ]]
      ;;
    config)
      [[ -f "$path/blsm.env" || -f "$path/release.conf" ]]
      ;;
    backup)
      find "$path" -mindepth 2 -maxdepth 2 -type f -name manifest.txt -print -quit 2>/dev/null | grep -q .
      ;;
    log|cache|runtime)
      [[ "$lower_name" == blsm || "$lower_name" == blsm-* || "$lower_name" == *_blsm ]]
      ;;
    *)
      return 1
      ;;
  esac
}

remove_managed_directory() {
  local path="$1"
  local kind="$2"
  [[ -e "$path" || -L "$path" ]] || return 0
  if [[ -L "$path" ]]; then
    record_remaining "$path (managed root is a symbolic link)"
    return 0
  fi
  if ! directory_is_blsm "$path" "$kind"; then
    record_remaining "$path (could not verify it as an exclusively BLSM-managed directory)"
    return 0
  fi
  log "Removing $path"
  rm -rf --one-file-system -- "$path" || record_error "Could not completely remove $path"
  [[ ! -e "$path" && ! -L "$path" ]] || record_remaining "$path"
}

remove_managed_directory "$RUNTIME_DIR" runtime
remove_managed_directory "$LOG_DIR" log
remove_managed_directory "$CACHE_DIR" cache
remove_managed_directory "$BACKUP_ROOT" backup
remove_managed_directory "$DATA_DIR" data
remove_managed_directory "$CONFIG_DIR" config
remove_managed_directory "$APP_ROOT" app

if [[ $ACCOUNT_VERIFIED -eq 1 ]] && id "$SERVICE_USER" >/dev/null 2>&1; then
  if ! userdel "$SERVICE_USER"; then
    record_error "Could not delete service account $SERVICE_USER"
  fi
elif id "$SERVICE_USER" >/dev/null 2>&1; then
  record_remaining "service account $SERVICE_USER (account profile was not verified)"
fi

if getent group "$SERVICE_GROUP" >/dev/null 2>&1; then
  group_entry="$(getent group "$SERVICE_GROUP")"
  group_gid="$(cut -d: -f3 <<< "$group_entry")"
  group_members="$(cut -d: -f4 <<< "$group_entry")"
  gid_min="$(awk '$1 == "GID_MIN" {print $2; exit}' /etc/login.defs 2>/dev/null)"
  gid_min="${gid_min:-1000}"
  if [[ "$group_gid" =~ ^[0-9]+$ && "$gid_min" =~ ^[0-9]+$ && "$group_gid" -lt "$gid_min" && -z "$group_members" ]]; then
    groupdel "$SERVICE_GROUP" || record_error "Could not delete service group $SERVICE_GROUP"
  else
    record_remaining "service group $SERVICE_GROUP (group profile or membership was not verified)"
  fi
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || record_error 'systemd daemon-reload failed'
  systemctl reset-failed blsm.service blsm-update.service blsm-update-helper.service \
    >/dev/null 2>&1 || true
fi

printf '\n'
if [[ ${#ERRORS[@]} -eq 0 && ${#REMAINING[@]} -eq 0 ]]; then
  log 'BLSM and all of its managed data were removed successfully'
  log 'Shared packages and repositories were left installed because other software may use them'
  exit 0
fi

warn 'Uninstall completed with items requiring attention'
if [[ ${#REMAINING[@]} -gt 0 ]]; then
  printf 'Remaining items:\n' >&2
  printf '  - %s\n' "${REMAINING[@]}" >&2
fi
if [[ ${#ERRORS[@]} -gt 0 ]]; then
  printf 'Errors:\n' >&2
  printf '  - %s\n' "${ERRORS[@]}" >&2
fi
exit 1
