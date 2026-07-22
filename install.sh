#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${THE333_PROJECT_DIR:-/opt/the333-bgp}"
INSTALL_CHANNEL="${THE333_CHANNEL:-beta}"
INSTALL_VERSION="${THE333_VERSION:-}"
RELEASE_INDEX_URL="${PRODUCT_UPDATE_MANIFEST_URL:-https://api.github.com/repos/The333tech/The333-bgp/releases?per_page=20}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN="false"
NON_INTERACTIVE="false"
INSTALL_ACTION="${THE333_INSTALL_ACTION:-}"
DOCKER_CMD=(docker)
MIN_FREE_DISK_KB=8388608
RECOMMENDED_FREE_DISK_GB=12

usage() {
  cat <<'USAGE'
Usage:
  install.sh [--dry-run] [--non-interactive] [--action update|repair|backup|status|quit]

Environment for --non-interactive:
  THE333_PROJECT_DIR=/opt/the333-bgp
  THE333_BIND_IP=192.168.1.10
  LOCAL_AS=64512
  ROUTER_ID=192.168.1.10
  BGP_NEXTHOP=192.168.1.10
  PEER_ADDRESS=192.168.1.1
  PEER_AS=65455
  BGP_COMMUNITY=64512:500
  WEB_PASSWORD=strong-password
  PRODUCT_UPDATE_MANIFEST_URL=https://api.github.com/repos/The333tech/The333-bgp/releases?per_page=20
  THE333_CHANNEL=beta
  THE333_VERSION=
  THE333_INSTALL_ACTION=update
USAGE
}

log() {
  printf '[install] %s\n' "$*" >&2
}

fail() {
  printf '[install] ERROR: %s\n' "$*" >&2
  exit 1
}

require_tty() {
  [[ -r /dev/tty ]] || fail "interactive terminal is unavailable; rerun with --non-interactive and required environment variables"
}

ask() {
  local prompt="$1"
  local default="$2"
  local value
  require_tty
  read -r -p "${prompt} [${default}]: " value </dev/tty
  printf '%s\n' "${value:-$default}"
}

ask_secret() {
  local prompt="$1"
  local value
  if [[ "${NON_INTERACTIVE}" == "true" ]]; then
    printf '%s\n' "${WEB_PASSWORD:-}"
    return 0
  fi
  require_tty
  read -r -s -p "${prompt}: " value </dev/tty
  printf '\n' >&2
  printf '%s\n' "${value}"
}

confirm() {
  local prompt="$1"
  local default="${2:-N}"
  local answer
  if [[ "${NON_INTERACTIVE}" == "true" ]]; then
    [[ "${default}" =~ ^[YyДд]$ ]]
    return $?
  fi
  require_tty
  read -r -p "${prompt} [${default}]: " answer </dev/tty
  answer="${answer:-$default}"
  [[ "${answer}" =~ ^[YyДд]$ ]]
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || return 1
}

docker_cli() {
  "${DOCKER_CMD[@]}" "$@"
}

docker_compose() {
  docker_cli compose -f docker-compose.yml -f docker-compose.portal.yml "$@"
}

configure_docker_access() {
  if docker info >/dev/null 2>&1; then
    DOCKER_CMD=(docker)
    return 0
  fi

  [[ "${EUID}" -ne 0 ]] || fail "Docker daemon is unavailable for root"
  need_cmd sudo || fail "Docker is installed, but the current user cannot access the daemon and sudo is unavailable"

  if [[ "${NON_INTERACTIVE}" == "true" ]]; then
    sudo -n docker info >/dev/null 2>&1 || fail "Docker daemon access requires an interactive sudo session"
  else
    sudo docker info >/dev/null || fail "Docker daemon is unavailable through sudo"
  fi

  DOCKER_CMD=(sudo docker)
  log "Docker daemon доступен через sudo. Команды установки будут выполнены с повышенными правами."
}

