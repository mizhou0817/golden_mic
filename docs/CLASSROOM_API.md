> **Retired API — 2026-09-27.** Classroom accounts, login/setup, roster, assignments, publication/review and their routes have been removed from the product. This document preserves the historical contract and dated test results only; statements below about mounted routers or the “current” frontend describe that old version. Historical source links may refer to removed files and must not be treated as implementation instructions.
>
> The active no-login authority, task capabilities, production anonymous session/CSRF and legacy-data boundaries are defined by [CORE_WORKSPACE_20260927.md](CORE_WORKSPACE_20260927.md). Existing classroom databases, backups and evidence remain protected historical data; do not initialize, migrate, delete or expose them to satisfy old tests. No current acceptance result is claimed here.

# Historical classroom backend contract

Implemented by [backend/classroom.py](../backend/classroom.py), persistence in
[backend/classroom_store.py](../backend/classroom_store.py). The router is mounted
in [backend/main.py](../backend/main.py), alongside classroom queue, workbench and
studio. The current frontend includes classroom login, teacher dashboard, creation,
private workbench and same-class work wall. This is no longer an unmounted module.

**2026-09-24 final frontend validation:** session ordering, cancellation, cross-tab recovery
and logout notices are documented below from the current client source. Backend
authentication, cookies, CSRF checks and authorization contracts are unchanged.
The final **37/37 page, 42/42 deep and 845 backend tests (843 passed, 2 skipped)**
complete the documented current canary scope, not all prototype features. Final
evidence is listed below; the main record is
[../canary_test/CANARY_20260924.md](../canary_test/CANARY_20260924.md).

## Current host integration (updated: 2026-09-23)

* Main's `_authorize_task` checks classroom ownership/session first; only an
  unowned legacy task can fall back to a task token. Private workbench/studio
  callbacks exclude published peers. Copy/studio busy guards precede mutations.
* Task creation validates classroom metadata before saving files, calls
  `add_task(..., defer_start=True)`, registers ownership, persists
  `classroom_task=True` / `queue_hold=True`, then retains the upload reservation
  and invokes the durable dispatcher. Admission/body/rate/disk checks are also
  active in middleware, before the route. Classroom creation returns
  `{task_id, access_token:null}`, not a token or registration object.
* `_authorize_private` preserves approval for recording drafts and duplication.
  Workbench edit/restore uses the separate `before_mutation` callback after
  validation, immediately before scheduling; legacy remix/replacement retains
  eager invalidation. Studio operations use `invalidate=False` because their
  outputs never replace the classroom final.
* Video/report reads select `committed_root(record)`; snapshots are authoritative
  when present, with a current-root fallback for legacy tasks without snapshots.
  Reports are sanitized. Peer thumbnails and posters are extracted from the
  committed final film, not discarded/raw source frames. Source media, studio
  projects and version history remain private to the author/member teacher.
* `video?download=true`, studio render/export submission and studio output reads
  check `can_export`. Studio project JSON backups, private source streaming and
  private historical previews are not the same export-gated operation. A browser
  that can preview bytes can save them: the export gate is not DRM.
* Teacher deletion of another author's work requires JSON
  `{student_name:"exact author name"}`; main reauthorizes before deletion.
  Explicit DELETE now reserves the manager record before any asynchronous SQL
  cleanup, rejects active studio/copy jobs first, then transactionally removes
  only this work's notes/comments/ownership row. Audit and dispatch budgets remain;
  deletion does not refund starts. Metadata failure restores only the reserved
  record without cancelling its worker or deleting files. Request cancellation
  drains cleanup. Physical deletion failure after metadata commit returns 503
  `task_artifact_cleanup_failed` and requires operator cleanup, never false 204;
  persisted classroom markers prevent token fallback for surviving files. TTL
  cleanup keeps its existing ownership tombstones, and unrelated missing works
  are not silently filtered or removed. SQLite/filesystem deletion is not
  power-loss atomic. Regressions: [deletion tests](../tests/test_classroom_deletion.py).
* TaskManager cleanup honors classroom deadlines even with legacy TTL zero;
  deletion/cleanup consult copy and studio reservations. Initial/remix/replacement
  success paths snapshot complete artifacts before exposing `done`. See
  [CLASSROOM_QUEUE_API.md](CLASSROOM_QUEUE_API.md) for held-task restart behavior.

Synchronous classroom helpers are offloaded by main where needed; router handlers
run in FastAPI's thread pool. These are single-worker application boundaries, not
a distributed transaction or power-loss recovery guarantee. SQLite cannot lock
arbitrary filesystem writers. Generated-media permission is checked at creation/
registration and by the publication gate; the current dispatcher does not recheck
`generative_allowed` immediately before each provider call. Disabling class policy
is therefore not a cancellation mechanism for already queued/running generation;
cancel/drain those jobs before changing operational policy.

Exact public helper signatures (existing positional arguments remain compatible):

* `authorize_classroom_task(request, settings, task_manager, task_id, write=False, *, invalidate=True)`
* `authorize_classroom_private(request, settings, manager, task_id, write=False, *, invalidate=True)`
* `classroom_identity(request, settings, required=False)`
* `classroom_cookie_present(request, settings) -> bool`
* `classroom_has_session(request, settings) -> bool`
* `public_classroom_available(settings, task_manager, task_id) -> bool`

