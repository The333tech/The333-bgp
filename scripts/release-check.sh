#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

fail() {
  printf '[release-check] ERROR: %s\n' "$*" >&2
  exit 1
}

log() {
  printf '[release-check] %s\n' "$*"
}

temp_env_created="false"

cleanup() {
  if [[ "${temp_env_created}" == "true" ]]; then
    rm -f .env
  fi
}

prepare_compose_env() {
  if [[ ! -f .env ]]; then
    cp .env.example .env
    temp_env_created="true"
  fi
}

trap cleanup EXIT

required_files=(
  ".env.example"
  ".dockerignore"
  ".gitattributes"
  ".gitignore"
  "LICENSE"
  "README.md"
  "SECURITY.md"
  "VERSION"
  "CHANGELOG.md"
  "Dockerfile"
  "docker-compose.yml"
  "docker-compose.portal.yml"
  "entrypoint.sh"
  "install.sh"
  "requirements.txt"
  "app/main.py"
  "app/updater.py"
  "portal/Dockerfile"
  "portal/.dockerignore"
  "portal/nginx.conf"
  "portal/package.json"
  "portal/package-lock.json"
  "portal/scripts/test-mikrotik-logic.mjs"
  "portal/src/App.tsx"
  "portal/src/components/MikroTikAssistant.tsx"
  "portal/src/components/mikrotikAssistantLogic.ts"
  "config/default_sources.json"
  "config/service_catalog.json"
  "config/service_candidates.seed.json"
  "scripts/the333bgp.sh"
  "update-manifest.json"
)

cd "${ROOT}"

for file in "${required_files[@]}"; do
  [[ -e "${file}" ]] || fail "required file is missing: ${file}"
done

if [[ -d .git || "${THE333_STRICT_RELEASE_TREE:-false}" == "true" ]]; then
  for forbidden in ".env" "data" "backups" "portal/node_modules" "portal/dist" "the333ctl.sh"; do
    if [[ -d .git ]]; then
      if git ls-files --error-unmatch "${forbidden}" >/dev/null 2>&1; then
        fail "forbidden release artifact is tracked: ${forbidden}"
      fi
    else
      [[ ! -e "${forbidden}" ]] || fail "forbidden release artifact exists: ${forbidden}"
    fi
  done
else
  [[ ! -e "the333ctl.sh" ]] || fail "old control script exists: the333ctl.sh"
  log "installed tree detected; runtime .env/data/backups checks skipped"
fi

if grep -RIn \
    --exclude-dir=.git \
    --exclude-dir=.github \
    --exclude-dir=node_modules \
    --exclude-dir=dist \
    --exclude-dir=__pycache__ \
    --exclude-dir=data \
    --exclude-dir=backups \
    --exclude='release-check.sh' \
    -E 'the333ctl|OWNER/REPO|TODO_RELEASE|CHANGE_ME_REAL_SECRET' .; then
  fail "forbidden placeholder or old control name found"
fi

python3 -m py_compile app/main.py app/updater.py
bash -n install.sh
bash -n entrypoint.sh
bash -n scripts/the333bgp.sh
bash -n scripts/release-check.sh

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck install.sh entrypoint.sh scripts/the333bgp.sh scripts/release-check.sh
else
  log "shellcheck not installed; skipping shell lint"
fi

if command -v docker >/dev/null 2>&1 \
    && command -v timeout >/dev/null 2>&1 \
    && timeout 10 docker compose version >/dev/null 2>&1; then
  prepare_compose_env
  timeout 30 docker compose -f docker-compose.yml -f docker-compose.portal.yml config >/dev/null
else
  log "docker compose not available; skipping compose config"
fi

log "OK"
