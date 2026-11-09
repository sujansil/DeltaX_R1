# R1 targeted UI corrections

Scope: two presentation fixes on `feature/cinematic-login-r1`, applied over the existing uncommitted cinematic/audience implementation. This report lists only changes from this task, not earlier work.

## Player layout

The original shared stylesheet caps `.game-shell` at 650px. The Atrium skin now overrides that limit only when the viewport is at least 601px wide:

- Up to 600px: retain the single-column touch interface.
- 601–1023px: use the available width, with two-column MCQ choices and a readable numeric form.
- 1024px and above: use a centered workspace up to 1400px wide, with the existing question and form siblings in balanced CSS Grid columns. The question/timer metadata spans both columns. Question length determines height; long content scrolls naturally.

Question and option images retain native rendering and sizing constraints. Mathematical text can scroll locally if necessary rather than widening the document. The original React gameplay DOM, timer calculations, question rendering, draft handling, submit/skip/READY controls, dialogs, session logic and authentication were not changed. No scene or gameplay animation was added.

`/player/?theme=classic` still disables the Atrium stylesheet and retains the original 650px classic layout. `/player/?login=classic` still selects the original login. The cinematic login and its generated bundles are unchanged.

## Audience Control layout

Audience Control now has a short introduction and three distinct sections: Presentation settings, Current audience display, and R1B post-round review. The two settings have separate explanatory cards and large native-button switches, with accessible names, descriptions, `role="switch"`, `aria-checked`, visible ON/OFF text, keyboard activation and distinct checked/off styling. Current display shows phase, question, synchronization, server timestamp/revision and the audience link. Review actions retain their existing server-provided permissions.

Only Audience Control's rendered markup and its dedicated stylesheet changed. The shared admin stylesheet, sidebar, navigation, other sections, polling, request bodies, revision checks, busy gate, error handling and authorization are unchanged. The generated admin stylesheet includes the new scoped CSS; every new selector begins with `.audience-control`.

## Complete changed-file list for this task

| File | Change |
| --- | --- |
| `frontend/react/src/player-atrium.css` | Responsive, scoped player layout rules |
| `frontend/player/player-atrium.css` | Rebuilt player skin |
| `frontend/admin-react/src/main.jsx` | Audience Control presentation markup and scoped stylesheet import |
| `frontend/admin-react/src/audience-control.css` | New section-scoped styles |
| `frontend/admin/react-assets/admin.css` | Rebuilt admin CSS with scoped additions |
| `frontend/admin/react-assets/admin.js` | Rebuilt admin bundle |
| `frontend/react/tests/browser.mjs` | Four-viewport R1A/R1B and classic-theme checks with synthetic math/media fixtures |
| `frontend/admin-react/tests/browser.mjs` | Switch accessibility/state, stale revision, review gates, responsive layout and screenshots |
| `frontend/react/R1_TARGETED_UI_CORRECTIONS.md` | This report |

Checksum comparison against the start of this task confirmed **208 protected files unchanged**, including backend/migrations, audience code/assets, the entire pre-existing player/login bundle, gameplay source, the shared player stylesheet, and the shared admin stylesheet. Existing uncommitted changes from the previous audience implementation were retained.

## Verification

| Check | Result |
| --- | --- |
| Player state/draft Node tests | 12 passed |
| Admin API Node tests | 7 passed |
| Player browser scenarios | 44 passed |
| Admin browser workflows | 31 passed |
| Existing audience browser regression scenarios | 12 passed |
| Existing cinematic-login browser regressions | 16 passed |
| Backend regressions on disposable PostgreSQL | 164 passed, 1 deselected, 354 warnings |
| Full player/login/audience production build | Passed |
| Admin production build | Passed |
| JavaScript syntax / `git diff --check` | Passed |

Responsive browser checks covered **390×844, 768×1024, 1366×768 and 1920×1080**, R1A and R1B, themed and classic layouts, long mathematical questions, KaTeX, question/option images, selection/submission eligibility, disabled controls, numeric draft autosave, skip confirmation/cancellation, READY/handover, logout confirmation and revoked-session recovery. Tests assert no horizontal page overflow or controls extending beyond the viewport. Long content is allowed to extend vertically and remains reachable by normal scrolling.

Admin checks covered both switches ON/OFF, Space-key activation, unchanged complete settings payloads, stale-revision 409 errors without a false switch update, recovery via existing polling, Start/Next permissions, and control bounds/touch targets at every requested viewport. Existing login regressions cover both login presentations, activation, session restoration/logout, errors, keyboard/reduced motion and WebGL fallbacks. Existing audience regressions cover phases/disclosure, publications, reconnection and scene cleanup.

Database tests used a **new disposable container**, a guarded loopback `round1_test` database, ORM-created test tables and an empty revision-metadata fixture needed by the export regression. No migrations or revision stamping were performed. `test_migrations_match_orm_metadata` was deliberately deselected because it requires an Alembic-migrated schema; no migration verification is claimed. No existing database, credentials, Redis or real application services were changed. The container was stopped after verification.

Browser tests use synthetic API responses; no live account credentials or real competition state were exercised. Screenshots were reviewed locally. Physical-device and projector testing remain outside this browser-based check.

## Screenshots

All screenshots are local ignored artifacts under `.preview/r1-ui-corrections/`:

- Desktop player: `player/responsive-R1A-1920.png`
- Laptop player: `player/responsive-R1A-1366.png`
- Tablet player: `player/responsive-R1A-768.png`
- Mobile player: `player/responsive-R1A-390.png`
- R1B variants: `player/responsive-R1B-1920.png`, `responsive-R1B-1366.png`, `responsive-R1B-768.png`, `responsive-R1B-390.png`
- Audience Control: `admin/audience-control-1920.png`, `audience-control-1366.png`, `audience-control-768.png`, `audience-control-390.png`

Full-page screenshots include content below the viewport deliberately; they do not represent a fixed-height page or clipped controls.

## Build and check commands

```bash
cd ~/Projects/DELTAX/round1_system/frontend/react
npm run build
npm test
DX_SCREENSHOTS=../../.preview/r1-ui-corrections/player npm run test:browser
npm run test:audience
npm run test:cinematic

cd ../admin-react
npm run build
npm test
DX_SCREENSHOTS=../../.preview/r1-ui-corrections/admin npm run test:browser
```

Backend command after preparing a new disposable ORM-only test database and the existing guard environment: `.venv/bin/python -B -m pytest -p no:cacheprovider -q --disable-warnings --tb=short -k 'not test_migrations_match_orm_metadata'`. Do not point these tests at an existing application database.

For the existing local FastAPI rehearsal server, hard-refresh `/player/` and `/admin-ui/#audience`. Compare `/player/?theme=classic` and `/player/?login=classic`. No new server, backend configuration or migration is needed for these fixes.

No backend files, R2, deployment configuration or production resources changed. Nothing committed or pushed.