`public_classroom_available` is a **server-only gate recheck**, not authentication
or permission to discover work. It returns false for unowned, unpublished, busy,
expired or blocked work; requires no request and returns no metadata. Use only
after same-class peer authorization, then open the same immutable final revision.
Do not expose it as an unauthenticated route or use it to authorize source exports.

`classroom_identity(request, settings, required=False)` returns
`{id, role, name, class_id?, number?}` or `None`; required unauthenticated access
raises 401. On unsafe methods even optional identity checks validate CSRF for a
present valid session. A presented invalid/expired cookie raises 401 **even when
required=False**, so it cannot fall through to no-login/legacy authorization.
`classroom_cookie_present` reports only a routing marker, never authentication.
`classroom_has_session` verifies a login **or** bootstrap session, returns only a
boolean (no cookie, hash, actor or CSRF secret), requires CSRF on unsafe requests,
and also raises 401 for invalid/expired cookies. A bootstrap session is not a
classroom identity and never authorizes owned work. Current main explicitly rejects
`POST /api/tasks` with a classroom cookie but no logged-in actor (401); bootstrap
does not grant creation rights. No cookie returns false/None; GET `/session` deliberately
refreshes an expired cookie into a bootstrap session for subsequent login.

`authorize_classroom_task(...)` returns the live TaskRecord
for authorized owned tasks, raises for owned unauthorized/expired/missing media,
and returns `None` **only** for legacy unowned tasks after session checks.
`authorize_classroom_private` has the same return/error/CSRF contract but permits
only the author or a member teacher, even if a peer can view the published work.
Use it before serving private reports, source assets or studio documents. The
main route separately enforces the `student_name` confirmation described above
before a teacher deletes another author's work; authorization alone does not confirm deletion.
`classroom_task_owned` remains
true after expiry/media removal (ownership tombstone). `retention_deadline` returns
an aware UTC datetime, including expired deadlines, or `None` for legacy.

Main exempts classroom session/login/setup from its legacy anonymous/authenticated
middleware requirements, validates classroom cookies on task routes, and permits
`X-Classroom-CSRF` in credentialed CORS for explicit origins. The current frontend
uses same-origin `/api` requests with `credentials: "include"`; cross-origin or
subpath API-base configuration is rejected by its app adapter. Classroom secrets
are not written to browser storage. The separately selected legacy-history mode
can read old localStorage task tokens; it does not authorize classroom work.
Keep HTTPS, Origin/Host restrictions and edge request/rate limits. A reverse proxy
can still impose Basic Auth before requests reach main: the authenticated Nginx
template applies it server-wide and does not exempt classroom login. Do not assume
the application exemption removes that extra login layer.

Restrict published-peer access to **final classroom preview, final-film thumbnails,
task status and sanitized report**: ownership authorization is not permission to expose uploads,
source media, private scripts, filesystem paths, or task tokens. Peers receive no
consent names, return notes or teacher notes from this API. Private author/teacher
preview is allowed while done even when publication/export is blocked.

`auto_release` is persisted class policy consumed by the mounted
[classroom dispatcher](CLASSROOM_QUEUE_API.md). The real manager owns execution,
reflected by `queue_control: "task_manager"`; the server-side dispatcher supports
teacher batch release and periodic automatic admission, not browser timers or an ETA.

## Persistence and operating requirements

* Database: `settings.data_dir.parent / "classroom.sqlite3"`. Factory construction
  is disk-free; first request/operation initializes schema version 2. Version 1
  migrates atomically under the writer lock by adding nullable REAL `due_at` to
  assignments, preserving existing rows, accounts, sessions and work. Concurrent
  initialization/migration rechecks the version under that lock. No seed data,
  default teacher, or demo PIN. Unsupported newer schemas fail closed.
* stdlib SQLite: foreign keys, WAL, FULL synchronous, 15s busy timeout,
  `BEGIN IMMEDIATE` transactions, unique class codes, class/student numbers,
  and partial unique index enforcing one active assignment per class.
  Read-only `store.read()` snapshots (including ownership/deadline lookups) use
  `BEGIN` plus `query_only`, without taking the writer lock. Existing version-2
  connections do not rerun initialization under a writer transaction. The journal
  transition lock is only used if the database is not already in WAL mode.
* Run on local persistent disk, **not a shared network/OneDrive-synced production
  database**. Restrict directory/backup access to service/operator; new database
  mode is 0600 on POSIX. Apply equivalent Windows ACLs. WAL/SHM and backups contain
  personal information. Use SQLite online backup or stop service for backup;
  never copy only the main DB while WAL is live. At-rest encryption/backup expiry
  are operator responsibilities.
* Student IDs are random immutable IDs, never names. Roster reimports update by
  `(class_id,number)` and preserve ID/history/PIN unless a replacement PIN is given.
  Name collisions are supported. Number changes are new students, not a rename.
* Draft forms expire 72 hours after their last save; unsaved work registrations
  expire 72 hours after creation. Publish/teacher retain sets the **actual** stored
  deadline to current term end. Unpublish/invalidation without retain shortens to
  at most 72 hours from then (never revives an expired item). Term changes update
  unexpired published/retained deadlines only. Ended assignments do not erase work.
* Reads/lists hide expired work; direct authorized access returns 410. This router
  does not delete media; TaskManager's hourly cleanup applies the deadline override
  even when legacy TTL is zero (eligible terminal/held tasks and orphan directories).
  Keep minimal ownership tombstones so leaked legacy
  tokens cannot regain access after metadata cleanup. Draft cleanup is lazy on
  saves; account/notes/audit retention needs an operator privacy policy.

