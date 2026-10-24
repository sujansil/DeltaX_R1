#!/usr/bin/env bash
set -euo pipefail

project_name="round1_system_test_${UID}_$$"
compose_file="docker-compose.test.yml"

cleanup() {
  docker compose --project-name "${project_name}" --file "${compose_file}" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker compose --project-name "${project_name}" --file "${compose_file}" up --detach --wait
published_address="$(docker compose --project-name "${project_name}" --file "${compose_file}" port postgres-test 5432)"
test_port="${published_address##*:}"

if [[ ! "${test_port}" =~ ^[0-9]+$ ]]; then
  echo "Could not determine the disposable PostgreSQL port." >&2
  exit 1
fi

export DATABASE_URL="postgresql+asyncpg://round1_test:round1_test_only@127.0.0.1:${test_port}/round1_test"
export SECRET_KEY="disposable-test-secret-not-for-deployment"
export ENVIRONMENT="test"
export RUN_DB_TESTS="1"
export ROUND1_TEST_DATABASE_GUARD="round1-disposable-postgres"

.venv/bin/alembic upgrade head
.venv/bin/pytest

