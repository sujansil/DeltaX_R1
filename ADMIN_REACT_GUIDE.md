# Delta X React admin console

Simple forms and tables, backed by the existing Round 1 APIs. This patch does not change the player UI, scoring, timers, session limits, or round migrations.

## Install

Extract the patch into a separate folder. Stop the local application, open a terminal in the extracted `round1_admin_react_patch` folder, and run:

```bash
python3 apply_patch.py /home/sujan/Projects/round1/round1_system --check
python3 apply_patch.py /home/sujan/Projects/round1/round1_system
```

The installer accepts the original uploaded ZIP or the earlier player-frontend overlay as baselines. It refuses unknown local changes. Replaced files are backed up beside your project, and the backup path is printed. Reapplying an identical patch makes no additional changes.

Start the application with your existing `./start.sh`, then open:

http://127.0.0.1:8000/admin-ui/

Reload the page after applying. Production assets are already built: no Node installation, Python dependency update, or database migration is required. Restart the application to load the new development setup API.

Rollback before making later edits to patched files:

```bash
python3 apply_patch.py /path/to/round1_system --rollback /backup/path/printed/above
```

## What is available

| Section | Existing backend capabilities exposed |
| --- | --- |
| Overview | Initialize missing round records; validate setup; start, pause, resume, permanently end; reveal the next R1A question with expected-position protection; inspect current R1A outcome counts and paginated R1B progress |
| Clock policy | Read/change R1B timer mode before start; show locked state afterward |
| Questions | List, read, create, update and delete; enable/disable; add/edit/delete options and select correctness; numerical answers/tolerances; question and option image uploads |
| Teams | Paginated directory, creation, detail/edit, pre-enrolment enablement, password reset that revokes sessions, and one-time activation-token display for newly created teams |
| Sessions | Per-team round/device-claim/lease listing, revoke one session or all team sessions with a required reason |
| Submissions | Round/question/team filters; pagination; optional polling; submitted inputs, timeouts, sources, draft provenance and score outcomes |
| Results | Publish an immutable leaderboard snapshot, inspect the publication returned by that action, and download real Excel results for R1A/R1B/both |
| Audit log | Paginated attributed administrative actions |

The browser does not invent rules: all permissions, timing, content locks, scoring, ranking and finalization remain authoritative in FastAPI/PostgreSQL. Buttons reflect the latest received state; the backend resolves any race with another admin. Confirmation dialogs precede permanent actions. Requests are not silently retried after timeouts.

Create questions first, then attach options/images. These are separate existing API operations; there is no pretend atomic bulk-question endpoint. If an upload fails, the created question remains visible for correction. Run round validation before start. Lists of at most 27 question positions fit the question endpoint's 100-item page; team, relay, outcome and audit lists are paginated.

The relay progress table and R1A controls show server snapshots, refreshed periodically. They are not independent timing authorities. Tables scroll horizontally on smaller phones. No external font host or CDN is used.

## Admin login and recovery

Admin and demo-team credentials are separate. The login screen distinguishes incorrect credentials from server connection failures and expired tokens.

The only production backend file changed is `app/api/auth.py`. It adds:

```
POST /api/auth/admin/setup
Content-Type: application/json
{"username": "your-new-admin", "password": "your-chosen-password"}
```

This is **development-only**, matching the existing provisioning policy. It accepts JSON rather than putting passwords into query strings. It returns no password/hash. Existing usernames return 409 and are NEVER reset by setup. All non-development environments return 404. Login, hashing and existing legacy provisioning behaviour are otherwise preserved.

In the login page, expand Login help and choose Create a different development admin. Use a new username. If the server is not development mode, the form explains that setup is unavailable.

For a forgotten password in the local `start.sh` preview, the recovery helper is now included in a fixed location. Run from the project root with preview PostgreSQL running:

```bash
.venv/bin/python scripts/reset_preview_admin.py
```

It lists admin usernames and prompts for a new password twice without displaying it. It targets only the known local preview database and does not change teams, questions, scores or progress. It does not reset disabled accounts or revoke already issued admin JWTs. No general production admin password-reset API has been invented.

Sign out clears this browser's admin token. The existing backend has no admin-session revocation endpoint; sign out does not invalidate copied JWTs on other devices. Production provisioning and revocation policy remain separate backend work.

## Source and build

- React source: `frontend/admin-react/src/`
- Bundled app: `frontend/admin/react-assets/`
- Entry HTML: `frontend/admin/index.html`
- The old admin JavaScript/CSS remain as unused files for compatibility; the new HTML loads only the React bundle.

To edit with Node 20+:

```bash
cd frontend/admin-react
npm ci
npm test
npm run build
```

`react-assets/` is owned build output and is replaced on rebuild. Do not hand-edit it.

Optional synthetic browser workflow tests:

```bash
npx playwright install chromium
npm run test:browser
```

Fixtures exist only in tests. The app itself calls the real APIs. The earlier player browser smoke test's admin portion is updated only if that test already exists in your project; its player assertions are unchanged.

## Verification and limits

- Production React build succeeded.
- Seven admin frontend unit tests passed: lifecycle actions, clock formatting, structured errors, JSON credential handling, multipart boundary preservation, export response validation and authorization error handling.
- Twenty admin browser workflow checks passed using API fixtures: login failure, development setup conflicts, successful login, timer mode, validation, question/option creation/editing, team editing, password reset confirmation, session revocation, submission filters, publication/download requests, audit, start confirmation, content locks, expired-token recovery and mobile layout.
- Four new FastAPI tests passed with a fake database for development setup guards, no reset of existing accounts, JSON response secrecy and validation. Together with the existing non-database tests: 10 passed, 73 database tests skipped.
- The prior player/browser smoke suite also passed: 10 checks using API fixtures.
- Full live PostgreSQL, real export contents, multi-worker behaviour and stress/load verification were not rerun here. Docker/PostgreSQL are unavailable in this environment. Run `./scripts/test_postgres.sh` locally after installation.

No bulk import, score correction, audience screen, database restore web interface, automatic backup, or new admin role system was added because the inspected backend does not expose those capabilities.