## Session and security

1. `GET /api/classroom/session` creates an anonymous CSRF bootstrap cookie when
   needed and returns `{configured, identity: null | Identity, csrf_token,
   idle_timeout_seconds}`. No DB configuration is implied by obtaining a session.
2. Every POST/DELETE requires its cookie and `X-Classroom-CSRF: <csrf_token>`,
   including first setup/login/logout. Missing session/login: 401; invalid CSRF:
   403. Use a fresh returned CSRF token after login rotation.
3. Cookies are HttpOnly, SameSite=Strict, path `/`, no Domain, browser-session
   lifetime. Production uses Secure `__Host-classroom_session`; development uses
   `classroom_session`. Random 256-bit opaque cookie values are only SHA-256 hashed
   in SQLite. CSRF is a domain-separated digest of the cookie, not a bearer token.
4. Idle expiry is enforced on server: teacher/bootstrap 600s, student 2700s;
   absolute maximum 24 hours. Logout and PIN/password reset revoke sessions.
  Browser polling is server activity; the current app stops work/queue polling
  while hidden or locally idle and locks the workspace after its idle interval.
  Login/logout without a valid bootstrap/login session return 403; refresh
  `GET /session` and use its CSRF token before retrying. Task identity helpers
  deliberately return 401 for a presented expired cookie instead of legacy fallback.
5. scrypt with per-account random salt (`N=16384,r=8,p=1`) hashes teacher passwords
   and student PINs. Missing accounts do comparable KDF work. Five attempts per
   account **and direct peer IP**, per role, per ten minutes; failures persist
   across restart; sixth returns 429 + `Retry-After: 600`. Successful attempts
   remove their own reservation, not preceding failures. Set trusted proxy handling
   in ASGI deployment; arbitrary forwarded headers are not trusted here. The
   edge must additionally limit total unauthenticated request/session volume.
6. At router level, mutation Origin, if present, must match the actual ASGI request base origin or
  explicit `settings.cors_origins`; `Sec-Fetch-Site: cross-site` is rejected.
  Main adds a stricter requirement when `enforce_origin_check` is enabled:
  task/classroom POST/DELETE must include an explicitly allowed Origin.
  For development Vite proxying, preserve the incoming frontend Host
  (`changeOrigin: false`) or explicitly configure that frontend origin. Arbitrary
  `X-Forwarded-Host` is not trusted. Main installs TrustedHost middleware;
  deployment must still configure the allowed hosts and trusted proxy TLS correctly.
  No secret printing.
  Router successes and HTTP/validation errors use Cache-Control no-store.
  Validation errors never echo credential-bearing input values. Existing security
  middleware should also protect unexpected 500 responses and scrub request bodies.

### Frontend session coordination — 2026-09-24

Implemented in [../frontend/src/classroomApi.ts](../frontend/src/classroomApi.ts)
and [../frontend/src/App.tsx](../frontend/src/App.tsx). These are client-side
ordering and UI safety rules, **not new authentication endpoints, request fields,
server permissions or a replacement for server CSRF/identity checks**.

* **Latest committed state, not last-arriving response.** The transport keeps
  `currentSession` and `sessionGeneration` in memory. Validated login/logout
  commits invalidate prior session reads; a valid observation of changed identity
  or configuration also advances the generation. Older GET bodies, auth responses,
  error refreshes, cache-hit deliveries and waiters cannot overwrite or return a
  superseded session. Invalid response shapes do not commit identity. CSRF-only
  renewal is an observation, not an actor change.
* **Owned bootstrap, independent callers.**
  `initializeClassroomSession(force = false, signal?)` coalesces concurrent callers
  onto one owned GET; `force=true` requests revalidation but still joins an active
  read. A caller's `AbortSignal` cancels **only that caller's wait**, never another
  waiter or the shared GET. An already-aborted caller starts no fetch.
  `invalidateClassroomSessionReads(clearSession = false)` deliberately fences and
  detaches the owned read before aborting it, so its late cleanup cannot detach
  a successor. App startup also checks its own generation and the still-current
  transport snapshot after slower limits/availability work; late startup cannot
  restore a previous actor's private UI after logout/lock.
* **Observation is not an identity commit.** `classroom-session-change` reports
  session observations, including initial GET and refresh. The separate
  `classroom-identity-commit` event is emitted only after a validated successful
  teacher login, student login or logout acknowledgement. Setup returns IDs, not
  an invented login; only its subsequent real login can commit identity. App
  sends the constant `identity-changed` notification on
  `BroadcastChannel('golden-mic.identity')` only for those commits. An initial
  same-cookie GET, local lock, failed logout or read-only recovery sends no
  identity broadcast. The cross-tab message contains no session/CSRF/identity data.
* **Peer changes lock, not switch, the old tab.** A real peer notification clears
  that tab's private workspace/session view and pending startup state. An ordinary
  read discovering a different `role:id` also locks rather than silently adopting
  it. The visible **核验其他标签页身份（只读）** action performs only session GET:
  any still-logged-in actor leaves the old tab locked, with no logout POST and no
  broadcast back. Only a fresh, still-current server observation of
  `identity=null` permits a return to login. Clearing the local cache or a failed/
  stale read is not evidence that the server is anonymous. Local cleanup does not
  delete the former author's persisted server draft.
