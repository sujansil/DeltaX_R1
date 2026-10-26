#!/usr/bin/env bash
set -euo pipefail
umask 077

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
source "${PROJECT_DIR}/scripts/lib_pg_tools.sh"
[[ $# -eq 3 ]] || { printf 'Usage: %s BACKUP_DIR DISPOSABLE_DATABASE_URL NEW_MEDIA_DIR\n' "$0" >&2; exit 2; }
backup_dir="$(cd -- "$1" && pwd)"; target_url="$2"; media_dir="$3"
[[ ! -e "${media_dir}" ]] || { printf 'Refusing to overwrite media target: %s\n' "${media_dir}" >&2; exit 2; }
[[ ! -e "${backup_dir}/INCOMPLETE" ]] || { printf 'Backup is marked INCOMPLETE.\n' >&2; exit 2; }
for name in database.dump media.tar manifest.json; do
  [[ -f "${backup_dir}/${name}" ]] || { printf 'Missing backup file: %s\n' "${name}" >&2; exit 2; }
done

BACKUP_DIR_VALUE="${backup_dir}" python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
root = Path(os.environ["BACKUP_DIR_VALUE"]); manifest = json.loads((root / "manifest.json").read_text())
if manifest.get("format") != "round1-backup-v1": raise SystemExit("Unsupported backup manifest format")
for name, expected in manifest["files"].items():
    h = hashlib.sha256((root/name).read_bytes()).hexdigest()
    if h != expected["sha256"] or (root/name).stat().st_size != expected["size"]:
        raise SystemExit(f"Backup checksum/size mismatch: {name}")
PY

pgpass_file="$(mktemp)"; trap 'rm -f "${pgpass_file}"' EXIT
prepare_pg_connection "${target_url}" "${pgpass_file}"
[[ "${PGDATABASE_VALUE}" =~ (rehearsal|disposable|restore|test) ]] || {
  printf 'Refusing target database without rehearsal/disposable/restore/test in its name.\n' >&2; exit 2; }
mapfile -t connection_args < <(pg_args)
printf 'Creating new disposable database %s...\n' "${PGDATABASE_VALUE}"
pg_tool createdb "${connection_args[@]}" "${PGDATABASE_VALUE}"
printf 'Restoring PostgreSQL dump...\n'
pg_tool pg_restore "${connection_args[@]}" -d "${PGDATABASE_VALUE}" --no-owner --no-acl --exit-on-error <"${backup_dir}/database.dump"
mkdir -m 700 "${media_dir}"
tar -C "${media_dir}" -xf "${backup_dir}/media.tar"

schema_revision="$(pg_tool psql "${connection_args[@]}" -d "${PGDATABASE_VALUE}" -Atqc 'SELECT version_num FROM alembic_version')"
counts_json="$(pg_tool psql "${connection_args[@]}" -d "${PGDATABASE_VALUE}" -Atqc \
  "SELECT json_build_object('teams',(SELECT count(*) FROM teams),'submissions',(SELECT count(*) FROM submissions),'score_events',(SELECT count(*) FROM score_events),'publications',(SELECT count(*) FROM leaderboard_snapshots))::text")"
BACKUP_DIR_VALUE="${backup_dir}" MEDIA_DIR_VALUE="${media_dir}" SCHEMA_REVISION="${schema_revision}" COUNTS_JSON="${counts_json}" python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
root, media = Path(os.environ["BACKUP_DIR_VALUE"]), Path(os.environ["MEDIA_DIR_VALUE"])
m = json.loads((root/"manifest.json").read_text()); actual = json.loads(os.environ["COUNTS_JSON"])
if os.environ["SCHEMA_REVISION"] != m["schema_revision"]: raise SystemExit("Restored schema revision differs from manifest")
if actual != m["database_counts"]: raise SystemExit(f"Restored representative counts differ: {actual} != {m['database_counts']}")
for item in m["media_files"]:
    path = media/item["path"]
    if not path.is_file(): raise SystemExit(f"Missing restored media: {item['path']}")
    if path.stat().st_size != item["size"] or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
        raise SystemExit(f"Restored media mismatch: {item['path']}")
print("Verified schema revision, representative record totals, published snapshots, and media checksums.")
PY
printf 'Restore rehearsal succeeded. Database and media were left in place: %s %s\n' "${PGDATABASE_VALUE}" "${media_dir}"
