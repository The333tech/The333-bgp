#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${THE333_PROJECT_DIR:-/opt/the333-bgp}"
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.portal.yml)
VERSION_FILE="${PROJECT_DIR}/VERSION"
BACKUP_DIR="${PROJECT_DIR}/backups"
# Resolve the manifest from the current on-disk .env on every invocation.
# A long-running host-updater may otherwise leak a stale URL into child runs.
MANIFEST_URL=""
CHANNEL="stable"
TARGET_VERSION=""
NON_INTERACTIVE="false"
DRY_RUN="false"
DOCKER_CMD=(docker)
DOCKER_ACCESS_READY="false"
LAST_BACKUP_ARCHIVE=""
HOST_UPDATER_SERVICE="the333-bgp-updater.service"
DEFAULT_MIN_UPDATE_FREE_BYTES=2147483648

log() {
  printf '[the333bgp] %s\n' "$*" >&2
}

fail() {
  printf '[the333bgp] ERROR: %s\n' "$*" >&2
  exit 1
}

require_tty() {
  [[ -r /dev/tty ]] || fail "interactive terminal is unavailable; use the documented non-interactive options"
}

usage() {
  cat <<'USAGE'
Usage:
  scripts/the333bgp.sh status
  scripts/the333bgp.sh doctor
  scripts/the333bgp.sh set-password
  scripts/the333bgp.sh tls-enable CERT_FILE KEY_FILE
  scripts/the333bgp.sh tls-disable
  scripts/the333bgp.sh install-updater-service
  scripts/the333bgp.sh backup
  scripts/the333bgp.sh check-update [--manifest URL] [--channel stable|beta]
  scripts/the333bgp.sh update [--manifest URL] [--channel stable|beta] [--version VERSION] [--non-interactive] [--dry-run]

Environment:
  THE333_PROJECT_DIR=/opt/the333-bgp
  PRODUCT_UPDATE_MANIFEST_URL=https://api.github.com/repos/The333tech/The333-bgp/releases?per_page=20
USAGE
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "command not found: $1"
}

configure_docker_access() {
  [[ "${DOCKER_ACCESS_READY}" == "true" ]] && return 0
  need_cmd docker

  if docker info >/dev/null 2>&1; then
    DOCKER_CMD=(docker)
  elif [[ "${EUID}" -ne 0 ]] && command -v sudo >/dev/null 2>&1; then
    if [[ "${NON_INTERACTIVE}" == "true" ]]; then
      sudo -n docker info >/dev/null 2>&1 || fail "Docker daemon access requires an interactive sudo session"
    else
      sudo docker info >/dev/null || fail "Docker daemon is unavailable through sudo"
    fi
    DOCKER_CMD=(sudo docker)
    log "Docker daemon доступен через sudo."
  else
    fail "Docker daemon is unavailable for the current user"
  fi

  DOCKER_ACCESS_READY="true"
}

docker_cli() {
  configure_docker_access
  "${DOCKER_CMD[@]}" "$@"
}

root_cmd() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
    return
  fi

  need_cmd sudo || fail "sudo is required for host service management"
  if [[ "${NON_INTERACTIVE}" == "true" ]]; then
    sudo -n "$@"
  else
    sudo "$@"
  fi
}

load_env() {
  if [[ -f "${PROJECT_DIR}/.env" ]]; then
    local restore_nounset="false"
    case "$-" in
      *u*)
        restore_nounset="true"
        set +u
        ;;
    esac
    set -a
    # shellcheck disable=SC1091
    source "${PROJECT_DIR}/.env"
    set +a
    if [[ "${restore_nounset}" == "true" ]]; then
      set -u
    fi
    MANIFEST_URL="${MANIFEST_URL:-${PRODUCT_UPDATE_MANIFEST_URL:-}}"
  fi
}

configure_compose_files() {
  COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.portal.yml)
  case "${PORTAL_TLS_ENABLED:-false}" in
    1|true|TRUE|yes|YES|on|ON)
      COMPOSE_FILES+=(-f docker-compose.tls.yml)
      ;;
  esac
}

compose() {
  docker_cli compose "${COMPOSE_FILES[@]}" "$@"
}

current_version() {
  if [[ -f "${VERSION_FILE}" ]]; then
    tr -d '[:space:]' < "${VERSION_FILE}"
  else
    printf '0.0.0'
  fi
}

check_update_disk_space() {
  local disk_kb min_bytes min_kb
  disk_kb="$(df -Pk "${PROJECT_DIR}" 2>/dev/null | awk 'NR==2 {print $4}' || true)"
  if [[ -z "${disk_kb}" || ! "${disk_kb}" =~ ^[0-9]+$ ]]; then
    fail "cannot determine free disk space for ${PROJECT_DIR}"
  fi

  min_bytes="${UPDATE_MIN_FREE_BYTES:-${DEFAULT_MIN_UPDATE_FREE_BYTES}}"
  [[ "${min_bytes}" =~ ^[0-9]+$ ]] || fail "UPDATE_MIN_FREE_BYTES must be a positive integer"
  (( min_bytes > 0 )) || fail "UPDATE_MIN_FREE_BYTES must be greater than zero"
  min_kb=$(((min_bytes + 1023) / 1024))

  if (( disk_kb < min_kb )); then
    fail "not enough free disk space for a safe update: $((disk_kb / 1024)) MB available, at least $(((min_kb + 1023) / 1024)) MB required. Free disk space and retry; existing containers were not changed"
  fi

  log "Disk preflight passed: $((disk_kb / 1024)) MB available."
}

