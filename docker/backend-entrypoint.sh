#!/bin/sh
set -eu

: "${WEB_HOST:=0.0.0.0}"
: "${WEB_PORT:=8088}"
: "${GOBGP_API_HOST:=the333-gobgp-core}"
: "${GOBGP_API_PORT:=50051}"

echo "[backend] Waiting for GoBGP API at ${GOBGP_API_HOST}:${GOBGP_API_PORT}..."
attempts=90
while [ "${attempts}" -gt 0 ]; do
  if gobgp -u "${GOBGP_API_HOST}" -p "${GOBGP_API_PORT}" global >/dev/null 2>&1; then
    echo "[backend] GoBGP API is ready"
    exec uvicorn app.main:app --host "${WEB_HOST}" --port "${WEB_PORT}"
  fi
  attempts=$((attempts - 1))
  sleep 1
done

echo "[backend] GoBGP API is not ready" >&2
exit 1
