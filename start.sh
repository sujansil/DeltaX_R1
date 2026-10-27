#!/usr/bin/env bash
set -uo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="${PROJECT_DIR}/.preview"
RUN_DIR="${RUNTIME_DIR}/run"
LOG_DIR="${RUNTIME_DIR}/logs"
ENV_FILE="${RUNTIME_DIR}/preview.env"
PID_FILE="${RUN_DIR}/uvicorn.pid"
APP_LOG="${LOG_DIR}/app.log"
COMPOSE_FILE="${PROJECT_DIR}/docker-compose.preview.yml"
COMPOSE_PROJECT="round1_local_preview"
PLAYER_URL="http://127.0.0.1:8000/player/"
ADMIN_URL="http://127.0.0.1:8000/admin-ui/"
HEALTH_URL="http://127.0.0.1:8000/health"

compose() {
  docker compose --project-name "${COMPOSE_PROJECT}" --env-file "${ENV_FILE}" --file "${COMPOSE_FILE}" "$@"
}

explain_prerequisite() {
  printf 'Missing prerequisite: %s\n' "$1" >&2
  printf '%s\n' "$2" >&2
}

check_prerequisites() {
  local failed=0
  if ! command -v docker >/dev/null 2>&1; then
    explain_prerequisite "Docker" "Install Docker Engine or Docker Desktop, then ensure the docker command is on PATH."
    failed=1
  elif ! docker compose version >/dev/null 2>&1; then
    explain_prerequisite "Docker Compose" "Install the Docker Compose v2 plugin (docker compose)."
    failed=1
  elif ! docker info >/dev/null 2>&1; then
    explain_prerequisite "running Docker daemon" "Start Docker Desktop or the Docker service, then retry."
    failed=1
  fi
  if ! command -v curl >/dev/null 2>&1; then
    explain_prerequisite "curl" "Install curl using your operating system package manager."
    failed=1
  fi
  for executable in python alembic uvicorn pytest; do
    if [[ ! -x "${PROJECT_DIR}/.venv/bin/${executable}" ]]; then
      explain_prerequisite ".venv/bin/${executable}" "Create .venv and run: .venv/bin/pip install -r requirements.txt"
      failed=1
    fi
  done
  [[ "${failed}" -eq 0 ]]
}

ensure_runtime_environment() {
  mkdir -p "${RUN_DIR}" "${LOG_DIR}"
  if [[ -f "${ENV_FILE}" ]]; then
    chmod 600 "${ENV_FILE}"
    return 0
  fi
  umask 077
  local db_password secret_key
  db_password="$("${PROJECT_DIR}/.venv/bin/python" -c 'import secrets; print(secrets.token_hex(24))')" || return 1
  secret_key="$("${PROJECT_DIR}/.venv/bin/python" -c 'import secrets; print(secrets.token_hex(32))')" || return 1
  printf 'PREVIEW_DB_PASSWORD=%s\nSECRET_KEY=%s\n' "${db_password}" "${secret_key}" >"${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
}

load_preview_environment() {
  # Generated values are hexadecimal and safe to source. The file is private and ignored by Git.
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
  export DATABASE_URL="postgresql+asyncpg://round1_preview:${PREVIEW_DB_PASSWORD}@127.0.0.1:55432/round1_preview"
  export REDIS_URL="redis://127.0.0.1:56379/0"
  export ENVIRONMENT="development"
}