create_update_backup_archive() {
  local archive="$1"
  tar \
    --exclude='./backups' \
    --exclude='./portal/node_modules' \
    --exclude='./portal/dist' \
    -czf "${archive}" \
    -C "${PROJECT_DIR}" \
    .env VERSION CHANGELOG.md README.md docker-compose.yml docker-compose.portal.yml docker-compose.tls.yml \
    requirements.in requirements.txt app portal config data docs docker deploy extras scripts install.sh LICENSE SECURITY.md \
    update-manifest.json update-manifest.example.json .env.example .dockerignore .gitattributes .gitignore
}

verify_update_backup_archive() {
  tar -tzf "$1" >/dev/null
}

wait_backend_after_backup() {
  local status
  for _ in $(seq 1 60); do
    status="$(docker_cli inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' the333-bgp-backend 2>/dev/null || true)"
    [[ "${status}" == "healthy" || "${status}" == "running" ]] && return 0
    [[ "${status}" == "unhealthy" || "${status}" == "exited" || "${status}" == "dead" ]] && return 1
    sleep 2
  done
  return 1
}

resume_backend_after_interrupted_backup() {
  local signal="$1"
  trap - INT TERM
  set +e
  if [[ "${backend_was_running:-false}" == "true" ]]; then
    log "Backup interrupted by ${signal}; starting backend before exit."
    docker_cli start the333-bgp-backend >/dev/null 2>&1 || true
  fi
  [[ -n "${archive:-}" ]] && rm -f "${archive}"
  [[ "${signal}" == "INT" ]] && exit 130
  exit 143
}

make_backup() {
  mkdir -p "${BACKUP_DIR}"
  local ts archive backend_was_running backup_rc restart_rc
  ts="$(date +%Y%m%d-%H%M%S)"
  archive="${BACKUP_DIR}/the333-bgp-before-update-${ts}.tar.gz"
  LAST_BACKUP_ARCHIVE="${archive}"
  backend_was_running="false"
  backup_rc=0
  restart_rc=0

  if docker_cli container inspect the333-bgp-backend >/dev/null 2>&1 \
    && [[ "$(docker_cli inspect -f '{{.State.Running}}' the333-bgp-backend 2>/dev/null || true)" == "true" ]]; then
    log "Pausing backend briefly to create a consistent update backup; GoBGP remains online."
    docker_cli stop --time 30 the333-bgp-backend >/dev/null \
      || fail "backend could not be paused for a consistent update backup"
    backend_was_running="true"
    trap 'resume_backend_after_interrupted_backup INT' INT
    trap 'resume_backend_after_interrupted_backup TERM' TERM
  fi

  log "Creating backup: ${archive}"
  if create_update_backup_archive "${archive}"; then
    backup_rc=0
  else
    backup_rc=$?
  fi

  if [[ "${backend_was_running}" == "true" ]]; then
    log "Starting backend after the backup snapshot."
    if ! docker_cli start the333-bgp-backend >/dev/null || ! wait_backend_after_backup; then
      restart_rc=1
    fi
  fi
  trap - INT TERM

  if (( restart_rc != 0 )); then
    rm -f "${archive}"
    fail "backend did not recover after the backup snapshot; GoBGP was not restarted"
  fi
  if (( backup_rc != 0 )); then
    rm -f "${archive}"
    fail "consistent update backup failed; backend was restored and the update was not started"
  fi

  verify_update_backup_archive "${archive}" || {
    rm -f "${archive}"
    fail "backup integrity check failed: ${archive}"
  }

  log "Backup ready: ${archive}"
}

runtime_host() {
  local host="${THE333_BIND_IP:-${ROUTER_ID:-${BGP_NEXTHOP:-127.0.0.1}}}"
  [[ "${host}" == "0.0.0.0" ]] && host="127.0.0.1"
  printf '%s\n' "${host}"
}

runtime_is_ready() {
  need_cmd curl
  need_cmd python3
  local host tmp status rc
  host="$(runtime_host)"
  tmp="$(mktemp)"
  [[ -n "${HOST_UPDATER_TOKEN:-}" ]] || return 1
  status="$(curl -sS -o "${tmp}" -w '%{http_code}' \
    -H "x-the333-updater-token: ${HOST_UPDATER_TOKEN}" \
    "http://${host}:8088/internal/ready" 2>/dev/null || true)"
  if [[ "${status}" != "200" ]]; then
    rm -f "${tmp}"
    return 1
  fi
  python3 - "${tmp}" >/dev/null 2>&1 <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = json.load(fh)
except Exception:
    raise SystemExit(1)

raise SystemExit(0 if data.get("ready") is True else 1)
PY
  rc=$?
  rm -f "${tmp}"
  [[ "${rc}" == "0" ]] || return 1
  case "${PORTAL_TLS_ENABLED:-false}" in
    1|true|TRUE|yes|YES|on|ON)
      curl -kfsS -o /dev/null "https://${host}:8090/" 2>/dev/null
      ;;
    *)
      curl -fsS -o /dev/null "http://${host}:8090/" 2>/dev/null
      ;;
  esac
}

