#!/usr/bin/env bash

prepare_pg_connection() {
  local database_url="$1" pgpass_file="$2"
  mapfile -t ROUND1_PG_FIELDS < <(ROUND1_PG_URL="${database_url}" ROUND1_PGPASS_FILE="${pgpass_file}" python3 - <<'PY'
import os
from urllib.parse import unquote, urlsplit
url = os.environ["ROUND1_PG_URL"].replace("postgresql+asyncpg://", "postgresql://", 1)
parts = urlsplit(url)
if parts.scheme not in {"postgres", "postgresql"} or not parts.hostname or not parts.path.lstrip("/"):
    raise SystemExit("DATABASE_URL must be a PostgreSQL URL with host and database")
host, port = parts.hostname, parts.port or 5432
user, password = unquote(parts.username or ""), unquote(parts.password or "")
database = unquote(parts.path.lstrip("/"))
def esc(value): return value.replace("\\", "\\\\").replace(":", "\\:")
with open(os.environ["ROUND1_PGPASS_FILE"], "w", encoding="utf-8") as handle:
    handle.write(":".join(map(esc, (host, str(port), "*", user, password))) + "\n")
os.chmod(os.environ["ROUND1_PGPASS_FILE"], 0o600)
print(host); print(port); print(user); print(database)
PY
  ) || return 1
  [[ ${#ROUND1_PG_FIELDS[@]} -eq 4 ]] || return 1
  PGHOST_VALUE="${ROUND1_PG_FIELDS[0]}"
  PGPORT_VALUE="${ROUND1_PG_FIELDS[1]}"
  PGUSER_VALUE="${ROUND1_PG_FIELDS[2]}"
  PGDATABASE_VALUE="${ROUND1_PG_FIELDS[3]}"
  PGPASS_FILE_VALUE="${pgpass_file}"
}

pg_tool() {
  local executable="$1"; shift
  if command -v "${executable}" >/dev/null 2>&1; then
    PGPASSFILE="${PGPASS_FILE_VALUE}" "${executable}" "$@"
    return
  fi
  command -v docker >/dev/null 2>&1 || {
    printf 'Missing prerequisite: %s (or Docker for the PostgreSQL 16 client fallback).\n' "${executable}" >&2
    return 127
  }
  docker info >/dev/null 2>&1 || { printf 'Docker daemon is unavailable.\n' >&2; return 127; }
  docker run --rm --interactive --network host \
    --volume "${PGPASS_FILE_VALUE}:/run/round1.pgpass:ro" \
    --env PGPASSFILE=/run/round1.pgpass \
    postgres:16-alpine "${executable}" "$@"
}

pg_args() {
  printf '%s\n' "-h" "${PGHOST_VALUE}" "-p" "${PGPORT_VALUE}" "-U" "${PGUSER_VALUE}"
}