sudo_cmd() {
  if [[ "${DRY_RUN}" == "true" ]]; then
    printf '[install] dry-run:'
    printf ' %q' "$@"
    printf '\n'
    return 0
  fi

  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

detect_ip() {
  ip route get 1.1.1.1 2>/dev/null | awk '{for (i=1;i<=NF;i++) if ($i=="src") {print $(i+1); exit}}'
}

detect_bgp_peer_mode() {
  local peer_ip="$1"
  local route
  route="$(ip -4 route get "${peer_ip}" 2>/dev/null | head -1 || true)"
  if [[ -n "${route}" && " ${route} " != *" via "* ]]; then
    printf 'direct\n'
  else
    printf 'multihop\n'
  fi
}

validate_ipv4() {
  local value="$1"
  [[ "${value}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
  local IFS=.
  local a b c d
  read -r a b c d <<< "${value}"
  for octet in "$a" "$b" "$c" "$d"; do
    [[ "${octet}" =~ ^[0-9]+$ ]] || return 1
    (( octet >= 0 && octet <= 255 )) || return 1
  done
}

validate_asn() {
  local value="$1"
  [[ "${value}" =~ ^[0-9]+$ ]] || return 1
  (( value > 0 && value <= 4294967295 ))
}

validate_community() {
  local value="$1"
  [[ "${value}" =~ ^([0-9]{1,5}):([0-9]{1,5})$ ]] || return 1
  (( 10#${BASH_REMATCH[1]} <= 65535 && 10#${BASH_REMATCH[2]} <= 65535 ))
}

validate_password() {
  local value="$1"
  [[ "${#value}" -ge 8 ]]
}

validate_release_selection() {
  case "${INSTALL_CHANNEL}" in
    stable|beta) ;;
    *) fail "THE333_CHANNEL must be stable or beta" ;;
  esac
  if [[ -n "${INSTALL_VERSION}" && ! "${INSTALL_VERSION}" =~ ^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$ ]]; then
    fail "THE333_VERSION has an invalid format"
  fi
  [[ "${RELEASE_INDEX_URL}" == https://* ]] || fail "PRODUCT_UPDATE_MANIFEST_URL must use HTTPS"
  [[ "${RELEASE_INDEX_URL}" != *$'\n'* && "${RELEASE_INDEX_URL}" != *$'\r'* ]] \
    || fail "PRODUCT_UPDATE_MANIFEST_URL contains a line break"
}

ask_ipv4() {
  local prompt="$1"
  local default="$2"
  local env_name="${3:-}"
  local value

  if [[ "${NON_INTERACTIVE}" == "true" && -n "${env_name}" ]]; then
    value="${!env_name:-${default}}"
    validate_ipv4 "${value}" || fail "invalid ${env_name}: ${value}"
    printf '%s\n' "${value}"
    return 0
  fi

  while true; do
    value="$(ask "${prompt}" "${default}")"
    if validate_ipv4 "${value}"; then
      printf '%s\n' "${value}"
      return 0
    fi
    log "Некорректный IPv4: ${value}"
  done
}

ask_asn() {
  local prompt="$1"
  local default="$2"
  local env_name="${3:-}"
  local value

  if [[ "${NON_INTERACTIVE}" == "true" && -n "${env_name}" ]]; then
    value="${!env_name:-${default}}"
    validate_asn "${value}" || fail "invalid ${env_name}: ${value}"
    printf '%s\n' "${value}"
    return 0
  fi

  while true; do
    value="$(ask "${prompt}" "${default}")"
    if validate_asn "${value}"; then
      printf '%s\n' "${value}"
      return 0
    fi
    log "Некорректный ASN: ${value}. Диапазон: 1..4294967295"
  done
}

ask_community() {
  local prompt="$1"
  local default="$2"
  local value

  if [[ "${NON_INTERACTIVE}" == "true" ]]; then
    value="${BGP_COMMUNITY:-${default}}"
    validate_community "${value}" || fail "invalid BGP_COMMUNITY: ${value}"
    printf '%s\n' "${value}"
    return 0
  fi

  while true; do
    value="$(ask "${prompt}" "${default}")"
    if validate_community "${value}"; then
      printf '%s\n' "${value}"
      return 0
    fi
    log "Некорректный BGP Community: ${value}. Ожидается формат 0..65535:0..65535"
  done
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run)
        DRY_RUN="true"
        shift
        ;;
      --non-interactive)
        NON_INTERACTIVE="true"
        shift
        ;;
      --action)
        [[ $# -ge 2 ]] || fail "--action requires update, repair, backup, status or quit"
        INSTALL_ACTION="$2"
        shift 2
        ;;
      --update)
        INSTALL_ACTION="update"
        shift
        ;;
      --repair)
        INSTALL_ACTION="repair"
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

check_basic_tools() {
  local missing=()
  local cmd
  for cmd in awk curl tar grep sed ip python3 sha256sum; do
    need_cmd "${cmd}" || missing+=("${cmd}")
  done
  if [[ "${#missing[@]}" -gt 0 ]]; then
    fail "required commands are missing: ${missing[*]}"
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

make_token() {
  if need_cmd openssl; then
    openssl rand -hex 32 | tr -d '\n'
  else
    tr -dc 'A-Fa-f0-9' </dev/urandom | head -c 64
  fi
}

check_os() {
  if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    log "Detected OS: ${PRETTY_NAME:-${ID:-unknown}}"
    case "${ID:-}" in
      ubuntu|debian)
        ;;
      *)
        log "Warning: automatic Docker install is tested only on Ubuntu/Debian. Existing Docker may still work."
        ;;
    esac
  else
    log "Warning: /etc/os-release not found; OS detection skipped."
  fi
}

check_resources() {
  local mem_kb disk_kb
  mem_kb="$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || true)"
  disk_kb="$(df -Pk "$(dirname "${PROJECT_DIR}")" 2>/dev/null | awk 'NR==2 {print $4}' || true)"

  if [[ -n "${mem_kb}" && "${mem_kb}" =~ ^[0-9]+$ && "${mem_kb}" -lt 900000 ]]; then
    fail "not enough RAM: $((mem_kb / 1024)) MB available. Need at least 1 GB; recommended 2 GB+."
  fi
  if [[ -n "${mem_kb}" && "${mem_kb}" =~ ^[0-9]+$ && "${mem_kb}" -lt 1800000 ]]; then
    log "Warning: RAM is below the recommended 2 GB level ($((mem_kb / 1024)) MB)."
  fi

  if [[ -n "${disk_kb}" && "${disk_kb}" =~ ^[0-9]+$ && "${disk_kb}" -lt "${MIN_FREE_DISK_KB}" ]]; then
    fail "not enough free disk space near ${PROJECT_DIR}: $((disk_kb / 1024)) MB available. Fresh installation needs at least 8 GB free; recommended ${RECOMMENDED_FREE_DISK_GB}+ GB."
  fi

  if [[ -n "${disk_kb}" && "${disk_kb}" =~ ^[0-9]+$ && "${disk_kb}" -lt $((RECOMMENDED_FREE_DISK_GB * 1024 * 1024)) ]]; then
    log "Warning: free disk space is below recommended level ($((disk_kb / 1024)) MB). Recommended: ${RECOMMENDED_FREE_DISK_GB}+ GB."
  fi
}

check_project_dir_safety() {
  [[ "${PROJECT_DIR}" == /* ]] || fail "THE333_PROJECT_DIR must be an absolute path"
  [[ "${PROJECT_DIR}" =~ ^/[A-Za-z0-9._/-]+$ ]] || fail "THE333_PROJECT_DIR contains unsupported characters"
  case "${PROJECT_DIR}" in
    ""|"/"|"/root"|"/root/"*|"/home"|"/home/"*|"/opt"|"/usr"|"/var")
      fail "unsafe THE333_PROJECT_DIR: ${PROJECT_DIR}"
      ;;
  esac
}

show_firewall_notice() {
  log "Firewall note: Docker publishes ports through iptables/nftables. If ufw/firewalld is enabled, verify access rules for 179, 8088 and 8090."
  log "Security note: do not expose ports 8090, 8088 or 179 directly to the Internet."
}

port_is_busy() {
  local port="$1"
  if need_cmd ss; then
    ss -ltnH 2>/dev/null | awk '{print $4}' | grep -Eq "(:|\\])${port}$"
    return $?
  fi
  return 1
}

check_ports() {
  local busy=()
  local port
  for port in 179 8088 8090; do
    if port_is_busy "${port}"; then
      busy+=("${port}")
    fi
  done

  if [[ "${#busy[@]}" -eq 0 ]]; then
    return 0
  fi

  log "Найдены занятые порты: ${busy[*]}"
  if [[ "${DRY_RUN}" == "true" ]]; then
    log "Dry-run: установка потребует освободить эти порты или подтвердить продолжение."
    return 0
  fi

  log "Если это старая установка The333-BGP, запусти install.sh ещё раз с тем же PROJECT_DIR: будет предложено обновление."
  confirm "Продолжить установку несмотря на занятые порты?" "N" || fail "ports are busy: ${busy[*]}"
}

ensure_docker() {
  if need_cmd docker && docker compose version >/dev/null 2>&1; then
    log "Step 1/7: Docker and Docker Compose plugin are already installed. Skipping Docker installation."
    configure_docker_access
    return 0
  fi

  log "Step 1/7: Docker or Docker Compose plugin not found."
  if [[ "${NON_INTERACTIVE}" == "true" ]]; then
    fail "Docker is required. Install Docker first, or rerun installer interactively to allow apt installation."
  fi

  require_tty
  read -r -p "Попробовать установить Docker через apt? [y/N]: " answer </dev/tty
  [[ "${answer}" =~ ^[Yy]$ ]] || fail "Docker is required"

  need_cmd apt-get || fail "automatic Docker install supports apt-based systems only"
  # shellcheck source=/dev/null
  . /etc/os-release
  local docker_os
  case "${ID:-}" in
    ubuntu|debian)
      docker_os="${ID}"
      ;;
    *)
      fail "automatic Docker install supports Ubuntu/Debian only. Install Docker manually and rerun install.sh"
      ;;
  esac

  sudo_cmd apt-get update
  sudo_cmd apt-get install -y ca-certificates curl gnupg
  sudo_cmd install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${docker_os}/gpg" | sudo_cmd gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo_cmd chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${docker_os} ${VERSION_CODENAME} stable" | sudo_cmd tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo_cmd apt-get update
  sudo_cmd apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  log "Docker installed successfully."
  configure_docker_access
}

normalize_release_index() {
  python3 -c '
import json
import re
import sys

try:
    payload = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError):
    raise SystemExit("release index is not valid JSON")
if isinstance(payload, dict):
    print(json.dumps(payload, ensure_ascii=False))
    raise SystemExit(0)
if not isinstance(payload, list):
    raise SystemExit("release index must be an object or GitHub releases array")

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
        "channel": channel,
        "archive_url": archive_url,
        "sha256": sha256,
    })
    if latest[channel] is None:
        latest[channel] = version

if not versions:
    raise SystemExit("GitHub releases index has no usable release assets")
print(json.dumps({"product": "The333-BGP", "latest": latest, "versions": versions}, ensure_ascii=False))
'
}

select_install_version_json() {
  python3 -c '
import json
import sys

manifest = json.load(sys.stdin)
channel, target = sys.argv[1:]
if channel not in {"stable", "beta"}:
    raise SystemExit("THE333_CHANNEL must be stable or beta")
versions = manifest.get("versions", [])
if target:
    selected = next((item for item in versions if str(item.get("version")) == target), None)
else:
    latest = (manifest.get("latest") or {}).get(channel)
    selected = next((item for item in versions if str(item.get("version")) == str(latest)), None)
if not selected:
    raise SystemExit(f"no release found for channel={channel!r} version={target!r}")
print(json.dumps(selected, ensure_ascii=False))
' "${INSTALL_CHANNEL}" "${INSTALL_VERSION}"
}

extract_bootstrap_release() {
  local archive="$1"
  local destination="$2"
  python3 - "${archive}" "${destination}" <<'PY'
import os
import shutil
import sys
import tarfile
from pathlib import Path, PurePosixPath

archive_path = Path(sys.argv[1]).resolve()
destination = Path(sys.argv[2]).resolve()
destination.mkdir(parents=True, exist_ok=True)
if any(destination.iterdir()):
    raise SystemExit("release extraction directory must be empty")

with tarfile.open(archive_path, mode="r:gz") as archive:
    members = archive.getmembers()
    if len(members) > 20_000:
        raise SystemExit("release archive has too many members")
    roots = set()
    expanded = 0
    validated = []
    for member in members:
        if "\\" in member.name:
            raise SystemExit("release archive contains an unsafe path")
        path = PurePosixPath(member.name)
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            raise SystemExit("release archive contains an unsafe path")
        if not (member.isdir() or member.isfile()):
            raise SystemExit("release archive contains an unsupported member type")
        roots.add(path.parts[0])
        expanded += max(0, member.size)
        if expanded > 512 * 1024 * 1024:
            raise SystemExit("release archive exceeds the expanded size limit")
        validated.append((member, path.parts))
    if len(roots) != 1:
        raise SystemExit("release archive must contain one top-level directory")

    destination_root = destination.resolve()
    for member, parts in validated:
        if len(parts) == 1:
            continue
        target = destination.joinpath(*parts[1:])
        target.parent.resolve().relative_to(destination_root)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            os.chmod(target, 0o755)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        source = archive.extractfile(member)
        if source is None:
            raise SystemExit("release archive member cannot be read")
        with source, target.open("xb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)
PY
}

download_repo_if_needed() {
  if [[ -f "${SCRIPT_DIR}/docker/backend.Dockerfile" && -d "${SCRIPT_DIR}/app" && -d "${SCRIPT_DIR}/portal" ]]; then
    printf '%s\n' "${SCRIPT_DIR}"
    return 0
  fi

  need_cmd curl || fail "curl is required"
  local tmp index_file index_size manifest version_json archive_url sha archive archive_size
  tmp="$(mktemp -d)"
  log "Reading release index: ${RELEASE_INDEX_URL}"
  index_file="${tmp}/release-index.json"
  if ! curl \
    --fail \
    --silent \
    --show-error \
    --location \
    --proto '=https' \
    --proto-redir '=https' \
    --max-filesize 2097152 \
    "${RELEASE_INDEX_URL}" \
    --output "${index_file}"; then
    rm -rf "${tmp}"
    fail "release index download failed"
  fi
  index_size="$(wc -c < "${index_file}")"
  if (( index_size > 2097152 )); then
    rm -rf "${tmp}"
    fail "release index exceeds the 2 MB limit"
  fi
  if ! manifest="$(normalize_release_index < "${index_file}")"; then
    rm -rf "${tmp}"
    fail "release index validation failed"
  fi
  version_json="$(printf '%s' "${manifest}" | select_install_version_json)"
  archive_url="$(printf '%s' "${version_json}" | python3 -c 'import json,sys; print((json.load(sys.stdin).get("archive_url") or "").strip())')"
  sha="$(printf '%s' "${version_json}" | python3 -c 'import json,sys; print((json.load(sys.stdin).get("sha256") or "").strip())')"
  [[ "${archive_url}" == https://* ]] || fail "release archive URL must use HTTPS"
  [[ "${sha}" =~ ^[A-Fa-f0-9]{64}$ ]] || fail "release asset has no valid SHA-256 digest"
  archive="${tmp}/the333.tar.gz"
  log "Downloading verified release archive: ${archive_url}"
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
  if ! printf '%s  %s\n' "${sha}" "${archive}" | sha256sum -c - >/dev/null; then
    rm -rf "${tmp}"
    fail "release archive SHA-256 verification failed"
  fi
  mkdir -p "${tmp}/src"
  if ! extract_bootstrap_release "${archive}" "${tmp}/src"; then
    rm -rf "${tmp}"
    fail "release archive extraction failed"
  fi
  printf '%s\n' "${tmp}/src"
}

copy_project_files() {
  local src="$1"
  local preserve_config="${2:-false}"
  log "Step 3/7: Copying project files into ${PROJECT_DIR}."
  sudo_cmd mkdir -p "${PROJECT_DIR}"
  sudo_cmd chown "$(id -u):$(id -g)" "${PROJECT_DIR}"

  for item in app portal docs docker deploy extras requirements.in requirements.txt docker-compose.yml docker-compose.portal.yml docker-compose.tls.yml VERSION CHANGELOG.md README.md LICENSE SECURITY.md scripts update-manifest.json update-manifest.example.json .env.example .dockerignore .gitattributes .gitignore install.sh; do
    if [[ -e "${src}/${item}" ]]; then
      rm -rf "${PROJECT_DIR:?}/${item}"
      cp -a "${src}/${item}" "${PROJECT_DIR}/${item}"
    fi
  done

  rm -f "${PROJECT_DIR}/Dockerfile" "${PROJECT_DIR}/entrypoint.sh" "${PROJECT_DIR}/app/updater.py"

  if [[ "${preserve_config}" == "true" ]]; then
    mkdir -p "${PROJECT_DIR}/config"
    if [[ -d "${src}/config" ]]; then
      local cfg target
      for cfg in "${src}"/config/*; do
        [[ -e "${cfg}" ]] || continue
        target="${PROJECT_DIR}/config/$(basename "${cfg}")"
        cp -a "${cfg}" "${target}"
      done
    fi
    log "Файлы поставки config обновлены; неизвестные пользовательские файлы и data сохранены."
  else
    rm -rf "${PROJECT_DIR:?}/config"
    cp -a "${src}/config" "${PROJECT_DIR}/config"
  fi

  mkdir -p "${PROJECT_DIR}/data/gobgp-state" "${PROJECT_DIR}/data/secrets" "${PROJECT_DIR}/backups"
  if [[ ! -f "${PROJECT_DIR}/data/secrets/bgp_tcp_md5" ]]; then
    : > "${PROJECT_DIR}/data/secrets/bgp_tcp_md5"
  fi
  chmod 600 "${PROJECT_DIR}/data/secrets/bgp_tcp_md5"
  chmod +x \
    "${PROJECT_DIR}/docker/gobgp-entrypoint.sh" \
    "${PROJECT_DIR}/docker/backend-entrypoint.sh" \
    "${PROJECT_DIR}/scripts/host-updater.py" \
    "${PROJECT_DIR}/scripts/extract-release.py" \
    "${PROJECT_DIR}/scripts/migrate-env.py" \
    "${PROJECT_DIR}/scripts/the333bgp.sh" \
    "${PROJECT_DIR}/install.sh"
}

write_env() {
  log "Step 4/7: Creating .env with network, BGP and portal settings."
  local bind_ip local_as router_id nexthop peer_ip peer_as community bgp_peer_mode bgp_ttl_security_enabled web_user web_password web_password_hash update_url host_updater_token generated_password product_version
  bind_ip="$(ask_ipv4 "IP VM, на котором слушать портал/BGP" "${THE333_BIND_IP:-$(detect_ip || true)}" "THE333_BIND_IP")"
  [[ -n "${bind_ip}" ]] || bind_ip="0.0.0.0"
  local_as="$(ask_asn "ASN сервиса The333-BGP (private ASN: 64512-65534)" "${LOCAL_AS:-64512}" "LOCAL_AS")"
  router_id="$(ask_ipv4 "Router ID сервиса" "${ROUTER_ID:-${bind_ip}}" "ROUTER_ID")"
  nexthop="$(ask_ipv4 "BGP nexthop" "${BGP_NEXTHOP:-${bind_ip}}" "BGP_NEXTHOP")"
  peer_ip="$(ask_ipv4 "IP MikroTik/BGP peer" "${PEER_ADDRESS:-192.168.1.1}" "PEER_ADDRESS")"
  peer_as="$(ask_asn "ASN MikroTik/BGP peer" "${PEER_AS:-65455}" "PEER_AS")"
  community="$(ask_community "BGP community по умолчанию" "${BGP_COMMUNITY:-64512:500}")"
  bgp_peer_mode="${BGP_PEER_MODE:-$(detect_bgp_peer_mode "${peer_ip}")}"
  bgp_ttl_security_enabled="${BGP_TTL_SECURITY_ENABLED:-false}"
  case "${bgp_ttl_security_enabled,,}" in
    true|1|yes|on) bgp_ttl_security_enabled="true" ;;
    false|0|no|off) bgp_ttl_security_enabled="false" ;;
    *) fail "BGP_TTL_SECURITY_ENABLED must be true or false" ;;
  esac
  if [[ "${bgp_peer_mode}" == "direct" && "${NON_INTERACTIVE}" != "true" ]]; then
    log "GTSM requires matching TTL security on MikroTik; enabling it on only one side breaks the BGP session."
    if confirm "Включить GTSM/TTL security для прямого peer?" "N"; then
      bgp_ttl_security_enabled="true"
    else
      bgp_ttl_security_enabled="false"
    fi
  elif [[ "${bgp_peer_mode}" != "direct" ]]; then
    bgp_ttl_security_enabled="false"
  fi
  log "BGP peer mode: ${bgp_peer_mode}; GTSM: ${bgp_ttl_security_enabled}."
  web_user="admin"
  log "Портал запросит только пароль; внутренняя учётная запись admin фиксирована."
  web_password="$(ask_secret "Пароль портала. Enter = сгенерировать")"
  generated_password="false"
  if [[ -z "${web_password}" ]]; then
    if need_cmd openssl; then
      web_password="$(openssl rand -base64 24 | tr -d '\n')"
    else
      web_password="$(tr -dc 'A-Za-z0-9_@%+=:,.-' </dev/urandom | head -c 32)"
    fi
    generated_password="true"
  fi
  validate_password "${web_password}" || fail "portal password must be at least 8 characters"
  web_password_hash="$(make_password_hash "${web_password}")"

  update_url="${PRODUCT_UPDATE_MANIFEST_URL:-https://api.github.com/repos/The333tech/The333-bgp/releases?per_page=20}"
  host_updater_token="${HOST_UPDATER_TOKEN:-$(make_token)}"
  product_version="$(tr -d '[:space:]' < "${PROJECT_DIR}/VERSION" 2>/dev/null || true)"
  product_version="${product_version:-0.82.2b}"
  log "Update manifest: ${update_url}"

  if [[ -f "${PROJECT_DIR}/.env" ]]; then
    cp "${PROJECT_DIR}/.env" "${PROJECT_DIR}/.env.backup-$(date +%Y%m%d-%H%M%S)"
  fi

  cat > "${PROJECT_DIR}/.env" <<ENV
APP_NAME=The333-BGP
THE333_BIND_IP=${bind_ip}
PUID=$(id -u)
PGID=$(id -g)

LOCAL_AS=${local_as}
ROUTER_ID=${router_id}
BGP_NEXTHOP=${nexthop}
BGP_LISTEN_PORT=1179
GOBGP_CORE_IMAGE_VERSION=4.7.0-r5
PEER_ADDRESS=${peer_ip}
PEER_AS=${peer_as}
BGP_COMMUNITY=${community}
BGP_PEER_MODE=${bgp_peer_mode}
BGP_DOCKER_BRIDGE_HOPS=1
BGP_MULTIHOP_TTL=10
BGP_TTL_SECURITY_ENABLED=${bgp_ttl_security_enabled}
BGP_TTL_SECURITY_MIN=255
BGP_TCP_MD5_CONFIGURED=false
BGP_GRACEFUL_RESTART=true
BGP_GRACEFUL_RESTART_TIME=300
BGP_REJECT_INBOUND_ROUTES=true
GOBGP_RECOVERY_POLL_SECONDS=5

WEB_USER=${web_user}
WEB_PASSWORD=
WEB_PASSWORD_HASH=${web_password_hash}
AUTH_MAX_FAILURES=10
AUTH_WINDOW_SECONDS=300
AUTH_BLOCK_SECONDS=600
AUTH_ALLOW_BASIC=false
AUTH_TRUSTED_PROXY_CIDRS=172.16.0.0/12
SESSION_COOKIE_NAME=the333_session
SESSION_TTL_SECONDS=43200
SESSION_COOKIE_SECURE=false
SESSION_MAX_ACTIVE=8
PORTAL_TLS_ENABLED=false
PORTAL_TLS_CERT_PATH=/etc/the333-bgp/tls/portal.crt
PORTAL_TLS_KEY_PATH=/etc/the333-bgp/tls/portal.key
WEB_HOST=0.0.0.0
WEB_PORT=8088

AUTO_UPDATE=true
UPDATE_INTERVAL_SECONDS=21600
MAX_PREFIXES=30000
MIN_PREFIXES_TO_APPLY=1
MIN_EXPECTED_PREFIXES=0
MAX_DELTA_PERCENT=35
REMOTE_FETCH_MAX_BYTES=16777216
REMOTE_FETCH_MAX_REDIRECTS=5
REMOTE_FETCH_CACHE_GRACE_SECONDS=86400
AGGREGATE_PREFIXES=true

SERVICE_ROUTES_ENABLED=true
SERVICE_DNS_CACHE_GRACE_SECONDS=86400
SERVICE_GEOSITE_MAX_DOMAINS_PER_PROVIDER=100
SERVICE_GEOIP_MAX_PREFIXES_PER_PROVIDER=500

SYSTEM_BACKUP_RETENTION=20
SYSTEM_BACKUP_MAX_BYTES=134217728

PRODUCT_VERSION=${product_version}
PRODUCT_CHANNEL=${INSTALL_CHANNEL}
PRODUCT_UPDATE_MANIFEST_URL=${update_url}
PRODUCT_UPDATE_ENABLED=true
PRODUCT_UPDATE_MODE=host-updater
PRODUCT_UPDATE_TIMEOUT_SECONDS=1800
UPDATE_MIN_FREE_BYTES=2147483648
HOST_UPDATER_SOCKET=/run/the333-bgp/updater.sock
HOST_UPDATER_RUN_DIR=/run/the333-bgp
HOST_UPDATER_RESULT_DIR=${PROJECT_DIR}/data/host-updater-results
HOST_UPDATER_TOKEN=${host_updater_token}
ENV

  chmod 600 "${PROJECT_DIR}/.env"

  if [[ "${generated_password}" == "true" ]]; then
    printf '\n[install] Сгенерированный пароль портала: %s\n' "${web_password}"
    log "Сохрани пароль сейчас: в .env записан только необратимый хеш, повторно показать пароль нельзя."
  fi
}

install_host_updater_service() {
  need_cmd python3 || fail "python3 is required for host updater"
  need_cmd systemctl || fail "systemd is required for automatic portal updates"

  local template="${PROJECT_DIR}/deploy/systemd/the333-bgp-updater.service.in"
  local rendered project_gid
  [[ -f "${template}" ]] || fail "host updater systemd template is missing"
  rendered="$(mktemp)"
  project_gid="$(awk -F= '$1 == "PGID" {print $2; exit}' "${PROJECT_DIR}/.env" 2>/dev/null | tr -d '[:space:]')"
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

  sudo_cmd install -d -m 0750 /run/the333-bgp
  sudo_cmd install -m 0644 "${rendered}" /etc/systemd/system/the333-bgp-updater.service
  rm -f "${rendered}"
  sudo_cmd systemctl daemon-reload
  sudo_cmd systemctl enable --now the333-bgp-updater.service

  local attempts=20
  while (( attempts > 0 )); do
    [[ -S /run/the333-bgp/updater.sock ]] && return 0
    attempts=$((attempts - 1))
    sleep 1
  done

  sudo_cmd systemctl status --no-pager the333-bgp-updater.service || true
  fail "host updater socket did not become ready"
}

resume_backend_after_interrupted_fallback_backup() {
  local signal="$1"
  trap - INT TERM
  set +e
  if [[ "${backend_was_running:-false}" == "true" ]]; then
    log "Repair backup interrupted by ${signal}; starting backend before exit."
    docker_cli start the333-bgp-backend >/dev/null 2>&1 || true
  fi
  [[ -n "${archive:-}" ]] && rm -f "${archive}"
  [[ "${signal}" == "INT" ]] && exit 130
  exit 143
}

fallback_backup_existing_install() {
  mkdir -p "${PROJECT_DIR}/backups"
  local ts archive backend_was_running backup_rc restart_rc status
  ts="$(date +%Y%m%d-%H%M%S)"
  archive="${PROJECT_DIR}/backups/the333-bgp-before-installer-upgrade-${ts}.tar.gz"
  backend_was_running="false"
  backup_rc=0
  restart_rc=0

  if docker_cli container inspect the333-bgp-backend >/dev/null 2>&1 \
    && [[ "$(docker_cli inspect -f '{{.State.Running}}' the333-bgp-backend 2>/dev/null || true)" == "true" ]]; then
    log "Pausing backend briefly for a consistent repair backup; GoBGP remains online."
    docker_cli stop --time 30 the333-bgp-backend >/dev/null \
      || fail "backend could not be paused for a consistent repair backup"
    backend_was_running="true"
    trap 'resume_backend_after_interrupted_fallback_backup INT' INT
    trap 'resume_backend_after_interrupted_fallback_backup TERM' TERM
  fi

  log "Creating fallback backup: ${archive}"
  if tar \
    --exclude='./backups' \
    --exclude='./portal/node_modules' \
    --exclude='./portal/dist' \
    --exclude='./.git' \
    --exclude='./.venv' \
    -czf "${archive}" \
    -C "${PROJECT_DIR}" \
    .; then
    backup_rc=0
  else
    backup_rc=$?
  fi

  if [[ "${backend_was_running}" == "true" ]]; then
    log "Starting backend after the repair snapshot."
    if ! docker_cli start the333-bgp-backend >/dev/null; then
      restart_rc=1
    else
      for _ in $(seq 1 60); do
        status="$(docker_cli inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' the333-bgp-backend 2>/dev/null || true)"
        [[ "${status}" == "healthy" || "${status}" == "running" ]] && break
        if [[ "${status}" == "unhealthy" || "${status}" == "exited" || "${status}" == "dead" ]]; then
          restart_rc=1
          break
        fi
        sleep 2
      done
      [[ "${status}" == "healthy" || "${status}" == "running" ]] || restart_rc=1
    fi
  fi
  trap - INT TERM

  if (( restart_rc != 0 )); then
    rm -f "${archive}"
    fail "backend did not recover after the repair snapshot; GoBGP was not restarted"
  fi
  if (( backup_rc != 0 )); then
    rm -f "${archive}"
    fail "fallback backup failed; backend was restored and repair was not started"
  fi
  [[ -s "${archive}" ]] || fail "fallback backup failed"
  tar -tzf "${archive}" >/dev/null || fail "fallback backup integrity check failed"
  log "Fallback backup ready: ${archive}"
}

ensure_env_defaults() {
  [[ -f "${PROJECT_DIR}/.env" ]] || fail ".env not found in existing installation"

  local product_version update_url backup_env
  product_version="$(tr -d '[:space:]' < "${PROJECT_DIR}/VERSION" 2>/dev/null || true)"
  product_version="${product_version:-0.82.2b}"
  update_url="${PRODUCT_UPDATE_MANIFEST_URL:-$(awk -F= '$1 == "PRODUCT_UPDATE_MANIFEST_URL" {print $2; exit}' "${PROJECT_DIR}/.env" | tr -d '[:space:]')}"
  update_url="${update_url:-https://api.github.com/repos/The333tech/The333-bgp/releases?per_page=20}"

  backup_env="${PROJECT_DIR}/.env.backup-before-v082b-env-migration-$(date +%Y%m%d-%H%M%S)"
  cp "${PROJECT_DIR}/.env" "${backup_env}"
  chmod 600 "${backup_env}"

  python3 "${PROJECT_DIR}/scripts/migrate-env.py" \
    --env "${PROJECT_DIR}/.env" \
    --project-dir "${PROJECT_DIR}" \
    --version "${product_version}" \
    --channel "${INSTALL_CHANNEL}" \
    --update-url "${update_url}"
  chmod 600 "${PROJECT_DIR}/.env"
  log ".env migrated for v${product_version}; previous copy: ${backup_env}"
}

wait_for_services() {
  local bind_ip="$1"
  local attempts=72

  log "Ожидание готовности backend, GoBGP и портала (до 6 минут)..."
  while (( attempts > 0 )); do
    if backend_health_ready "${bind_ip}" && portal_http_ready "${bind_ip}"; then
      log "Все сервисы готовы."
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 5
  done

  docker_compose ps || true
  fail "services did not become ready within 6 minutes; inspect container logs"
}

portal_http_ready() {
  local bind_ip="$1"
  local tls_enabled
  tls_enabled="$(awk -F= '$1 == "PORTAL_TLS_ENABLED" {print tolower($2); exit}' "${PROJECT_DIR}/.env" 2>/dev/null | tr -d '[:space:]')"
  case "${tls_enabled}" in
    1|true|yes|on)
      curl -kfsS -o /dev/null "https://${bind_ip}:8090/" 2>/dev/null
      ;;
    *)
      curl -fsS -o /dev/null "http://${bind_ip}:8090/" 2>/dev/null
      ;;
  esac
}

backend_health_ready() {
  local bind_ip="$1"
  local tmp status rc
  tmp="$(mktemp)"
  status="$(curl -sS -o "${tmp}" -w '%{http_code}' "http://${bind_ip}:8088/health" 2>/dev/null || true)"
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
  return "${rc}"
}

first_install() {
  local src="$1"
  check_project_dir_safety
  check_basic_tools
  check_os
  check_resources
  show_firewall_notice
  ensure_docker
  log "Step 2/7: Checking required ports 179, 8088 and 8090."
  check_ports

  if [[ "${DRY_RUN}" == "true" ]]; then
    log "Dry-run complete. No files were copied and no containers were changed."
    return 0
  fi

  copy_project_files "${src}" "false"
  write_env
  log "Step 5/7: Installing isolated host updater service."
  install_host_updater_service

  cd "${PROJECT_DIR}"
  log "Step 6/7: Building Docker images."
  docker_compose build
  log "Step 7/7: Starting The333-BGP containers."
  docker_compose up -d --remove-orphans
  wait_for_services "$(awk -F= '$1 == "THE333_BIND_IP" {print $2; exit}' .env)"
  "${PROJECT_DIR}/scripts/the333bgp.sh" status
}

existing_install_flow() {
  log "Existing The333-BGP installation detected in ${PROJECT_DIR}"
  local control="${PROJECT_DIR}/scripts/the333bgp.sh"
  local action="${INSTALL_ACTION:-}"

  check_project_dir_safety
  check_basic_tools
  ensure_docker

  if [[ -x "${control}" ]]; then
    "${control}" status || true
  fi

  if [[ "${DRY_RUN}" == "true" ]]; then
    log "Dry-run: existing installation found. No changes were made."
    return 0
  fi

  if [[ -z "${action}" && "${NON_INTERACTIVE}" == "true" ]]; then
    "${control}" status
    return 0
  fi

  if [[ -z "${action}" ]]; then
    require_tty
    read -r -p "Что сделать? [u]pdate / [b]ackup / [r]epair / [s]tatus / [q]uit: " action </dev/tty
  fi

  case "${action}" in
    u|U|update|UPDATE)
      local src update_controller
      src="$(download_repo_if_needed)"
      update_controller="${src}/scripts/the333bgp.sh"
      [[ -f "${update_controller}" ]] || fail "verified release has no update controller. Use repair instead."
      local update_args=(update --manifest "${RELEASE_INDEX_URL}" --channel "${INSTALL_CHANNEL}" --non-interactive)
      if [[ -n "${INSTALL_VERSION}" ]]; then
        update_args+=(--version "${INSTALL_VERSION}")
      fi
      THE333_PROJECT_DIR="${PROJECT_DIR}" bash "${update_controller}" "${update_args[@]}"
      ;;
    b|B|backup|BACKUP)
      "${control}" backup
      ;;
    r|R|repair|REPAIR)
      local src
      src="$(download_repo_if_needed)"
      if [[ -x "${control}" ]]; then
        "${control}" backup || fallback_backup_existing_install
      else
        fallback_backup_existing_install
      fi
      copy_project_files "${src}" "true"
      ensure_env_defaults
      install_host_updater_service
      cd "${PROJECT_DIR}"
      docker_compose build
      docker_compose up -d --remove-orphans
      wait_for_services "$(awk -F= '$1 == "THE333_BIND_IP" {print $2; exit}' .env)"
      "${PROJECT_DIR}/scripts/the333bgp.sh" status
      ;;
    s|S|status|STATUS)
      "${control}" status
      ;;
    q|Q|quit|QUIT)
      log "Cancelled"
      ;;
    *)
      fail "unknown action: ${action}. Use update, repair, backup, status or quit"
      ;;
  esac
}

main() {
  parse_args "$@"
  validate_release_selection
  check_basic_tools

  if [[ -f "${PROJECT_DIR}/docker-compose.yml" && -f "${PROJECT_DIR}/.env" ]]; then
    existing_install_flow
    return 0
  fi

  local src
  src="$(download_repo_if_needed)"
  first_install "${src}"
}

main "$@"