legacy_runtime_is_ready() {
  need_cmd curl
  need_cmd python3
  local host tmp status rc
  host="$(runtime_host)"
  tmp="$(mktemp)"
  status="$(curl -sS -o "${tmp}" -w '%{http_code}' "http://${host}:8088/health" 2>/dev/null || true)"
  if [[ "${status}" != "200" ]]; then
    rm -f "${tmp}"
    return 1
  fi
  python3 - "${tmp}" >/dev/null 2>&1 <<'PY'
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = json.load(fh)
except Exception:
    raise SystemExit(1)

raise SystemExit(0 if data.get("ok") and data.get("gobgp_ready") else 1)
PY
  rc=$?
  rm -f "${tmp}"
  [[ "${rc}" == "0" ]] || return 1
  case "${PORTAL_TLS_ENABLED:-false}" in
    1|true|TRUE|yes|YES|on|ON)
      curl -kfsS -o /dev/null "https://${host}:8090/" 2>/dev/null
      ;;
    *)
      curl -fsS -o /dev/null "http://${host}:8090/" 2>/dev/null
      ;;
  esac
}

wait_for_services() {
  local mode="${1:-strict}"
  local attempts=72
  log "Ожидание готовности backend, GoBGP и портала (до 6 минут)..."

  while (( attempts > 0 )); do
    if [[ "${mode}" == "legacy" ]] && legacy_runtime_is_ready; then
      log "Предыдущая версия восстановлена и прошла совместимую health-проверку."
      return 0
    fi
    if [[ "${mode}" != "legacy" ]] && runtime_is_ready; then
      log "Все сервисы готовы."
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 5
  done

  compose ps || true
  return 1
}

status() {
  cd "${PROJECT_DIR}"
  log "Version: $(current_version)"
  compose ps

  if ! systemctl is-active --quiet "${HOST_UPDATER_SERVICE}" || [[ ! -S /run/the333-bgp/updater.sock ]]; then
    log "Host updater service or its Unix socket is not ready."
    if docker_cli ps --format '{{.Names}}' | grep -Fxq 'the333-host-updater'; then
      log "Legacy updater container detected. Install the host-side updater with:"
      log "  cd ${PROJECT_DIR} && ./scripts/the333bgp.sh install-updater-service"
    fi
    return 1
  fi

  if runtime_is_ready; then
    log "Runtime health: backend, GoBGP and portal are ready."
  else
    log "Runtime health: one or more services are not ready."
    return 1
  fi

  if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
    local ready_host="${THE333_BIND_IP:-${ROUTER_ID:-${BGP_NEXTHOP:-127.0.0.1}}}"
    local ready_password="${THE333_PORTAL_PASSWORD:-${WEB_PASSWORD:-}}"
    if [[ -n "${ready_password}" ]]; then
      curl -sS --fail-with-body -u "${WEB_USER:-admin}:${ready_password}" "http://${ready_host}:8090/backend/ready" || true
      printf '\n'
    else
      log "Ready API auth check skipped: plaintext WEB_PASSWORD is not stored. Set THE333_PORTAL_PASSWORD temporarily if needed."
    fi
  fi
}

install_host_updater_service() {
  need_cmd python3
  need_cmd systemctl

  local template="${PROJECT_DIR}/deploy/systemd/the333-bgp-updater.service.in"
  local rendered project_gid
  [[ -f "${template}" ]] || fail "host updater systemd template is missing"
  rendered="$(mktemp)"
  project_gid="${PGID:-}"
  if [[ -z "${project_gid}" && -f "${PROJECT_DIR}/.env" ]]; then
    project_gid="$(awk -F= '$1 == "PGID" {print $2; exit}' "${PROJECT_DIR}/.env" | tr -d '[:space:]')"
  fi
  [[ "${project_gid}" =~ ^[0-9]+$ ]] || project_gid="$(id -g)"

  python3 - "${template}" "${PROJECT_DIR}" "${project_gid}" > "${rendered}" <<'PY'
from pathlib import Path
import sys

template = Path(sys.argv[1]).read_text(encoding="utf-8")
project_dir = sys.argv[2]
project_gid = sys.argv[3]
if "\n" in project_dir or "\r" in project_dir:
    raise SystemExit("invalid project directory")
print(
    template
    .replace("@PROJECT_DIR@", project_dir)
    .replace("@PROJECT_GID@", project_gid),
    end="",
)
PY

  root_cmd install -d -m 0750 /run/the333-bgp
  root_cmd install -m 0644 "${rendered}" "/etc/systemd/system/${HOST_UPDATER_SERVICE}"
  rm -f "${rendered}"
  root_cmd systemctl daemon-reload
  root_cmd systemctl enable "${HOST_UPDATER_SERVICE}"

  if [[ "${THE333_HOST_UPDATER_ACTIVE:-false}" == "true" ]]; then
    root_cmd systemctl is-active --quiet "${HOST_UPDATER_SERVICE}" \
      || root_cmd systemctl start "${HOST_UPDATER_SERVICE}"
  else
    root_cmd systemctl restart "${HOST_UPDATER_SERVICE}"
  fi

  local attempts=20
  while (( attempts > 0 )); do
    [[ -S /run/the333-bgp/updater.sock ]] && return 0
    attempts=$((attempts - 1))
    sleep 1
  done

  root_cmd systemctl status --no-pager "${HOST_UPDATER_SERVICE}" || true
  fail "host updater socket did not become ready"
}

