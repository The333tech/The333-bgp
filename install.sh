#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${THE333_PROJECT_DIR:-/opt/the333-bgp}"
REPO_SLUG="${THE333_REPO_SLUG:-The333tech/The333-bgp}"
BRANCH="${THE333_BRANCH:-main}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DRY_RUN="false"
NON_INTERACTIVE="false"

usage() {
  cat <<'USAGE'
Usage:
  install.sh [--dry-run] [--non-interactive]

Environment for --non-interactive:
  THE333_PROJECT_DIR=/opt/the333-bgp
  THE333_BIND_IP=192.168.1.111
  LOCAL_AS=64500
  ROUTER_ID=192.168.1.111
  BGP_NEXTHOP=192.168.1.111
  PEER_ADDRESS=192.168.1.1
  PEER_AS=65455
  BGP_COMMUNITY=65432:500
  WEB_PASSWORD=strong-password
  PRODUCT_UPDATE_MANIFEST_URL=
USAGE
}

log() {
  printf '[install] %s\n' "$*"
}

fail() {
  printf '[install] ERROR: %s\n' "$*" >&2
  exit 1
}

ask() {
  local prompt="$1"
  local default="$2"
  local value
  read -r -p "${prompt} [${default}]: " value
  printf '%s\n' "${value:-$default}"
}

ask_secret() {
  local prompt="$1"
  local value
  if [[ "${NON_INTERACTIVE}" == "true" ]]; then
    printf '%s\n' "${WEB_PASSWORD:-}"
    return 0
  fi
  read -r -s -p "${prompt}: " value
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
  read -r -p "${prompt} [${default}]: " answer
  answer="${answer:-$default}"
  [[ "${answer}" =~ ^[YyДд]$ ]]
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || return 1
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

validate_password() {
  local value="$1"
  [[ "${#value}" -ge 8 ]]
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
  for cmd in awk curl tar grep sed ip python3; do
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
    log "Warning: RAM looks low ($((mem_kb / 1024)) MB). Recommended minimum: 1 GB, better 2 GB+."
  fi

  if [[ -n "${disk_kb}" && "${disk_kb}" =~ ^[0-9]+$ && "${disk_kb}" -lt 5242880 ]]; then
    log "Warning: free disk space looks low ($((disk_kb / 1024)) MB). Recommended minimum: 5 GB."
  fi
}

check_project_dir_safety() {
  case "${PROJECT_DIR}" in
    ""|"/"|"/root"|"/home"|"/opt"|"/usr"|"/var")
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
    log "Step 1/6: Docker and Docker Compose plugin are already installed. Skipping Docker installation."
    return 0
  fi

  log "Step 1/6: Docker or Docker Compose plugin not found."
  if [[ "${NON_INTERACTIVE}" == "true" ]]; then
    fail "Docker is required. Install Docker first, or rerun installer interactively to allow apt installation."
  fi

  read -r -p "Попробовать установить Docker через apt? [y/N]: " answer
  [[ "${answer}" =~ ^[Yy]$ ]] || fail "Docker is required"

  need_cmd apt-get || fail "automatic Docker install supports apt-based systems only"
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
}

download_repo_if_needed() {
  if [[ -f "${SCRIPT_DIR}/Dockerfile" && -d "${SCRIPT_DIR}/app" && -d "${SCRIPT_DIR}/portal" ]]; then
    printf '%s\n' "${SCRIPT_DIR}"
    return 0
  fi

  need_cmd curl || fail "curl is required"
  local tmp archive_url archive
  tmp="$(mktemp -d)"
  archive_url="https://github.com/${REPO_SLUG}/archive/refs/heads/${BRANCH}.tar.gz"
  archive="${tmp}/the333.tar.gz"
  log "Downloading project archive: ${archive_url}"
  curl -fL "${archive_url}" -o "${archive}"
  mkdir -p "${tmp}/src"
  tar -xzf "${archive}" -C "${tmp}/src" --strip-components=1
  printf '%s\n' "${tmp}/src"
}

copy_project_files() {
  local src="$1"
  log "Step 3/6: Copying project files into ${PROJECT_DIR}."
  sudo_cmd mkdir -p "${PROJECT_DIR}"
  sudo_cmd chown "$(id -u):$(id -g)" "${PROJECT_DIR}"

  for item in app portal config docs Dockerfile entrypoint.sh requirements.txt docker-compose.yml docker-compose.portal.yml VERSION CHANGELOG.md LICENSE SECURITY.md scripts update-manifest.example.json .env.example .gitignore install.sh; do
    if [[ -e "${src}/${item}" ]]; then
      rm -rf "${PROJECT_DIR:?}/${item}"
      cp -a "${src}/${item}" "${PROJECT_DIR}/${item}"
    fi
  done

  mkdir -p "${PROJECT_DIR}/data" "${PROJECT_DIR}/backups"
  chmod +x "${PROJECT_DIR}/entrypoint.sh" "${PROJECT_DIR}/scripts/the333bgp.sh" "${PROJECT_DIR}/install.sh"
}

