# R1 rehearsal reset and final combined standings

Implemented locally on 11 November 2026. No Azure access, deployment, competition
database connection, production reset, or production migration was performed.
R2/R3 code, question data, authentication, and scoring algorithms are unchanged.

## Behavior and audit findings

The repository already had a development reset and confirmation form. It trusted
`ENVIRONMENT=development`, accepted paused rounds, deleted tables without R1
filters, and did not explicitly clear R1B durable receipts. That path is reused
and hardened instead of introducing another reset mechanism.

Reset now requires all of the following server-side:

- `ENVIRONMENT=rehearsal`, `R1_RESET_ENABLED=true`, an exact
  `R1_RESET_DATABASE_NAME`, and a rehearsal token of at least 32 characters.
- A matching operator-provisioned database attestation: actual database name,
  PostgreSQL database OID, and SHA-256 of the rehearsal token. The UI cannot
  provision or override this attestation. Neither migration seeds it.
- Both R1 rows exist and neither is `LIVE` nor `PAUSED`.
- An admin bearer token, a nonblank reason, and exact `RESET ROUND 1` confirmation.

The operator script accepts only an explicitly named loopback disposable/rehearsal
database, refuses URL query overrides, requires both initialized round rows, and
refuses existing competition progress/history or an existing attestation. A
restored competition dump cannot be relabeled as rehearsal by this script. Use a
fresh rehearsal DB and import only the teams/questions needed for rehearsal.

Reset locks R1A then R1B exclusively, matching player actions, round controls,
publication, and reconciliation. One PostgreSQL transaction revokes R1 leases;
clears R1 score events, both durable receipt types, submissions, numerical drafts,
team progress, enrollments, and R1/R1A/R1B snapshots; resets both rounds to DRAFT;
clears clocks, pauses, rules snapshots and completion state; preserves the
admin-selected relay timer configuration; appends default projector controls; and records
`RESET_ROUND_ONE` with actor, reason, timestamp, database and affected counts.
Historical admin audit records remain. Other round-coded records remain.

Teams, usernames, password hashes, activation credentials, enabled status, admin
accounts, questions, correct answers, options, images and the configured rules
file remain unchanged. Enabled teams can sign in again and start from the beginning;
old player session tokens stay revoked. Team disablement is not silently reversed.

An interruption before commit rolls back the operation and audit together. If a
response is lost after commit, retrying is safe and refresh shows the persisted
state. Concurrent resets serialize. A delayed submission response whose receipt
was removed by reset returns `409 SUBMISSION_STATE_CHANGED` without rescoring.
The current R1 code does not use Redis for state, caches, timers or publication:
there is only an unused Redis import/configuration. No Redis flush is performed,
including against the separate R2/R3 services.

Combined scores sum the existing R1A and R1B score-event ledger, preserving signed
integer points. Participation is the union of R1 enrollment, team state,
submissions and score events, including participants later disabled. Missing
subround totals are zero. Registered teams with no R1 participation records are
not represented as participants. Both rounds must officially be ENDED and all
durable admissions resolved before preview/publication is ready.

Publication freezes every participant's team identity and both component scores,
combined total, ranks and qualification metadata. A partial unique index permits
one combined publication per run; an UPDATE trigger prevents editing it.
Duplicate/concurrent publish calls return the same publication without recomputing
or adding another publication audit. A rehearsal reset invalidates that rehearsal's
snapshot. There is no competition unpublish/delete API.

Equal totals share competition rank (1, 1, 3). Team code then ID provide stable
display order only. The organizer's competition tie-break policy remains
unresolved. A tie spanning places 16 and 17 blocks qualifier selection; it never
selects by name/code or arbitrarily admits an extra team. Publication itself may
still occur with that explicit unresolved qualification status.

The frozen combined qualification roster is the canonical R1 handoff for selecting
up to 16 R2 teams. No existing cross-round qualification integration was present
in this repository; this change does not write into or alter R2. Consumers must
use the new qualifiers endpoint rather than an individual subround board.

