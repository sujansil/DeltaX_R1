# Safe admin team deletion — local implementation

Verified on 11 November 2026. No deployment, Azure changes, existing database
migration, or existing database reset was performed. With explicit approval,
the existing migrations were applied only to a newly created disposable local
PostgreSQL 16 container; all database tests ran inside that container.

## Repository audit

| Model / mechanism | Finding and deletion treatment |
| --- | --- |
| `Team` | Owns username, password hash, activation hash/expiry, and enabled flag. No separate `User` model or team credential table exists. The team row is permanently removed. |
| `Admin` | Separate administrator credentials. The endpoint requires an admin JWT and a currently enabled persisted admin account. |
| `PlayerDevice` | Persisted authority for team JWT sessions; FK cascades on team deletion. Every active row blocks deletion, including expired but renewable leases. Only inactive session rows can be removed. |
| `RoundEnrollment` | Immutable participation/eligibility evidence. Any row blocks deletion. |
| `TeamRoundState` | Relay participation evidence, also used by combined standings. Any row blocks deletion, including `WAITING` states. |
| `Submission`, `ScoreEvent` | Any row blocks deletion, including unanswered submissions, zero scores and negative scores. |
| `R1BDraft`, `R1AReceipt`, `R1BReceipt` | Any row blocks deletion, including pending and rejected receipts and empty drafts. |
| `LeaderboardSnapshot` | Stores JSON rather than team FKs. All publications are inspected: legacy lists, question/turn reveal envelopes, final subround envelopes, and combined R1 envelopes. Team rows, ties, qualification lists and cutoff ties are checked. |
| Qualification | Stored inside the immutable combined R1 publication; no separate qualification model exists. |
| `AdminAction` | No FK to a team. Existing audit events survive; a `DELETE_TEAM` event stores actor, time, team ID, code, exact name, login username, creation time and deletion reason. No credential secrets are included. |
| `ActivationThrottle` | A shared username-digest rate limiter, not a credential or team-owned FK row. Retained to preserve authentication protection and avoid reversing activation's throttle → team lock order. |

Existing mechanisms include enable/disable, password reset, activation regeneration,
and session revocation. No team deletion existed. The implementation reuses the
admin authentication dependency, audit helper, session authorization, existing
FK cascades, and round/team mutation locking conventions. Disabling remains
available through the existing flow and retains its enrollment restrictions.

## Exact deletion conditions

`DELETE /api/admin/teams/{team_id}` takes JSON:

```json
{"confirmation":"Exact current team name","reason":"Why this unused team is being removed"}
```

All conditions must hold within one transaction:

1. The caller has an admin JWT and a persisted, enabled admin account.
2. The team exists. Confirmation equals its current name exactly, including case
   and whitespace; the reason has non-whitespace content and at most 500 characters.
3. No `PlayerDevice` row for the team is marked active. Expired active leases must
   also be revoked first, because heartbeat can otherwise renew them.
4. No enrollment, relay state, submission, score, numerical draft, or admission
   receipt references the team, regardless of round, result, status or points.
5. No stored publication references the team's ID or current code in a team row,
   or its ID in a team-ID list, including ties and qualification snapshots. Every
   historical snapshot is checked, not only the latest or public top 16.
6. Every stored publication can be interpreted sufficiently to verify identities.
   Malformed or unknown formats block deletion rather than permitting data loss.
7. No `RESET_ROUND_ONE` audit event is dated at or after the team's creation.
   Reset removes competition records and publications, and its durable audit
   retains aggregate counts rather than team identities. An older team's absence
   from the current competition tables cannot establish nonparticipation.

An inactive login session alone is not competition participation. Activated or
unactivated teams, enabled or disabled, can be deleted if all conditions hold.
There is no blanket restriction based on another team's participation or a round
being live; the decision is based on the target team's available history.

The admin team editor requires typing the exact name and an audited reason,
followed by the existing explicit confirmation dialog. The server independently
checks confirmation against the locked current team row. It returns structured
409 errors for history/session blockers, 422 for invalid confirmation/reason,
and 404 for a missing or already deleted team.

## Integrity, concurrency and authentication

Deletion takes exclusive R1A then R1B round locks, followed by the team row and
its session rows. Existing login, enrollment, gameplay, reconciliation,
publication and reset paths already use these round locks. Team row locking
also serializes edits and password activation, and conflicts with PostgreSQL's
foreign-key key-share locks for newly inserted dependent records. Explicit
session locks cover heartbeat/logout, which do not take round locks.

The team deletion, credential removal, inactive-session cascades and audit insert
commit atomically. If auditing fails, the deletion and cascades roll back.
Competition records are blockers and are never intentionally cascaded by the
new action. Other teams and all publications remain unchanged.

Deleting the team removes its password and activation credentials. Cascading
session removal also makes old JWTs fail the existing persisted-session checks.
No authentication or session algorithm changes are required.

## Files changed

- `app/team_deletion.py`: eligibility checks, snapshot inspection and locks.
- `app/api/admin.py`: request validation and atomic audited admin endpoint.
- `frontend/admin-react/src/main.jsx`: exact-name/reason form and confirmation.
- `frontend/admin/react-assets/admin.js`: rebuilt checked-in admin bundle.
- `tests/test_team_deletion.py`: PostgreSQL integration tests.
- `frontend/admin-react/tests/browser.mjs`: confirmation, cancellation, rejected
  deletion and successful deletion browser coverage.
- `TEAM_DELETION_REPORT.md`: this audit and verification report.

## Verification

| Check | Result |
| --- | --- |
| Full Python/R1 regression suite, guarded disposable PostgreSQL (`python -m pytest -q --tb=short`) | **287 passed, 0 failed, 0 skipped** |
| New PostgreSQL deletion integration suite (included above) | **53 passed** |
| Admin unit tests (`npm test` in `frontend/admin-react`) | **7 passed** |
| Admin browser workflow checks (`npm run test:browser` there, synthetic API fixtures) | **37 passed** |
| Player unit tests (`npm test` in `frontend/react`) | **12 passed** |
| Admin production asset build (`npm run build` in `frontend/admin-react`) | **Passed** |

New database tests cover successful deletion, all blockers, current admin status,
exact confirmation, audit rollback, deleted password/activation/JWT credentials,
unchanged teams/results/publications, and both transaction orderings for login,
enrollment, publication, activation and FK insertion races. Concurrent deletion
produces one successful deletion, one 404, and exactly one deletion audit event.
The backend suite emits Python 3.14 dependency deprecation warnings and the
existing question/options cyclic-FK metadata warning; there are no test failures.

## Required migrations and regression risks

**No new migrations or schema changes are required.** Existing PostgreSQL FKs and
the existing admin audit store are sufficient. Scoring, timers, session algorithms,
leaderboard calculations, reset implementation, R2 and R3 code were not changed.

- Eligibility intentionally rejects some teams that may actually be unused:
  teams predating a recorded reset, teams with any relay state/receipt, and all
  teams when a publication cannot be interpreted. This preserves evidence when
  nonparticipation cannot be proven.
- Every publication is scanned while round locks are held. Very large historical
  publication stores can briefly delay gameplay or other admin transitions, as
  other exclusive admin operations do. No scoring or clock values are changed.
- Correct serialization assumes competition/publication writers follow the
  repository's existing round-lock protocol. Direct SQL writers must follow it
  as well; snapshot JSON has no FK to the team. Historical edits performed outside
  the audited application cannot be reconstructed by this feature.
- Future snapshot formats must be supported before deletion can proceed with
  those formats present. Future team-owned competition tables must also be added
  to the explicit blocker list before enabling this action against them.
