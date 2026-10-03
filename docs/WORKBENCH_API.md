# Core task workbench API

> **Current no-login contract, 2026-09-27; local integration passed and deployed on daily port 8000.**
> Backend: 764 passed / 2 skipped of 766; frontend: 286/286; browser: 14/14,
> zero retries. Exact evidence and exclusions are in
> [CORE_WORKSPACE_VALIDATION_20260927.md](CORE_WORKSPACE_VALIDATION_20260927.md).
> [CORE_WORKSPACE_20260927.md](CORE_WORKSPACE_20260927.md) defines task capabilities,
> signed anonymous production CSRF/Origin, strict direct-loopback development
> authority and legacy `local_only` preservation. There are no account approvals.
> Earlier [C26 evidence](../canary_test/CANARY_C26_20260926.md) describes the old
> version, not acceptance of this change; its native-runtime and shutdown risks
> are not resolved by removing accounts.

## Current host integration

- [backend/main.py](../backend/main.py) mounts `create_workbench_router(settings, task_manager, _authorize_private, _authorize_workbench_mutation)` from [backend/workbench.py](../backend/workbench.py). The fourth, optional `before_mutation` callback is part of the current factory contract. The app connects [frontend/src/components/ResultWorkbench.tsx](../frontend/src/components/ResultWorkbench.tsx).
- `authorize(request, task_id, write=False)` can be synchronous or asynchronous, returning the existing record/`None` or raising `HTTPException`. Every route calls it, including historical media. Main requires a valid task capability (`X-Task-Token` or protected media `token`) or strict direct-loopback local authority. Production writes additionally require signed anonymous CSRF and Origin. A session alone never authorizes a task; invalid/conflicting supplied capabilities never fall back to local access. Legacy `local_only` tasks are never remotely token-authorized.
- Edit/restore validates first, reserves and persists its owned queued slot, then invokes `before_mutation` before handing that slot to a background job. The callback rechecks task authority and competing copy/Studio/legacy-risk state; the router rechecks ownership/revision after the await and rolls back only its own reservation on failure. There is no approval invalidation or teacher workflow. Stale/invalid/no-op requests and recording uploads are not themselves final-media edits; accepted edits still require media/QC and transactional checks.
- The router uses `record.background`, the manager semaphore, pending cap and drain state. These are resource protections, not a general provider-spend cap. One router per manager, one application worker and exclusive task filesystem ownership remain required. Text or instruction changes can call configured LLM/TTS/Embedding providers; no-login editing is not necessarily free.
- Main sets private/no-store API headers and checks `Content-Length` before task mutation parsing: missing length returns 411; recording multipart requests are capped at 21 MiB (20 MiB file plus overhead), and workbench JSON at 256 KiB. The separate, exact Studio image/LUT POST paths have 8 MiB / 2 MiB limits, not a blanket exemption for task mutations. Invalid/oversized declared lengths return 413. Retain reverse-proxy limits and handler actual-byte validation; none of these limits relaxes authorization, CSRF or Origin checks.
- [backend/task_manager.py](../backend/task_manager.py) now calls `snapshot_revision` through a cancellation-drained blocking worker on initial/remix/replacement success, before setting live status `done`, when all five required final/report/timing/EDL/match-plan artifacts exist. Workbench prepares its own immutable snapshot before publication; legacy completed tasks get a lazy baseline protected by status/background reservation. Main's current video/report endpoints prefer the committed snapshot too.
- Core `POST /api/tasks/{id}/duplicate` takes `{expected_revision}` and returns a new task ID/nonempty capability for an independent local copy; it does not call a Provider or copy an account approval. Explicit paid `/retry` is only for eligible failed initial runs and uses the creation rate limiter, not a workbench crash-resume path. See [CORE_WORKSPACE_20260927.md](CORE_WORKSPACE_20260927.md). Whole-document own-voice creation uses [MEDIA_INPUT_API.md](MEDIA_INPUT_API.md), distinct from the **unverified-transcript** sentence recordings below. Startup crash reconciliation is still not implemented; see the failure limits below.

## Routes

All paths below are relative to `/api/tasks/{task_id}/workbench`. No response includes task credentials or disk paths.