## APIs and admin controls

| Endpoint | Access | Behavior |
| --- | --- | --- |
| `GET /api/admin/dashboard` | Admin | Adds `reset_control.authorized`, `available`, and human-readable `reason`. |
| `POST /api/admin/rounds/reset` | Admin + rehearsal attestation | Existing endpoint, hardened; body `{"reason":"rerun rehearsal","confirmation":"RESET ROUND 1"}`. Returns reset time and affected counts. |
| `GET /api/admin/leaderboard/combined` | Admin | Preview only after both end; returns frozen board once published. |
| `POST /api/admin/leaderboard/publish` | Admin | Existing endpoint accepts `{"round_code":"R1"}` for final combined publication. Existing R1A/B publication remains. |
| `GET /api/admin/leaderboard/current?round_code=R1` | Admin | Frozen combined publication, if any. |
| `GET /api/admin/leaderboard/combined/qualifiers` | Admin | Frozen combined top 16, publication provenance; 409 before publication or on cutoff tie. |
| `GET /api/leaderboard/published?round_code=R1` | Valid player session | Published combined standings and caller's row only after publication. |
| `GET /api/leaderboard/mine?round_code=R1` | Valid player session | Published caller rank/total. |
| `GET /api/leaderboard/final` | Public | Published full combined standings only; empty before publication; `Cache-Control: no-store`. |
| `GET /api/public/audience` | Public | Existing projector feed displays combined standings after publication when host enables leaderboard; existing review slides remain available. |

Admin Overview now explains reset authorization, requires a reason and typed
confirmation, disables reset when unavailable, and refreshes state with clear
success/failure feedback. The leaderboard page adds combined preview, explicit
publication confirmation, frozen-publication identity and tie notices. Player
completion screens and the projector display Rank, Team Name, R1(a), R1(b) and
Combined Total for every published participant, including rows below 16.

The existing `scope=BOTH` XLSX export adds a `Final Combined R1` sheet only after
combined publication. It reads frozen names, scores, ranks, publication provenance
and qualifier status from that snapshot. Individual R1A/R1B exports are unchanged.
Player final-board checks run every 30 seconds while visible and 120 seconds
while hidden; the existing gameplay and session polling schedules are unchanged.

## Migrations and preserved seed data

New chain, starting at the repository's previous head:

1. `0015_combined_r1_publication`: partial unique R1 snapshot index and immutable
   publication UPDATE trigger. Existing subround snapshot rows are untouched.
2. `0016_rehearsal_reset_guard`: creates an empty attestation table. No existing
   database becomes resettable, and no team/question/credential row is modified.

No seeding, schema recreation, truncation or reset runs at application startup.
The existing seeded teams/questions/images/accounts are preserved. Apply only these
two migrations to an existing database already at `0014_type_final_publications`;
if the deployed schema is older, separately review its intervening migrations.

## Validation

The table below records the original implementation run. The corrective pass
passed 212 backend tests, 19 frontend unit tests, 35 admin browser checks, 46
participant checks, and 13 audience checks, with no failures. Twenty-two
database-backed reset cases were not executed under the no-reset instruction;
reset preservation checks used mocks. Both builds passed. No new migration was added.

Original implementation checks:

| Check | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| Complete backend suite through `./scripts/test_postgres.sh` | 220 | 0 | 0 |
| Frontend Node unit tests (admin 7, player 12) | 19 | 0 | 0 |
| Admin browser workflows | 34 | 0 | 0 |
| Participant browser scenarios | 45 | 0 | 0 |
| Audience browser checks, including full rendering | 13 | 0 | 0 |
| Admin and player/audience production builds | 2 | 0 | 0 |

