#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${THE333_PROJECT_DIR:-/opt/the333-bgp}"
COMPOSE_FILES=(-f docker-compose.yml -f docker-compose.portal.yml)
VERSION_FILE="${PROJECT_DIR}/VERSION"
BACKUP_DIR="${PROJECT_DIR}/backups"
MANIFEST_URL="${PRODUCT_UPDATE_MANIFEST_URL:-}"
CHANNEL="stable"
TARGET_VERSION=""
NON_INTERACTIVE="false"
DRY_RUN="false"

log() {
  printf '[the333bgp] %s\n' "$*"
}

fail() {
  printf '[the333bgp] ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage:
  scripts/the333bgp.sh status
  scripts/the333bgp.sh doctor
  scripts/the333bgp.sh set-password
  scripts/the333bgp.sh backup
  scripts/the333bgp.sh check-update [--manifest URL] [--channel stable|beta]
  scripts/the333bgp.sh update [--manifest URL] [--channel stable|beta] [--version VERSION] [--non-interactive] [--dry-run]

Environment:
  THE333_PROJECT_DIR=/opt/the333-bgp
  PRODUCT_UPDATE_MANIFEST_URL=https://raw.githubusercontent.com/The333tech/The333-bgp/main/update-manifest.json
USAGE
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "command not found: $1"
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

compose() {
  docker compose "${COMPOSE_FILES[@]}" "$@"
}

current_version() {
  if [[ -f "${VERSION_FILE}" ]]; then
    tr -d '[:space:]' < "${VERSION_FILE}"
  else
    printf '0.0.0'
  fi
}

make_backup() {
  mkdir -p "${BACKUP_DIR}"
  local ts
  ts="$(date +%Y%m%d-%H%M%S)"
  local archive="${BACKUP_DIR}/the333-bgp-before-update-${ts}.tar.gz"

  log "Creating backup: ${archive}"
  tar \
    --exclude='./backups' \
    --exclude='./portal/node_modules' \
    --exclude='./portal/dist' \
    -czf "${archive}" \
    -C "${PROJECT_DIR}" \
    .env VERSION docker-compose.yml docker-compose.portal.yml Dockerfile entrypoint.sh requirements.txt app portal config data

  log "Backup ready: ${archive}"
}

status() {
  cd "${PROJECT_DIR}"
  log "Version: $(current_version)"
  compose ps

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

make_password_hash() {
  local password="$1"
  printf '%s' "${password}" | python3 -c '
import binascii
import hashlib
import os
import sys

password = sys.stdin.buffer.read()
iterations = 260000
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
    read -r -s -p "New portal password: " password
    printf '\n'
    read -r -s -p "Repeat portal password: " password_again
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
  need_cmd curl
  [[ -n "${MANIFEST_URL}" ]] || fail "manifest URL is empty. Set PRODUCT_UPDATE_MANIFEST_URL or pass --manifest URL"
  curl -fsSL "${MANIFEST_URL}"
}

select_version_json() {
  need_cmd python3
  python3 - "$CHANNEL" "$TARGET_VERSION" <<'PY'
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

  local archive_url sha tmp archive
  archive_url="$(printf '%s' "${version_json}" | python3 -c 'import json,sys; print((json.load(sys.stdin).get("archive_url") or "").strip())')"
  sha="$(printf '%s' "${version_json}" | python3 -c 'import json,sys; print((json.load(sys.stdin).get("sha256") or "").strip())')"
  [[ -n "${archive_url}" ]] || fail "selected version has no archive_url"
  [[ "${archive_url}" == https://* ]] || fail "archive_url must use https"
  if [[ -z "${sha}" && "${THE333_ALLOW_UNSIGNED_UPDATE:-false}" != "true" ]]; then
    case "${archive_url}" in
      https://github.com/The333tech/The333-bgp/archive/refs/heads/main.tar.gz|https://github.com/The333tech/The333-bgp/archive/refs/tags/v*.tar.gz)
        log "selected version has no sha256; continuing because archive_url points to the official The333-BGP GitHub repository"
        ;;
      *)
        fail "selected version has no sha256. Refusing unsigned update. Set THE333_ALLOW_UNSIGNED_UPDATE=true only for local testing."
        ;;
    esac
  fi

  tmp="$(mktemp -d)"
  archive="${tmp}/release.tar.gz"
  log "Downloading release archive..."
  curl -fL "${archive_url}" -o "${archive}"

  if [[ -n "${sha}" ]]; then
    need_cmd sha256sum
    printf '%s  %s\n' "${sha}" "${archive}" | sha256sum -c -
  fi

  mkdir -p "${tmp}/src"
  tar -xzf "${archive}" -C "${tmp}/src" --strip-components=1
  printf '%s\n' "${tmp}/src"
}

copy_release_files() {
  local src="$1"
  [[ -d "${src}/app" ]] || fail "release archive has no app/"
  [[ -d "${src}/portal" ]] || fail "release archive has no portal/"

  log "Copying release files while preserving .env, data and user state..."
  mkdir -p "${PROJECT_DIR}"

  for item in app portal docs Dockerfile entrypoint.sh requirements.txt docker-compose.yml docker-compose.portal.yml VERSION CHANGELOG.md LICENSE SECURITY.md install.sh scripts update-manifest.json update-manifest.example.json .env.example .gitignore; do
    if [[ -e "${src}/${item}" ]]; then
      rm -rf "${PROJECT_DIR:?}/${item}"
      cp -a "${src}/${item}" "${PROJECT_DIR}/${item}"
    fi
  done

  mkdir -p "${PROJECT_DIR}/config" "${PROJECT_DIR}/data"
  if [[ -d "${src}/config" ]]; then
    for cfg in "${src}"/config/*; do
      [[ -e "${cfg}" ]] || continue
      local target
      target="${PROJECT_DIR}/config/$(basename "${cfg}")"
      if [[ ! -e "${target}" ]]; then
        cp -a "${cfg}" "${target}"
      fi
    done
  fi
}

build_and_restart() {
  cd "${PROJECT_DIR}"
  log "Building images..."
  compose build
  log "Restarting services..."
  if [[ "${THE333_SKIP_SELF_RECREATE:-false}" == "true" ]]; then
    compose up -d the333-gobgp-core the333-bgp-backend the333-portal
    log "Host updater was not recreated during this portal-triggered update."
  else
    compose up -d
  fi
  log "Waiting for backend ready..."
  sleep 5
  status
}

update_project() {
  local manifest version_json release_dir
  manifest="$(fetch_manifest)"
  version_json="$(printf '%s' "${manifest}" | select_version_json)"

  log "Selected version: $(printf '%s' "${version_json}" | python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("version"))')"

  if [[ "${DRY_RUN}" == "true" ]]; then
    printf '%s\n' "${version_json}"
    return 0
  fi

  if [[ "${NON_INTERACTIVE}" != "true" ]]; then
    read -r -p "Update The333-BGP from $(current_version)? Type YES: " answer
    [[ "${answer}" == "YES" ]] || fail "cancelled"
  fi

  make_backup
  release_dir="$(download_release "${version_json}")"
  copy_release_files "${release_dir}"
  build_and_restart
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
