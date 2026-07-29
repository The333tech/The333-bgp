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

detect_python() {
  local candidate
  for candidate in "${PYTHON:-}" python3 python; do
    [[ -n "${candidate}" ]] || continue
    if "${candidate}" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
    then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  return 1
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
  "CONTRIBUTING.md"
  "SECURITY.md"
  "VERSION"
  "CHANGELOG.md"
  "docker-compose.yml"
  "docker-compose.portal.yml"
  "docker-compose.tls.yml"
  "portal/nginx-tls.conf"
  "docker/gobgp.Dockerfile"
  "docker/backend.Dockerfile"
  "docker/gobgp-entrypoint.sh"
  "docker/backend-entrypoint.sh"
  "extras/docker-awg/Dockerfile"
  "extras/docker-awg/entrypoint.sh"
  "extras/docker-awg/README.md"
  "deploy/systemd/the333-bgp-updater.service.in"
  "install.sh"
  "requirements.txt"
  "requirements.in"
  "app/main.py"
  "scripts/host-updater.py"
  "scripts/extract-release.py"
  "scripts/migrate-env.py"
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
  "config/service_catalog.builtin.json"
  "config/service_candidates.seed.json"
  "scripts/the333bgp.sh"
  "update-manifest.json"
)

source_required_files=(
  ".github/dependabot.yml"
  ".github/ISSUE_TEMPLATE/bug_report.yml"
  ".github/ISSUE_TEMPLATE/config.yml"
  ".github/ISSUE_TEMPLATE/feature_request.yml"
  ".github/pull_request_template.md"
  ".github/workflows/ci.yml"
  ".github/workflows/codeql.yml"
  ".github/workflows/docker-awg.yml"
  ".github/workflows/release.yml"
  "tests/test_auth_sessions.py"
  "tests/test_background_jobs.py"
  "tests/test_catalog_migration.py"
  "tests/test_host_updater.py"
  "tests/test_installer_upgrade_flow.py"
  "tests/test_release_metadata.py"
  "tests/test_remote_fetch_security.py"
  "tests/test_route_transaction.py"
  "tests/test_release_archive_and_env_migration.py"
  "tests/test_security_helpers.py"
  "tests/test_state_integrity.py"
)

cd "${ROOT}"

PYTHON_BIN="$(detect_python)" || fail "Python 3.10+ is required"

for file in "${required_files[@]}"; do
  [[ -e "${file}" ]] || fail "required file is missing: ${file}"
done

if [[ -d .git || "${THE333_STRICT_SOURCE_TREE:-false}" == "true" ]]; then
  for file in "${source_required_files[@]}"; do
    [[ -e "${file}" ]] || fail "required source file is missing: ${file}"
  done
fi

if [[ -d .git && "${THE333_REQUIRE_TRACKED_RELEASE_FILES:-false}" == "true" ]]; then
  for file in "${required_files[@]}" "${source_required_files[@]}"; do
    git ls-files --error-unmatch "${file}" >/dev/null 2>&1 \
      || fail "required release file is not tracked by Git: ${file}"
  done
  for file in "${required_files[@]}"; do
    export_attr="$(git check-attr export-ignore -- "${file}" | awk -F': ' '{print $3}')"
    [[ "${export_attr}" != "set" ]] \
      || fail "required release file is excluded from git archive by export-ignore: ${file}"
  done
fi

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

"${PYTHON_BIN}" -m py_compile app/main.py scripts/host-updater.py scripts/extract-release.py scripts/migrate-env.py
bash -n install.sh
bash -n docker/gobgp-entrypoint.sh
bash -n docker/backend-entrypoint.sh
bash -n extras/docker-awg/entrypoint.sh
bash -n scripts/the333bgp.sh
bash -n scripts/release-check.sh

if command -v shellcheck >/dev/null 2>&1; then
  shellcheck install.sh docker/gobgp-entrypoint.sh docker/backend-entrypoint.sh extras/docker-awg/entrypoint.sh scripts/the333bgp.sh scripts/release-check.sh
else
  log "shellcheck not installed; skipping shell lint"
fi

if command -v docker >/dev/null 2>&1 \
    && command -v timeout >/dev/null 2>&1 \
    && timeout 10 docker compose version >/dev/null 2>&1; then
  prepare_compose_env
  timeout 30 docker compose -f docker-compose.yml -f docker-compose.portal.yml config >/dev/null
  timeout 30 docker compose -f docker-compose.yml -f docker-compose.portal.yml -f docker-compose.tls.yml config >/dev/null
else
  log "docker compose not available; skipping compose config"
fi

log "OK"
