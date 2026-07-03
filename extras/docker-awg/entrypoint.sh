#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG_DIR="${AWG_CONFIG_DIR:-/etc/amnezia/amneziawg}"
ENABLE_IPV6_NAT="${AWG_ENABLE_IPV6_NAT:-false}"
declare -a STARTED_INTERFACES=()
declare -a STARTED_CONFIGS=()

log() {
  printf '[docker-awg] %s\n' "$*"
}

fail() {
  printf '[docker-awg] ERROR: %s\n' "$*" >&2
  exit 1
}

remove_nat() {
  local interface="$1"
  iptables -t nat -D POSTROUTING -o "${interface}" -j MASQUERADE >/dev/null 2>&1 || true
  if [[ "${ENABLE_IPV6_NAT}" == "true" ]]; then
    ip6tables -t nat -D POSTROUTING -o "${interface}" -j MASQUERADE >/dev/null 2>&1 || true
  fi
}

cleanup() {
  local index
  for ((index=${#STARTED_CONFIGS[@]} - 1; index >= 0; index--)); do
    remove_nat "${STARTED_INTERFACES[index]}"
    awg-quick down "${STARTED_CONFIGS[index]}" >/dev/null 2>&1 || true
  done
}

self_test() {
  command -v amneziawg-go >/dev/null
  command -v awg >/dev/null
  command -v awg-quick >/dev/null
  command -v iptables >/dev/null
  command -v ip6tables >/dev/null
  test -r /usr/share/licenses/amneziawg-go/LICENSE
  test -r /usr/share/licenses/amneziawg-tools/COPYING
  awg --version
  amneziawg-go --version
  log "Self-test passed."
}

if [[ "${1:-}" == "--self-test" ]]; then
  self_test
  exit 0
fi

[[ "${EUID}" -eq 0 ]] || fail "container must run as root to create the tunnel and NAT rules"
[[ -d "${CONFIG_DIR}" ]] || fail "configuration directory does not exist: ${CONFIG_DIR}"

shopt -s nullglob
configs=("${CONFIG_DIR}"/*.conf)
(( ${#configs[@]} > 0 )) || fail "no .conf files found in ${CONFIG_DIR}"

sysctl -w net.ipv4.ip_forward=1 >/dev/null || fail "cannot enable IPv4 forwarding"
if [[ "${ENABLE_IPV6_NAT}" == "true" ]]; then
  sysctl -w net.ipv6.conf.all.forwarding=1 >/dev/null || fail "cannot enable IPv6 forwarding"
fi

trap cleanup EXIT
trap 'exit 0' INT TERM

for config in "${configs[@]}"; do
  grep -Eq '^[[:space:]]*\[Interface\][[:space:]]*$' "${config}" \
    || fail "invalid AmneziaWG configuration: missing [Interface] in $(basename "${config}")"
  grep -Eq '^[[:space:]]*\[Peer\][[:space:]]*$' "${config}" \
    || fail "invalid AmneziaWG configuration: missing [Peer] in $(basename "${config}")"

  interface="$(basename "${config}" .conf)"
  log "Starting interface ${interface}."
  awg-quick up "${config}"

  iptables -t nat -C POSTROUTING -o "${interface}" -j MASQUERADE >/dev/null 2>&1 \
    || iptables -t nat -A POSTROUTING -o "${interface}" -j MASQUERADE
  if [[ "${ENABLE_IPV6_NAT}" == "true" ]]; then
    ip6tables -t nat -C POSTROUTING -o "${interface}" -j MASQUERADE >/dev/null 2>&1 \
      || ip6tables -t nat -A POSTROUTING -o "${interface}" -j MASQUERADE
  fi

  STARTED_INTERFACES+=("${interface}")
  STARTED_CONFIGS+=("${config}")
done

log "Ready: ${#STARTED_INTERFACES[@]} interface(s) active."
while :; do
  sleep 86400 &
  wait "$!" || true
done