The final backend run finished in 16.96 seconds. The documented test entry point
was verified on another newly created container, in addition to a direct complete
pytest pass. Provisioning smoke checks also confirmed successful authorization
of a newly migrated/seeded disposable rehearsal and rejection of missing round
rows and duplicate authorization. `git diff --check` passes.

Initial full attempts exposed old hardcoded question-count/timer fixtures, one
new test using a different asyncio loop scope, and a nonrepeatable existing
activation fixture. Test fixtures were corrected to declare their intended rules
or use the configured values; the production rules/scorers were unchanged. All
final checks pass. Remaining warnings are framework Python 3.14 deprecations and
the existing question/options circular-FK metadata warning.

Integration tests use newly created local PostgreSQL 16 containers with
tmpfs storage, loopback binding, an explicit disposable-database guard and no
competition credentials. A separate isolation test creates two UUID-named
disposable databases on that guarded local server, proves that a reset in one
cannot reset the other (including a copied attestation), and removes both. All
containers created for this task were removed afterward. Browser suites serve
local synthetic API fixtures.

Coverage includes before-start, partial-progress, completed, repeated, concurrent,
active/paused and interrupted resets; database identity/attestation failures;
preservation of content and unrelated round history; rollback/retry after database
failure; signed/missing/zero scoring; tied ranks and cutoff refusal; publication
embargo, concurrency, duplicate calls, frozen rows and database UPDATE rejection;
admin/player/public access boundaries; individual scoring and round lifecycle
regressions; and delayed receipt responses crossing a reset.

## Reproduce automated tests safely

From this repository, with Docker available:

```bash
cd /home/sujan/Projects/DELTAX/round1_system
./scripts/test_postgres.sh
npm --prefix frontend/admin-react test
npm --prefix frontend/react test
npm --prefix frontend/admin-react run build
npm --prefix frontend/react run build
npm --prefix frontend/admin-react run test:browser
npm --prefix frontend/react run test:browser
npm --prefix frontend/react run test:audience
```

`test_postgres.sh` creates a uniquely named Compose project, exports an explicit
loopback `round1_test` URL and test guard, migrates only that new database, runs
pytest, and removes only its own container/volumes on exit. Python module entry
points are used because the local virtualenv's command shebangs reference an old
workspace path. Browser binaries must already be installed (`npx playwright install
chromium` in the corresponding frontend directory if required).

## Disposable rehearsal reset walkthrough

These commands are for a NEW local disposable container, never an existing preview
or Azure tunnel. They do not read the deployed `.env` connection. Run in one shell
from the repository root. New demo credentials are confined to this disposable DB.