write_env() {
  log "Step 4/6: Creating .env with network, BGP and portal settings."
  local bind_ip local_as router_id nexthop peer_ip peer_as community web_user web_password web_password_hash update_url
  bind_ip="$(ask_ipv4 "IP VM, на котором слушать портал/BGP" "${THE333_BIND_IP:-$(detect_ip || true)}" "THE333_BIND_IP")"
  [[ -n "${bind_ip}" ]] || bind_ip="0.0.0.0"
  local_as="$(ask_asn "ASN сервиса The333-BGP" "${LOCAL_AS:-64500}" "LOCAL_AS")"
  router_id="$(ask_ipv4 "Router ID сервиса" "${ROUTER_ID:-${bind_ip}}" "ROUTER_ID")"
  nexthop="$(ask_ipv4 "BGP nexthop" "${BGP_NEXTHOP:-${bind_ip}}" "BGP_NEXTHOP")"
  peer_ip="$(ask_ipv4 "IP MikroTik/BGP peer" "${PEER_ADDRESS:-192.168.1.1}" "PEER_ADDRESS")"
  peer_as="$(ask_asn "ASN MikroTik/BGP peer" "${PEER_AS:-65455}" "PEER_AS")"
  if [[ "${NON_INTERACTIVE}" == "true" ]]; then
    community="${BGP_COMMUNITY:-65432:500}"
  else
    community="$(ask "BGP community по умолчанию" "${BGP_COMMUNITY:-65432:500}")"
  fi
  web_user="admin"
  log "Portal admin user is fixed: admin. The installer will ask only for the password."
  web_password="$(ask_secret "Пароль портала. Enter = сгенерировать")"
  if [[ -z "${web_password}" ]]; then
    if need_cmd openssl; then
      web_password="$(openssl rand -base64 24 | tr -d '\n')"
    else
      web_password="$(tr -dc 'A-Za-z0-9_@%+=:,.-' </dev/urandom | head -c 32)"
    fi
    log "Сгенерирован пароль портала. Сохрани его из файла .env."
  fi
  validate_password "${web_password}" || fail "portal password must be at least 8 characters"
  web_password_hash="$(make_password_hash "${web_password}")"

  if [[ "${NON_INTERACTIVE}" == "true" ]]; then
    update_url="${PRODUCT_UPDATE_MANIFEST_URL:-}"
  else
    update_url="$(ask "GitHub update manifest URL. Можно оставить пустым до публикации GitHub" "${PRODUCT_UPDATE_MANIFEST_URL:-}")"
  fi

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
PEER_ADDRESS=${peer_ip}
PEER_AS=${peer_as}
BGP_COMMUNITY=${community}

WEB_USER=${web_user}
WEB_PASSWORD=
WEB_PASSWORD_HASH=${web_password_hash}
AUTH_MAX_FAILURES=10
AUTH_WINDOW_SECONDS=300
AUTH_BLOCK_SECONDS=600
WEB_HOST=0.0.0.0
WEB_PORT=8088

AUTO_UPDATE=true
UPDATE_INTERVAL_SECONDS=21600
MAX_PREFIXES=30000
MIN_PREFIXES_TO_APPLY=1
MIN_EXPECTED_PREFIXES=0
MAX_DELTA_PERCENT=0
AGGREGATE_PREFIXES=true

SERVICE_ROUTES_ENABLED=true
SERVICE_DNS_CACHE_GRACE_SECONDS=86400
SERVICE_GEOSITE_MAX_DOMAINS_PER_PROVIDER=100
SERVICE_GEOIP_MAX_PREFIXES_PER_PROVIDER=500

PRODUCT_VERSION=0.1
PRODUCT_CHANNEL=stable
PRODUCT_UPDATE_MANIFEST_URL=${update_url}
PRODUCT_UPDATE_ENABLED=false
PRODUCT_UPDATE_COMMAND=${PROJECT_DIR}/scripts/the333bgp.sh update --non-interactive
ENV

  chmod 600 "${PROJECT_DIR}/.env"
}

first_install() {
  local src="$1"
  check_project_dir_safety
  check_basic_tools
  check_os
  check_resources
  show_firewall_notice
  ensure_docker
  log "Step 2/6: Checking required ports 179, 8088 and 8090."
  check_ports

  if [[ "${DRY_RUN}" == "true" ]]; then
    log "Dry-run complete. No files were copied and no containers were changed."
    return 0
  fi

  copy_project_files "${src}"
  write_env

  cd "${PROJECT_DIR}"
  log "Step 5/6: Building Docker images."
  docker compose -f docker-compose.yml -f docker-compose.portal.yml build
  log "Step 6/6: Starting The333-BGP containers."
  docker compose -f docker-compose.yml -f docker-compose.portal.yml up -d
  log "Waiting for services..."
  sleep 8
  "${PROJECT_DIR}/scripts/the333bgp.sh" status
}

existing_install_flow() {
  log "Existing The333-BGP installation detected in ${PROJECT_DIR}"
  local control="${PROJECT_DIR}/scripts/the333bgp.sh"

  if [[ -x "${control}" ]]; then
    "${control}" status || true
  fi

  if [[ "${DRY_RUN}" == "true" ]]; then
    log "Dry-run: existing installation found. No changes were made."
    return 0
  fi

  if [[ "${NON_INTERACTIVE}" == "true" ]]; then
    "${control}" status
    return 0
  fi

  read -r -p "Что сделать? [u]pdate / [b]ackup / [r]epair / [s]tatus / [q]uit: " action
  case "${action}" in
    u|U)
      "${control}" update
      ;;
    b|B)
      "${control}" backup
      ;;
    r|R)
      local src
      src="$(download_repo_if_needed)"
      "${control}" backup
      copy_project_files "${src}"
      cd "${PROJECT_DIR}"
      docker compose -f docker-compose.yml -f docker-compose.portal.yml build
      docker compose -f docker-compose.yml -f docker-compose.portal.yml up -d
      "${control}" status
      ;;
    s|S)
      "${control}" status
      ;;
    *)
      log "Cancelled"
      ;;
  esac
}

main() {
  parse_args "$@"

  if [[ -f "${PROJECT_DIR}/docker-compose.yml" && -f "${PROJECT_DIR}/.env" ]]; then
    existing_install_flow
    return 0
  fi

  local src
  src="$(download_repo_if_needed)"
  first_install "${src}"
}

main "$@"