make_password_hash() {
  local password="$1"
  printf '%s' "${password}" | python3 -c '
import binascii
import hashlib
import os
import sys

password = sys.stdin.buffer.read()
iterations = 600000
salt = os.urandom(16)
digest = hashlib.pbkdf2_hmac("sha256", password, salt, iterations)
print("pbkdf2_sha256:%d:%s:%s" % (
    iterations,
    binascii.hexlify(salt).decode("ascii"),
    binascii.hexlify(digest).decode("ascii"),
))
'
}

set_password() {
  need_cmd python3
  cd "${PROJECT_DIR}"
  [[ -f .env ]] || fail ".env not found in ${PROJECT_DIR}"

  local password password_again password_hash
  if [[ -n "${THE333_NEW_PASSWORD:-}" ]]; then
    password="${THE333_NEW_PASSWORD}"
    password_again="${THE333_NEW_PASSWORD}"
  else
    require_tty
    read -r -s -p "New portal password: " password </dev/tty
    printf '\n'
    read -r -s -p "Repeat portal password: " password_again </dev/tty
    printf '\n'
  fi

  [[ "${password}" == "${password_again}" ]] || fail "passwords do not match"
  [[ "${#password}" -ge 8 ]] || fail "password must be at least 8 characters"

  password_hash="$(make_password_hash "${password}")"
  cp .env ".env.backup-before-password-change-$(date +%Y%m%d-%H%M%S)"

  python3 - ".env" "${password_hash}" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
password_hash = sys.argv[2]
lines = env_path.read_text(encoding="utf-8").splitlines()
out = []
seen_password = False
seen_hash = False

for line in lines:
    if line.startswith("WEB_PASSWORD="):
        out.append("WEB_PASSWORD=")
        seen_password = True
    elif line.startswith("WEB_PASSWORD_HASH="):
        out.append(f"WEB_PASSWORD_HASH={password_hash}")
        seen_hash = True
    else:
        out.append(line)

if not seen_password:
    out.append("WEB_PASSWORD=")
if not seen_hash:
    out.append(f"WEB_PASSWORD_HASH={password_hash}")

env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY

  chmod 600 .env
  log "Password hash updated in .env. Restarting backend only..."
  compose up -d --no-deps --force-recreate the333-bgp-backend
}

set_env_values() {
  [[ -f "${PROJECT_DIR}/.env" ]] || fail ".env not found in ${PROJECT_DIR}"
  python3 - "${PROJECT_DIR}/.env" "$@" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
pairs = sys.argv[2:]
if len(pairs) % 2:
    raise SystemExit("set_env_values requires key/value pairs")
updates = dict(zip(pairs[::2], pairs[1::2]))
lines = env_path.read_text(encoding="utf-8").splitlines()
written = set()
result = []
for line in lines:
    key = line.split("=", 1)[0] if "=" in line else ""
    if key in updates:
        result.append(f"{key}={updates[key]}")
        written.add(key)
    else:
        result.append(line)
for key, value in updates.items():
    if key not in written:
        result.append(f"{key}={value}")
env_path.write_text("\n".join(result) + "\n", encoding="utf-8")
PY
  chmod 600 "${PROJECT_DIR}/.env"
}