```bash
set -euo pipefail
DX_REHEARSAL_CONTAINER="deltax-r1-rehearsal-$(date -u +%Y%m%d%H%M%S)-$$"
export DATABASE_URL SECRET_KEY ENVIRONMENT R1_RESET_ENABLED
export R1_RESET_DATABASE_NAME R1_REHEARSAL_TOKEN
export R1_RESET_DATABASE_NAME=round1_disposable_rehearsal
DX_REHEARSAL_PASSWORD="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(24))')"
export SECRET_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')"
export R1_REHEARSAL_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))')"
docker run --detach --name "$DX_REHEARSAL_CONTAINER" \
  --tmpfs /var/lib/postgresql/data \
  --publish 127.0.0.1::5432 \
  --env POSTGRES_DB="$R1_RESET_DATABASE_NAME" \
  --env POSTGRES_USER=round1_rehearsal \
  --env POSTGRES_PASSWORD="$DX_REHEARSAL_PASSWORD" postgres:16-alpine
for DX_WAIT in $(seq 1 30); do
  if docker exec "$DX_REHEARSAL_CONTAINER" pg_isready -U round1_rehearsal >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$DX_REHEARSAL_CONTAINER" pg_isready -U round1_rehearsal >/dev/null
DX_REHEARSAL_PORT="$(docker port "$DX_REHEARSAL_CONTAINER" 5432/tcp)"
DX_REHEARSAL_PORT="${DX_REHEARSAL_PORT##*:}"
export DATABASE_URL="postgresql+asyncpg://round1_rehearsal:${DX_REHEARSAL_PASSWORD}@127.0.0.1:${DX_REHEARSAL_PORT}/${R1_RESET_DATABASE_NAME}"
export ENVIRONMENT=development R1_RESET_ENABLED=false
.venv/bin/python -m alembic upgrade head
.venv/bin/python scripts/seed_sample.py
# Create a disposable admin through the existing development API:
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18081 >/tmp/deltax-rehearsal-setup.log 2>&1 &
DX_SETUP_PID=$!
for DX_WAIT in $(seq 1 30); do
  if curl --fail --silent http://127.0.0.1:18081/health >/dev/null; then break; fi
  sleep 1
done
curl --fail --silent http://127.0.0.1:18081/health >/dev/null
curl --fail-with-body --silent --show-error --request POST \
  --header 'Content-Type: application/json' \
  --data '{"username":"rehearsal-admin","password":"Disposable-Rehearsal-Only!"}' \
  http://127.0.0.1:18081/api/auth/admin/setup >/dev/null
kill "$DX_SETUP_PID"
wait "$DX_SETUP_PID" || true
export ENVIRONMENT=rehearsal R1_RESET_ENABLED=true
.venv/bin/python scripts/authorize_rehearsal.py --database "$R1_RESET_DATABASE_NAME"
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 18081
```

Open `http://127.0.0.1:18081/admin-ui/`, sign in as that disposable admin, and
verify the authorization message. Reset once before start. Play part of each round,
then explicitly End the active rounds before resetting again. Confirm old tokens
are rejected and normal login starts over. Complete/end both rounds, inspect the
combined preview, verify `/api/leaderboard/final` is empty, explicitly publish,
then verify player/projector standings. Reset the completed rehearsal and verify
the public final endpoint becomes empty again. Attempts while LIVE or PAUSED must
return 409. The automated suite also tests failures and concurrency.

Stop Uvicorn with Ctrl-C. In the same shell, remove ONLY the container created above:

```bash
docker rm --force "$DX_REHEARSAL_CONTAINER"
unset DATABASE_URL SECRET_KEY ENVIRONMENT R1_RESET_ENABLED R1_RESET_DATABASE_NAME R1_REHEARSAL_TOKEN
unset DX_REHEARSAL_PASSWORD DX_REHEARSAL_PORT DX_REHEARSAL_CONTAINER DX_SETUP_PID
```

## Azure VM deployment commands — operator-run only

