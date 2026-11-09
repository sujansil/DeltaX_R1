# R1 Infinite Atrium audience implementation

Implemented locally on `feature/cinematic-login-r1`. Nothing committed, pushed or deployed. R2 was not modified.

## Presentation behavior

The audience uses a separate native HTML/CSS/JavaScript display with a dedicated React/Three.js scene wrapper. It reuses the existing Atrium scene without importing the login form or authentication owner. The player change is scoped CSS only.

R1A officially live questions always win, including while paused. Closing a question releases its correct answer independently of either audience switch. Existing AUTO scheduling, captured rules, deadlines and pause/resume shifts determine presentation; audience reads never reconcile or advance gameplay.

Default eight-second AUTO results interval:

| Leaderboard | Upcoming preview | Presentation within the existing interval |
| --- | --- | --- |
| OFF | OFF | Answer throughout |
| ON | OFF | Answer for 3 seconds, then answer with matching published top-16 standings |
| OFF | ON | Answer for 3 seconds, then full-screen next-question preview |
| ON | ON | Answer for 3 seconds, answer with standings for 2.5 seconds, then full-screen preview |

Short intervals proportionally reduce these stages. Zero-length intervals add no delay. With no upcoming question, the answer/optional standings remain. Preview replaces the entire prior answer screen, as explicitly clarified by the organizer; it is not a secondary pane. In MANUAL mode the answer stage is three seconds, followed by a three-second standings stage when both switches are ON, then preview when enabled. Without preview, the answer/optional standings remain until existing admin advancement starts the next question. No audience control advances questions.

R1B remains cinematic standby throughout asynchronous team play, with no individual questions, answers or clocks. Only the entire round's `ENDED` status enables explicit Start review. Next advances Question 1 → Answer 1 → Question 2 → Answer 2, etc. The selected slide persists. After review the one global leaderboard switch chooses the existing final published top 16 or a thank-you screen. Missing final publication shows an awaiting-results state; scores are never calculated here.

Admin Audience Control retains the existing admin styles and authentication. There are exactly two switches: upcoming preview and the one leaderboard switch shared by both rounds.

## Isolation and public disclosure

- `GET /api/public/audience`: explicit public-field whitelist, no-store, read-only repeatable-read transaction. Polling is every 500 ms after completion of the previous request; failures preserve the last valid content.
- `GET /api/admin/audience`: existing administrator authorization, plus control/review metadata.
- `PUT /api/admin/audience`: existing administrator authorization, strict settings and expected revision.
- `POST /api/admin/audience/review`: existing administrator authorization, strict Start/Next action and expected revision, server-side ENDED gating.
- Durable full-state records use validated, versioned `AdminAction.details` under `AUDIENCE_STATE_V1`. A dedicated PostgreSQL transaction advisory lock serializes writes; stale requests return 409. Review state is tied to the round ID/start timestamp. No model changes or new migrations.
- Correct answers appear only on server-selected answer stages and are matched to question ID/position in the browser. Relay standby never fetches individual question content. Public responses exclude submissions, drafts, credentials, sessions, raw audit records and extra fields in stored leaderboard JSON.
- Existing immutable publications retain their ranks, order, ties and score values. R1A publications must match the displayed question unless final; R1B accepts only final publication. The browser animates keyed positions only for a new publication ID.
- Reduced motion cancels transitions and stops ambient animation. WebGL failure leaves functional public content over a CSS Atrium environment. Page hide/unmount aborts requests, cancels animations/timers and unmounts/disposes the scene; browser-history restoration creates a fresh owner.

## Verification results

| Check | Result |
| --- | --- |
| Full Python suite against new disposable PostgreSQL | **165 passed**, zero skipped; 355 warnings reported |
| Player state/draft Node tests | **12 passed** |
| Admin API Node tests | **7 passed** |
| Audience deterministic browser scenarios | **12 passed** |
| Player browser workflows, including themed/classic native actions | **28 passed** |
| Admin browser workflows, including mobile Audience Control | **25 passed** |
| Existing cinematic-login browser regression suite | **16 passed** |
| Full player/login + separate audience production build | Passed |
| Admin production build | Passed |
| JavaScript syntax and `git diff --check` | Passed |

Audience coverage includes all stages, AUTO/MANUAL flags, short/zero intervals, pause, server-selected live priority, exact numeric answers, KaTeX/media, disclosure boundaries, ties/top-16, long/special names, score changes, XY rank movement across columns, publication-only motion, reduced motion, connectivity failure/recovery, stale responses, history restoration, WebGL failure/context loss and cleanup. Database cases prove concurrent admin conflicts, concurrent review advancement without skipping, persistence in a new Python process, role boundaries and corrupt-record failure without leaks. Existing gameplay tests ran against the same isolated fixture database.

