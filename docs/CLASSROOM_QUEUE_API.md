> **Retired classroom queue contract — 2026-09-27.** Teacher release, class/actor dispatch budgets and classroom authorization below are historical and no longer current routes or prerequisites. Do not restore login or replay old held uploads. Historical source/test links may refer to removed files; old results are not current regression counts.
>
> Explicit task retry and local duplication remain active product features under the no-login authority/capability, cost, revision and resource rules in [CORE_WORKSPACE_20260927.md](CORE_WORKSPACE_20260927.md). Their active tests are retained. Startup/history reads must not launch paid work; old task records and databases remain protected, not automatically published or removed by this cleanup.

# Historical classroom queue, retry and duplicate API

Implemented in [backend/classroom_queue.py](../backend/classroom_queue.py) and mounted in [backend/main.py](../backend/main.py) with the private authorization callback. Source reviewed on 2026-09-21; this is no longer an unmounted handoff. Focused coverage is in [tests/test_classroom_queue.py](../tests/test_classroom_queue.py), with host scenarios in [tests/test_ui_integration.py](../tests/test_ui_integration.py).

## Current host integration

1. Main includes `create_classroom_queue_router(settings, task_manager, _authorize_private)`; the factory supplies `/api` once.
2. Classroom uploads use `defer_start=True`, register ownership before dispatch, persist `classroom_task=True` and **always** `queue_hold=True`, including auto-release classes. After retaining the upload reservation, main calls `release_classroom_tasks(settings, task_manager)` **without a class ID**, respecting database auto-release policy. Its best-effort dispatch suppresses `RuntimeError`/`OSError`; held tasks remain available to the lifespan dispatcher rather than being deleted on those dispatch failures.
3. `_authorize_private` preserves author/member-teacher access, CSRF, expired-cookie rejection and legacy-only token fallback. Duplicate uses `invalidate=False`: it does not edit the source film. Retry retains normal invalidation. Both endpoints still require write authorization.
4. `_authorize_task` rejects a copy reservation with 409 before invalidation/mutation. Studio, workbench, remix, replacement and DELETE use this boundary; studio reservations also block non-studio writes. Queue endpoints acquire their reservation after authorization. The marker does not falsify the source's status, revision or QC binding.
5. [backend/task_manager.py](../backend/task_manager.py) now checks copy **and studio** reservations in `cancel_and_delete` and skips them in `cleanup_expired`; classroom retention overrides legacy TTL. These HTTP and housekeeping guards are implemented, not pending integration. They do not protect against external filesystem writers or cross-process access.
6. Main restores records before entering the included router lifespan. The dispatcher starts one named server task using `asyncio.Event` and `wait_for`, wakes once per second, and stops/drains at shutdown. No browser timer is required. Keep the single-process/single-worker deployment and production instance lock.

### Existing interrupted/legacy classroom records

Release persists a held restart marker until actual admission; before the charged runner enters the pipeline it persists `running`. Current `restore_tasks` re-holds **all records marked `classroom_task=True` with `status=queued` and no `processing_started_at`**, even when their old `queue_hold` was false. They re-enter the durable dispatcher rather than the legacy start path. Other interrupted queued/running work becomes failed; there is no stage-resume guarantee. Historical records missing the classroom marker are not automatically migrated by this branch: drain and reconcile those records through an operator-controlled migration, not by clearing ownership or exposing task tokens.

## Authentication and errors

Class queue GET and release POST require the owning class teacher, checked with the existing `_class(..., teacher=True)` helper. Students get 403, unrelated teachers get 404, missing/expired sessions get 401. Classroom mutations require `X-Classroom-CSRF` and pass the existing Origin check. Task-token access never overrides a classroom ownership rejection.

Task endpoints use `authorize(request, task_id, write=True)` supplied by the host. The callback must be **private**, not published-work access. Legacy non-classroom requests still require host middleware's authentication/CSRF/rate controls. All routes reuse `ClassroomRoute`: 256 KiB streamed JSON limit, no-store responses/errors, sanitized validation errors, extra fields rejected. Invalid bodies return 422, oversized bodies 413; busy/stale/incomplete media generally returns 409; retry capacity/draining returns 503. Bodies and quota limits use strict integers (booleans/strings rejected).

## GET /api/classroom/classes/{class_id}/queue

Returns exactly these top-level fields:

```json
{
  "items": [
    {
      "task_id": "task-id",
      "title": "Campus news",
      "author_name": "Student name",
      "status": "queued",
      "held": true,
      "created_at": "2026-09-20T10:00:00+00:00",
      "position": 1
    }
  ],
  "remaining": {"class": 5, "global": 10, "actors": {"student-id": 2}},
  "reset_at": {"class": null, "global": null, "actors": {"student-id": null}},
  "auto_release": false,
  "estimated_start_at": null,
  "limits": {
    "actor": 2,
    "class": 5,
    "global": 10,
    "window_seconds": 3600,
    "max_concurrent_tasks": 2,
    "max_pending_tasks": 5
  }
}
```

