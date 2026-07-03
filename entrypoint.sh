#!/usr/bin/env bash
set -euo pipefail

mkdir -p /data

: "${THE333_ROLE:=combined}"
: "${LOCAL_AS:=64512}"
: "${ROUTER_ID:=127.0.0.1}"
: "${BGP_LISTEN_PORT:=179}"
: "${PEER_ADDRESS:=192.168.1.1}"
: "${PEER_AS:=65455}"
: "${WEB_HOST:=0.0.0.0}"
: "${WEB_PORT:=8088}"
: "${GOBGP_API_HOST:=127.0.0.1}"
: "${GOBGP_API_PORT:=50051}"
: "${UPDATE_HOST:=0.0.0.0}"
: "${UPDATE_PORT:=8091}"

write_gobgp_config() {
  cat > /data/gobgpd.toml <<CFG
[global.config]
  as = ${LOCAL_AS}
  router-id = "${ROUTER_ID}"
  port = ${BGP_LISTEN_PORT}

[[neighbors]]
  [neighbors.config]
    neighbor-address = "${PEER_ADDRESS}"
    peer-as = ${PEER_AS}

  [neighbors.ebgp-multihop.config]
    enabled = true
    multihop-ttl = 10

  [[neighbors.afi-safis]]
    [neighbors.afi-safis.config]
      afi-safi-name = "ipv4-unicast"
CFG
}

wait_for_gobgp_api() {
  local host="${1}"
  local port="${2}"

  echo "[entrypoint] Waiting for gobgpd API at ${host}:${port}..."
  local attempts=60
  while (( attempts > 0 )); do
    if gobgp -u "${host}" -p "${port}" global >/dev/null 2>&1; then
      echo "[entrypoint] gobgpd API is ready"
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 1
  done

  echo "[entrypoint] gobgpd API is not ready at ${host}:${port}" >&2
  return 1
}

start_gobgp_background() {
  write_gobgp_config

  echo "[entrypoint] Starting gobgpd..."
  gobgpd -f /data/gobgpd.toml > /data/gobgpd.log 2>&1 &
  echo $! > /data/gobgpd.pid
  wait_for_gobgp_api "127.0.0.1" "50051"
}

case "${THE333_ROLE}" in
  gobgp)
    write_gobgp_config
    echo "[entrypoint] Starting gobgpd only..."
    exec gobgpd -f /data/gobgpd.toml
    ;;
  backend)
    wait_for_gobgp_api "${GOBGP_API_HOST}" "${GOBGP_API_PORT}"
    echo "[entrypoint] Starting backend only..."
    exec uvicorn app.main:app --host "${WEB_HOST}" --port "${WEB_PORT}"
    ;;
  updater)
    echo "[entrypoint] Starting host updater..."
    exec uvicorn app.updater:app --host "${UPDATE_HOST}" --port "${UPDATE_PORT}"
    ;;
  combined)
    start_gobgp_background
    echo "[entrypoint] Starting combined web app..."
    exec uvicorn app.main:app --host "${WEB_HOST}" --port "${WEB_PORT}"
    ;;
  *)
    echo "[entrypoint] Unsupported THE333_ROLE=${THE333_ROLE}" >&2
    exit 2
    ;;
esac