| Method | Path | Input / output |
|---|---|---|
| GET | `/context` | `task_id`, `revision`, live `status`, successful revision's `script` and `preferences`, `last_operation_error`, `report`, `timings`, `current_shots`, `shots`, `versions` |
| GET | `/versions` | `{current_revision, versions:[{revision,label,created_at}]}` descending |
| GET | `/versions/{rev}/video` | Actual immutable revision MP4, Range support, private/no-store; **not** a query-string cache-bust of the current video |
| GET | `/versions/{rev}/report` | `{revision,report}` from that immutable revision |
| POST | `/edit` | JSON described below; 202 `{task_id,revision,current_revision,status:"queued"}` |
| POST | `/restore` | JSON `{revision,expected_revision}`; same 202 receipt, new monotonically increasing revision |
| POST | `/recordings` | Multipart `sentence_id`, `expected_revision`, `file`; 201 recording receipt described below |

### Context details

- `timings` contains existing sentence timing fields **except** `audio_path`, plus `audio_source` (`tts`, `sync`, or `recording`). Sentence-uploaded recordings additionally contain `recording_id`, `transcript_verified:false`, `uploaded_revision`; their model `audio_kind` remains `sync`. Whole-document own-voice narration instead reports `audio_source:"recording", transcript_verified:true` after its strict ASR alignment, without implying word-level timing or speaker-identity verification. Later TTS replacements report `tts`, not stale recording verification. Report rows also contain `audio_source`.
- Successful-revision `preferences` includes `caption_style` (`news`, `big`, `none`; legacy default `news`) and `enhance_speech` (boolean; legacy default false). Only missing legacy fields receive defaults; malformed present values are not treated as saved preferences. Optional timing `speech_enhancement_applied` is derived from the current processing receipt, **not** transcription or speaker verification.
- `current_shots` is `[{sentence_id,shot_ids}]` from the actual EDL.
- `shots` contains `shot_id`, `source_index`, `source_scene_index`, `start`, `end`, `duration`, `status`, `media_origin`, `description`, `scene_type`, `subjects`, `actions`, `keywords`, `ocr_texts`, `entities`, `source_transcript`, `quality`, `unused`, `used_by_sentence_id`, `selectable`, and protected `thumb_url`. No `norm_path`, `thumb_path`, provider error or original filesystem name is exposed. Generated shots remain disclosed but are not directly selectable.
- Report quality exposes counts, typed issues, and scalar numerical/boolean metrics only; arbitrary nested diagnostic metadata is omitted. Thumbnail URLs are reconstructed without embedded credentials.
- Poll the host task status endpoint or `/context`. `revision` remains the last successful revision until commit. Errors are discoverable as `last_operation_error` and persisted task `error_stage="workbench"`/`error_message`; status returns to `done`, not an unopenable failed task.

### Batch edit JSON

`expected_revision` is a nonnegative integer. `keep_sentence_ids` is a nonempty unique subset of **current** sentence IDs; original order is preserved, not arbitrary reorder. Omitted IDs are deleted together when the whole batch succeeds. Deleted sentences can be recovered by restoring a version.

`edits` defaults to an empty list. Each entry has `sentence_id` and at least one of:

- `text`: nonempty stripped text, no control characters, maximum 2000 characters. Whole edited narration maximum 8000 characters.
- `shot_id`: an actual source shot; cannot be used by another current sentence (including a sentence pending deletion). Submit deletion first to release its shots. Batch duplicate selections are rejected. Existing selection on the target sentence is allowed. `shot_id` and `instruction` are mutually exclusive.
- `instruction`: nonempty, maximum 500 characters, text-only retrieval plus existing embedding/reranking against source annotations; excludes other current shots and the target's previous shots.
- `recording_id`: 32-character random lowercase hex ID previously uploaded to this task and sentence. It is not a path or URL.

Optional batch preferences:

| Field | Accepted value | Applied behavior |
|---|---|---|
| `pacing` | `slow`, `normal`, `fast` | Persisted target 230/265/290 CPM; existing audio is pitch-preserving time-stretched by the new/old target ratio, while new TTS uses the new target. Recorded/source-sync units are not thereby claimed to have verified rates or word alignment. |
| `caption_style` | `news`, `big`, `none` | Pipeline body-caption presentation; **not** Studio's `standard/large/none` export enum. |
| `enhance_speech` | genuine JSON boolean | Local recorded/sync-unit DSP at narration assembly; false explicitly disables it. Strings and 0/1 are invalid. |