Only a newly created disposable PostgreSQL container/database was initialized for testing using existing migrations. Existing databases, Redis instances and real application credentials were not changed. No live FastAPI lifecycle or real-account authentication was exercised; browser workflows use synthetic API fixtures, and database HTTP tests use isolated ASGI fixtures. The temporary preview server and disposable test container were stopped after verification. Preview route/asset checks also passed, including the refusal of admin API requests.

Protected-file checksums confirm unchanged gameplay, scoring, authentication, session/device handling, rules, player application source, draft queue, cinematic-login source, admin CSS and **all pre-existing player/login generated assets**. The old unused `frontend/player/app.js` is untouched.

## Local visual preview (no backend or database)

```bash
cd ~/Projects/DELTAX/round1_system/frontend/react
npm ci
npm run build
npm run preview:audience
```

Open `http://127.0.0.1:8771/audience/?demo=ANSWER_LEADERBOARD&motion=1`.

Other `demo` values: `STANDBY`, `PREVIEW`, `QUESTION`, `ANSWER`, `RELAY_STANDBY`, `REVIEW_READY`, `REVIEW_QUESTION`, `REVIEW_ANSWER`, `LEADERBOARD`, `THANK_YOU`. `motion=1` creates fictional publications every seven seconds. Add `&render=fallback` to check the static environment. OS/browser reduced-motion preference is respected. This loopback-only preview serves fictional public fixtures; every other API returns 503. It cannot authenticate teams or operate the admin panel.

Build admin assets separately:

```bash
cd ~/Projects/DELTAX/round1_system/frontend/admin-react
npm ci
npm run build
```

## Real FastAPI rehearsal

Use an **already initialized, approved local rehearsal copy** of R1 with PostgreSQL and Redis running and existing test users/questions. No migration, seeding, reset or credential change is necessary for this integration. The inspected `.env` points to loopback PostgreSQL on 5432/database `round1`, and loopback Redis on 6379; credentials are deliberately omitted here. Loopback alone cannot distinguish a rehearsal database from a forwarded live service: verify which local services own those ports before starting.

Starting FastAPI runs the existing reconciliation workers, and normal gameplay/admin actions write to that configured database. Do not run this against a production copy or a tunnel to Azure. The following refuses remote endpoints, unexpected ports/databases, query options and production environment labels; it does not provision anything:

```bash
cd ~/Projects/DELTAX/round1_system
.venv/bin/python -B - <<'PY'
from urllib.parse import urlsplit
from app.config import get_settings
import uvicorn

s = get_settings()
pg, cache = urlsplit(s.database_url), urlsplit(s.redis_url)
local = {"localhost", "127.0.0.1", "::1"}
assert s.environment.lower() in {"development", "dev", "test"}, "Not a local environment"
assert pg.scheme == "postgresql+asyncpg" and pg.hostname in local, "Nonlocal PostgreSQL"
assert (pg.port or 5432) == 5432 and pg.path == "/round1" and not pg.query, "Unexpected PostgreSQL target"
assert cache.scheme == "redis" and cache.hostname in local, "Nonlocal Redis"
assert (cache.port or 6379) == 6379 and cache.path in {"", "/0"} and not cache.query, "Unexpected Redis target"
uvicorn.run("app.main:app", host="127.0.0.1", port=8001)
PY
```

If your approved rehearsal services use different local ports, deliberately adjust the assertions and process environment to that known copy. Do not silently use whatever database is listening. A static Python HTTP server cannot serve these API/control flows.

| Page | Local URL |
| --- | --- |
| Audience | `http://127.0.0.1:8001/audience/` |
| Audience without WebGL | `http://127.0.0.1:8001/audience/?render=fallback` |
| Admin Audience Control | `http://127.0.0.1:8001/admin-ui/#audience` |
| Player | `http://127.0.0.1:8001/player/` |
| Classic gameplay theme | `http://127.0.0.1:8001/player/?theme=classic` |
| Original login form | `http://127.0.0.1:8001/player/?login=classic` |

Rehearse with existing local admin/test accounts: check both switches during DRAFT/live/results; pause/resume; test AUTO then MANUAL through existing controls; verify the next live question immediately replaces preview/results; run asynchronous R1B with audience standby; after the entire round ends explicitly Start/Next through every review slide; publish final results through existing Results controls. Check refresh/reconnection on the current review slide. Use only the rehearsal copy because these normal operations change competition state.

## Reproducing automated checks

From `frontend/react`: `npm test`, `npm run test:browser`, `npm run test:audience`, `npm run test:cinematic`, `npm run build`.

From `frontend/admin-react`: `npm test`, `npm run test:browser`, `npm run build`.