* **Logout remains bound to the original actor.** App retains the original
  in-memory `role:id` across a pending logout and explicit retry; it does not bind
  by display name. `classroomRequest(path, init, expectedLogoutIdentity?)` checks
  this optional binding after awaited bootstrap and again before sending **POST
  logout only**. A mismatch returns a client-side `ClassroomApiError` with status
  409 without sending that logout; the UI takes the read-only peer path. The empty
  binding represents anonymous legacy-entry cleanup. It is not transmitted as a
  new authorization field and is not an atomic server-side identity/write lock.

Logout immediately clears the local private view and says **locked / awaiting
server confirmation**, not “successfully logged out.” Its outcomes are distinct:

| Actual result | Frontend behavior |
| --- | --- |
| Successful POST `/logout` with validated `{ok:true}` | Acknowledged logout; commit/broadcast, then fresh startup/login. |
| Rejected logout, followed by a fresh current GET returning the original actor | Remain locked with **服务器注销未确认**; offer an explicit actor-bound retry, never replay POST automatically. |
| Invalidated/expired cookie gives actual logout 403, then a fresh current GET returns anonymous | Clear the local suspension and return to login with a **server-verified anonymous** notice. The 403 is still a rejection, not a successful POST; no automatic second POST or identity-commit broadcast is invented. |
| Refresh observes a different actor | Keep the old tab in read-only peer lock; do not adopt or log out the new teacher/student. |
| Network failure, failed refresh or superseded observation | Do not infer server logout from local cleanup; keep recovery explicit and avoid a false success notice. |

Protected writes, including logout and ordinary classroom/task edits, are
**never automatically replayed**. On a recognized classroom CSRF rejection or
non-prelogin 401, the transport attempts a read-only session refresh and throws
`ClassroomApiError` with the valid refreshed `.session` when available; App may
use a still-current anonymous observation as above. The narrow existing exception
is **one** setup/teacher-login/student-login retry after an explicit preflight-CSRF
403 and a valid refresh with the same generation. Wrong credentials, ordinary
permission denials, unknown network outcomes, failed/malformed refreshes and
actor/configuration changes do not authorize that retry. Client cancellation or
generation fencing does not undo a request already executed by the server.

Classroom CSRF/session data remain in memory plus the browser-managed **HttpOnly
cookie**, never authentication caches in localStorage/sessionStorage. Evidence
must not contain cookies, CSRF, raw auth headers, login/session bodies or saved
browser authentication state. This leaves the existing font preference, explicitly
selected legacy-history mode and **minimal opaque cloud-cost receipt** exception
unchanged: the receipt in sessionStorage is not authentication state, contains no
credentials/source content, and is not permission to reset/retry spending. See
[CLOUD_API.md](CLOUD_API.md) and
[../frontend/e2e/round2/README.md](../frontend/e2e/round2/README.md).

Optional **`npm run test:session`** from the frontend directory runs
[../frontend/scripts/test-classroom-session.mjs](../frontend/scripts/test-classroom-session.mjs)
against actual transport source with controlled responses. This does not replace
the real local browser/backend session tests.

### Final session validation — 2026-09-24

| Recorded final check | Result and existing evidence |
| --- | --- |
| Native transport | **45 passed**, controlled responses, not real-server authentication. [../canary_test/artifacts/c24-validation-20260924/session-final.log](../canary_test/artifacts/c24-validation-20260924/session-final.log). |
| Full page suite | **37/37 passed, 337.462241 seconds**, including all SESSION-01–05 cases. [../canary_test/artifacts/c24-pages-verified/summary.json](../canary_test/artifacts/c24-pages-verified/summary.json). |
| Full deep suite | **42/42 passed, 290.818192 seconds**: 31 real local-backend cases + 11 explicitly synthetic cloud instances, not Tencent live acceptance. [../canary_test/artifacts/round2/c24-deep-verified/summary.json](../canary_test/artifacts/round2/c24-deep-verified/summary.json). |
| Full backend | **845 tests: 843 passed, 2 skipped, 692.988 seconds**. [../canary_test/artifacts/c24-validation-20260924/backend-final.log](../canary_test/artifacts/c24-validation-20260924/backend-final.log). |
| Isolated host/build checks | **50 passed, 4.268 seconds**; already included in the 845 backend tests, **not added again**. [../canary_test/artifacts/c24-validation-20260924/isolation-final.log](../canary_test/artifacts/c24-validation-20260924/isolation-final.log). |

SESSION-05 now passes with **real server-side cookie invalidation → actual logout
403 → fresh, still-current anonymous session GET → login restored**. It does not
retry the logout POST, turn the 403 into a successful logout, adopt a different
actor or fabricate an identity-commit broadcast. SESSION-01–04 cover real local
multi-tab coordination, cookie/CSRF rotation and startup ordering. Earlier filtered
passes and the retained SESSION-05 baseline failure are not combined into the
complete final page result.

Both full browser runs used one Edge worker and zero retries, serving the dedicated
build represented by
[../frontend/dist-canary-c24-verified/ASSET_MANIFEST.sha256](../frontend/dist-canary-c24-verified/ASSET_MANIFEST.sha256),
not the shared output. All four asset hashes were verified over actual HTTP on
each of the three final instances before the full browser runs. The intermediate
37/37 page run predates the final `ClassroomLogin` **登录身份** and
`ResultWorkbench` **句子选择** `role=group` fixes; it is not current acceptance.
Final browser assertions verify the real named groups and axe no longer reports
those two invalid-label findings. Gradient contrast and video-caption manual
review remain; this is not complete WCAG conformance.