Omitted or null optional preferences leave the current value unchanged. A
preference-only edit may retain all current IDs and send `edits:[]`, but at least
one preference must actually change. Unknown fields, invalid values and an empty
no-op return 422; a stale **pipeline** revision returns 409. Success remains the
normal 202 queued receipt and new-revision/QC/rollback workflow, not a separate
preference PATCH or Studio project revision. No account approval is required;
removing it does not bypass QC or allow stale-revision writes.

Text changes synthesize only changed sentences (unless a recording is supplied) and re-match only changed semantics. Unchanged matching and audio are reused. Numeric pronunciation uses the existing document-context planner. A direct manual shot selection is intentionally marked confidence 0 / fallback, **not** asserted as a verified semantic match; strict QC may reject it. Matching/provider/QC failure rolls back the whole batch. Instruction matching is fast text/OCR/ASR retrieval, not new video embeddings. No vision/ASR regeneration is required.

Only affected video segments are rendered; unchanged EDL clips/in-points are preserved. Shot-only changes without preference changes keep narration, timing and subtitles byte-for-byte. Text/deletion/pace/recording changes rebuild narration, measured timing, subtitles and QC. Changing `enhance_speech` rebuilds from reusable original audio units; changing only `caption_style` reuses narration and segments and rerenders the finishing presentation/QC. Finishing preferences and generated-media disclosure are preserved through existing pipeline helpers. Current/source EDL, timings and segment baselines are updated together on success for later legacy deletion operations; historical baselines remain immutable.

### Body captions and recorded-speech processing

Canonical ASS and its manifest are validated for all three caption modes.
`news` keeps that presentation; `big` enlarges both body-caption styles 1.5×,
preserving timing and safely reflowing text or failing if it cannot fit. `none`
omits body captions only. Title, separate graphics, mandatory generated-content
disclosure and text already burned into source footage are not erased. The
temporary presentation does not replace canonical subtitle/ASR evidence.

Only current `audio_kind:"sync"` units qualify for `enhance_speech`: source sync,
aligned whole-document recordings and sentence recordings. TTS is excluded even
when older recording provenance exists. After matching the reference's 48 kHz
stereo conversion, the pipeline applies its 70 Hz high-pass followed by the shared
clock-compensated denoiser in [../backend/audio_filters.py](../backend/audio_filters.py):
1,200-sample pre-roll, 1,200-sample EOF flush, fixed `afftdn`, then removal of
exactly 2,400 synthetic startup samples. Studio uses that same denoiser **without
the pipeline high-pass**.

