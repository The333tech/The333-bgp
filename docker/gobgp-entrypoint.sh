#!/bin/sh
set -eu

: "${LOCAL_AS:=64512}"
: "${ROUTER_ID:=127.0.0.1}"
: "${BGP_LISTEN_PORT:=1179}"
: "${PEER_ADDRESS:=192.168.1.1}"
: "${PEER_AS:=65455}"
: "${BGP_PEER_MODE:=direct}"
: "${BGP_DOCKER_BRIDGE_HOPS:=1}"
: "${BGP_MULTIHOP_TTL:=10}"
: "${BGP_TTL_SECURITY_ENABLED:=false}"
: "${BGP_TTL_SECURITY_MIN:=255}"
: "${BGP_TCP_MD5_KEY:=}"
: "${BGP_TCP_MD5_KEY_FILE:=/run/secrets/bgp_tcp_md5}"
: "${BGP_GRACEFUL_RESTART:=true}"
: "${BGP_GRACEFUL_RESTART_TIME:=300}"
: "${BGP_REJECT_INBOUND_ROUTES:=true}"
: "${GOBGP_CONFIG_VALIDATE_ONLY:=false}"
: "${GOBGP_STATE_DIR:=/state}"

if [ -f "${BGP_TCP_MD5_KEY_FILE}" ]; then
  IFS= read -r BGP_TCP_MD5_KEY < "${BGP_TCP_MD5_KEY_FILE}" || BGP_TCP_MD5_KEY=""
fi

CONFIG_FILE=/tmp/gobgpd.toml
CONFIG_TMP=/tmp/gobgpd.toml.tmp

fail() {
  printf '[gobgp] ERROR: %s\n' "$*" >&2
  exit 1
}

is_uint() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
    *) return 0 ;;
  esac
}

validate_uint_range() {
  is_uint "$2" || fail "$1 must be an unsigned integer"
  [ "$2" -ge "$3" ] 2>/dev/null || fail "$1 must be >= $3"
  [ "$2" -le "$4" ] 2>/dev/null || fail "$1 must be <= $4"
}

validate_ipv4() {
  if ! printf '%s\n' "$2" | awk -F. '
    NF != 4 { exit 1 }
    {
      for (i = 1; i <= 4; i++) {
        if ($i !~ /^[0-9]+$/ || $i < 0 || $i > 255) exit 1
      }
    }
  '; then
    fail "$1 must be an IPv4 address"
  fi
}

validate_boolean() {
  case "$2" in
    true|false) ;;
    *) fail "$1 must be true or false" ;;
  esac
}

validate_uint_range LOCAL_AS "${LOCAL_AS}" 1 4294967295
validate_uint_range PEER_AS "${PEER_AS}" 1 4294967295
validate_uint_range BGP_LISTEN_PORT "${BGP_LISTEN_PORT}" 1 65535
validate_uint_range BGP_DOCKER_BRIDGE_HOPS "${BGP_DOCKER_BRIDGE_HOPS}" 0 254
validate_uint_range BGP_MULTIHOP_TTL "${BGP_MULTIHOP_TTL}" 1 255
validate_uint_range BGP_TTL_SECURITY_MIN "${BGP_TTL_SECURITY_MIN}" 1 255
validate_uint_range BGP_GRACEFUL_RESTART_TIME "${BGP_GRACEFUL_RESTART_TIME}" 1 4095
validate_ipv4 ROUTER_ID "${ROUTER_ID}"
validate_ipv4 PEER_ADDRESS "${PEER_ADDRESS}"
validate_boolean BGP_GRACEFUL_RESTART "${BGP_GRACEFUL_RESTART}"
validate_boolean BGP_REJECT_INBOUND_ROUTES "${BGP_REJECT_INBOUND_ROUTES}"
validate_boolean GOBGP_CONFIG_VALIDATE_ONLY "${GOBGP_CONFIG_VALIDATE_ONLY}"
validate_boolean BGP_TTL_SECURITY_ENABLED "${BGP_TTL_SECURITY_ENABLED}"
case "${BGP_PEER_MODE}" in
  direct|multihop) ;;
  *) fail "BGP_PEER_MODE must be direct or multihop" ;;
esac
if [ -n "${BGP_TCP_MD5_KEY}" ]; then
  [ "${#BGP_TCP_MD5_KEY}" -le 80 ] || fail "BGP_TCP_MD5_KEY must be at most 80 characters"
  case "${BGP_TCP_MD5_KEY}" in
    *[!A-Za-z0-9._+-]*) fail "BGP_TCP_MD5_KEY contains unsupported characters" ;;
  esac