tls_enable() {
  [[ $# -eq 2 ]] || fail "usage: scripts/the333bgp.sh tls-enable CERT_FILE KEY_FILE"
  need_cmd openssl
  need_cmd sha256sum
  need_cmd python3

  local cert_source key_source cert_hash key_hash backup_env project_gid
  cert_source="$(realpath "$1")"
  key_source="$(realpath "$2")"
  [[ -f "${cert_source}" ]] || fail "TLS certificate not found: ${cert_source}"
  [[ -f "${key_source}" ]] || fail "TLS private key not found: ${key_source}"

  openssl x509 -in "${cert_source}" -noout >/dev/null 2>&1 || fail "invalid TLS certificate"
  openssl pkey -in "${key_source}" -passin pass: -noout >/dev/null 2>&1 || fail "invalid or encrypted TLS private key"
  cert_hash="$(openssl x509 -in "${cert_source}" -pubkey -noout | openssl pkey -pubin -outform DER 2>/dev/null | sha256sum | cut -d' ' -f1)"
  key_hash="$(openssl pkey -in "${key_source}" -passin pass: -pubout -outform DER 2>/dev/null | sha256sum | cut -d' ' -f1)"
  [[ -n "${cert_hash}" && "${cert_hash}" == "${key_hash}" ]] || fail "TLS certificate and private key do not match"

  backup_env="${PROJECT_DIR}/.env.backup-before-tls-$(date +%Y%m%d-%H%M%S)"
  cp "${PROJECT_DIR}/.env" "${backup_env}"
  chmod 600 "${backup_env}"

  project_gid="${PGID:-$(id -g)}"
  [[ "${project_gid}" =~ ^[0-9]+$ ]] || fail "PGID must be numeric"
  root_cmd install -d -m 0750 -o root -g "${project_gid}" /etc/the333-bgp/tls
  root_cmd install -m 0640 -o root -g "${project_gid}" "${cert_source}" /etc/the333-bgp/tls/portal.crt
  root_cmd install -m 0640 -o root -g "${project_gid}" "${key_source}" /etc/the333-bgp/tls/portal.key
  set_env_values \
    PORTAL_TLS_ENABLED true \
    SESSION_COOKIE_SECURE true \
    PORTAL_TLS_CERT_PATH /etc/the333-bgp/tls/portal.crt \
    PORTAL_TLS_KEY_PATH /etc/the333-bgp/tls/portal.key

  export PORTAL_TLS_ENABLED=true SESSION_COOKIE_SECURE=true
  export PORTAL_TLS_CERT_PATH=/etc/the333-bgp/tls/portal.crt
  export PORTAL_TLS_KEY_PATH=/etc/the333-bgp/tls/portal.key
  configure_compose_files

  if ! compose config >/dev/null \
    || ! compose build the333-portal \
    || ! compose run --rm --no-deps the333-portal nginx -t; then
    cp "${backup_env}" "${PROJECT_DIR}/.env"
    chmod 600 "${PROJECT_DIR}/.env"
    PORTAL_TLS_ENABLED=false
    SESSION_COOKIE_SECURE=false
    configure_compose_files
    fail "TLS preflight failed; .env was restored from ${backup_env}"
  fi

  if ! compose up -d --build the333-bgp-backend the333-portal; then
    cp "${backup_env}" "${PROJECT_DIR}/.env"
    chmod 600 "${PROJECT_DIR}/.env"
    export PORTAL_TLS_ENABLED=false SESSION_COOKIE_SECURE=false
    configure_compose_files
    compose up -d --build the333-bgp-backend the333-portal || true
    fail "TLS activation failed; HTTP configuration was restored from ${backup_env}"
  fi
  log "TLS включён. Портал: https://${THE333_BIND_IP:-${ROUTER_ID}}:8090/"
}

tls_disable() {
  need_cmd python3
  local backup_env original_tls original_secure
  original_tls="${PORTAL_TLS_ENABLED:-false}"
  original_secure="${SESSION_COOKIE_SECURE:-false}"
  backup_env="${PROJECT_DIR}/.env.backup-before-tls-disable-$(date +%Y%m%d-%H%M%S)"
  cp "${PROJECT_DIR}/.env" "${backup_env}"
  chmod 600 "${backup_env}"
  set_env_values PORTAL_TLS_ENABLED false SESSION_COOKIE_SECURE false
  export PORTAL_TLS_ENABLED=false SESSION_COOKIE_SECURE=false
  configure_compose_files
  compose config >/dev/null
  if ! compose up -d --build the333-bgp-backend the333-portal; then
    cp "${backup_env}" "${PROJECT_DIR}/.env"
    chmod 600 "${PROJECT_DIR}/.env"
    export PORTAL_TLS_ENABLED="${original_tls}" SESSION_COOKIE_SECURE="${original_secure}"
    configure_compose_files
    compose up -d --build the333-bgp-backend the333-portal || true
    fail "TLS deactivation failed; previous configuration was restored from ${backup_env}"
  fi
  log "TLS выключен. Портал: http://${THE333_BIND_IP:-${ROUTER_ID}}:8090/"
}

doctor() {
  cd "${PROJECT_DIR}"
  log "Running local diagnostics..."
  status

  if [[ -x scripts/release-check.sh ]]; then
    scripts/release-check.sh
  else
    log "scripts/release-check.sh not found; skipping release tree check"
  fi
}

fetch_manifest() {
  local manifest_file manifest_json manifest_size
  need_cmd curl
  need_cmd python3
  [[ -n "${MANIFEST_URL}" ]] || fail "manifest URL is empty. Set PRODUCT_UPDATE_MANIFEST_URL or pass --manifest URL"
  [[ "${MANIFEST_URL}" == https://* ]] || fail "manifest URL must use HTTPS"
  [[ "${MANIFEST_URL}" != *$'\n'* && "${MANIFEST_URL}" != *$'\r'* ]] \
    || fail "manifest URL contains a line break"
  case "${CHANNEL}" in
    stable|beta) ;;
    *) fail "channel must be stable or beta" ;;
  esac
  if [[ -n "${TARGET_VERSION}" && ! "${TARGET_VERSION}" =~ ^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$ ]]; then
    fail "target version has an invalid format"
  fi
  manifest_file="$(mktemp)"
  if ! curl \
    --fail \
    --silent \
    --show-error \
    --location \
    --proto '=https' \
    --proto-redir '=https' \
    --max-filesize 2097152 \
    "${MANIFEST_URL}" \
    --output "${manifest_file}"; then
    rm -f "${manifest_file}"
    fail "update manifest download failed"
  fi
  manifest_size="$(wc -c < "${manifest_file}")"
  if (( manifest_size > 2097152 )); then
    rm -f "${manifest_file}"
    fail "update manifest exceeds the 2 MB limit"
  fi

  if ! manifest_json="$(python3 -c '
import json
import re
import sys

try:
    payload = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError):
    raise SystemExit("update manifest is not valid JSON")
if isinstance(payload, dict):
    print(json.dumps(payload, ensure_ascii=False))
    raise SystemExit(0)
if not isinstance(payload, list):
    raise SystemExit("update index must be an object or GitHub releases array")

versions = []
latest = {"stable": None, "beta": None}
for release in payload[:20]:
    if not isinstance(release, dict) or release.get("draft"):
        continue
    tag = str(release.get("tag_name") or "").strip()
    version = tag[1:] if tag.lower().startswith("v") else tag
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,63}", version):
        continue
    channel = "beta" if release.get("prerelease") else "stable"
    expected = f"the333-bgp-v{version}.tar.gz"
    asset = next((item for item in release.get("assets", []) if isinstance(item, dict) and item.get("name") == expected), None)
    if not asset:
        continue
    digest = str(asset.get("digest") or "")
    sha256 = digest.split(":", 1)[1] if digest.startswith("sha256:") else ""
    archive_url = str(asset.get("browser_download_url") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256) or not archive_url.startswith("https://"):
        continue
    versions.append({
        "version": version,
        "title": str(release.get("name") or f"v{version}"),
        "channel": channel,
        "date": str(release.get("published_at") or release.get("created_at") or "")[:10],
        "recommended": latest[channel] is None,
        "archive_url": archive_url,
        "sha256": sha256,
    })
    if latest[channel] is None:
        latest[channel] = version

if not versions:
    raise SystemExit("GitHub releases index has no usable release assets")
print(json.dumps({"product": "The333-BGP", "latest": latest, "versions": versions}, ensure_ascii=False))
' < "${manifest_file}")"; then
    rm -f "${manifest_file}"
    fail "update manifest validation failed"
  fi
  rm -f "${manifest_file}"
  printf '%s\n' "${manifest_json}"
}