Detailed retained failures, the native-play test-helper correction, isolated
host/source-hash cleanup and scope limits are in
[../frontend/e2e/README.md](../frontend/e2e/README.md) and
[../frontend/e2e/round2/README.md](../frontend/e2e/round2/README.md).
No new paid requests were made in this cycle; the prior Kimi **100 fen (CNY 1)
held reservation is unchanged**, with actual bill unknown. All 58 unsupported
advanced catalogue items remain gaps. This documentation-only sync performs no
terminal, Python, network, source-code or service changes; it records existing
validation and the earlier research in
[../canary_test/CANARY_20260924.md](../canary_test/CANARY_20260924.md).

## Provisioning

Development-only `POST /setup` additionally requires both an actual loopback
client IP and loopback URL host, and atomically refuses a second setup (409).
Use the local initialization form on a loopback-only development server (the local
Vite `/api` proxy preserves the frontend Host); never expose development setup via
a public reverse proxy or treat a UI-hidden button as the security boundary.
First obtain `/session` and its CSRF token. There is no default teacher/password/PIN.

Request:
```json
{"username":"teacher-account","teacher_name":"陈老师","password":"a-new-private-password","code":"CLASS-2026-A","name":"五年级一班","grade":"五年级","term_end":"2027-01-31T23:59:59+08:00","auto_release":false,"generative_allowed":false}
```
Response 201: `{teacher_id, class_id}`. Password is 12–128 characters; usernames
and codes 1–64 ASCII letters/digits/underscore/hyphen; names and grade 1–40 chars.
Term end must have timezone and be future, no more than two years ahead.

Production provisioning: [deploy/classroom_admin.py](../deploy/classroom_admin.py)
is an interactive module with `--data-dir ABSOLUTE_TASKS_DIRECTORY` and commands
`init`, `add-teacher --class-id ID [--class-id ID ...]`, `reset-password`.
It uses `getpass` twice, accepts no password command-line argument, refuses
non-interactive input, does not load environment files, and never prints secrets
or validation inputs. Only filesystem-authorized operators should run it.

The CLI file and argparse commands above were verified in source for this review;
no provisioning was executed. Invoke it as `python -m deploy.classroom_admin`
from the release root, using the release interpreter. Put `--data-dir` before the
subcommand and use the **same absolute tasks directory as the service**, not the
database filename or its parent. A wrong directory provisions a different database.
Run with service-compatible filesystem ownership/permissions, not an unrestricted
web endpoint. `init` defaults `auto_release` and `generative_allowed` to false;
the teacher can change policy after login. Import the roster and privately deliver
each generated PIN once; students need class code, number, name and PIN. Do not
paste passwords/PINs into shell arguments, logs, screenshots or shared documents.
Back up the database consistently and agree term/retention/cloud-processing policy
before admitting students. Production HTTP setup is always refused, even on loopback.

## Endpoint JSON contract

All paths below have prefix `/api/classroom`. JSON objects reject extra fields;
booleans must be JSON booleans. Whitespace is trimmed. Invalid body: 422;
all unsafe router bodies are limited to **262144 bytes (256 KiB)** before JSON
parsing, including chunked bodies and UTF-8 bytes; larger requests return 413
without echoing the body. This does not replace main's upload limits.
stale revisions/action preconditions/duplicates: 409. Class membership failures
are 404; role violations 403. IDs are opaque strings. All unsafe operations use
the session/CSRF flow above. No public work response contains provider data,
original task tokens, source paths, or a raw QC report.

### Login/classes/roster

| Method/path | Exact request | Success response |
|---|---|---|
| GET `/session` | none | Session described above |
| POST `/login` | `{username,password}` | Session, with teacher identity |
| POST `/student-login` | `{code,number,name,pin}` | Session, with student identity |
| POST `/logout` | no body | `{ok:true}` |
| GET `/classes` | none | `{classes:[Class]}`; teacher's memberships/student's own class |
| POST `/classes` | ClassInput | 201 Class; teacher becomes member |
| POST `/classes/{class_id}` | ClassInput (full replacement) | Class; member teacher only |
| GET `/classes/{class_id}/roster` | none | `{students:[{id,number,name}]}`; member teacher |
| POST `/classes/{class_id}/roster` | `{lines:"名字 001 optional-PIN\n..."}` OR `{students:[{name,number,pin?}]}` | `{students:[{id,number,name,generated_pin?}]}` |

ClassInput is `{code,name,grade,term_end,auto_release?:false,generative_allowed?:false}`;
Class response has the same fields plus `id`, with UTC ISO term end.
Grade is preserved as supplied, including `primary`, `middle`, `senior` and
existing display-grade strings; the main/UI adapter owns any display mapping.
Roster lines also accept commas/Chinese commas, order **name number optional-PIN**. Exactly
one import format, 1–500 entries; duplicate numbers within one import reject the
entire import. Names may contain spaces only in JSON-list format. PIN is 6–128
characters. New entries without a PIN receive a random PIN returned **once** as
`generated_pin`; user-supplied PINs and existing PINs are never echoed. Reimporting
an existing number without PIN preserves it; supplying a PIN resets and logs the
student out. Student login requires all four fields: name/number alone never work.
The import reads authorized roster IDs, generates PINs and computes all scrypt
hashes **outside** the writer transaction, then rechecks membership and each
`(class_id, number)` ID before atomically applying the batch. A concurrent insert
or ID replacement returns 409 and rolls back the batch; retry from a fresh roster.
No-PIN reimports preserve even a concurrently reset PIN. Single login credential
verification remains in its short transaction; bulk KDF work never holds it open.