fi
case "${GOBGP_STATE_DIR}" in
  /*) ;;
  *) fail "GOBGP_STATE_DIR must be an absolute path" ;;
esac
mkdir -p "${GOBGP_STATE_DIR}"

cat > "${CONFIG_TMP}" <<CFG
[global.config]
  as = ${LOCAL_AS}
  router-id = "${ROUTER_ID}"
  port = ${BGP_LISTEN_PORT}
CFG

if [ "${BGP_REJECT_INBOUND_ROUTES}" = "true" ]; then
  cat >> "${CONFIG_TMP}" <<'CFG'

[global.apply-policy.config]
  import-policy-list = ["the333-inbound-guard"]
  default-import-policy = "reject-route"
  default-export-policy = "accept-route"
CFG
fi

cat >> "${CONFIG_TMP}" <<CFG

[[neighbors]]
  [neighbors.config]
    neighbor-address = "${PEER_ADDRESS}"
    peer-as = ${PEER_AS}
    admin-down = true
CFG

if [ -n "${BGP_TCP_MD5_KEY}" ]; then
  cat >> "${CONFIG_TMP}" <<CFG
    auth-password = "${BGP_TCP_MD5_KEY}"
CFG
fi

if [ "${BGP_PEER_MODE}" = "multihop" ]; then
  cat >> "${CONFIG_TMP}" <<CFG
  [neighbors.ebgp-multihop.config]
    enabled = true
    multihop-ttl = ${BGP_MULTIHOP_TTL}
CFG
elif [ "${BGP_TTL_SECURITY_ENABLED}" = "true" ]; then
  cat >> "${CONFIG_TMP}" <<CFG
  [neighbors.ttl-security.config]
    enabled = true
    ttl-min = ${BGP_TTL_SECURITY_MIN}
CFG
elif [ "${BGP_DOCKER_BRIDGE_HOPS}" -gt 0 ]; then
  direct_transport_ttl=$((BGP_DOCKER_BRIDGE_HOPS + 1))
  cat >> "${CONFIG_TMP}" <<CFG
  # The peer is directly connected to the Docker host. GoBGP needs one extra
  # TTL hop for each bridge namespace between this container and the LAN.
  [neighbors.ebgp-multihop.config]
    enabled = true
    multihop-ttl = ${direct_transport_ttl}
CFG
fi

cat >> "${CONFIG_TMP}" <<CFG
  [[neighbors.afi-safis]]
    [neighbors.afi-safis.config]
      afi-safi-name = "ipv4-unicast"
CFG

if [ "${BGP_GRACEFUL_RESTART}" = "true" ]; then
  cat >> "${CONFIG_TMP}" <<CFG

  [neighbors.graceful-restart.config]
    enabled = true
    restart-time = ${BGP_GRACEFUL_RESTART_TIME}

    [neighbors.afi-safis.mp-graceful-restart.config]
      enabled = true
CFG
fi

if [ "${BGP_REJECT_INBOUND_ROUTES}" = "true" ]; then
  cat >> "${CONFIG_TMP}" <<'CFG'

[[policy-definitions]]
  name = "the333-inbound-guard"
  [[policy-definitions.statements]]
    name = "accept-local"
    [policy-definitions.statements.conditions.bgp-conditions]
      route-type = "local"
    [policy-definitions.statements.actions]
      route-disposition = "accept-route"
  [[policy-definitions.statements]]
    name = "reject-non-local"
    [policy-definitions.statements.actions]
      route-disposition = "reject-route"
CFG
fi

chmod 600 "${CONFIG_TMP}"
mv -f "${CONFIG_TMP}" "${CONFIG_FILE}"
rm -f "${GOBGP_STATE_DIR}/gobgpd.toml"

gobgpd -d -f "${CONFIG_FILE}" || fail "generated GoBGP configuration is invalid"
if [ "${GOBGP_CONFIG_VALIDATE_ONLY}" = "true" ]; then
  printf '[gobgp] Configuration is valid\n'
  exit 0
fi

cat /proc/sys/kernel/random/uuid > "${GOBGP_STATE_DIR}/gobgp_generation.tmp"
chmod 600 "${GOBGP_STATE_DIR}/gobgp_generation.tmp"
mv -f "${GOBGP_STATE_DIR}/gobgp_generation.tmp" "${GOBGP_STATE_DIR}/gobgp_generation"

printf '[gobgp] Starting gobgpd with graceful-restart=%s...\n' "${BGP_GRACEFUL_RESTART}"
if [ "${BGP_GRACEFUL_RESTART}" = "true" ]; then
  exec gobgpd -f "${CONFIG_FILE}" -r
fi
exec gobgpd -f "${CONFIG_FILE}"
