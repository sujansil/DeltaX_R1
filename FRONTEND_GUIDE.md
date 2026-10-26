# Delta X mobile frontend

This patch replaces the player page with a real React application, using Three.js for the live 3D login surface and CSS/SVG/requestAnimationFrame for transitions. The admin remains a basic functional page. It is designed primarily for phones, with a usable desktop layout.

## Apply and run

Stop the local application, extract the patch ZIP into its own folder, then run:

```bash
python3 apply_patch.py /absolute/path/to/round1_system --check
python3 apply_patch.py /absolute/path/to/round1_system
```

The installer compares replaced files with the uploaded baseline. It refuses unexpected local edits and backs up overwritten files before applying. It never changes databases, credentials, media, `.env`, or `.venv`. Reapplying an identical patch is harmless. The command prints the backup directory; to undo later:

```bash
python3 apply_patch.py /absolute/path/to/round1_system --rollback /path/printed/by/the/installer
```

Run your existing `./start.sh` and visit `/player/` and `/admin-ui/`. Reload the browser after updating. No Node installation or frontend build is needed to run the included assets. This patch requires no migration or new Python dependency. It expects the supplied backend at schema head `0008_r1b_drafts_timeout`.

## Player interface

- A full viewport of slowly rotating 3D mathematics, followed by a scroll-down login form. A keyboard skip link goes directly to login.
- Muted charcoal, steel-blue lines, warm-white questions and subdued sage controls.
- The question is the main content. Team name, current-round score and **last published** rank stay secondary.
- MCQ text/images and numerical input. Inline `$...$`, display `$$...$$`, `\(...\)` and `\[...\]` equations are rendered by self-hosted KaTeX.
- Relay Ready, waiting, pause, completion and expiry screens. Inactive slots never fetch another player's question through a separate question request.
- Serialized, versioned numerical autosave. Polling does not overwrite typing. Refresh explicitly restores the server draft; a conflict asks for restoration rather than silently overwriting another tab.
- Full-screen points reveal after R1A question closure or completion of the caller's relay turn. It uses finalized server outcomes, is not replayed just because the page reloads, and yields immediately to a new active question/Ready screen. If the client misses the result interval entirely, it does not obscure the next question just to show an old animation.
- Counts are for the current session round, not a combined R1A+R1B total. Rank comes only from published snapshots; it is never estimated client-side.
- Heartbeats, server logout, best-effort departing hints and 401 recovery preserve the existing lease/session policy. A closed browser cannot be detected with certainty. Browser suspension can still cause a lease to expire. Duplicated tabs share the stored session identity.

Animations respect reduced motion. The 3D renderer limits resolution/frame rate, stops drawing off-screen/when hidden, and has a static fallback if WebGL or loading fails. No CDN, external font host or image-generation asset is required to play. Animations never control game deadlines or scoring.

## Backend change

`GET /api/player/bootstrap` adds `score_total`, the caller team's current-round sum of `ScoreEvent.points`, read in the existing consistent database snapshot. Only this API file changes backend behaviour; scoring, timer, session and migration logic are unchanged. An isolated PostgreSQL test for cross-team/cross-round score isolation is included.

## Admin additions

The existing controls are retained. Added timer-mode selection, R1B team-progress inspection, question/option text and image editing, team editing/password reset and attributed admin-log access. Server content locks still decide what is editable. Timeout rows now display timeout timestamps instead of January 1970. Audience work is intentionally deferred.

## Editing and rebuilding

Source: `frontend/react/src/`. Built app: `frontend/player/assets/`.

With Node 20+:

```bash
cd frontend/react
npm ci
npm test
npm run build
```

`assets/` is exclusively build output and is replaced on rebuild. Do not hand-edit it. The old `frontend/player/style.css` is retained because the admin uses it; the new player CSS is separate.

Browser contract tests (synthetic API fixtures, not a live backend):

```bash
npx playwright install chromium
npm run test:browser
```

The browser test starts its own loopback static server. Fixtures exist only in the tests, never in the production app.

Full backend verification on your machine:

```bash
./scripts/test_postgres.sh
```

## Checks performed for this patch

- Production React build succeeded.
- Ten frontend unit tests passed (draft serialization, clearing, pause, conflict/failure recovery, disposed turns, timing, result gating and API errors).
- Ten browser scenario checks passed in Chromium using API fixtures: 3D login/scroll, narrow layout, answer locking, delayed result reveal, no replay on reload, revocation, autosave without input reset, draft restoration and manual submission, inactive-slot privacy/reduced motion, and admin timer/question-editor rendering.
- Python module import/schema compilation succeeded; non-database pytest suite passed (6 passed, 73 database tests skipped). Docker/PostgreSQL are unavailable in the build environment, so the full database suite and new score-isolation integration test must be run locally.
- No claim of stress testing, real-phone performance certification, or full live-backend end-to-end verification is made.

## Manual rehearsal after applying

1. Login on a phone: scroll opening, activation/sign-in, password visibility and logout.
2. Run an R1A question, submit, wait for closure and score reveal. Refresh after acceptance; it must stay locked. Publish a board and check the last-published rank.
3. Use four separate device sessions for R1B. Check each slot's Ready/waiting permissions. Test both timer modes using separate fresh preview rounds.
4. Type a numerical draft, wait for Saved, refresh, and verify it. Test skip, timeout, pause/resume and admin End. Unsaved text is not guaranteed at expiry.
5. Revoke a session, reconnect within/outside its lease, inspect admin controls and download Excel.
6. Run the full PostgreSQL suite before accepting the backend addition. Stress testing remains after R2 integration.