### Assignments/drafts

| Method/path | Request | Success response |
|---|---|---|
| GET `/classes/{class_id}/assignments` | none | `{assignments:[Assignment]}` |
| POST `/classes/{class_id}/assignments` | `{title,instructions?:"",due_at?:null}` | 201 Assignment; member teacher |
| POST `/classes/{class_id}/assignments/{id}` | `{title,instructions?:"",due_at?:null}` | Assignment; member teacher, active assignment and unended term only |
| POST `/classes/{class_id}/assignments/{id}/end` | no body | `{ok:true}`; member teacher, idempotent |
| GET `/classes/{class_id}/drafts` | none | `{draft:null|Draft,retain_until:null|ISO}` |
| POST `/classes/{class_id}/drafts` | Draft | `{draft:Draft,retain_until:ISO}` |

Assignment: `{id,class_id,title,instructions,created_at,ended_at,due_at}`.
`created_at`/`ended_at` remain Unix seconds; `ended_at` is nullable. `due_at` is a
nullable **UTC ISO datetime**, accepting timezone-aware datetimes on create/update
(naive datetimes reject with 422). Title 1–60, instructions 0–2000 chars. Update is
full replacement: omitted instructions becomes empty, omitted/null due_at clears
the deadline. Foreign ID returns 404; ended assignment/term returns 409. Deadline
is scheduling metadata, not automatic assignment closure or a media-retention rule.
Creating when another assignment is active returns 409; explicitly end it first.
Creation links work to the active assignment in the same transaction; specifying
a nonactive/foreign assignment fails. No active assignment is allowed (null link).
Omitting assignment_id selects the current active assignment; explicitly passing
null requires that none is active. Preflight returns the explicit value to detect
assignment changes during an upload rather than silently relinking the work.

Draft accepts the complete CreateWizard JSON, keeping all old fields optional:

```typescript
type Draft = {
  title?: string;                 // default "", <=40 characters (legacy)
  script?: string;                // default "", <=20000 characters
  consent?: Consent;
  generative_fill?: boolean;      // legacy default false
  preferences?: EditingPreferences;
  step?: 1 | 2 | 3;               // default 1; strict integer, not bool/string
  files?: DraftMedia[];           // default [], <=100
  elements?: Partial<Record<"time" | "place" | "who" | "what" | "why",
                            {confirmed?: boolean; evidence?: string}>>;
  sentenceChecks?: Record<string, boolean>; // <=1000; keys 1–2100 chars
  voice?: "ai" | "self";         // default "ai"
  ownVoice?: DraftMedia | null;   // default null; kind must be "audio"
};
type DraftMedia = {
  id: string;                    // browser fileIdentity or opaque UI ID
  name: string;                  // plain basename, 1–255 characters
  size: number;                  // integer 0..2^53-1
  lastModified: number;          // integer 0..2^53-1 (browser milliseconds)
  type?: string;                 // MIME type, <=128; default ""
  kind: "video" | "image" | "audio";
  duration?: number | null;      // finite nonnegative seconds, default null
  width?: number | null;         // integer 1..100000, default null
  height?: number | null;        // integer 1..100000, default null
  note?: string;                 // default "", <=20 characters
  trim_start?: number;           // finite nonnegative seconds, default 0
  trim_end?: number | null;      // finite nonnegative seconds, default null
  status?: "reselect";           // only accepted/persisted status; default reselect
};
```

`preferences` uses the backend `EditingPreferences` schema with strict booleans
and no extra fields: `pacing=slow|normal|fast` (normal),
`tone=solemn|neutral|energetic` (neutral),
`music_mood=auto|solemn|neutral|uplifting|tense` (auto),
`background_music`, `motion_effects`, `transitions`, `news_graphics`,
`color_consistency`, `generative_fill` (all false), and `custom_instructions`
(default empty, ≤500 characters). Element confirmation defaults false; evidence
defaults empty, ≤2000 characters. Response draft includes defaults. Old saved
drafts remain readable and old minimal save payloads continue working.

`id` accepts `[A-Za-z0-9_-]{1,128}` or JSON.stringify of exactly
`[name,size,lastModified]` matching the other metadata (maximum 1100 characters).
MIME type accepts an optional `;codecs=...` suffix for browser recordings.
Extra fields at every structured level reject, including file/path/token/binary/
blob/URL/thumbnail fields. Media identity and basename fields reject paths and
URLs, including blob/data/file URLs and drive/UNC paths; basenames reject
separators, controls, dot/dot-dot and Windows reserved punctuation. Ordinary prose
in scripts, evidence, notes, sentence-check keys and custom instructions may contain
URLs or discuss paths; it is stored as text, never resolved or fetched as media.
No recursive URL blacklist is applied to prose. No file content or browser storage
is used. Numeric metadata is not proof of uploaded bytes or valid trims: unfinished
trim selection can be saved, but real files must be reselected, probed and checked
again on submission. Drafts never resume a File/Blob object or imply upload success.

One draft per actor/class; neither peers nor teachers can read another actor's
draft. Both legacy `generative_fill` and `preferences.generative_fill` require
the teacher-controlled class permission if true; neither can bypass the other.

Consent: `{nobody?:false,told?:false,agreed?:false,noface?:false,who?:""}`.
Valid declaration is `nobody=true` and empty `who`, OR `nobody=false`, all three
declarations true, and nonblank `who` (≤40). Teachers still must confirm the current
revision even when nobody appears. Incomplete declarations can be saved but block
confirmation/publication/export.