tracked_app_pid() {
  [[ -f "${PID_FILE}" ]] || return 1
  local pid command cwd
  pid="$(<"${PID_FILE}")"
  [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${pid}" 2>/dev/null || return 1
  command="$(tr '\0' ' ' <"/proc/${pid}/cmdline" 2>/dev/null || true)"
  cwd="$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)"
  [[ "${command}" == *"uvicorn app.main:app"* && "${cwd}" == "${PROJECT_DIR}" ]] || return 1
  printf '%s\n' "${pid}"
}

wait_for_health() {
  local attempt
  for attempt in $(seq 1 40); do
    if curl --silent --fail --max-time 1 "${HEALTH_URL}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  return 1
}

port_8000_available() {
  "${PROJECT_DIR}/.venv/bin/python" - <<'PY'
import socket

sock = socket.socket()
try:
    sock.bind(("127.0.0.1", 8000))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

offer_demo_seed() {
  local answer
  printf 'Seed sample teams and questions in the preview database? [y/N] '
  read -r answer || return 0
  [[ "${answer}" =~ ^[Yy]$ ]] || return 0
  if [[ "${DATABASE_URL}" != *"@127.0.0.1:55432/round1_preview" ]]; then
    printf 'Refusing to seed: the target is not the identified preview database.\n' >&2
    return 1
  fi
  printf 'Seeding preview data...\n'
  (cd "${PROJECT_DIR}" && "${PROJECT_DIR}/.venv/bin/python" scripts/seed_sample.py) \
    | sed -E 's/ \| password=.*$//'

  printf 'Create or retain a preview admin now? [y/N] '
  read -r answer || return 0
  [[ "${answer}" =~ ^[Yy]$ ]] || return 0
  local username password
  printf 'Preview admin username: '
  read -r username
  [[ -n "${username}" ]] || { printf 'Admin username cannot be empty.\n' >&2; return 1; }
  printf 'Preview admin password (input hidden): '
  read -r -s password
  printf '\n'
  [[ ${#password} -ge 8 ]] || { printf 'Password must be at least 8 characters.\n' >&2; return 1; }
  PREVIEW_ADMIN_USERNAME="${username}" PREVIEW_ADMIN_PASSWORD="${password}" \
    "${PROJECT_DIR}/.venv/bin/python" - <<'PY'
import asyncio
import os
from sqlalchemy import select
from app.db import SessionLocal, engine
from app.models import Admin
from app.security import hash_password_async

async def main():
    username = os.environ["PREVIEW_ADMIN_USERNAME"]
    password = os.environ["PREVIEW_ADMIN_PASSWORD"]
    async with SessionLocal() as db:
        existing = await db.scalar(select(Admin).where(Admin.username == username))
        if existing:
            print("Preview admin already exists; credentials were not changed.")
        else:
            db.add(Admin(username=username, password_hash=await hash_password_async(password)))
            await db.commit()
            print("Preview admin created.")
    await engine.dispose()

asyncio.run(main())
PY
  unset password PREVIEW_ADMIN_PASSWORD
}

start_preview() {
  check_prerequisites || return 1
  ensure_runtime_environment || { printf 'Could not create the private preview environment.\n' >&2; return 1; }
  load_preview_environment

  printf 'Starting loopback-only PostgreSQL and Redis preview services...\n'
  if ! compose up --detach --wait; then
    printf 'Preview services failed to start. Recent service logs:\n' >&2
    compose logs --tail 80 >&2 || true
    return 1
  fi

  printf 'Applying migrations to the identified preview database...\n'
  if ! (cd "${PROJECT_DIR}" && "${PROJECT_DIR}/.venv/bin/alembic" upgrade head); then
    printf 'Preview migration failed. PostgreSQL logs:\n' >&2
    compose logs --tail 80 postgres-preview >&2 || true
    return 1
  fi

  local pid
  if pid="$(tracked_app_pid)"; then
    printf 'Restarting this Round 1 preview (PID %s) so current code and routes are loaded...\n' "${pid}"
    stop_tracked_app || return 1
  elif ! port_8000_available; then
    printf 'Port 8000 is already in use by another process or container.\n' >&2
    printf 'Round 1 was not started, and the conflicting application was left untouched.\n' >&2
    printf 'Stop or reconfigure the conflicting application, then run: %s start\n' "${PROJECT_DIR}/start.sh" >&2
    return 1
  fi
  if ! port_8000_available; then
    printf 'Port 8000 remained occupied after the tracked Round 1 process stopped.\n' >&2
    printf 'Nothing else was terminated; Round 1 was not started.\n' >&2
    return 1
  fi
  rm -f "${PID_FILE}"
  printf 'Starting this Round 1 project from %s on 127.0.0.1:8000...\n' "${PROJECT_DIR}"
  (
    cd "${PROJECT_DIR}" || exit 1
    nohup "${PROJECT_DIR}/.venv/bin/uvicorn" app.main:app --host 127.0.0.1 --port 8000 \
      >>"${APP_LOG}" 2>&1 &
    printf '%s\n' "$!" >"${PID_FILE}"
  )
  if ! wait_for_health; then
    printf 'Application failed its health check. Recent application logs:\n' >&2
    tail -n 80 "${APP_LOG}" >&2 || true
    stop_tracked_app
    return 1
  fi
  offer_demo_seed || printf 'Demo seeding did not complete; the preview itself is still running.\n' >&2
  show_status
}

show_status() {
  ensure_runtime_environment || return 1
  printf '\nPlayer: %s\nAdmin:  %s\nDocs:   http://127.0.0.1:8000/docs\n' "${PLAYER_URL}" "${ADMIN_URL}"
  local pid
  if pid="$(tracked_app_pid)" && curl --silent --fail --max-time 1 "${HEALTH_URL}" >/dev/null 2>&1; then
    printf 'Application: running and healthy (PID %s)\n' "${pid}"
  elif pid="$(tracked_app_pid)"; then
    printf 'Application: process is running but health check failed (PID %s)\n' "${pid}"
  else
    printf 'Application: stopped\n'
  fi
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    compose ps 2>/dev/null || printf 'Preview services: unavailable\n'
  else
    printf 'Preview services: Docker Compose unavailable\n'
  fi
}

view_logs() {
  ensure_runtime_environment || return 1
  printf '\nApplication log (%s):\n' "${APP_LOG}"
  tail -n 100 "${APP_LOG}" 2>/dev/null || printf '(no application log yet)\n'
  printf '\nService logs:\n'
  if command -v docker >/dev/null 2>&1; then
    compose logs --tail 100 2>&1 || printf '(preview services are not available)\n'
  else
    printf '(Docker is not installed)\n'
  fi
}

run_tests() {
  check_prerequisites || return 1
  printf 'Running the isolated PostgreSQL suite via scripts/test_postgres.sh...\n'
  (cd "${PROJECT_DIR}" && ./scripts/test_postgres.sh)
}

migrate_configured_development() {
  [[ -x "${PROJECT_DIR}/.venv/bin/python" && -x "${PROJECT_DIR}/.venv/bin/alembic" ]] || {
    explain_prerequisite "project virtual environment" "Create .venv and install requirements.txt first."
    return 1
  }
  local identity database_name
  if ! identity="$(cd "${PROJECT_DIR}" && env \
      -u DATABASE_URL -u REDIS_URL -u SECRET_KEY -u ENVIRONMENT \
      "${PROJECT_DIR}/.venv/bin/python" - <<'PY'
from urllib.parse import urlsplit
from app.config import get_settings
s = get_settings()
u = urlsplit(s.database_url)
database = u.path.lstrip("/")
if s.environment != "development":
    raise SystemExit("Refusing migration: ENVIRONMENT is not development.")
if u.hostname not in {"localhost", "127.0.0.1", "::1"}:
    raise SystemExit("Refusing migration: database host is not local.")
if not database:
    raise SystemExit("Refusing migration: database name is missing.")
print(f"environment={s.environment} host={u.hostname} port={u.port or 5432} database={database}")
print(database)
PY
  )"; then
    printf '%s\n' "${identity}" >&2
    return 1
  fi
  database_name="${identity##*$'\n'}"
  printf 'Identified configured target: %s\n' "${identity%%$'\n'*}"
  printf 'Type the database name (%s) to apply Alembic migrations, or press Enter to cancel: ' "${database_name}"
  local confirmation
  read -r confirmation || return 1
  if [[ "${confirmation}" != "${database_name}" ]]; then
    printf 'Migration cancelled; no database changes were made.\n'
    return 0
  fi
  (cd "${PROJECT_DIR}" && env \
    -u DATABASE_URL -u REDIS_URL -u SECRET_KEY -u ENVIRONMENT \
    "${PROJECT_DIR}/.venv/bin/alembic" upgrade head)
}

stop_tracked_app() {
  local pid
  if ! pid="$(tracked_app_pid)"; then
    rm -f "${PID_FILE}"
    return 0
  fi
  kill "${pid}" 2>/dev/null || true
  local attempt
  for attempt in $(seq 1 20); do
    kill -0 "${pid}" 2>/dev/null || break
    sleep 0.1
  done
  if kill -0 "${pid}" 2>/dev/null; then
    printf 'Application did not stop cleanly; leaving PID %s untouched. See logs.\n' "${pid}" >&2
    return 1
  fi
  rm -f "${PID_FILE}"
}

stop_preview() {
  ensure_runtime_environment || return 1
  stop_tracked_app || return 1
  if command -v docker >/dev/null 2>&1; then
    compose stop || { printf 'One or more preview services failed to stop.\n' >&2; return 1; }
  fi
  printf 'Preview stopped. Its PostgreSQL volume and all preview data were preserved.\n'
}

create_backup() {
  [[ -x "${PROJECT_DIR}/.venv/bin/python" ]] || { printf 'Create .venv and install requirements first.\n' >&2; return 1; }
  printf 'Pause question/team/media changes for a coordinated database + media recovery checkpoint.\n'
  "${PROJECT_DIR}/scripts/backup_round1.sh" "${PROJECT_DIR}/backups"
}

show_backups() {
  printf 'Backup artifacts (INCOMPLETE means unusable):\n'
  find "${PROJECT_DIR}/backups" -mindepth 1 -maxdepth 2 \( -name manifest.json -o -name INCOMPLETE \) -print 2>/dev/null || true
}

print_menu() {
  cat <<'MENU'

Round 1 local menu
1. Start the local preview and required services
2. Show player/admin URLs and service status
3. View application/service logs
4. Run the isolated PostgreSQL test suite
5. Apply migrations to the configured local development database
6. Create a backup of the configured database and local media
7. Show backup artifact status
8. Stop preview services
0. Exit without stopping running services
MENU
}

main() {
  cd "${PROJECT_DIR}" || exit 1
  if [[ $# -gt 0 ]]; then
    case "$1" in
      start) start_preview ;;
      status) show_status ;;
      logs) view_logs ;;
      test) run_tests ;;
      stop) stop_preview ;;
      *) printf 'Usage: %s [start|status|logs|test|stop]\n' "$0" >&2; return 2 ;;
    esac
    return $?
  fi
  while true; do
    print_menu
    printf 'Choose an option: '
    local choice
    read -r choice || { printf '\n'; return 0; }
    case "${choice}" in
      1) start_preview || true ;;
      2) show_status || true ;;
      3) view_logs || true ;;
      4) run_tests || true ;;
      5) migrate_configured_development || true ;;
      6) create_backup || true ;;
      7) show_backups || true ;;
      8) stop_preview || true ;;
      0) return 0 ;;
      *) printf 'Unknown option: %s\n' "${choice}" ;;
    esac
  done
}

main "$@"
