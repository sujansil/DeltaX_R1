#!/usr/bin/env bash
set -euo pipefail
umask 077

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "${PROJECT_DIR}/scripts/lib_pg_tools.sh"
BACKUP_ROOT="${1:-${PROJECT_DIR}/backups}"
mkdir -p "${BACKUP_ROOT}"; chmod 700 "${BACKUP_ROOT}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
artifact="${BACKUP_ROOT}/round1-${timestamp}-$$"
mkdir -m 700 "${artifact}"; touch "${artifact}/INCOMPLETE"
pgpass_file="$(mktemp)"; trap 'rm -f "${pgpass_file}"' EXIT

settings_output="$(cd "${PROJECT_DIR}" && .venv/bin/python - <<'PY'
from app.config import get_settings
s = get_settings(); print(s.database_url); print(s.media_root)
PY
)"
database_url="${settings_output%%$'\n'*}"; media_root="${settings_output#*$'\n'}"
[[ "${media_root}" = /* ]] || media_root="${PROJECT_DIR}/${media_root#./}"
prepare_pg_connection "${database_url}" "${pgpass_file}"
mapfile -t connection_args < <(pg_args)

printf 'Creating PostgreSQL custom-format dump...\n'
pg_tool pg_dump "${connection_args[@]}" --format=custom --no-owner --no-acl \
  "${PGDATABASE_VALUE}" >"${artifact}/database.dump.tmp"
[[ -s "${artifact}/database.dump.tmp" ]] || { printf 'Database dump is empty.\n' >&2; exit 1; }
mv "${artifact}/database.dump.tmp" "${artifact}/database.dump"

printf 'Archiving local media...\n'
mkdir -p "${media_root}"
tar -C "${media_root}" -cf "${artifact}/media.tar.tmp" .
mv "${artifact}/media.tar.tmp" "${artifact}/media.tar"
schema_revision="$(pg_tool psql "${connection_args[@]}" -d "${PGDATABASE_VALUE}" -Atqc 'SELECT version_num FROM alembic_version')"
counts_json="$(pg_tool psql "${connection_args[@]}" -d "${PGDATABASE_VALUE}" -Atqc \
  "SELECT json_build_object('teams',(SELECT count(*) FROM teams),'submissions',(SELECT count(*) FROM submissions),'score_events',(SELECT count(*) FROM score_events),'publications',(SELECT count(*) FROM leaderboard_snapshots))::text")"

ARTIFACT="${artifact}" MEDIA_ROOT_VALUE="${media_root}" SCHEMA_REVISION="${schema_revision}" \
COUNTS_JSON="${counts_json}" EXPORTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
APP_VERSION="1.0.0" python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
artifact, media = Path(os.environ["ARTIFACT"]), Path(os.environ["MEDIA_ROOT_VALUE"])
def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()
media_files = []
for path in sorted(p for p in media.rglob("*") if p.is_file()):
    media_files.append({"path": path.relative_to(media).as_posix(), "size": path.stat().st_size,
                        "sha256": digest(path)})
manifest = {
    "format": "round1-backup-v1", "created_at_utc": os.environ["EXPORTED_AT"],
    "schema_revision": os.environ["SCHEMA_REVISION"], "application_version": os.environ["APP_VERSION"],
    "database_counts": json.loads(os.environ["COUNTS_JSON"]),
    "files": {name: {"size": (artifact/name).stat().st_size, "sha256": digest(artifact/name)}
              for name in ("database.dump", "media.tar")},
    "media_files": media_files,
    "consistency_note": "Pause content changes while database and media are captured for a coordinated recovery checkpoint."
}
target = artifact / "manifest.json.tmp"
target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
os.chmod(target, 0o600)
PY
mv "${artifact}/manifest.json.tmp" "${artifact}/manifest.json"
rm "${artifact}/INCOMPLETE"; chmod 600 "${artifact}"/*
printf 'Backup completed successfully: %s\n' "${artifact}"