This is algorithmic warm-up/flush, not padding or trimming speech to force a
target duration. The pipeline checks actual decoded PCM frames against the same
format reference (absolute sample-count difference ≤1) and unchanged source
SHA-256. Rebuilds process originals rather than already-denoised mixes. The
`local_highpass_afftdn_clock_compensated_v2` receipt records requested/applied,
unit hashes/counts and `tts_filtered:false`; requested=true may be applied=false
when all units are TTS. Processing does not verify a sentence recording's text,
identify a speaker or constitute AI repair/cloning. Exact filters and limits are
documented in [STUDIO_API.md](STUDIO_API.md#exact-current-enhance_speech-dsp).

### Sentence recordings

Accepted extensions: WebM, Ogg, WAV, MP3, M4A. Upload maximum **20 MiB**, duration **120 seconds**, one audio stream and no video (attached cover art permitted). File extensions/MIME are not trusted: local ffprobe plus bounded FFmpeg decode verify actual audio, reject unsupported demuxers and disable network protocols. Browser WebM without duration metadata is measured after decode. Audio is stored as mono 48 kHz PCM under a random ID; original filenames and container metadata are not returned.

Receipt: `{recording_id,sentence_id,revision,duration,size,audio_source:"recording",transcript_verified:false}`. `size` is the original byte count. Upload alone does not change the revision or apply audio; reference the ID in an edit. Uploads serialize against editing via task status/background/semaphore. Per-task quota: 100 decoded recordings and 200 MiB decoded audio. No automatic destructive cleanup of recordings referenced by history.

The recording is used as real narration, not a placeholder or TTS fallback. Its transcript is **not** ASR-verified and no word timestamps are invented. Supplied/current sentence text is used for subtitles. This does not perform voice cloning, validate speaker identity, or guarantee the speaker said the text.

## Transactions, limits and failure semantics

- Optimistic concurrency mismatch: 409 with `{code:"stale_revision",current_revision}`. Busy/not-completed task: 409. Unauthorized requests propagate host 401/403/404. Invalid edits/recordings: 422; unsupported extension: 415; upload/resource quota: 413; pending capacity: 429; draining: 503. Missing version: 404.
- Edit/restore assigns and persists `queued` synchronously before returning. A task owns one background job and acquires the manager semaphore. Heavy operations run in an isolated task-local work directory with a 30-minute processing deadline. Blocking copy/QC workers are drained before cleanup on cancellation. Upload validation has a 180-second deadline after admission.
- Snapshot/workspace copying is capped at 8 GiB per copy batch, with a 64 MiB free-space margin; history is capped at 100 revisions. Source media copy and revision artifact copy are separate bounded batches. Operators must enforce whole-task disk quotas/retention externally; snapshots deliberately use copies rather than unsafe hard links to overwriteable media.
- New immutable history is prepared before publishing. Publication has no coroutine suspension points and backs up every changed current artifact. I/O or persistence exceptions restore video/report/metadata together and remove the uncommitted revision. Cancellation (including before the coroutine starts) retains the last successful revision and reports `operation_cancelled`. Provider failures expose `operation_failed`, deadlines `operation_timed_out`, safe input failures `validation_failed: ...`; raw provider exceptions/credentials are not returned.
- **Not a crash journal:** process termination, host power loss, disk failure during rollback, and cross-process filesystem writers are outside exception-atomic guarantees. TaskManager marks interrupted jobs failed after restart, including old unstarted held uploads; it does not automatically run paid work or reconcile interrupted edits to a successful immutable revision. Main's current video/report routes prefer snapshots, but legacy tasks without one still use current files. Lazy snapshot creation reserves status/background so normal manager deletion cancels/drains it rather than deleting under the worker.
- No token, token hash, task-state file, raw upload, provider cache or task log is included in a snapshot. Snapshot audio/segments and version paths are allowlisted with traversal/ADS/symlink escape checks.

## Validation scope — local pass, not live-provider acceptance

The [final backend summary](../canary_test/artifacts/workspace-validation-20260927-131111-860100/summary.json)
records **766 tests: 764 passed, 2 skipped, no failures/errors, 435.199335s**.
It includes the current capability/local-authority adapter, production
CSRF/Origin, legacy local-only tasks, stale/post-await guards, copy/Studio
exclusion, failure rollback and no paid starts on restart. Focused contracts in
[tests/test_workbench.py](../tests/test_workbench.py) and the mounted app coverage in
[tests/test_workspace_access.py](../tests/test_workspace_access.py) are part of
that run, not additional counts. The skips are opt-in historical audio replay
and unavailable Windows symlink privilege.

The [final browser run](../canary_test/artifacts/workspace-core-final-20260927-212523/summary.json)
passed **14/14, retry 0, global errors 0, 69.495374s**. WS-03 used only a TEMP
duplicate: real sentence edit to r1, no-token same-origin remix to r2, UI restore
of r0 as r3, then UI sentence deletion as r4. Four committed changes plus r0
remain five readable versions; immutable history, real local rendering and QC
were not replaced with success stubs. WS-06 separately verifies draft restore
and a held cold deep-link without unsolicited HTTP/storage writes. The daily
service also opened an existing 15-sentence, 61.8s task read-only, without login;
no edit/generation was submitted on user tasks.

Frontend **286/286** contracts include **12 workbench-preference cases** and
**71 app-request/session cases**, not account-session tests. Application/E2E
types and the current build passed. The anonymous request client preserves
task-scoped capabilities and same-origin CSRF without `sessionOnly` or mutation
replay. See the [validation report](CORE_WORKSPACE_VALIDATION_20260927.md) for
the exact seven-script breakdown, build binding and retained earlier failures.

**Not performed:** live transcription, real speech/model quality, microphone
hardware, Linux/public-production acceptance or full WCAG. Browser providers
were exact-loopback fakes with tone TTS, not speech; ASR, whole-document own voice
and direct video embeddings were disabled there. Existing unit/real-codec
contracts are separate from those browser omissions. No new paid calls occurred.
Future tests must still use fresh TEMP, explicit no-dotenv settings and provider
confinement, isolating data/cache/secrets **before importing** the app. This
documentation-only closeout reads existing evidence; it reruns no tests/builds.

Historical results remain in [C26](../canary_test/CANARY_C26_20260926.md) and
[C25](../canary_test/CANARY_20260926.md); their counts, failure evidence and runtime
risks are not rewritten or relabelled as validation of the no-login workspace.