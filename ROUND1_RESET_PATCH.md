# Round 1 development reset patch

**Superseded:** use [R1_FEATURE_REPORT.md](R1_FEATURE_REPORT.md) and the current
source. The old development-only guard and bundled reset-patch ZIP do not meet
the current rehearsal database authorization requirements. Reset now requires an
operator-attested disposable rehearsal database and rejects LIVE and PAUSED rounds.

## Install

1. Extract the ZIP at the project root, preserving paths.
2. No database migration is required. Do not remove or recreate PostgreSQL data or Docker volumes.
3. If rebuilding the admin bundle instead of using the included production assets, run:

   ```sh
   cd frontend/admin-react
   npm ci
   npm run build
   ```

4. Restart the application normally. The maintenance panel appears under **Overview** only when `ENVIRONMENT=development`.

Applying or building this patch never invokes the reset. An authenticated admin must enter a reason, type `RESET ROUND 1` exactly, and press the reset button while neither round is `LIVE`.

## Verification performed

- `npm test` in `frontend/admin-react`: **7 passed, 0 failed**.
- `npm run build` in `frontend/admin-react`: **passed**; production assets generated.
- `.venv/bin/python -m py_compile app/api/admin.py tests/test_round_reset.py`: **passed**.
- `./scripts/test_postgres.sh`: **88 passed, 0 failed** against a disposable PostgreSQL 16 container with tmpfs storage. The run included five reset tests covering authorization/environment gating, exact confirmation, live-round rejection, preserved configuration/audit history, complete state clearing, successful restart of both rounds, old-token rejection, round-lock ordering, and transaction rollback on forced failure.

The PostgreSQL run emitted 296 existing deprecation/metadata warnings and no test failures. Its disposable container and volume were removed by the test harness.