select_version_json() {
  need_cmd python3
  python3 -c '
import json
import sys

manifest = json.load(sys.stdin)
channel = sys.argv[1]
target = sys.argv[2]
versions = manifest.get("versions", [])

selected = None
if target:
    selected = next((item for item in versions if str(item.get("version")) == target), None)
else:
    latest = (manifest.get("latest") or {}).get(channel)
    selected = next((item for item in versions if str(item.get("version")) == str(latest)), None)
    if selected is None:
        selected = next((item for item in versions if str(item.get("channel", "stable")) == channel), None)

if not selected:
    raise SystemExit(f"no version found for channel={channel!r} target={target!r}")

print(json.dumps(selected, ensure_ascii=False))
' "$CHANNEL" "$TARGET_VERSION"
}

version_is_newer() {
  python3 - "$1" "$2" <<'PY'
import re
import sys

def weight(value):
    raw = str(value or "").strip()
    if raw[:1].lower() == "v":
        raw = raw[1:]
    match = re.match(r"^(\d+(?:\.\d+)*)", raw)
    numbers = [int(part) for part in match.group(1).split(".")] if match else []
    numbers = (numbers + [0, 0, 0])[:3]
    prerelease = -1 if re.search(r"(?:b|-beta(?:\.|$))", raw, flags=re.IGNORECASE) else 0
    return (*numbers, prerelease)

raise SystemExit(0 if weight(sys.argv[1]) > weight(sys.argv[2]) else 1)
PY
}

check_update() {
  local manifest
  manifest="$(fetch_manifest)"
  printf '%s' "${manifest}" | select_version_json
  printf '\n'
}