For database regressions, the existing root `scripts/test_postgres.sh` creates its own uniquely named disposable Compose project, initializes only its new `round1_test` database with existing migrations, sets the existing safety guard and removes that project afterward. Run `bash scripts/test_postgres.sh` from the project root with Docker available. Never aim database tests at an existing rehearsal or production database. This implementation was verified using an equivalent separately created disposable PostgreSQL container, explicitly guarded test settings, and `python -B -m pytest -p no:cacheprovider -q --disable-warnings --tb=short`.

## Screenshots

Local, ignored browser artifacts (not committed):

- `../../.preview/r1-audience/STANDBY-1920.png`
- `../../.preview/r1-audience/QUESTION-1920.png`
- `../../.preview/r1-audience/ANSWER_LEADERBOARD-1920.png`
- `../../.preview/r1-audience/REVIEW_ANSWER-1920.png`
- `../../.preview/r1-audience/LEADERBOARD-1280.png`
- `../../.preview/r1-audience/QUESTION-390.png`
- `../../.preview/r1-audience/admin-audience.png`
- `../../.preview/r1-audience/admin-audience-mobile.png`
- `../../.preview/r1-player/player-R1A-atrium.png` and `player-R1A-classic.png`
- `../../.preview/r1-player/player-R1B-atrium.png` and `player-R1B-classic.png`

## Limits and operational notes

- No physical projector, real mobile keyboard or production-scale load test was performed; responsive screenshots and browser tests cover desktop/mobile layouts and reduced motion.
- A zero/very short existing results interval cannot guarantee a visible three-second answer. Existing gameplay timing takes priority.
- Presentation state requires retaining its `AUDIENCE_STATE_V1` audit records. Deleting those records loses its flags/review position. An invalid latest record fails closed with 503 and requires deliberate administrator recovery rather than silently accepting malformed state.
- If both rounds are accidentally live/paused, R1A has deterministic display priority. Otherwise active/ended R1B takes over. This adds no new round selector or gameplay policy.
- Missing question content produces an explicit unavailable state; missing eligible standings never trigger calculation or substitution. If the entire scene module fails to load, escaped plain text remains functional; the normal WebGL fallback still renders KaTeX.
- The player classic-theme switch affects gameplay styling; the separate existing `login=classic` switch controls the login form. No pre-existing R1 audience application existed to retain as a classic audience route.

## Complete changed-file manifest

The following manifest includes source, tests, documentation and every generated asset. Existing files are marked `M`; additions are marked `A`.

<!-- FILE_MANIFEST -->

```text
A app/api/audience.py
A app/audience.py
M app/main.py
M frontend/admin-react/src/main.jsx
M frontend/admin-react/tests/browser.mjs
M frontend/admin/react-assets/admin.js
A frontend/audience/assets/THIRD_PARTY_NOTICES.txt
A frontend/audience/assets/chunk-E4EH3YP5.js
A frontend/audience/assets/chunk-EX7TRSJS.js
A frontend/audience/assets/chunk-G2JQPR7G.js
A frontend/audience/assets/chunk-KI35B3P3.js
A frontend/audience/assets/chunk-UBDLCK7R.js
A frontend/audience/assets/ibm-plex-mono-latin-400-normal-4MWPPJAM.woff
A frontend/audience/assets/ibm-plex-mono-latin-400-normal-A2WATXFY.woff2
A frontend/audience/assets/manrope-cyrillic-ext-wght-normal-2GMQGZDT.woff2
A frontend/audience/assets/manrope-cyrillic-wght-normal-F76KYTZG.woff2
A frontend/audience/assets/manrope-greek-wght-normal-NNUWM3E7.woff2
A frontend/audience/assets/manrope-latin-ext-wght-normal-6XGHMEDV.woff2
A frontend/audience/assets/manrope-latin-wght-normal-73PUZEUA.woff2
A frontend/audience/assets/manrope-vietnamese-wght-normal-HA5TFICD.woff2
A frontend/audience/assets/outfit-latin-ext-wght-normal-5Z2WCAL6.woff2
A frontend/audience/assets/outfit-latin-wght-normal-5FXBKKJF.woff2
A frontend/audience/assets/scene.css
A frontend/audience/assets/scene.js
A frontend/audience/audience.css
A frontend/audience/audience.js
A frontend/audience/index.html
M frontend/player/index.html
A frontend/player/player-atrium.css
A frontend/react/R1_AUDIENCE_IMPLEMENTATION.md
A frontend/react/audience-build.mjs
A frontend/react/audience-fixtures.mjs
A frontend/react/audience-preview.mjs
M frontend/react/package.json
A frontend/react/src/audience/scene.tsx
A frontend/react/src/player-atrium.css
A frontend/react/tests/audience.browser.mjs
M frontend/react/tests/browser.mjs
A tests/test_audience.py
A tests/test_audience_database.py
```