### Works/editorial actions

`GET /works?class_id=ID&scope=mine|class|wall&assignment_id=ID&offset=0&limit=100`
returns `{works:[Work],total}`. Default scope mine; student class defaults to own;
teacher must supply class ID. Class scope is teacher-only; wall requires same
class login and shows only currently publishable published work. Limit 1–200,
offset ≥0; filtering precedes pagination. `GET /works/{task_id}` returns Work plus
`comments`; private author/teacher also receive `notes`.

Work common fields:
`{task_id,class_id,assignment_id,author_id,author_name,title,reflection,status,
revision,progress,created_at,retain_until,published,exemplar,retained,
comments_closed,can_preview,can_export}`. Times here are UTC ISO strings; live
status/progress/revision come from `task_manager.get`. No fabricated done states.
Private author/teacher additionally receive
`{gate,consent,confirmed_by,confirmed_revision,return_note,return_read}`.
`confirmed_by` is immutable teacher ID, never a submitted display name.

`POST /works/{task_id}` body:
```json
{"revision":0,"title":"校园新闻","reflection":"我学会核对新闻中的人物和地点。","consent":{"nobody":true},"manual_checks":{"fact":true}}
```
Only revision is required. All other fields are optional; manual_checks replaces
the map rather than merging. Author or member teacher can edit. Unknown check
keys fail 422. Reflection 0–120 while saving, 6–120 trimmed characters to publish.
Changing consent fully invalidates manual checks, teacher confirmation and
publication. Saving title, valid reflection or completed manual checks preserves
the teacher signature; invalid reflection or incomplete manual checks unpublishes
without clearing that signature. Invalid reflection alone does not block export.
Unchanged payloads are idempotent. The response is Work.

`POST /works/{task_id}/actions` body
`{action,revision,title?,note?,value?:true}` returns Work:

| action | Role/precondition | Effect |
|---|---|---|
| `rename` | author/member teacher; title required | changes title, preserves confirmation/publication |
| `confirm-consent` | member teacher; gate.can_confirm | binds teacher ID to committed revision/report/edit epoch and declarations |
| `publish` | author only; gate.can_publish | publishes, term-end deadline, clears return note; teacher may publish only their own teacher-authored work |
| `unpublish` | author/member teacher | hides wall/exemplar; unretained TTL at most 72h |
| `return` | member teacher; nonblank note ≤1000 | invalidates checks/confirmation/publication; unread return |
| `revoke` | member teacher | invalidates checks/confirmation/publication |
| `exemplar` | member teacher; value=true requires published gate | toggles exemplar |
| `retain` | member teacher; value=true requires unended term | term-end retention, never permanent |
| `close-comments` | member teacher | sets comments_closed=value |
| `read-return` | author only | sets return_read=true |

All actions require exact current integer revision. Retain/publish cannot resurrect
expired work. Neither confirm nor publish accepts client-supplied QC or approver.

### Gate

```json
{"checks":[{"key":"fact","label":"核对人物、地点、时间及事实","checked":false}],"can_confirm":false,"can_publish":false,"can_export":false,"reasons":["manual_checks_required","teacher_confirmation_required"]}
```

`can_confirm` depends **only** on complete consent, a current done revision and
unexpired work/term. It does not require reflection, manual QC or passing automated
QC; it is consent, not quality approval. Revision checks reject stale actions (409),
and only a member teacher can confirm. Missing reports still block export/publication
but not this independent signature.

`can_export` requires complete consent, live done/nonexpired work, teacher
confirmation and all automated/manual QC below, **not reflection**.
`can_publish` additionally requires a 6–120-character trimmed reflection. A
private response's `gate.can_publish` is actor-specific: only the author can
publish. Teacher views of student work therefore return false even when that work
is publishable/published by its author; the common `published` field and wall
visibility still reflect the work's gate, not the viewer's role. No teacher can
publish on a student's behalf. Teacher-authored works follow the same QC gate.

Server export/publication gate reads the committed revision report using existing
`ReportResponse` validation (8 MiB read cap); missing/malformed/foreign/empty report
or missing QC fails closed. It requires live status done, nonexpired work/term,
zero reported blockers **and** no error issues, zero row/metric fallback,
explicit entity coverage ratio exactly 1, and no individual freeze pad **>0.3s**.
`freeze_clip_count` includes any positive pad; it is not the excessive-pad count.
Use `maximum_freeze_pad_seconds`, `excessive_freeze_clip_count`, and real
`FREEZE_PAD_EXCESSIVE` issues. A total of several allowed pads may exceed 0.3s.
A positive count without valid maximum evidence fails closed with
`qc_metrics_incomplete`; maximum >0.3, excessive count >0 or the issue blocks with
`freeze`. All actual error/blocker issues still block regardless of these metrics.
Required finite numeric metrics: `fallback_count`,
`explicit_entity_coverage_ratio`, `freeze_clip_count`,
`generated_media_clip_count`; missing/malformed values fail closed. Warning count
must equal actual warning issue count. A warn-mode pipeline does not bypass this.

Deterministic manual check keys (render labels from gate, do not invent keys):
* `fact`: always.
* `low:{sentence_id}`: row **or any visual beat** confidence below
  `max(0.5, settings.quality_min_match_confidence)`. Labels use the one-based
  current report row position, **not** sentence ID; keys keep stable sentence IDs.
