# R1 Infinite Atrium integration

Implemented locally on 9 November 2026 in `/home/sujan/Projects/DELTAX/round1_system`.

## Open and build

Use the existing R1 local server:

- `/player/` — cinematic Infinite Atrium login when signed out.
- `/player/?login=classic` — original scroll-down login and activation presentation.
- `/player/?render=fallback` — atmospheric WebGL fallback, useful for local review.

The server and API configuration are unchanged. There is no additional frontend server or SPA router. Existing authenticated sessions continue directly into R1, including after refresh.

```bash
cd /home/sujan/Projects/DELTAX/round1_system/frontend/react
npm ci
npm run build
npm test
npm run test:browser
npm run test:cinematic
```

Reload the local player page after building. Node is needed to build, not to serve the finished frontend. Browser tests require Playwright Chromium (`npx playwright install chromium` if absent).

## Authentication and activation

`src/main.jsx::Login` still owns the single existing submit handler. Both presentations render the same form and call that handler. The cinematic shell receives the form as children and has no authentication, credential storage, or navigation implementation.

Verified byte-for-byte against the repository baseline:

- Existing login/activation `submit()` handler, including username trimming and unchanged password/token values.
- Entire application source from `useClock()` onward, including the login callback, token storage, bootstrap, heartbeat, departure, logout, 401 recovery, cross-tab handling, questions, timers, and scoring presentation.
- `src/api.js`, `src/draft-queue.mjs`, the original `src/style.css`, and unused `frontend/player/app.js`.

Unchanged contracts:

```text
POST /api/auth/team/login
{ username, password }

POST /api/auth/team/set-password
{ username, activation_token, new_password }
```

Successful login immediately invokes the existing `onLogin(access_token)` callback. It stores `r1token` and starts the existing R1 connection/bootstrap flow. Animation never delays or gates that callback. Activation keeps its native required/minimum/maximum validation, server errors, success notice, and return to sign-in. The prototype's demo authentication and mock welcome were not imported.

## Visual implementation

Only the Atrium scene, control types, and relevant visual behavior were reused from `deltax-login-lab`. The React application, router, Round 2 engine, preview launcher, demo form, and preview images were not copied.

- `src/cinematic/CinematicLogin.jsx` supplies the presentation, drag/tap/focus interactions, pause/reduced motion, GSAP entrance, and visual-viewport keyboard handling.
- `src/cinematic/AtriumWorld.tsx` lazily loads the actual scene, creates the Canvas and postprocessing, and handles unavailable/lost WebGL.
- `src/cinematic/Atrium.tsx` contains procedural geometry, materials, lighting, and inline GLSL. Its mobile framing is adapted to the real R1 form and activation controls.
- `src/cinematic/AtmosphericFallback.jsx` is independent of Three.js, allowing the form to remain usable when the visual bundle cannot load.
- `src/cinematic/login.css` scopes every normal style rule beneath `.dx-login`. Local font-face declarations are the only shared font registrations; gameplay typography and styles remain unchanged.

React and React DOM remain at **19.2.0**, Three.js at **0.180.0**, and the existing esbuild build remains in use. Added packages are pinned: Fiber 9.8.2, Drei 10.7.9, React Three Postprocessing 3.2.2, postprocessing 6.39.6, GSAP 3.15.1, Manrope/Outfit font packages 5.2.8, and IBM Plex Mono 5.2.7. Assets and fonts are served locally. Added dependency notices are included in the generated notices file.

## Lifecycle and keyboard behavior

The scene belongs only to the signed-out login subtree. Existing successful authentication unmounts it before the existing game interface appears. The tests instrument WebGL and verify buffer/texture deletion, context release, and no further draw calls during gameplay.

React Three Fiber owns renderer teardown after Drei's framebuffer/render-target cleanup. The custom geometry supplied directly as a mesh property has an explicit disposal effect. Event listeners, media-query handlers, keyboard resize callbacks, and GSAP animations are cleaned up on unmount. Ambient rendering is suspended when the document is hidden. System reduced motion and the local pause control freeze ambient animation.

