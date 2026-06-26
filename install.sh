#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${THE333_PROJECT_DIR:-/opt/the333-bgp}"
REPO_SLUG="${THE333_REPO_SLUG:-The333tech/The333-bgp}"
BRANCH="${THE333_BRANCH:-main}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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
  read -r -s -p "${prompt}: " value
  printf '\n' >&2
  printf '%s\n' "${value}"
}

confirm() {
  local prompt="$1"
  local default="${2:-N}"
  local answer
  read -r -p "${prompt} [${default}]: " answer
  answer="${answer:-$default}"
  [[ "${answer}" =~ ^[YyДд]$ ]]
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || return 1
}

sudo_cmd() {
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

ask_ipv4() {
  local prompt="$1"
  local default="$2"
  local value
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
  local value
  while true; do
    value="$(ask "${prompt}" "${default}")"
    if validate_asn "${value}"; then
      printf '%s\n' "${value}"
      return 0
    fi
    log "Некорректный ASN: ${value}. Диапазон: 1..4294967295"
  done
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
  log "Если это старая установка The333-BGP, запусти install.sh ещё раз с тем же PROJECT_DIR: будет предложено обновление."
  confirm "Продолжить установку несмотря на занятые порты?" "N" || fail "ports are busy: ${busy[*]}"
}

ensure_docker() {
  if need_cmd docker && docker compose version >/dev/null 2>&1; then
    log "Step 1/6: Docker and Docker Compose plugin are already installed. Skipping Docker installation."
    return 0
  fi

  log "Step 1/6: Docker or Docker Compose plugin not found."
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
  local bind_ip local_as router_id nexthop peer_ip peer_as community web_user web_password update_url
  bind_ip="$(ask_ipv4 "IP VM, на котором слушать портал/BGP" "$(detect_ip || true)")"
  [[ -n "${bind_ip}" ]] || bind_ip="0.0.0.0"
  local_as="$(ask_asn "ASN сервиса The333-BGP" "64500")"
  router_id="$(ask_ipv4 "Router ID сервиса" "${bind_ip}")"
  nexthop="$(ask_ipv4 "BGP nexthop" "${bind_ip}")"
  peer_ip="$(ask_ipv4 "IP MikroTik/BGP peer" "192.168.1.1")"
  peer_as="$(ask_asn "ASN MikroTik/BGP peer" "65455")"
  community="$(ask "BGP community по умолчанию" "65432:500")"
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
  update_url="$(ask "GitHub update manifest URL. Можно оставить пустым до публикации GitHub" "")"

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
WEB_PASSWORD=${web_password}
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
  ensure_docker
  log "Step 2/6: Checking required ports 179, 8088 and 8090."
  check_ports
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

  read -r -p "Что сделать? [u]pdate / [b]ackup / [s]tatus / [q]uit: " action
  case "${action}" in
    u|U)
      "${control}" update
      ;;
    b|B)
      "${control}" backup
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
  if [[ -f "${PROJECT_DIR}/docker-compose.yml" && -f "${PROJECT_DIR}/.env" ]]; then
    existing_install_flow
    return 0
  fi

  local src
  src="$(download_repo_if_needed)"
  first_install "${src}"
}

main "$@"