* `generated:{shot_id}`: current generated shots from the strictly validated real
  disclosure manifest intersected with report row/beat shots. Count must match QC;
  class permission and `generated_media_disclosure_complete=true` are required.
* `warning:{code}:{sentence_id|-}:{beat_id|-}:{shot_id|-}`: every QC warning,
  deduplicated by this tuple (not unstable issue index or localized message).

Reasons: `task_not_done`, `expired`, `report_unavailable`,
`qc_metrics_incomplete`, `qc_blockers`, `fallback`, `required_entities`, `freeze`,
`generative_not_allowed`, `generated_disclosure_missing`, `consent_incomplete`,
`reflection_required`, `manual_checks_required`, `teacher_confirmation_required`.
Reasons include reflection even when export is allowed; use the explicit
`can_export`/`can_publish`/`can_confirm` flags rather than guessing from the reasons
array. Author-role restrictions are enforced separately (403) and reflected in
private `gate.can_publish`. No warning acknowledgement overrides a
hard blocker. Generated fallback remains blocked even if teacher allows generation.

Task ID, captured revision, report+disclosure content hash and a persisted
`data.edit_epoch` form the binding. Status and `updated_at` are **not** editorial
inputs. Reads capture one revision tuple before artifact I/O and do not sign a
new revision with an old report. A matching manifest in `revisions/r{revision}`
makes that immutable directory authoritative; report/disclosure must be listed
and are opened through `revisions.local_file`. No manifest-supplied root/path is
followed. Invalid/mismatched manifests and unsafe/missing snapshot artifacts fail
closed, never fall back to mutable files. Legacy tasks without a snapshot read
safe local current artifacts: same-revision report/disclosure tampering invalidates.
Creating an identical lazy snapshot does not change the binding.

Binding changes clear manual checks/confirmation/publication/exemplar. Temporary
queued/running/failed status hides public work and disables preview, export and
confirmation **without** changing persisted approval, checks, publication intent
or retention. Returning to done with the same artifacts restores visibility.
An explicit media edit with `write=True, invalidate=True` increments the epoch
and eagerly clears approval even if the edit fails/reverts. Studio saves/recording
ingestion using `invalidate=False` do not represent final-media edit intent.
Consent changes, revoke and return still invalidate explicitly; title/reflection/
manual-check changes retain the independent consent signature as described above.
Observation is lazy on every work access; integrations must call write
authorization *before* real media mutations. Class policy is evaluated
live on every gate; peer media authorization also checks it. Do not serve stale
wall lists or cache classroom-authorized media publicly.

### Notes/comments/statistics

| Method/path | Request/response |
|---|---|
| GET `/works/{id}/notes` | `{notes:[Note]}`; author/member teacher only |
| POST `/works/{id}/notes` | `{revision,sentence_id?:null,text}` → Note; member teacher |
| DELETE `/works/{id}/notes/{note_id}` | `{ok:true}`; member teacher |
| GET `/works/{id}/comments` | `{comments:[Comment]}`; authorized work readers |
| POST `/works/{id}/comments` | `{text}` → `{comments:[Comment]}` |
| DELETE `/works/{id}/comments/{comment_id}` | `{ok:true}`; own commenter or member teacher |
| GET `/classes/{id}/stats?assignment_id=ID` | Stats; member teacher |

Note: `{id,task_id,teacher_id,sentence_key,revision,text,updated_at}`. General
notes use sentence_key -1; sentence notes must reference a current report row.
Text 1–1000; upsert per teacher/work/sentence/revision. Historical notes remain
revision-tagged, never silently attach to a changed sentence.

Comment: `{id,actor_id,text,updated_at,author_name}`. Text ≤60 trimmed characters
and at least **two effective alphanumeric/CJK characters**; whitespace,
punctuation and zero-width formatting do not count;
one comment per actor/work, upsert preserves ID. Requires open comments, published
work and live gate. No self-praise for either role. Teachers may delete anyone's
comment in their class. Own comments can be retracted after unpublication/expiry,
but never cross class. Notes/comment timestamps are Unix seconds.

Stats: `{class_id,assignment_id,roster_count,work_count,distinct_submissions,
published_count,notstarted,not_submitted,issues,queue,auto_release,queue_control}`.
Defaults to active assignment, otherwise all unexpired work. Distinct submissions
counts unique student IDs with a currently done task; multiple versions/tasks do
not inflate it. `notstarted` lists roster `{id,name,number}` with no current work;
`not_submitted` lists those with no done work. `issues` maps gate reason → affected
work count. `queue` contains actual `{task_id,author_id,status,progress}` for live
queued/running tasks, not invented reservations. Teacher-authored work does not
inflate student completion counts.

## Validation

[tests/test_classroom.py](../tests/test_classroom.py) mounts a fresh standalone
FastAPI router with a fake manager, temporary database/report roots, and TestClient.
It never imports application main, touches production task data, loads environment
secrets, deletes media, or calls providers.
[tests/test_ui_integration.py](../tests/test_ui_integration.py) separately exercises
the actual main app, middleware and router lifespans in subprocesses with temporary
data roots and mocked cloud execution. Its source includes ownership, publication,
final-preview, queue/restart, export, deletion, retention and snapshot cases.
The latest full tests and real-browser acceptance are recorded separately in
[the 2026-09-23 refactor report](PROTOTYPE_REFACTOR_20260923.md); this does not
claim real-provider, physical hardware or production deployment validation.