The form scrolls naturally, has 16 px mobile inputs, and keeps safe-area spacing. On keyboard-related visual-viewport changes, additional bottom scroll space is reserved and the focused input or button is brought into the visible portion of the page. The normal layout viewport and a shortened visual viewport are handled separately.

## Final verification results

| Check | Result |
| --- | --- |
| `npm run build` | Passed; self-hosted player assets regenerated |
| `npm test` | 12 existing unit tests passed |
| `npm run test:browser` | 24 existing browser scenario checks passed |
| `npm run test:cinematic` | 16 cinematic integration checks passed |
| `git diff --check` | Passed |
| Source invariants | Existing auth handler and application/session/gameplay source verified unchanged as described above |
| Protected paths | No changes to backend, migrations, existing backend tests, admin frontends, API helper, original stylesheet, draft queue, or unused legacy player script |
| R2 | Working tree remains unchanged |

The existing browser suite keeps its original scroll-down presentation assertion against `?login=classic`; its gameplay assertions remain intact and other scenarios exercise the cinematic entry. Diagnostic stack output was added for browser errors.

The integration checks cover:

1. Actual WebGL rendering and visible submit controls at 390 × 844.
2. Actual WebGL rendering and visible submit controls at 360 × 740.
3. Actual WebGL rendering and visible submit controls at 1440 × 900.
4. Invalid credentials, existing error text, busy lock, unchanged payload, and retry availability.
5. Enter submission, password visibility, one login request, bearer bootstrap/heartbeat, and entry into the existing R1 question interface.
6. GPU resource disposal and no login rendering during gameplay.
7. Refresh restoration without a second login request.
8. Existing authenticated logout and return to cinematic login with the existing notice.
9. Cinematic activation validation, exact token/password payload, failure, success notice, and subsequent sign-in.
10. The same activation and sign-in contract through the classic fallback.
11. Existing 401 recovery and session notice.
12. Stable reduced-motion scene, keyboard Tab/Enter navigation, and a 360 × 480 viewport.
13. A modeled Safari-style visual viewport shrink with unchanged layout height; focused password and submit stay above the modeled keyboard.
14. Authentication with WebGL unavailable.
15. Authentication after WebGL context loss.
16. Authentication after failure to load the visual JavaScript chunk.

Tests use synthetic API fixtures and loopback static servers, which close after completion. No live login, backend process, database, Redis, or session policy was exercised or changed. Expected HTTP errors and a deliberately failed asset load are distinguished from unexpected JavaScript/rendering errors.

Rendered screenshots were inspected for framing, contrast, input/button visibility, and overflow. They are local ignored QA artifacts:

- `.preview/cinematic-login/r1-390x844.png`
- `.preview/cinematic-login/r1-360x740.png`
- `.preview/cinematic-login/r1-1440x900.png`
- `.preview/cinematic-login/r1-short-viewport.png`

## Verification limits and unresolved items

- No physical phone or native software keyboard was available. Keyboard behavior was verified through shortened viewport and visual-viewport simulation; a real-device Safari/Chrome rehearsal remains advisable.
- No live-backend authentication/session rehearsal was performed. The production handler is unchanged and its frontend contract was verified with fixtures.
- `npm audit --json` reports one **pre-existing low-severity advisory in KaTeX 0.16.25** (GHSA-238p-pmpm-9mq7). KaTeX was intentionally not upgraded because it is unrelated to this login integration and renders gameplay questions.
- There are no known remaining failures in the checks above.

During implementation, an initial font import path failed the build and was corrected to the packages' exported CSS entry points. An initial renderer cleanup attempt caused a Drei render-target disposal error; renderer teardown was returned to R3F's correct cleanup order and both browser suites then passed. The failure-test harness was also adjusted to recognize deliberately expected HTTP error diagnostics. Final results above reflect the corrected implementation.