download_release() {
  local version_json="$1"
  need_cmd python3
  need_cmd curl

  local archive_url sha tmp archive archive_size
  archive_url="$(printf '%s' "${version_json}" | python3 -c 'import json,sys; print((json.load(sys.stdin).get("archive_url") or "").strip())')"
  sha="$(printf '%s' "${version_json}" | python3 -c 'import json,sys; print((json.load(sys.stdin).get("sha256") or "").strip())')"
  [[ -n "${archive_url}" ]] || fail "selected version has no archive_url"
  [[ "${archive_url}" == https://* ]] || fail "archive_url must use https"
  [[ "${sha}" =~ ^[A-Fa-f0-9]{64}$ ]] || fail "selected version has no valid sha256; refusing update"

  tmp="$(mktemp -d)"
  archive="${tmp}/release.tar.gz"
  log "Downloading release archive..."
  if ! curl \
    --fail \
    --show-error \
    --location \
    --proto '=https' \
    --proto-redir '=https' \
    --max-filesize 268435456 \
    "${archive_url}" \
    --output "${archive}"; then
    rm -rf "${tmp}"
    fail "release archive download failed"
  fi
  archive_size="$(wc -c < "${archive}")"
  if (( archive_size > 268435456 )); then
    rm -rf "${tmp}"
    fail "release archive exceeds the 256 MB compressed-size limit"
  fi

  need_cmd sha256sum
  if ! printf '%s  %s\n' "${sha}" "${archive}" | sha256sum -c - >/dev/null; then
    rm -rf "${tmp}"
    fail "release archive SHA-256 verification failed"
  fi

  mkdir -p "${tmp}/src"
  if ! python3 "${PROJECT_DIR}/scripts/extract-release.py" "${archive}" "${tmp}/src"; then
    rm -rf "${tmp}"
    fail "release archive extraction failed"
  fi
  printf '%s\n' "${tmp}/src"
}

copy_release_files() {
  local src="$1"
  [[ -d "${src}/app" ]] || fail "release archive has no app/"
  [[ -d "${src}/portal" ]] || fail "release archive has no portal/"

  log "Copying release files while preserving .env, data and user state..."
  mkdir -p "${PROJECT_DIR}"

  for item in app portal docs docker deploy extras requirements.in requirements.txt docker-compose.yml docker-compose.portal.yml docker-compose.tls.yml VERSION CHANGELOG.md README.md LICENSE SECURITY.md install.sh scripts update-manifest.json update-manifest.example.json .env.example .dockerignore .gitattributes .gitignore; do
    if [[ -e "${src}/${item}" ]]; then
      rm -rf "${PROJECT_DIR:?}/${item}"
      cp -a "${src}/${item}" "${PROJECT_DIR}/${item}"
    fi
  done

  rm -f "${PROJECT_DIR}/Dockerfile" "${PROJECT_DIR}/entrypoint.sh" "${PROJECT_DIR}/app/updater.py"

  chmod +x \
    "${PROJECT_DIR}/docker/gobgp-entrypoint.sh" \
    "${PROJECT_DIR}/docker/backend-entrypoint.sh" \
    "${PROJECT_DIR}/scripts/host-updater.py" \
    "${PROJECT_DIR}/scripts/extract-release.py" \
    "${PROJECT_DIR}/scripts/migrate-env.py" \
    "${PROJECT_DIR}/scripts/release-check.sh" \
    "${PROJECT_DIR}/scripts/the333bgp.sh" \
    "${PROJECT_DIR}/install.sh"

  mkdir -p "${PROJECT_DIR}/config" "${PROJECT_DIR}/data"
  if [[ -d "${src}/config" ]]; then
    for cfg in "${src}"/config/*; do
      [[ -e "${cfg}" ]] || continue
      local target
      target="${PROJECT_DIR}/config/$(basename "${cfg}")"
      cp -a "${cfg}" "${target}"
    done
  fi
}

migrate_release_env() {
  local version update_url
  version="$(current_version)"
  update_url="${MANIFEST_URL:-https://api.github.com/repos/The333tech/The333-bgp/releases?per_page=20}"
  python3 "${PROJECT_DIR}/scripts/migrate-env.py" \
    --env "${PROJECT_DIR}/.env" \
    --project-dir "${PROJECT_DIR}" \
    --version "${version}" \
    --channel "${CHANNEL}" \
    --update-url "${update_url}"
  load_env
  configure_compose_files
}

restore_release_files_from_backup() {
  local archive="$1"
  [[ -f "${archive}" ]] || fail "rollback archive not found: ${archive}"

  local tmp
  tmp="$(mktemp -d)"
  tar -xzf "${archive}" -C "${tmp}"

  log "Восстановление файлов предыдущей версии из ${archive}..."
  for item in app portal docs docker deploy extras requirements.in requirements.txt docker-compose.yml docker-compose.portal.yml docker-compose.tls.yml VERSION CHANGELOG.md README.md LICENSE SECURITY.md install.sh scripts update-manifest.json update-manifest.example.json .env.example .dockerignore .gitattributes .gitignore; do
    if [[ -e "${tmp}/${item}" ]]; then
      rm -rf "${PROJECT_DIR:?}/${item}"
      cp -a "${tmp}/${item}" "${PROJECT_DIR}/${item}"
    fi
  done

  for state_item in .env config data; do
    [[ -e "${tmp}/${state_item}" ]] || fail "rollback archive has no ${state_item}"
    rm -rf "${PROJECT_DIR:?}/${state_item}"
    cp -a "${tmp}/${state_item}" "${PROJECT_DIR}/${state_item}"
  done
  chmod 600 "${PROJECT_DIR}/.env"

  rm -rf "${tmp}"
}

stop_runtime_for_rollback() {
  local container
  log "Остановка runtime перед восстановлением согласованного состояния..."
  for container in the333-portal the333-bgp-backend the333-gobgp-core; do
    if docker_cli container inspect "${container}" >/dev/null 2>&1; then
      docker_cli stop --time 30 "${container}" >/dev/null || return 1
    fi
  done
}

build_update_images() {
  local readiness_mode="${1:-strict}"
  local core_image="the333-bgp-core:${GOBGP_CORE_IMAGE_VERSION:-4.7.0-r5}"
  if [[ "${readiness_mode}" == "legacy" ]]; then
    log "Rollback: выполняется полная сборка предыдущего runtime."
    compose build
    return
  fi
  if docker_cli image inspect "${core_image}" >/dev/null 2>&1; then
    log "Routing-core ${core_image} уже установлен; собираются только backend и portal."
    compose build the333-bgp-backend the333-portal
    return
  fi

  log "Routing-core ${core_image} отсутствует; выполняется полная сборка."
  compose build
}

build_and_restart() {
  local readiness_mode="${1:-strict}"
  cd "${PROJECT_DIR}"
  if [[ "${THE333_HOST_UPDATER_ACTIVE:-false}" == "true" ]]; then
    log "Host updater выполняет обновление: стабильный systemd unit будет переиспользован."
  else
    install_host_updater_service
  fi
  log "Building images..."
  build_update_images "${readiness_mode}"
  log "Restarting services..."
  compose up -d --remove-orphans
  wait_for_services "${readiness_mode}" || return 1
  if [[ "${readiness_mode}" == "legacy" ]]; then
    compose ps
    return 0
  fi
  status
}

update_project() {
  local manifest version_json selected_version installed_version release_dir backup_archive release_tmp
  manifest="$(fetch_manifest)"
  version_json="$(printf '%s' "${manifest}" | select_version_json)"
  selected_version="$(printf '%s' "${version_json}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version") or "")')"
  installed_version="$(current_version)"

  log "Selected version: ${selected_version}"
  version_is_newer "${selected_version}" "${installed_version}" \
    || fail "selected version ${selected_version} is not newer than installed ${installed_version}; use repair or a verified backup for recovery"

  if [[ "${DRY_RUN}" == "true" ]]; then
    printf '%s\n' "${version_json}"
    return 0
  fi

  if [[ "${NON_INTERACTIVE}" != "true" ]]; then
    require_tty
    read -r -p "Update The333-BGP from $(current_version)? Type YES: " answer </dev/tty
    [[ "${answer}" == "YES" ]] || fail "cancelled"
  fi

  check_update_disk_space
  make_backup
  backup_archive="${LAST_BACKUP_ARCHIVE}"
  release_dir="$(download_release "${version_json}")"
  copy_release_files "${release_dir}"
  migrate_release_env
  release_tmp="$(dirname "${release_dir}")"
  [[ "${release_tmp}" == /tmp/* ]] && rm -rf "${release_tmp}"

  if build_and_restart strict; then
    log "Обновление успешно завершено."
    return 0
  fi

  log "Обновление не прошло проверку готовности. Запускается автоматический rollback."
  stop_runtime_for_rollback || fail "update failed and runtime could not be stopped safely; inspect ${backup_archive}"
  restore_release_files_from_backup "${backup_archive}"
  if build_and_restart legacy; then
    fail "update failed; the previous version was restored automatically"
  fi

  fail "update and automatic rollback both failed; inspect ${backup_archive} and container logs"
}

parse_common_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --manifest)
        MANIFEST_URL="${2:-}"
        shift 2
        ;;
      --channel)
        CHANNEL="${2:-stable}"
        shift 2
        ;;
      --version)
        TARGET_VERSION="${2:-}"
        shift 2
        ;;
      --non-interactive)
        NON_INTERACTIVE="true"
        shift
        ;;
      --dry-run)
        DRY_RUN="true"
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        fail "unknown argument: $1"
        ;;
    esac
  done
}

main() {
  local command="${1:-}"
  [[ -n "${command}" ]] || { usage; exit 2; }
  shift || true

  load_env
  configure_compose_files

  case "${command}" in
    status)
      parse_common_args "$@"
      status
      ;;
    doctor)
      parse_common_args "$@"
      doctor
      ;;
    set-password)
      parse_common_args "$@"
      set_password
      ;;
    tls-enable)
      tls_enable "$@"
      ;;
    tls-disable)
      parse_common_args "$@"
      tls_disable
      ;;
    install-updater-service)
      parse_common_args "$@"
      install_host_updater_service
      ;;
    backup)
      parse_common_args "$@"
      make_backup
      ;;
    check-update)
      parse_common_args "$@"
      check_update
      ;;
    update)
      parse_common_args "$@"
      update_project
      ;;
    *)
      usage
      exit 2
      ;;
  esac
}

main "$@"
