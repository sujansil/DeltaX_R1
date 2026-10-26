# R1A question-close results and automatic leaderboard

## New player flow

1. A question is revealed by the organiser and runs for 30 server-timed seconds.
2. A team's early submission is accepted and locked, but the player header keeps the score from previously closed questions. Correctness and points are hidden until closure, including after refresh.
3. At the effective deadline, reconciliation records unanswered outcomes and creates one immutable automatic leaderboard for that question.
4. The full-screen points animation plays, the header reflects the revealed total, and the question leaderboard appears automatically. The board shows the top 16 plus the caller's own standing if outside that range.
5. The organiser still reveals the next question manually. A new active question takes priority over any old animation/leaderboard.

This is the shared WizTime (R1A) question flow. R1B has individual relay turns rather than a global question window; its existing score timing, drafts, timer modes and publication controls are unchanged.

## Install

Stop the local application. Extract this ZIP into its own folder, keep apply_patch.py, manifest.json and payload together, and run from that extracted folder:

```bash
python3 apply_patch.py /home/sujan/Projects/round1/round1_system --check
python3 apply_patch.py /home/sujan/Projects/round1/round1_system
```

The baseline is the latest uploaded `round1(1).zip`, including the Codex reset changes. Unexpected local edits cause the installer to stop. It backs up replaced files and can roll back using the printed backup path:

```bash
python3 apply_patch.py /path/to/round1_system --rollback /printed/backup/path
```

Restart with the existing start.sh and hard-refresh player/admin pages. Built React assets are included. No Node build, Python dependency update, database migration or database reset is required. Applying this patch never invokes Reset.

Run the full database suite on your local machine before event use:

```bash
./scripts/test_postgres.sh
```

## Backend implementation

- `app/question_reveal.py`: closed-question visibility, gated totals, versioned automatic snapshot creation, legacy/new snapshot decoding, and shared leaderboard reads.
- `app/r1a_reconciliation.py`: creates the automatic snapshot under its existing exclusive Round lock after timeout outcomes are persisted; repeated worker/request passes reuse the same snapshot. It does not reveal the next question.
- `app/api/player.py`: bootstrap returns the revealed total, masks current-question outcome points/correctness while open, and includes the leaderboard in the same read-only snapshot as identity/state/timing.
- `app/api/r1a.py`: successful submit returns an acceptance marker with null points/result. Actual scoring and stored results are unchanged. The player receives the result at closure via bootstrap.
- `app/api/leaderboard.py`: player R1A reads select automatic question-close snapshots rather than prematurely created manual snapshots. Existing R1B list snapshots stay supported.
- `app/api/admin.py`: adds an authenticated read-only current-leaderboard endpoint for the React results page. The existing reset endpoint is preserved.
- `app/result_export.py`: exports rows from both old list snapshots and the new automatic snapshot envelope.

Automatic snapshots use a versioned JSON envelope in the existing snapshot text column. Their identity is the question position plus exact UTC effective deadline. The existing exclusive Round lock serializes competing publishers. Publication time remains processing time; the envelope separately stores the effective question deadline. No schema or reset-specific tracking columns are introduced.

Questions paused before their deadline remain unrevealed regardless of wall-clock time. Resume uses the existing clock-shifting rules. The enrolment roster determines automatic leaderboard membership. Tie ordering stays descending score then ascending team code.

Early admin End policy is preserved: it keeps accepted scores and does not create premature timeout outcomes. The automatic board remains the last fully closed-question snapshot. Legacy/manual snapshots remain stored and included in Excel; R1A players are no longer served a premature manual snapshot.

## Frontend changes

- Header total no longer changes just because an R1A answer was accepted.
- Hidden outcomes do not trigger a score animation or get marked as already shown during early refresh.
- Question-end bootstrap drives the points reveal and leaderboard; no extra Publish button or 8-second ranking poll is needed for R1A.
- The admin Results tab states that R1A is automatic and shows its latest automatic board. Its R1A Publish control is removed. R1B's existing Publish control is retained.
- Reset handler and development maintenance form are preserved verbatim from the upload. No player/admin layout redesign is included.
- HTML asset URLs receive cache-busting versions.

## Backend features verified in the latest upload

This is a source inspection, not independent production certification.

| Capability | Source findings |
| --- | --- |
| R1A quiz | 23-question start validation, 30-second persisted global timing, manual advance, precise scoring and duplicate guards present |
| R1A expiry | Start-time enrolment snapshot, durable unanswered outcomes, automatic Q23 completion and periodic reconciliation present |
| R1B relay/timer | Four sequential Ready/submit/skip turns; first Ready starts 28 minutes; continuous/paused-handover modes and global pauses present |
| Player sessions | Round-scoped persisted leases; R1A one/R1B four slots; heartbeat, logout, stale-session handling and audited admin revocation present |
| Drafts/recovery | Versioned turn-specific numerical drafts, timeout scoring, idempotent worker finalization and bootstrap restoration present |
| Admin tools | Configuration validation, question/option/image management, teams/password resets, dashboards, publication, exports and audit access present |
| Development reset | Authenticated development-only endpoint, typed confirmation/reason, LIVE rejection, transactional progress clearing, session revocation and preserved credentials/content present |
| Backups | PostgreSQL/media backup and isolated restore-rehearsal scripts present; not rerun against PostgreSQL here |
| React interfaces | Player and admin source plus built assets present |
| Requested automatic R1A reveal | Missing in the uploaded version; added by this patch |

The included reset patch report records an earlier 88-pass PostgreSQL run. That is the previous tool's report, not a run reproduced in this environment. Admin JWT server-side revocation, R2 integration and event-scale stress verification are separate work; this patch does not claim them completed.

## Verification performed here

- Production player and admin builds succeeded.
- 11 player/frontend unit tests and 7 admin/frontend unit tests passed.
- 14 player browser scenario checks passed using API fixtures. They cover unchanged header after early submission, refresh while accepted but hidden, automatic animation/board after close, no replay on reload, manual next-question takeover, plus prior draft/session/mobile flows.
- 22 admin browser workflow checks passed using API fixtures. They cover removal of the R1A Publish button, presence of the reset control, and existing admin workflows.
- Python tests: 19 passed; 81 PostgreSQL-dependent tests skipped because Docker/PostgreSQL are unavailable here. New tests cover exact timing boundaries, pause/end visibility, legacy snapshot decoding, snapshot identity/provenance and own rank outside top 16 using pure/mocked checks.
- Three additional real-PostgreSQL integration tests are supplied for early-score hiding/automatic closure, concurrent reconciler idempotence without advancement, and paused/premature-manual-snapshot privacy. These were NOT run here.
- Automatic leaderboard/gated-score SQL queries compile for the PostgreSQL dialect.
- Reset endpoint AST, reset React handler and reset form were compared with the uploaded code and are unchanged.
- Installer preflight/apply/conflict detection/idempotence/rollback are verified separately during packaging.
- No stress testing was performed.

## Manual checks after installation

Use disposable rehearsal data. Start R1A, answer early, and verify the header stays fixed and says Answer locked. Refresh before 30 seconds; it must stay fixed. At closure, verify the points animation, new total and automatic leaderboard. Leave the page open; question 2 must NOT appear until the admin reveals it. Repeat with a wrong answer, no answer and a global pause. Verify Excel export and Reset on your local database before accepting the patch.