No deployment or automatic commit was performed. The standalone prototype and R2 source were not modified.

## Complete changed-file manifest

All paths below are relative to `/home/sujan/Projects/DELTAX/round1_system/`. Generated hashed files replace previous build output; they were not hand-edited. Dependency-managed ignored `node_modules` and the ignored QA screenshots listed above are not source files in this manifest.

| Status | File |
| --- | --- |
| Added | `frontend/player/assets/chunk-4EJWAXJO.js` |
| Added | `frontend/player/assets/chunk-4UFAQYWO.js` |
| Added | `frontend/player/assets/chunk-BGU5UYEC.js` |
| Added | `frontend/player/assets/chunk-E4EH3YP5.js` |
| Removed (replaced build chunk) | `frontend/player/assets/chunk-EK7ODJWE.js` |
| Added | `frontend/player/assets/chunk-EX7TRSJS.js` |
| Added | `frontend/player/assets/chunk-FY33DNU3.js` |
| Added | `frontend/player/assets/chunk-KI35B3P3.js` |
| Removed (replaced build chunk) | `frontend/player/assets/chunk-LCHUMGRD.js` |
| Added | `frontend/player/assets/chunk-NAUBUBY7.js` |
| Added | `frontend/player/assets/chunk-PHPKWA4D.js` |
| Added | `frontend/player/assets/chunk-R5EAWTMJ.js` |
| Removed (replaced build chunk) | `frontend/player/assets/chunk-RN7HMQZL.js` |
| Added | `frontend/player/assets/chunk-TZOKVDQY.js` |
| Removed (replaced build chunk) | `frontend/player/assets/chunk-XQCEOLQD.js` |
| Added | `frontend/player/assets/ibm-plex-mono-latin-400-normal-4MWPPJAM.woff` |
| Added | `frontend/player/assets/ibm-plex-mono-latin-400-normal-A2WATXFY.woff2` |
| Added | `frontend/player/assets/manrope-cyrillic-ext-wght-normal-2GMQGZDT.woff2` |
| Added | `frontend/player/assets/manrope-cyrillic-wght-normal-F76KYTZG.woff2` |
| Added | `frontend/player/assets/manrope-greek-wght-normal-NNUWM3E7.woff2` |
| Added | `frontend/player/assets/manrope-latin-ext-wght-normal-6XGHMEDV.woff2` |
| Added | `frontend/player/assets/manrope-latin-wght-normal-73PUZEUA.woff2` |
| Added | `frontend/player/assets/manrope-vietnamese-wght-normal-HA5TFICD.woff2` |
| Added | `frontend/player/assets/outfit-latin-ext-wght-normal-5Z2WCAL6.woff2` |
| Added | `frontend/player/assets/outfit-latin-wght-normal-5FXBKKJF.woff2` |
| Modified | `frontend/player/assets/player.css` |
| Modified | `frontend/player/assets/player.js` |
| Modified | `frontend/player/assets/THIRD_PARTY_NOTICES.txt` |
| Modified | `frontend/react/build.mjs` |
| Added | `frontend/react/CINEMATIC_LOGIN.md` |
| Modified | `frontend/react/package-lock.json` |
| Modified | `frontend/react/package.json` |
| Added | `frontend/react/src/cinematic/AtmosphericFallback.jsx` |
| Added | `frontend/react/src/cinematic/Atrium.tsx` |
| Added | `frontend/react/src/cinematic/AtriumWorld.tsx` |
| Added | `frontend/react/src/cinematic/CinematicLogin.jsx` |
| Added | `frontend/react/src/cinematic/login.css` |
| Added | `frontend/react/src/cinematic/types.ts` |
| Modified | `frontend/react/src/main.jsx` |
| Modified | `frontend/react/tests/browser.mjs` |
| Added | `frontend/react/tests/cinematic-login.browser.mjs` |