- Only unexpired registered queued/running tasks with a manager record are listed. Sort is stable `(works.created_at, task_id)`; no use of mutable updated timestamps.
- `held=true` means queued **without a live scheduled worker**. A live scheduled worker is queued/non-held even during the short restart-safe persisted hold window. Running tasks are never held and have `position=null`.
- Queued positions are 1-based in this class, including held and released tasks. This is not a promise of global execution order: quota-blocked actors can be skipped.
- `remaining` reports sliding-hour class/global budgets and each listed task author's budget (not the releasing teacher's budget). `reset_at` has the same shape with UTC ISO timestamps, or null when no charges remain in the window. When exhausted, its timestamp is the earliest expiry permitting another admission, including after limits were lowered. A non-exhausted budget's timestamp is its next expiry.
- `estimated_start_at` is always null: no fabricated ETA.

## POST /api/classroom/classes/{class_id}/queue/release

Body `{"limit": 1}`; `{}` defaults to 1. Range 1–20. A body is required.

Response: the GET queue fields plus `"released": ["task-id", ...]`. IDs identify scheduled jobs, not completed provider requests. A class teacher may release regardless of `auto_release`, but **cannot bypass actor/class/global budgets, expiry, draining, busy markers or available worker slots**. Zero availability returns 200 with an empty list; check `remaining`/`reset_at` and current jobs. A request does not reserve future slots for its unfulfilled batch remainder.

The callable `await dispatch_ready_tasks(settings, manager, class_id=None, limit=None)` returns the scheduled ID list. No explicit class selects only database `auto_release=1` classes; an explicit class is an internal/manual release and must only be passed after teacher authorization. `limit=None` means up to 20, still bounded by slots. `release_classroom_tasks` is an alias.

## Durable start budgets and exact charging boundary

Settings already available are reused without adding environment fields:

| Scope | Existing setting | Default |
| --- | --- | --- |
| Author/student (stable actor ID, not cookie) | `anonymous_session_task_rate_limit_per_hour` | 2 |
| Class (not IP in this module) | `anonymous_ip_task_rate_limit_per_hour` | 5 |
| All classroom starts across all classes | `anonymous_global_task_rate_limit_per_hour` | 10 |

These are classroom **initial/retry pipeline start** quotas, not counts of individual ASR/LLM/TTS HTTP requests and not monetary budgets. Legacy non-classroom creation, workbench/studio renders and remix/replacement remain outside this ledger; their existing safeguards are unchanged. Do not advertise this as an account-wide cloud spending cap. Their active jobs *do* occupy worker slots for classroom release.

`classroom_dispatch(task_id PRIMARY KEY, actor_id, class_id, at)` is added using the existing `ClassroomStore.transaction()` / `BEGIN IMMEDIATE`. No `user_version` changes. An additive `classroom_dispatch_attempts(id PRIMARY KEY, task_id, actor_id, class_id, at)` event table preserves every retry charge; otherwise the required unique task ledger would erase previous attempts. Existing unique-ledger rows are imported once into the attempt table. Queries use `at > now-3600`. Charges survive new managers, process restart and task deletion. Events are not automatically deleted; retention/archival is an operator concern.

Release calls real `manager.start_task`. A short-lived, synchronous coroutine-factory adapter intercepts only that call, restores the manager method immediately, then uses its real semaphore and original runner. Before the pipeline begins, it rechecks retention/term/draining/budgets and commits a charge. If another waiter owns the next semaphore turn, the job is re-held rather than charged while waiting. The adapter depends on the current Python 3.11 asyncio semaphore and current TaskManager contract and is tested against both; it is not a generic pluggable manager interface.

No charge for merely uploading/holding, or cancelling a scheduled job before admission. Once a run is admitted, failure/cancellation **does not refund** it. Admission includes configuration validation before the first network request; this is a conservative run-start budget, not proof that a provider billed anything. SQLite and filesystem commits are not one crash-atomic transaction: a crash at the boundary can leave a charged interrupted run; the durable running marker prevents automatic uncharged replay. Retrying consumes another attempt.

The existing global pending limit is unchanged. Held queued tasks still count against `max_pending_tasks`, including production's maximum 5. A classroom of arbitrary size cannot upload all work at once; no unlimited admission is implemented. Release also counts ordinary queued/running/live manager jobs against `max_concurrent_tasks`. Duplicate is a completed local-copy operation, not a provider/pending job; its separate disk limit applies.

## POST /api/tasks/{task_id}/retry

Required body: `{"expected_revision": 0}`.