The user identified Azure VM + systemd/Nginx. The VM address, unit name,
WorkingDirectory, environment-file path, service account and public origin are
absent from this repository. Supply the actual values at the prompts below.
No Azure subscription or VM changes were made. Azure CLI can retrieve an existing
VM IP with `az vm show --show-details --query publicIps --output tsv`; see
[Microsoft's Linux VM guide](https://learn.microsoft.com/en-us/azure/virtual-machines/linux/tutorial-manage-vm).

Run during an agreed maintenance window, with both R1 rounds inactive and all
instances/workers of this R1 service stopped. The commands do not change Nginx,
R2/R3, `.env`, media, or `competition_rules.toml`. Do not run the rehearsal
authorization script or sample seed on the VM.

On the development machine, build a release from the reviewed working tree:

```bash
set -euo pipefail
npm --prefix frontend/admin-react run build
npm --prefix frontend/react run build
tar --exclude='__pycache__' --exclude='*.pyc' --exclude='node_modules' \
  -czf /tmp/deltax-r1-release.tgz \
  app migrations frontend/admin frontend/player frontend/audience \
  scripts alembic.ini requirements.txt R1_FEATURE_REPORT.md
sha256sum /tmp/deltax-r1-release.tgz
read -r -p 'Existing SSH user@VM-host: ' DX_VM_SSH
scp /tmp/deltax-r1-release.tgz "$DX_VM_SSH":/tmp/deltax-r1-release.tgz
ssh "$DX_VM_SSH"
```

On the VM, inspect service paths without printing environment secrets:

```bash
set -euo pipefail
read -r -p 'Existing R1 systemd unit (including .service): ' DX_R1_UNIT
sudo systemctl show "$DX_R1_UNIT" \
  --property=WorkingDirectory,User,FragmentPath,EnvironmentFiles
read -r -p 'Exact existing R1 WorkingDirectory: ' DX_R1_DIR
read -r -p 'Exact existing service user: ' DX_R1_USER
read -r -p 'Exact existing service EnvironmentFile: ' DX_R1_ENV
read -r -p 'Existing public HTTPS origin (no trailing slash): ' DX_R1_ORIGIN
test -x "$DX_R1_DIR/.venv/bin/python"
sudo test -f "$DX_R1_ENV"
sha256sum /tmp/deltax-r1-release.tgz
# Match the checksum printed locally before continuing.
DX_R1_RELEASE="$(date -u +%Y%m%dT%H%M%SZ)"
DX_R1_BACKUP="/var/backups/deltax-r1/$DX_R1_RELEASE"
sudo install -d -m 700 -o "$DX_R1_USER" "$DX_R1_BACKUP"
sudo systemctl stop "$DX_R1_UNIT"

dx_r1_run() {
  sudo systemd-run --quiet --wait --pipe --collect \
    --property="User=$DX_R1_USER" \
    --property="WorkingDirectory=$DX_R1_DIR" \
    --property="EnvironmentFile=$DX_R1_ENV" \
    /usr/bin/env ENVIRONMENT=production R1_RESET_ENABLED=false "$@"
}

# Read-only schema check. Expected prior head: 0014_type_final_publications.
dx_r1_run "$DX_R1_DIR/.venv/bin/python" -m alembic current
# If older/different, stop and review its migration path before proceeding.
# Existing backup helper captures DB and local media; requires pg client tools
# or its documented Docker fallback and permissions for the service user.
dx_r1_run /usr/bin/bash "$DX_R1_DIR/scripts/backup_round1.sh" "$DX_R1_BACKUP"
sudo tar --exclude='__pycache__' --exclude='*.pyc' \
  -C "$DX_R1_DIR" -czf "$DX_R1_BACKUP/code-before.tgz" \
  app migrations frontend/admin frontend/player frontend/audience \
  scripts alembic.ini requirements.txt
sudo tar --no-same-owner -xzf /tmp/deltax-r1-release.tgz -C "$DX_R1_DIR"

# Review the existing service EnvironmentFile without publishing its contents.
sudoedit "$DX_R1_ENV"
# Ensure these exact entries are set (retain all other credentials/settings):
# ENVIRONMENT=production
# R1_RESET_ENABLED=false
# R1_RESET_DATABASE_NAME=
# R1_REHEARSAL_TOKEN=

# This is the explicit production migration step. Execute only after operator
# approval and verified backup. It has NOT been executed by this implementation.
dx_r1_run "$DX_R1_DIR/.venv/bin/python" -m alembic upgrade head
dx_r1_run "$DX_R1_DIR/.venv/bin/python" -m alembic current
# Expected: 0016_rehearsal_reset_guard (head).
sudo systemctl start "$DX_R1_UNIT"
sudo systemctl is-active --quiet "$DX_R1_UNIT"
curl --fail-with-body --silent --show-error "$DX_R1_ORIGIN/health"
curl --fail-with-body --silent --show-error "$DX_R1_ORIGIN/api/leaderboard/final"
```

Verify normal admin login, questions/teams and individual controls; reset must
report unauthorized in production. Check publication through the normal admin UI
only when both rounds end. Do not test destructive reset on the VM competition DB.
If systemd has multiple environment files, credentials, inline settings or a
different Python layout, adapt `dx_r1_run` to the actual unit rather than guessing.
All workers must run the new code together; no rolling mixed-code rehearsal reset.

For application rollback after a failed health check, keep the service stopped,
restore `code-before.tgz`, and restart the original unit. New schema objects are
additive and compatible with the old app, so do not automatically downgrade or
restore the database. Preserve the backup and any error logs for investigation.

## Remaining production limits

- An official tie-break rule at the R2 cutoff is still needed if that tie occurs.
  The published ranking is immutable; no silent post-publication tie resolution
  is provided. Use the organizer's explicitly approved external adjudication.
- R2 ingestion remains an operator/integration step using the canonical frozen
  qualifiers endpoint, because R2/R3 must remain unchanged.
- The schema has one current R1 run, not event-scoped competition history. Reset
  is intentionally limited to fresh attested rehearsal databases. Historical
  competition databases stay separate and are refused by provisioning. An
  attested database must remain exclusively rehearsal data; do not import
  historical competition runs into it later.
- Database owners can bypass database/application protections with privileged
  SQL; restrict production DB access and never issue a rehearsal attestation or
  token there. An attestation copied to another database on the same cluster
  fails its name/OID check. OIDs are cluster-local, so never promote a rehearsal
  clone with matching rehearsal settings/secrets into a competition environment.
  The publication trigger blocks UPDATE, not DELETE/TRUNCATE. Ordinary application
  routes expose no edit/unpublish operation; the explicitly guarded disposable
  rehearsal reset is the only application path that invalidates the final snapshot.
- New migrations/index/trigger need deployment-role privileges. Nginx should
  forward API `Cache-Control: no-store`; browsers may retain previously viewed
  results in memory until their next poll after a rehearsal reset.
- Live VM paths, credentials, PostgreSQL version and installed schema were not
  accessed. The new changes were validated on local PostgreSQL 16. Existing
  framework deprecation warnings on Python 3.14 do not fail tests.


## Modified file inventory

43 source, test, documentation and served-build files remain changed from the
original commit after the corrective pass:

- `.env.example`
- `R1_FEATURE_REPORT.md`
- `README.md`
- `ROUND1_RESET_PATCH.md`
- `app/api/admin.py`
- `app/api/leaderboard.py`
- `app/api/r1a.py`
- `app/api/r1b.py`
- `app/audience.py`
- `app/combined_leaderboard.py`
- `app/config.py`
- `app/models.py`
- `app/rehearsal_safety.py`
- `app/result_export.py`
- `frontend/admin-react/src/api.js`
- `frontend/admin-react/src/main.jsx`
- `frontend/admin-react/tests/browser.mjs`
- `frontend/admin/react-assets/admin.js`
- `frontend/audience/audience.css`
- `frontend/audience/audience.js`
- `frontend/audience/index.html`
- `frontend/player/assets/player.css`
- `frontend/player/assets/player.js`
- `frontend/react/src/main.jsx`
- `frontend/react/src/style.css`
- `frontend/react/tests/audience.browser.mjs`
- `frontend/react/tests/browser.mjs`
- `migrations/versions/add_combined_r1_publication.py`
- `migrations/versions/add_rehearsal_reset_authorization.py`
- `scripts/authorize_rehearsal.py`
- `scripts/test_postgres.sh`
- `tests/test_activation_codes.py`
- `tests/test_audience_database.py`
- `tests/test_combined_ranking.py`
- `tests/test_database_baseline.py`
- `tests/test_player_bootstrap.py`
- `tests/test_r1_corrective.py`
- `tests/test_r1_global_features.py`
- `tests/test_r1a_reconciliation.py`
- `tests/test_r1b_timer_modes.py`
- `tests/test_receipt_reset_race.py`
- `tests/test_rehearsal_authorization.py`
- `tests/test_round_reset.py`