Only idle `status=failed`, current revision 0, expected revision 0 is allowed. Busy background/studio/copy jobs, any revision directory or final film, missing/empty/size-mismatched/unsafe original uploads are rejected. Prepared uploads and declared original own-voice audio must also exist. Completed films that became failed because of restore/report errors cannot be overwritten by retry. This is a full rerun, not a continuation from the failed stage: the current `_run` invokes pipeline configuration validation and all pipeline stages again.

Stage states/timing, task timing/monotonic clocks, progress, error and current-stage fields are reset and persisted before scheduling. Script, preferences, upload metadata and access credentials remain the original task's. Classroom tasks are marked `classroom_task=True`, held and routed through the same quota checks; manual-release classes stay held. Legacy tasks use existing `start_task`. Failure to persist/start restores the previous record. Pending capacity is checked before resetting.

Response: `{"task_id":"...","status":"queued","held":true,"revision":0}` for a held classroom retry. `held=false` for a directly scheduled legacy retry. No new access token is issued.

## POST /api/tasks/{task_id}/duplicate

Required body: `{"expected_revision": 3}` matching the source's **current** revision. Source must be idle and done. Returns:

```json
{"task_id":"new-random-id","access_token":null,"status":"done","revision":0}
```

Legacy non-classroom copies return a new random access token instead of null. Every copy has a new `TaskRecord`/token hash, real independent copied bytes, current script/preferences/completed film and a new initial revision-0 snapshot after JSON remapping. No pipeline is scheduled and no providers run. No hard links.

### Ownership and privacy

Private authorization is mandatory. A classroom teacher can duplicate **their own teacher-authored work only**, not re-own a student's raw sources. Student peers, unrelated teachers and published-gallery readers cannot clone private source media. Registration assigns the requesting actor, original class, title truncated plus ` (副本)`, and the **currently active** assignment. Consent is empty, publication/exemplar/retention/confirmation/manual checks start fresh. Registration rechecks session, CSRF, class term and generative permission after the potentially long copy and happens **before** manager insertion/persisted state publication. Private audit records `duplicate-source:<original-id>`; this provenance is not emitted in public responses.

Allowlisted current `ARTIFACTS` and their explicit local source dependencies are copied: raw/prepared original uploads, declared voice input, normalized/generated media, thumbnails, current/source timing audio, TTS group audio and current/source segments. Dependencies are discovered from `norm_path`, `thumb_path`, `audio_path`, `source_audio_path`, `source_audio`, `normalized_path`, `src`, and segment lists. Report row and visual-beat thumbnail URLs are checked, copied and remapped, stripping any token query. JSON task IDs/revisions are reset and credential/editorial identity keys removed before the snapshot. Only explicitly referenced media files are copied, not whole directories.

Core final/video-only/narration/subtitle/manifest/report/timing/EDL/match-plan/shot/segment artifacts are required; report schema must validate. Missing or empty dependencies fail, rather than returning a successful corrupt clone. Absolute paths, traversal, ADS, linked files/directories and symlink/junction escapes are rejected using existing `local_file` checks plus a root check. Supported roots are managed `settings.data_dir / task_id` records only: arbitrary sample/evaluation paths are **not** supported implicitly. Import samples as independent managed tasks first.

Source studio outputs/project files, old revisions, task state, tokens, logs, provider caches and unreferenced files are not copied. A fresh upload manifest/state is generated. Copy plus initial snapshot must fit 8 GiB and available free space with 64 MiB headroom. This is a preflight check, not a filesystem reservation against unrelated external writers.

The per-source reservation is acquired before awaiting the copy, without falsifying source task status. Blocking copy/snapshot work runs in a thread, shielded and drained even under repeated cancellation; cleanup cannot race a worker still writing. On ordinary exceptions/cancellation, no new manager record survives, the new directory is removed, and any committed registration/audit is removed. Filesystem/SQLite failures during cleanup and host power loss are not a distributed transaction guarantee. Host mutation/TTL guards listed above are mandatory to prevent source mutation/deletion during copying.

## Validation scope

The dedicated unittest module uses temporary `DATA_DIR`, `Settings(_env_file=None)`, real SQLite transactions, real TaskManager persistence and mocked pipeline execution; no main import. Async HTTP cloud sends are fail-fast mocked. Coverage includes FIFO/slots, actor/class/global limits and restart persistence, retry attempts, additive ledger migration, expiry/drain/auto-release lifespan, cancellation before/after admission, retained pending caps, teacher/class/CSRF/Origin guards, retry safety, copy ownership/remapping/revision-0 state, missing dependencies, disk limits, registration/persistence rollback and copy-worker cancellation draining. Real symlink creation is conditionally skipped on Windows hosts without permission. The separate main-app integration module covers held restart/release, durable quota and duplication through the mounted routes. This documentation review did not rerun tests, browsers or providers; source coverage is not a fresh pass result.