# Local studio API

> **Current no-login contract, 2026-09-27; local integration passed and deployed on daily port 8000.**
> Backend: 764 passed / 2 skipped of 766; frontend: 286/286; browser: 14/14,
> zero retries. Exact evidence, preserved failures and exclusions are in
> [CORE_WORKSPACE_VALIDATION_20260927.md](CORE_WORKSPACE_VALIDATION_20260927.md).
> [CORE_WORKSPACE_20260927.md](CORE_WORKSPACE_20260927.md) defines task capabilities,
> production anonymous CSRF/Origin and strict direct-loopback development access.
> Account approvals and standalone cloud-tool routes/UI are removed; real QC,
> revision, source, job, path and resource guards remain. Historical
> [C26 evidence](../canary_test/CANARY_C26_20260926.md) is not acceptance of this
> change and its native-runtime/shutdown risks remain unresolved.

## Current integration boundary

The factory is `create_studio_router(settings, task_manager, authorize)` in
[../backend/studio.py](../backend/studio.py).
[../backend/main.py](../backend/main.py) mounts it with `_authorize_studio`;
[../frontend/src/App.tsx](../frontend/src/App.tsx) connects
[../frontend/src/components/Studio.tsx](../frontend/src/components/Studio.tsx).
The current warm news-creation workspace has no account or classroom extension.
Studio remains a separate derivative editor, not a replacement for the pipeline
final, report or QC. No account approval is needed to enter or use it.

Reference producers inspected directly:

| Source | Contract |
|---|---|
| [../backend/studio.py](../backend/studio.py) | Routes, actions, optimistic revisions, jobs, admission, source catalog |
| [../backend/studio_render.py](../backend/studio_render.py) | Typed project/export models, geometry, color, ASS, audio, transitions |
| [../backend/studio_sequences.py](../backend/studio_sequences.py) | Whole-graph validation, active selection, bounded identity expansion and render semantics |
| [../backend/audio_filters.py](../backend/audio_filters.py) | Shared 48 kHz denoiser warm-up/flush and clock compensation; no high-pass |
| [../backend/studio_assets.py](../backend/studio_assets.py) | Raw image/LUT validation, canonicalization, content-addressed index |
| [../backend/studio_proxy.py](../backend/studio_proxy.py) | Fixed RAW preview profile, source/output hashes, delivery lease |
| [../frontend/src/lib/studioApi.ts](../frontend/src/lib/studioApi.ts) | Client types, defaults, validation, requests and safe media URLs |
| [../frontend/src/lib/timelineEditing.ts](../frontend/src/lib/timelineEditing.ts), [../frontend/src/lib/studioCompositions.ts](../frontend/src/lib/studioCompositions.ts) | Frame/snap/edit proposals, explicit transition shifts and manual camera composition |
| [../frontend/src/lib/studioSequences.ts](../frontend/src/lib/studioSequences.ts) | Complete-project sequence selection, active edits, duration-binding proposals and scoped clipboard |
| [../frontend/src/components/StudioAssets.tsx](../frontend/src/components/StudioAssets.tsx), [../frontend/src/components/StudioProxy.tsx](../frontend/src/components/StudioProxy.tsx) | Capability-gated import and source-proxy UI |

The [PROTOTYPE_CAPABILITY_MATRIX_20260925.md](PROTOTYPE_CAPABILITY_MATRIX_20260925.md)
contains the **113-ID requirement inventory and missing subfeatures**, not 113
completed tools. That historical snapshot classified **58 partial, 8 metadata,
11 renderer, 36 unsupported**; these are not 77 fully implemented tools or a
new acceptance count. Use the served `/capabilities` and this contract for the
current bounded feature set; old cloud navigation does not restore removed tools.

`authorize(request, task_id, write=False)` must return the authorized TaskRecord or
raise an HTTP exception. Both synchronous and asynchronous callbacks work. Every
route calls it; all POST/DELETE routes pass `write=True`. The host callback is
responsible for task capability or trusted local authority, not account identity.
Main accepts `X-Task-Token` / protected media `token`, or strict direct-loopback
development access. Production writes additionally require signed anonymous
cookie, `X-CSRF-Token` and allowed Origin; the cookie alone is not task authority.
Local token-free writes require the exact same Origin, and explicit invalid or
conflicting capabilities do not fall back to local access. Legacy `local_only`
tasks remain available only in that trusted local workspace.

There is no `can_export` account gate, teacher approval or classroom CSRF header.
New renders/exports must still pass `check_qc()`, finished-task status, current
pipeline/project revision, record/root/source binding and job admission checks.
Successful historical render/export files remain private immutable downloads;
later film edits do not impose the retired classroom approval/current-film gate
on those files. Their original revision and unreviewed status remain visible.
RAW proxy reuse/delivery still requires its current pipeline/source/hash binding;
historical-output access cannot bypass that route. A Studio project `revision`
is not the film's `pipeline_revision`.
Ordinary project/import, action (including undo/redo), SRT import, render and
export requests re-run the same host `authorize(..., write=True)` after body
parsing. Render/export re-run it again after awaited preparation/probing and
before persistent job admission; same record/root, current status, pipeline
revision and expected project revision are checked **after** that await. A
changed/deleted task, stale film/project or competing operation cannot rely on
earlier request authorization to submit a new edit/job. This does not revoke
already delivered bytes or replace per-request task authorization. Asset/proxy
ingestion retains its existing repeated authorization and RAW-preview boundary.
Private sources, image/LUT imports, project JSON backups and **RAW source proxies**
have their own authorization, not an account approval or proof of export QC.
Proxy media has current-source/revision/hash checks; it is not an edited output
that bypasses export QC. Preview access is not
DRM. No route exposes the whole task directory or accepts arbitrary file URLs.

**Host body admission:** `security_headers()` in
[../backend/main.py](../backend/main.py) now grants **8 MiB / 2 MiB** only to exact
**POST** paths matching `/api/tasks/[0-9a-f]{32}/studio/assets/image` or `/lut`.
Other task edit POST/PUT/PATCH requests remain **256 KiB**, except recording
multipart's 21 MiB allowance. PUT/PATCH, trailing/path aliases and unrelated JSON
routes do not inherit the asset allowance. Main still requires `Content-Length`
(missing: **411**; invalid or over the applicable bound: **413**); asset handlers
also count actual streamed bytes and check length agreement. Capability/local
authorization, CSRF, Origin, revision and media checks remain required; this is
not a general upload-limit or authorization exemption. Mounted-host regression
against the no-login adapter passed as part of the final 766-test backend run;
this is not a production reverse-proxy upload or capacity test.

Use **one application worker**, matching the existing TaskManager deployment.
Project compare-and-write sections contain no awaits; process-global reservations
bound renders, exports, imports, proxy preparation and proxy delivery across
factory instances. This is not a distributed lock or filesystem sandbox against
a privileged concurrent writer. The router lifespan begins draining, waits its
configured grace period, then cancels/awaits owned work. The host must run that
lifespan. Do not expose task data with StaticFiles.

All routes have prefix `/api/tasks/{task_id}/studio`. Mutations require task status
`done`, no live pipeline background work, and reject drain or unresolved cloud
work from the read-only legacy risk check; there are no new standalone cloud jobs.
They do not change pipeline task status/revision or overwrite its source,
final, report or QC. Main, TaskManager deletion/TTL and core task-copy admission
consult `studio_task_busy(task_dir)`. Asset ingestion also blocks project writes;
after a render/proxy snapshot is admitted, later project saves are not universally
blocked and do not rewrite that snapshot. Pipeline/source changes invalidate
in-flight result publication; original render/export inputs use size/mtime fences plus imported
asset hashes, whereas proxies additionally hash actual source and output bytes.

## Routes and exact envelopes

Every path in this table is relative to the prefix above. Success is JSON unless
the response column says media/attachment. Host authorization and path-specific
body admission above apply before handler-specific errors.

| Method/path | Request | Success status and response |
|---|---|---|
| GET `/capabilities` | none | **200** `{schema_version,tools,tool_count,limits,sequence_contract,export_schema,project_schema,action_schema,proxy_request_schema,subtitle_import_schema,unsupported,render_available,quality}` |
| GET `/project` | none | **200** `{revision,project,can_undo,can_redo}`; absent project is empty at revision 0; older schema-1 snapshots hydrate additive defaults |
| POST `/project` | `{expected_revision,project}` | **200** same envelope, project revision incremented |
| GET `/project/export` | none | **200** same envelope, JSON backup; does not bundle media/LUTs |
| POST `/project/import` | `{expected_revision,project}` | **200** same as save; send the backup's `project` member, not the whole envelope |
| POST `/actions` | action below | **200** complete updated project envelope; clip/track/marker actions target the saved active timeline only |
| POST `/subtitles/import` | `{expected_revision,track_id,srt}` | **200** complete updated project; adds a text track to the saved active timeline |
| GET `/audit` | none | **200** `{records:[{id,time,op,...details}]}` |
| GET `/sources` | none | **200** source catalog described below; no LUT or proxy entries |
| GET `/sources/{source_id}` | optional `Range` header | **200 / 206** protected original or canonical image bytes |
| GET `/preview/{source_id}` | optional `Range` header | **200 / 206** alias of source streaming; **not** a proxy or rendered timeline preview |
| GET `/assets` | none | **200** `{images:[ImageAsset],luts:[LutAsset],limits}`; verified task-local index |
| POST `/assets/image?expected_revision=n` | raw PNG/JPEG body and exact image MIME; not multipart | **200** `{asset:ImageAsset,project_revision:n,deduplicated:boolean}`; no revision increment, including dedup |
| POST `/assets/lut?expected_revision=n` | raw UTF-8 `text/plain` body; not JSON/multipart | **200** `{asset:LutAsset,project_revision:n,deduplicated:boolean}`; no revision increment |
| POST `/render` | `{expected_revision,options?:ExportOptions}` | **202** job for the captured saved Studio project's active timeline, with bounded nested expansion |
| POST `/export` | `{expected_revision,options?:ExportOptions}` | **202** job for pipeline artifacts, **not the Studio timeline** |
| POST `/proxies` | `{expected_revision,source_id}` only | **202** new queued/running RAW-source job; **200** on verified reuse of the same successful job with `cached:true` |
| GET `/proxies` | none | **200** `{proxies:[Job]}`; persisted proxy jobs only, no hash/probe/render on this list read |
| GET `/proxies/{job_id}/media` | optional `Range` header | **200 / 206** inline MP4 only after successful RAW binding/source/output verification; not the `/outputs` route |
| GET `/jobs/{job_id}` | none | **200** one render/export/proxy job; lazy restart recovery may mark it interrupted; a terminal `cleanup_pending` job retries local removal once, never encoding |
| DELETE `/jobs/{job_id}` | none | **200** cancel and await terminal persistence, returning real job even if physical cleanup is pending; persistence errors remain errors; terminal jobs are **not deleted** |
| GET `/outputs/{output_id}` | optional `Range` header | **200 / 206** authorized successful immutable render/export attachment, including historical revisions; proxy jobs rejected |

Source catalog:
`{sources:[{id,name,bytes,url,burned_subtitles,is_image,width?,height?,duration?,duration_semantics?}],shots:[{shot_id?,start?,end?,duration?,description?,media_origin?,source_id}],report:{rows:[{sentence_id?,sentence?,duration?,confidence?,is_fallback?,audio_kind?}],quality}}`.
`shots[].source_id` can be null when there is no catalog mapping. `is_image` is
present for all current rows; only images currently add width/height,
`duration:120` and `duration_semantics:"still_hold_limit_not_source_eof"`.
That duration is a display budget, **not an image/video EOF**. `burned_subtitles`
is conservatively true for `final`, regardless of its caption preference; it is
not detection of visible dialogue in the file. Optional
`has_video`/`has_audio` types in the client are not promises that this server
catalog probes or returns those measurements for ordinary videos.

`ImageAsset`: `{id,name,bytes,width,height,url}`.
`LutAsset`: `{id,name,bytes,size}`; **no LUT download URL**.
Asset `limits` is `{image_bytes:8388608,lut_bytes:2097152,assets:64,dimension:4096,pixels:8847360,lut_size:33}`.
Capability `limits.assets` contains this same object; capabilities also expose
`source_marks:200`, `rgb_knots_per_channel:8`, `subtitle_bytes:262144`,
`transition_seconds:1.2` and the fixed proxy duration/dimensions/FPS limits.
Sequence limits are `tracks:8` per timeline, `sequences:4`, `total_tracks:32`,
`clips:64` stored project-wide, `markers_per_timeline:100`, `nesting_depth:2`,
`expanded_tracks:8`, `expanded_clips:64`, `decoder_inputs:16`.

Job: `{id, revision, pipeline_revision, state, kind, created_at, options,
output_id, error, qc, disclosure, export_semantics?, transition_semantics?,
sequence_semantics?, source_id?, cache_key?, cached?, finished_at?, result?,
cleanup_pending?, cleanup_error?}`.

- IDs are server-created lowercase 32-character hex strings. `output_id == id`
  only after success; this alone does **not** make a proxy an export attachment.
- States: `queued`, `running`, `succeeded`, `failed`, `cancelled`, `interrupted`.
- `kind`: `render`, `export` or `proxy`; times are Unix seconds. There is no
  progress percentage, ETA, automatic retry or generic job-list endpoint.
- `revision` captures the Studio project; `pipeline_revision` captures the film.
  A cache hit retains the older job's Studio revision. RAW proxy media requires
  a valid current film binding; ordinary historical downloads are not reapproved
  against a newer film revision.
- `qc` identifies an **unreviewed derivative** or **RAW source preview**, never
  pipeline approval. `disclosure` is a boolean, not an authorization flag.
- Successful media `result`: `{file,bytes,duration,probe,applied,disclosure,
  png_semantics,frame_time,gif_duration_limit,gif_fps_semantics,transition_semantics?,sequence_semantics?}`.
  `probe` exposes allowlisted stream properties and format duration/size/bitrate,
  not filesystem paths/tags. Timed-text results contain
  `{file,bytes,duration,applied,transition_semantics?,sequence_semantics?}`. Proxy results additionally
  carry the verified output `sha256`; `source_id`/`cache_key` occur on proxy jobs.
- Simple jobs add `export_semantics:{picture,audio,framing,warnings}` and persist
  it in the output manifest. This describes the source strategy, not proof that a
  particular input has music/graphics. Clients must show restyle warnings.
- Active declared transitions add `transition_semantics` to the job/result and
  result manifest. It contains `items`, `geometry`, `visual`, `audio`, `export`,
  `warnings`; each item has `track_id,right_clip_id,start` plus `TransitionIn`.
- Projects containing named sequences add `sequence_semantics` to timeline jobs,
  results and manifests: `active_sequence_id`, `mode:"full_length_identity_flatten"`,
  duration, expanded track/clip/decoder counts, actual nesting depth,
  `instances:[{sequence_id,clip_id,path,start,duration,mute}]`, used source/LUT IDs,
  composition and warnings. Counts are **occurrences**, not just distinct IDs.
  Legacy projects without sequences and simple pipeline exports keep their
  sequence-free result shape.
- Failures never produce downloadable output. Ordinary success is assigned only
  after the result manifest is written; failed/cancelled/interrupted jobs have
  `output_id:null` and no `result`. Public process errors are sanitized.
- `cleanup_pending` / `cleanup_error` are an optional **pair**. When present,
  pending is a genuine JSON boolean; true requires a nonblank generic error of
  **at most 256 characters**, only on an unsuccessful terminal job. False pairs
  with `cleanup_error:null`. A missing legacy pair is not proof of cleanup.
- Physical cleanup is attempted within the contained job workspace and the
  captured task-root identity. If deletion fails, the true terminal state is
  still persisted with `cleanup_pending:true` and a generic bounded
  `cleanup_error` (no filesystem paths or raw exception). Partial files remain
  quota-counted under `studio/outputs`, not free disk or successful output.
  Successful cleanup has `cleanup_pending:false, cleanup_error:null`; older
  jobs may omit both fields and are not assumed to have been verified clean.
  A removed/replaced task root is not recreated, cleaned or written just to
  return a terminal receipt; root-identity and metadata-persistence errors
  remain errors. The negative contracts do not promise power-loss atomicity.
- Known persisted running jobs without a worker are marked interrupted when
  fetched after restart, even if physical removal fails. The terminal outcome is
  never resumed or rewritten as success. A later authorized job GET retries one
  pending removal, preserving the original error, completion time and project
  history. Already-clean reads are idempotent; no duplicate interruption audit,
  new job, provider call or encoding retry is introduced.
- The UI shows a quota/residue warning plus **读取状态并重试清理**, which makes only
  the existing job GET. Pending cleanup is not an active encoding progress bar,
  and an older pending response cannot replace a confirmed cleared terminal
  receipt. Job metadata and audit are separate atomic writes, not a multi-file
  power-loss transaction; failure to persist metadata is not swallowed as 200.

Errors normally use FastAPI `{detail:...}` (detail can be a string or object):

| Status | Boundary |
|---|---|
| 400 | Asset name header, malformed/duplicate handler `Content-Length`, received-length mismatch or disconnected body; malformed Range is handled by FileResponse |
| 401 / 403 / 404 | Host capability/local-authority/CSRF/Origin denial; path escape or links are 403; unknown source/job is 404; no login challenge |
| 408 | Asset body timeout or proxy JSON body timeout |
| 409 | Stale project/film revision, locked/busy/not-done task, QC/export gate, unavailable/stale/corrupt proxy, undo/redo unavailable, 100-mutation limit |
| 411 | Current main host requires `Content-Length` on task POST/PUT/PATCH |
| 413 | JSON >256 KiB, state/history >32 MiB, image >8 MiB, LUT >2 MiB, or invalid/oversized declared length at host admission |
| 415 | Wrong raw MIME, multipart instead of raw asset body, or non-identity `Content-Encoding` |
| 416 | Unsatisfiable media Range |
| 422 | Invalid/unknown fields or IDs, unsupported source/container/options, bad asset/index/content, illegal transition/edit geometry, failed proxy preflight |
| 429 | Shared capacity, 20-job task quota, or 64-asset collection cap for a new unique asset |
| 503 | Drain; required image/proxy codecs absent; proxy disk-lease admission timeout or host resource failure |
| 507 | Shared Studio quota, minimum free/reserved disk or local asset storage failure |

After 202, codec/probe failures become failed jobs rather than a new HTTP error.
Simple export probes the final before 202; its explicit unavailable/length/title
checks return 422. Not every exception from that legacy preflight is normalized
to 422, so clients must handle unsuccessful/unknown submissions without replay.
Proxy preflight explicitly normalizes media/data failures to 422. `render_available`
only checks discovery of FFmpeg/ffprobe; it is not preflight success or permission.

`POST /render` example body:

```json
{"expected_revision":1,"options":{"format":"mp4","resolution":720,"fps":25,"aspect":"9:16","subtitles":"none","video_bitrate_kbps":2500,"audio_bitrate_kbps":128}}
```

## Project schema and timing semantics

Studio request/project models forbid unknown fields and non-finite numbers.
Full nested JSON schemas, including bounds, are returned by capabilities. Schema
version remains **1**: absent additive fields hydrate defaults on reads, saves,
imports and undo/redo. Old snapshots are not silently retimed or re-styled.
Unknown enums/keys fail, rather than being dropped; null is valid only on nullable
members. The backend's general `StrictModel` is not globally `strict=True`;
`TransitionIn.duration/audio`, proxy revision and imported asset records have
additional strict typing. Clients should send real JSON numbers/booleans, not
depend on legacy coercion. Controls do not accept executable shell/filter/ASS
expressions, paths, URLs, provider keys or arbitrary fonts. Text remains literal
and escaped, even when it resembles an ASS command.

Project defaults: `{schema_version:1,name:"Studio",tracks:[],markers:[],sequences:[],active_sequence_id:null,groups:[],assets:[],workspace:{layout:"default",timeline_zoom:1,timeline_fps:30,time_display:"seconds",snap_enabled:true,ripple_enabled:false,source_marks:[],shortcuts:{}}}`.
Root `tracks/markers` **always mean main**, not whichever child is active. There
may be **four additional named sequences**; each timeline has at most 8 tracks
and 100 markers. Main plus all sequences share **32 stored tracks and 64 stored
clips**, including nested descriptors. Sequence/track/clip/marker/group IDs are
globally unique and match `[A-Za-z0-9_-]{1,64}`. Project name is at most 120
characters. Duration is the active selection's maximum visible/solo-resolved
clip end. Hidden and inactive content still undergoes full model/reference checks.

Track: `{id,type,name:"",locked:false,hidden:false,solo:false,color:"gray",group_id:null,clips:[]}`.
`type`: `video`, `audio`, `text`, `overlay`, `adjustment`.
Track color: `gold`, `cyan`, `red`, `purple`, `gray`; name length ≤80.

- Hidden tracks never contribute to output. If any visible track is solo, only
  visible solo tracks render. A muted clip affects audio only.
- Tracks composite in array order, later above earlier. Within a media track,
  legacy/no-transition clips retain array order. **A track with any declared
  transition uses stable start-time order for all its clips**; removing its last
  transition restores array order. Other tracks keep their own ordering.
- Text renders after all media/adjustment tracks; mandatory disclosure is last.
- Adjustment clips apply the typed color chain, including RGB curves/LUT, to
  already-composited lower media during `[start,start+duration)`, not later text.
- Gaps are black/silent. Adjacent clips hard-cut; ordinary overlaps use normal
  alpha composition. Only explicit `transition_in` enables a boundary transition.
  There is no implicit ripple or optical-flow interpolation. Nested gaps can
  expose lower parent layers, as specified below; they are not opaque group beds.
- Track lock protects clip edits, replacement, deletion and reordering. Unlock
  explicitly with a track action. Undo/redo intentionally restore whole snapshots,
  including historical lock state.

Clip required fields: `{id,duration}` plus `source_id` for ordinary media,
or `sequence_id` instead for a nested video/overlay descriptor; audio cannot
reference a sequence. Text clips require nonempty `text`. Duration is **output seconds**; source interval
for moving media is `[trim, trim + duration * speed]`; reverse changes playback
direction, not the interval's low-bound meaning. Start is output timeline seconds.
Render checks actual EOF (25 ms codec tolerance), with no automatic freeze padding.
Explicit `freeze` reads one frame at trim and holds it for at most 10 seconds.
Imported stills instead use an authored hold, no source clock. Export frames
sample/quantize the timeline at **export** FPS, independently of workspace FPS.

| Fields | Defaults and bounds | Applied to |
|---|---|---|
| `start`, `duration`, `trim` | start 0; duration >0; start+duration ≤120; trim 0–3600 | all start/duration; trim media only |
| `source_id`, `lut_id` | null; current authorized source ID / `lut_` plus 24 lowercase hex digits | source on video/audio/overlay; LUT on video/overlay/adjustment only |
| `sequence_id` | null; existing named sequence ID, mutually exclusive with source_id | full-length identity reference on video/overlay only; restrictions below |
| `speed`, `reverse` | 1 (0.5–2), false | media: setpts/atempo; reversed video/audio |
| `freeze` | false; if true requires mute=true, speed=1, reverse=false, duration≤10 | video/overlay only; clone one frame at trim |
| `rotation`, `mirror`, `flip` | 0 (0/90/180/270), false, false | visual transpose/hflip/vflip |
| `crop` | `{x:0,y:0,width:1,height:1}`; x/y in [0,1), width/height in (0,1], sums ≤1 with 1e-6 serialization tolerance | visual source rectangle, before rotation/scale |
| `scale`, `x`, `y` | 1 (0.05–1), 0 (-1–1), 0 (-1–1) | visual scaling/position; text uses a different center mapping below |
| `opacity` | 1 (0–1) | visual alpha / text ASS alpha |
| `fit` | `contain` (or `cover`) | visual aspect fit; cover center-crops after scaling |
| `keyframes` | `{x:[],y:[],scale:[],opacity:[]}` | video/overlay only; details below |
| `mask` | null or typed rectangle/ellipse | video/overlay alpha; details below |
| `transition_in` | null or `TransitionIn` | incoming boundary on the right video/overlay clip; exact constraints below |
| `volume`, `pan`, `mute` | 1 (0–2), 0 (-1–1), false | audio: stereo balance (not spatial pan); mix has a pre-encode limiter |
| `brightness`, `contrast`, `saturation` | 0 (-1–1), 1 (0–2), 1 (0–3) | visual and adjustment eq |
| `temperature`, `hue` | 0 (-1–1), 0 (-180–180 degrees) | visual/adjustment bounded RGB cast and hue rotation |
| `shadows`, `highlights`, `fade_amount` | 0 (-1–1), 0 (-1–1), 0 (0–1) | visual/adjustment tone curves; photographic fade is **not** a temporal fade |
| `color_preset`, `rgb_curves` | `none` (`warm`, `cool`, `cinema`, `mono`), `{}` | visual/adjustment deterministic preset and per-channel curves; not AI or an imported LUT |
| `sharpen`, `noise` | 0 (0–1), 0 integer (0–20) | visual unsharp / temporal noise with fixed seed |
| `fade_in`, `fade_out` | 0 seconds; nonnegative, sum ≤duration | visual alpha, audio gain, text ASS fade |
| `audio_effect` | `none`; choices `compressor`, `limiter`, `denoise`, `invert`, `reverb`, `normalize` | fixed local audio filters, detailed below |
| `bass_db`, `treble_db` | 0 (-12–12 dB) | real bass/treble shelf EQ at 100/3000Hz, after selected audio effect |
| `text`, `subtitle` | empty (≤500 chars), true | text only; literal text escaped, no ASS injection |
| `font_size`, `color` | 42 (12–160), `FFFFFF` (six hex digits) | fixed bundled Noto Sans SC; size scales from 1080-height reference |
| `bold`, `outline`, `shadow`, `background` | false, 2 (0–8), 0 (0–8), false | text only: ASS bold/border/shadow/background box (BorderStyle 3); text fades remain supported |
| `text_template`, `text_animation` | `custom`, `none` | text only; template/animation choices below |
| `chroma_color`, `chroma_similarity`, `chroma_blend` | null/six hex digits, 0.1 (0.01–1), 0 (0–1) | visual chromakey; parameters require a color |

Non-default controls that cannot apply to a track type are rejected. Non-default
volume/pan/effect/EQ on sources with no audio fail instead of silently succeeding.
Mute and visual fades do not invent a missing audio stream. Explicit
audio-only/video-only export intentionally omits the other stream type.

### Named sequences and full-length identity nesting

`Sequence` is `{id,name,tracks:[],markers:[]}`, name length ≤80. Groups, asset
ratings/tags and workspace (including FPS and source marks) remain shared at the
project root. Selecting `active_sequence_id:null` means main; an unknown ID fails
instead of falling back. Selection is saved through `/project` or `/project/import`
and increments the normal project revision/history. GET/save/action responses
always contain the **complete project**. `/render` captures that saved selection;
simple pipeline `/export` remains independent of Studio selection and geometry.

- A nested descriptor uses only non-default `id,sequence_id,start,duration,mute`.
  `source_id:null`, trim=0, speed=1, reverse/freeze=false, fit=contain and every
  other clip control must remain default. No nested source range, scale/crop,
  opacity, FX, LUT, volume/pan, fades or incoming transition is accepted. Edit
  ordinary child clips for those effects. `mute` suppresses descendant audio only.
- Duration must equal the child's complete visible/solo-resolved end, **>0 and
  ≤120s**, within 1e-12 serialization noise. A changed child end requires every
  affected parent/ancestor descriptor to be updated in the same complete save;
  the server never silently resizes, clips or repairs it. Empty children cannot
  be referenced. A nest span cannot overlap any clip on its own track; separate
  parent layers may overlap.
- All references, including hidden, solo-excluded and inactive timelines, are
  checked for missing targets/cycles and at most **two reference edges from any
  timeline**. Main→A→B is legal; a third edge is not. References target named
  sequences, never main or another task. Remove all references in a separate
  save before deleting a referenced sequence.
- Locks preserve each track's content, order and timeline container. A locked
  parent nest additionally protects referenced child track content/order
  **transitively**, even when the parent is hidden/inactive. Unlock through the
  explicit active-timeline track action, not a combined unlock-and-edit save.
  Undo/redo restore complete validated snapshots, including selection and locks.
- Visibility/solo resolves independently per timeline, then child layers are
  inserted at the parent's layer position. Original trims, local envelopes and
  internal transitions survive; only ancestor start offsets and mute propagate.
  **This is not an isolated group:** gaps/alpha expose lower parent layers,
  child adjustments affect the accumulated lower composite, and text from all
  timelines renders above all media. There is one final audio mix/disclosure
  burn and **no intermediate media or proxy substitution**.
- Active expansion is limited to **8 nonempty layers, 64 leaf clip occurrences
  and 16 decoder/source occurrences**, plus the existing frame/memory/graph/time
  bounds. Repeated references count each time; empty/hidden/solo-excluded lanes
  add no decoding/render-layer cost, while stored limits still apply. Every
  declared source/LUT is authorized/validated and watched, including inactive
  content; active expanded leaves determine actual decoding. Imported hashes
  are checked again before output publication.

The current UI can create/select/open children, insert a complete child on a new
parent lane, confirm all changed duration bindings, and copy complete clips across
sequences **within one task**, with fresh global IDs and intact nested references.
**Cross-sequence cut is blocked**; same-sequence cut remains one atomic move.
Ordinary source playback is not a nested composite preview. Parent-reference UI
editing is narrower than the API: move, ordinary delete and validated copy; no
parent mute/effect inspector, split, ripple or trim controls. The API accepts
identity `mute` and the action subset documented below.

The existing transition/manual-multicam builders are explicitly disabled in child
timelines and in main when it contains a nest. They must not save an active child
view as root tracks. Existing ordinary camera tracks and internal transitions
can be stored in a child and rendered through nesting, but there is no automatic
camera recipe conversion, auto-sync, multi-window editing or sequence-clone UI.

The manual-camera ID allocator scans groups, sequence IDs, and all track/clip/
marker IDs in **main plus every child, including inactive children**. Append and
root replacement both avoid that global namespace without changing the retained
child projects. C26 fixes ID allocation, not the nesting/builder limits above.

### Geometry, color and audio

Visual order is source trim/reverse/speed → normalized crop → discrete
rotation/mirror/flip → aspect scale/cover crop → color → animated scale →
sharpen/noise → chromakey → alpha/mask/transition → fades → timeline placement.
For output `W,H`, static target scale is even-rounded down with a 2-pixel minimum;
`cover` fills and center-crops that rectangle, while `contain` preserves aspect
inside it. Visual overlay top-left is `(W-w)/2 + W*x`, `(H-h)/2 + H*y`.
Offsets are full-canvas fractions, **not** half-width fractions or crop coordinates.

Color order is **EQ → preset → temperature → shadows/highlights → hue →
photographic fade → RGB curves → imported 3D LUT**. Each supplied `red`, `green`
or `blue` curve has 2–8 `{x,y}` knots in [0,1], strictly increasing x, first x=0,
last x=1. Omitted channels are identity; an explicitly empty channel is invalid;
y need not increase. No curve expressions or preset filenames are accepted.
Current warm/cool presets use red/blue midpoint 0.58/0.42 and its inverse;
temperature uses midpoint `0.5 ± 0.15*temperature`. Shadows/highlights use
quarter knots `0.25 + 0.18*shadows`, `0.75 + 0.18*highlights`. Fade lifts black
to `0.16*fade_amount` and reduces white to `1-0.10*fade_amount`. Cinema adds
contrast 1.08/saturation 0.85 plus fixed red/blue curves; mono removes saturation.
`color_preset:"none"` skips the preset, **not** other authored color controls.
No AI matching, HDR color management, color wheels or selective-region grading
is implied. Image/LUT color branches preserve source alpha separately before
normal compositing; explicit transition participants are the opaque exception.

For encoded video, the final chain performs **actual sample conversion** via
`scale=out_color_matrix=bt709:out_range=tv`, then `format=yuv420p` and BT.709 frame
tags plus encoder tags. Tags alone had left a BT.601 matrix on 360p frames
(the lime regression decoded green near 212 instead of the correct ≈250).
The matrix/range conversion fixes that cause; it is not HDR/ICC color management.
PNG remains RGB and GIF uses its separate palette branch.

Audio order is source trim/reverse → pitch-preserving `atempo` **only if speed≠1** → 48 kHz stereo
conversion → volume/balance → selected effect → bass/treble → authored fades →
optional transition envelopes → clip-duration trim → sample-based timeline delay.
`pan` attenuates L by `min(1,1-pan)` and R by `min(1,1+pan)`; it is not spatial pan.
Effects are fixed: compressor threshold 0.125/ratio 4; limiter 0.95 with no gain
boost; **clock-compensated `afftdn`** for Studio `denoise`; phase inversion; echo-based reverb
with 60/120 ms delays, gains 0.3/0.2 and input/output gain 0.8/0.88; or single-pass
loudnorm -16 LUFS/-1.5 dBTP/LRA 11. Echo tails are bounded to the clip interval.
The final mix uses a silent bed and latency-compensated 0.95 limiter, stereo
48 kHz output. There is no exact mixed-programme LUFS guarantee.

Studio `denoise` now shares `AFFTDN_CLOCK_FILTER` from
[../backend/audio_filters.py](../backend/audio_filters.py) with the pipeline:
**1,200-sample pre-roll + 1,200 EOF flush → fixed afftdn → remove 2,400 synthetic
startup samples**, on the post-tempo 48 kHz clock before authored fades/delay.
It adds **no high-pass**; the pipeline's recorded-only `enhance_speech` adds its
own 70 Hz high-pass and processing receipt. Neither path pads/trims speech to
repair a measured duration mismatch. Exact 1× bypasses `atempo`, including
`audio_effect:none`: real odd-length 96,037-sample PCM previously lost 55 samples
through `atempo=1`, concealed by the silent mix bed. This correctness change also
requires the RAW proxy **profile version 2**; old command byte-equivalence is not
a valid acceptance criterion for the faulty path.

### Typed animation and masks

Each animated property accepts 2–8 `{time,value,easing:"linear"}` points, starting
at local output time 0, strictly increasing, ending no later than clip duration.
Values have the same bounds as static x/y/scale/opacity. Empty lists use static
values; nonempty lists replace them. After the last point, its value is held.
The left point controls the segment easing: `linear`, quadratic `ease_in`, or
quadratic `ease_out`. Times are seconds (a client frame index must be divided by
the chosen workspace FPS); output frames sample the curve at export FPS.
`ease_in_out` is also supported: cubic smoothstep $3u^2-2u^3$, not a Bezier editor.
No expression strings, paths, arbitrary curves, color/audio/text keyframes or
motion tracking are accepted.
Scale animation uses a fixed transparent output canvas to avoid FFmpeg filter
reinitialization resetting downstream clocks. Split refuses animated clips until
keyframes are removed; move keeps local timing; trim revalidates point bounds.

Mask: `{type:"rectangle"|"ellipse",x:0.5,y:0.5,width:1,height:1,feather:0,invert:false}`.
Coordinates/size are normalized to the post-transform frame (the fixed canvas for
animated scale), before timeline position. x/y are 0–1; width/height are (0,1].
Feather is an inward linear coverage ramp of 0–0.5 relative to shape radius;
invert complements shape coverage. Server-generated `geq` multiplies shape,
opacity and existing source alpha; chromakey remains compatible. This is not a
tracked/Bezier mask. Equal normalized ellipse width/height does not guarantee a
pixel circle on a nonsquare frame. Inversion cannot restore transparent source
pixels because source alpha is still multiplied by coverage.

### Typed text templates and animation

Text subtitles default to ASS bottom-center; a nonzero x/y selects center-based
position `(W*(0.5+x/2), H*(0.5+y/2))`. Non-subtitle text defaults to that center
even when x=y=0. This differs from visual-track position. Font size is
`font_size*H/1080`, with a further 1.5 multiplier for subtitle clips when export
`subtitles:"large"`. `subtitles:"none"` hides only text clips with `subtitle:true`;
ordinary text, images, overlays, intrinsic source alpha and required disclosure
remain. It does not erase anything already burned into a source.

| `text_template` | Rendered color / bold / outline / shadow / background |
|---|---|
| `custom` | Authored `color,bold,outline,shadow,background` |
| `news` | `FFFFFF`, true, 3, 1, false |
| `outline` | `FFFFFF`, false, 5, 0, false |
| `gold` | `FFD166`, true, 2, 2, false; solid gold, not gradient |
| `note` | `FFF1BE`, false, 4, 0, true |

Named templates override only those five **rendered** attributes. Stored custom
values, text, size, position and opacity survive; returning to `custom` restores
them. No handwriting font, external template or raw ASS import is implemented.

`text_animation`: `none`, `typewriter`, `fade`, `pop`. `fade` honors explicit
`fade_in/out`; only when both are zero does it use 20% of duration at each edge,
capped at 300 ms. `pop` scales 70%→100% over the first min(300 ms,20% of duration),
at least 1 ms; it is not rotation or repeating bounce. `typewriter` stages prefixes
over about the first 60% of local duration with at most 64 stages; long strings
reveal groups. ASS centisecond rounding must leave positive text duration and
at least two centiseconds for typewriter. The original clip's opacity/fade
envelope is evaluated across prefixes rather than restarted for each prefix.
These are authored animations, **not ASR word timing**. SRT remains one plain
subtitle cue per clip. Generated ASS/SRT is capped at 256 KiB UTF-8, including
all typewriter events; required disclosure is a separate final, static layer.

### Explicit incoming transitions

`transition_in` is absent/null for no transition. Otherwise its right clip owns
`{left_clip_id,kind,duration,easing:"linear",audio:true}`. Kind is exactly
`dissolve`, `wipe_left`, `wipe_right`, `wipe_up`, `wipe_down`; there is no
`crossfade` enum or arbitrary FFmpeg transition name. Numeric duration and
boolean audio are strict; only easing/audio have defaults inside the object.

- Two different clips on the **same video/overlay track**; left/right starts
  and ends both strictly increase. Let `d` be duration: `left.end-right.start`
  must equal `d` to 1e-12 serialization tolerance, not a frame-sized gap tolerance.
- `0 < d ≤ 1.2` seconds, `d ≥ 1/workspace.timeline_fps`, and `d` cannot exceed
  half the duration of **either** participant. It need not be an integer number
  of frames. No third same-track clip may intersect the open overlap interval.
  A→B→C is possible when incoming/outgoing overlaps do not exceed these bounds.
- Both ends must already have `fit:"cover"`, scale=1, x=y=0, opacity=1, no
  mask/chroma/keyframes/fades/freeze. Source trim/speed/reverse, crop, discrete
  rotation/mirror and typed color/LUT remain valid. Hidden tracks validate too.
- Participants use **opaque source RGB**, intentionally discarding intrinsic
  source alpha. Only the incoming picture gets the transition coverage; the
  outgoing picture is not faded a second time. Wipe names describe the moving
  edge: left reveals right→left, right left→right, up bottom→top, down top→bottom.
  Visual easing has the same four choices as keyframes.
- `audio:true` applies complementary **linear** fade-in/out to existing unmuted
  participant audio **after tempo/effects and before timeline delay**, independent
  of visual easing. This path restores 48 kHz after effects such as loudnorm
  before the sample-based delay. No `acrossfade`, source cut, timeline shortening
  or generated replacement sound is used. `audio:false` leaves the overlap mix
  unchanged; simultaneous audio can be louder and is disclosed in warnings.
- Audio-only exports use the audio envelopes only; timed-text exports do not
  execute transitions. Render never shifts either clip or changes project length.
- Delete/split or cross-track move of either bound endpoint requires a prior
  explicit removal of the binding while retaining both endpoints; save/import
  cannot evade that rule by deleting the owning right clip. Other geometry edits
  must retain all constraints. Undo/redo restore whole validated snapshots.

The frontend transition builder is a **separate explicit authoring proposal**:
starting from a contiguous boundary it shifts the right clip and same-track
chronological successors earlier by `d`, shows movements/warnings, then saves
after confirmation. Removal proposes the inverse shift. Other tracks, markers,
trims, clip durations and effects are not changed. Thus the proposal may change
the project end; the renderer itself does not. The UI's audio checkbox initially
starts false, whereas omitting API `audio` means true. This builder is only enabled
on main with no nested references; internally valid child transitions remain
renderable, but a sequence descriptor cannot be a transition endpoint.

### Workspace metadata, timecodes and source marks

Marker: `{id,time,label:""}`; 0≤time≤120, label length ≤120; metadata only.

Metadata only (never simulated pixel effects): `groups` holds ≤8 `{id,name}`
entries (name≤80); track.group_id must reference one. Group IDs share the global
ID namespace. `assets` holds ≤200 unique `{source_id,rating:0,tags:[]}` records,
authorized catalog IDs only, ratings 0–5, ≤16 tags of 1–40 characters. This is
rating/tag metadata, not the separate 64-entry image/LUT collection. `workspace`
stores layout `default`, `editing`, `audio`, `captions`, zoom 0.25–8, and shortcuts for
`play_pause|split|delete|undo|redo|marker`. Chords use optional Ctrl/Alt/Shift plus
A–Z, 0–9, Space, Delete, Left or Right; repeated modifiers/duplicate chords fail.
The current client consumes layout, shortcuts and group views; they do not
implement backend render grouping or nesting.

Workspace timing controls:

- `timeline_fps`: 24/25/30/60 (default 30), independent of source and export FPS.
  `time_display`: `seconds` or `frames` (default seconds). The client accepts
  integer frame counts or non-drop `HH:MM:SS:FF`, with `FF < timeline_fps`.
  The API still takes **seconds**, not frame strings or source SMPTE metadata.
- `snap_enabled:true` lets the client propose the nearest origin, marker or clip
  boundary within six frames, with disclosed quantization. The server does no
  magnetic target search. Non-grid legacy targets are not moved to make a match.
- `ripple_enabled:false` is a client preference, **not** a change to ordinary
  `delete`/`trim` API semantics; the client sends the explicit ripple op.
- `source_marks:[]`: ≤200 unique `{source_id,in_point,out_point}` entries,
  `0 ≤ in_point < out_point ≤ 3600`, authorized current source IDs. These are
  exact **source seconds**, not timeline frames. Saving marks does not probe
  EOF, trim a clip or create one. The UI reads original media `currentTime` or
  explicit values, preserves invalid draft values separately, and requires
  explicit insertion. Images use an authored display duration instead; proxies
  cannot provide precise original-frame mark points.

All workspace metadata survives save/export/import/undo/redo. Changing FPS or
display preference does not round all pre-existing geometry or word cues.

Minimal project save (get actual source IDs from `/sources`):

```json
{"expected_revision":0,"project":{"schema_version":1,"name":"Example","tracks":[{"id":"video","type":"video","clips":[{"id":"clip1","source_id":"final","trim":0,"start":0,"duration":1}]}],"markers":[]}}
```

## Actions, history and audit

Every action requires `{expected_revision,op}`. Clip/track/marker actions and SRT
import address **only the saved active timeline**; IDs from another timeline are
not implicitly followed. Undo/redo operate on the entire project. Switching
selection requires a normal complete-project save, not an action target override.
Required/optional additional fields:

| op | Required | Optional |
|---|---|---|
| `split` | `clip_id,at,new_id` | none; at is absolute output time inside clip; quantized half-up to workspace frame |
| `delete` | `clip_id` | none; no ripple |
| `duplicate` | `clip_id,new_id,at` | `track_id` same-type unlocked destination; at quantized |
| `move` | `clip_id,at` | `track_id` same-type unlocked destination; at quantized |
| `trim` | `clip_id,trim,duration` | none; explicit source trim/output duration, not frame-rounded by server |
| `ripple_delete` | `clip_id` | none; shifts same-track successors by minus old duration |
| `ripple_trim` | `clip_id,trim,duration` | none; new duration quantized; shifts successors by new-minus-old duration |
| `slip` | `clip_id,trim` | none; source trim only, video/overlay/audio, not imported stills |
| `roll` | `clip_id,at` | none; selected left clip's new shared boundary with chronological next clip |
| `slide` | `clip_id,at` | none; selected middle clip's new start between previous/next clips |
| `marker` | `new_id,at` | `label`; marker time is not frame-rounded by server |
| `clear_markers` | none | none |
| `track` | `track_id` | at least one of `locked,hidden,solo,color` |
| `undo`, `redo` | none | none |

Unknown fields and non-null inapplicable action members fail. Nullable common
action members sent as null count as absent; required members cannot be null.
`at` is 0–120 seconds; `trim` 0–3600; duration is positive and ≤120. Half-up
rounding applies only to user-supplied split/move/duplicate/roll/slide `at` and
ripple-trim duration. Existing neighbors, marks and source trim are not re-snapped.
Backend rational arithmetic anchors deltas to old endpoints rather than
accumulating frame-roundtrip drift.

- Ripple shifts only same-track clips starting at/after the selected **old end**;
  other gaps, other tracks and markers are retained. The ripple flag alone does
  nothing server-side. Edits/shifted clips cannot overlap affected neighbors;
  unrelated legacy overlaps elsewhere remain legal.
- Slip changes only source trim (including explicit reverse/freeze frame choice),
  not start/duration/speed/keyframes; it rejects affected same-track overlaps.
- Roll requires two contiguous chronological neighbors, keeps their outer
  endpoints and changes the shared cut. Slide requires three contiguous neighbors,
  keeps the outer endpoints and the selected clip's duration/source range, resizing
  its neighbors. Gaps are not implicitly closed. Forward/reverse source handles
  are adjusted to preserve the played outer endpoints; image/freeze heads do not
  acquire moving-media trims. Edited durations must retain ≥one workspace frame.
- Negative/out-of-range handles, affected overlaps, lock violations or shortened
  clips whose keyframes/fades no longer fit fail atomically. Source-handle checks
  for these edits cap the moving range at 3600 seconds; actual EOF is still a
  render-time check, not established by a successful action/save.
- Split remaps reverse/speed source intervals and requires at least one frame on
  both sides. Remove fades, keyframes, **text animation** and transition bindings
  first; no envelopes are invented. Freeze/still halves retain their original
  trim. Text split repeats literal text with split timing, not a semantic split.
- Nested descriptors accept only `move`, `duplicate`, `delete`, `ripple_delete`
  through `/actions`, always within the active timeline and subject to full graph,
  duration, overlap and lock validation. Split/trim/slip/roll/slide cannot resize
  a nest. The current UI deliberately exposes a narrower parent-edit subset.

To add/reorder tracks, merge text manually or edit parameters, save a validated
project. A locked track first requires a separate `track` action with only
`locked:false`; a save cannot unlock and edit it in one step.

Each successful **project** mutation increments revision, including undo/redo; stale revisions
return 409 with `{message:"revision conflict",current_revision:n}` in `detail`.
New edits clear redo. At most 100 mutation revisions are retained; exhaustion is
explicit, not silently truncated. State, history, and audit are committed together
with the existing atomic JSON writer. Audit contains operation, timestamp, ID,
revision/project hash for edits and job lifecycle/options for rendering; no tokens.
This is a local audit trail, **not** a signed compliance log or actor identity log.
Anonymous sessions do not identify a person. Asset imports/dedup do not
create project revisions, history entries or render jobs. Proxy creation records
job lifecycle but does not advance project revision; verified reuse creates no
new encoder, job or audit entry. There is no historical project-snapshot GET.

## Export compliance

ExportOptions defaults: `{format:"mp4",resolution:1080,fps:30,aspect:"16:9",
subtitles:"standard",video_bitrate_kbps:4000,audio_bitrate_kbps:192}`.
Optional `frame_time:null` selects the first PNG frame by default.

- Formats: mp4/mov/mkv (H.264 + AAC), avi (MPEG-4 Part 2 + MP3), gif
  (palettegen/paletteuse, first `min(duration,6)` seconds), mp3 (libmp3lame), wav
  (PCM 16-bit), png (one selected frame, not a sequence), srt/ass (actual text timings).
  PNG `frame_time` must be ≥0 and <active timeline duration; it floors to the output
  frame grid and the actual sampled time is returned in result.frame_time. Explicit
  frame_time (even 0) is rejected for every other format. GIF result.duration is
  the capped duration and gif_duration_limit is 6; PNG result.duration remains the
  source timeline duration, not a fictitious still-image playback duration.
- Video sizes: 360 →640×360, 720 →1280×720, 1080 →1920×1080. Portrait reverses
  dimensions; square uses 360/720/1080 on both axes. Aspect values exactly
  `16:9|9:16|1:1`. Timeline media defaults to contain-fit; simple exports use
  center cover-crop, including portrait and square. Burned overlays near the edges
  may be cropped with final picture; mandatory AI notice is re-burned afterward.
- FPS exactly 24/25/30/60 for video. GIF stores centisecond delays, so 24/30/60
  are quantized; this is stated in result metadata. PNG rejects any FPS other
  than the default 30, **uses that 30 FPS grid for `frame_time`**, and produces
  one RGB frame, not a timed sequence. No 120 FPS, 2K, 4K, 8K, HDR, Alpha or TGA output.
- Video bitrate is an **encoder target**, not an exact file bitrate guarantee.
  H.264 also receives maxrate/bufsize. Range 250–12000 kbps; compressed audio
  96/128/192/256/320 kbps. Audio is stereo 48 kHz. Tiny clips can have substantial
  container overhead; actual ffprobe bitrate is reported rather than fabricated.
- Audio-only/timed-text modes reject non-default video geometry/FPS/bitrate.
  GIF/PNG/text reject non-default video bitrate; uncompressed/no-audio modes reject
  non-default audio bitrate. SRT rejects large styling because SRT cannot carry it.
- Output dimensions and video FPS are measured after encoding; duration mismatch
  >max(150ms,2 frames), empty files, and size-truncated files fail publication.
  Encoded video is converted to BT.709 limited-range YUV before frame/encoder
  tagging; this is not a color-managed HDR converter. HDR/BT.2020 inputs are
  explicitly rejected. Frame selection and nested subtitle/audio exports use
  the selected expanded timeline, not root tracks substituted for a child.

Standard simple visual export **always uses final**, preserving its mixed music,
title, graphics, subtitles and finishing effects (subject to output crop). `none`
and `large` require clean picture, but take **audio from final, never narration**:
this preserves the music mix and avoids the 60.7s-final/60.63s-narration mismatch.
Large captions are rebuilt from actual current sentence timings; none suppresses
only dialogue captions. A plain non-subtitle title is safely reconstructed when
script title and timings exist, from 0 to min(final duration, pipeline title duration
constant, last timing end). No stored raw ASS is executed. This is an approximate
title style, not an exact recreation; optional burned graphics/finishing fades on
the clean path cannot be preserved, and export_semantics.warnings discloses this.
Missing required clean media/timings fails, not silent fallback to burned final.
Timeline `none`/`large` rejects visual final occurrences in the active expansion,
but permits final on audio tracks. Inactive references still undergo ordinary
authorization/asset validation; they are not decoded into that output.
SRT/ASS exports use timed text independently of clean-media availability; no OCR
or invented word timings. SRT is plain text; styling is only in ASS/video/PNG/GIF.

### Safe SRT import

`POST /subtitles/import` accepts JSON `{expected_revision,track_id,srt}`. SRT is
≤64,000 characters inside the existing 256KiB body bound, 1–64 cues, optional
numeric cue indices, CRLF/LF/BOM supported. Strict `HH:MM:SS,mmm --> HH:MM:SS,mmm`
timestamps must be ordered, nonoverlapping, positive-length and end ≤120s. Text
must pass the existing ≤500-character/control-character rules. HTML/ASS markup
is rejected, never interpreted. This adds a new track to the saved active timeline
with server-generated global clip IDs. Existing IDs, the per-timeline 8-track and
project-wide 32-track/64-clip budgets, stale revision, auth/CSRF, parent locks or
invalid content fail atomically. Normal project undo/export and SRT/ASS rendering apply.

## Sources, disclosure and QC

Catalog IDs resolve only to exact known final/video-only/narration artifacts,
registered UploadedAsset paths contained in the task, and contained normalized
video files, **plus canonical images in the verified Studio asset index**.
The fixed IDs are `final`, `video_only`, `narration`; uploaded IDs use `upload_`
plus 20 SHA-256 hex characters of the task-relative path; normalized IDs use
`norm_` plus 20 hex characters of the basename. Imported images use `image_`
plus 24 hex characters of canonical content SHA-256. LUTs and proxies are never
source IDs. Raw legacy image uploads are not automatically admitted as sanitized
Studio images. Shots/report contribute allowlisted metadata, not readable paths.

Generated raw directories, credentials, logs, manifests, another task and
arbitrary request paths are not source endpoints. Lexical escapes, symlinks and
Windows name-surrogate junctions are rejected along existing path components,
including task ancestors. Ordinary hydrated OneDrive reparse points are not
indiscriminately treated as links. FFmpeg media input forces an allowlisted
demuxer and file/pipe protocols; renamed playlists cannot request HTTP or use
concat to read other files. Verified images use a single-file PNG decoder with
no filename pattern expansion or network protocol.

Generated-media metadata is checked against the existing strict disclosure schema.
If generated footage is indicated but its manifest is absent/empty/malformed,
render/export is refused. For any task with generated content, studio visual
outputs conservatively burn `AI生成示意画面（本片含AI生成内容）` over the **entire
derived timeline**, after crops, transforms, overlays, and subtitle suppression.
This deliberately over-discloses rather than guessing exact remapped provenance.
The output manifest records `[0, result.duration]` (GIF is capped). Audio-only and standalone
subtitle export are refused for such tasks because they strip the visual notice.
Users cannot turn the notice off through project JSON.

Existing QC blockers in either quality report or report summary reject jobs even
if the old pipeline ran in warning mode, **for render/export**. Private RAW proxy
previews deliberately do not call `check_qc()`; there is no account export gate.
They still require the current finished source, valid disclosure and private
authorization. Studio does not rerun semantic QC or declare edits approved;
original QC/report bytes remain unchanged.

## Private image and LUT ingestion

The two import routes take one raw body, **not** FormData or a JSON file/path.
`expected_revision` must occur once in the query as `0` or a nonzero decimal
integer of at most 18 digits; leading zeros/signs/duplicates fail. Images require
exact `image/png` or `image/jpeg`. LUT accepts `text/plain`, optionally
`charset=utf-8` (quoted or unquoted). `Content-Encoding` must be absent/identity.
`X-Asset-Name` is rejected; names/extensions/paths are server-generated, and the
frontend does not transmit the local filename. The isolated handler accepts an
omitted length and counts streamed bytes; the current main host requires length
and applies the exact POST route's 8 MiB image / 2 MiB LUT limit. Empty content fails 422;
body receive is bounded to 60 seconds. Imports are synchronous HTTP operations,
not media jobs with fabricated progress.

### Canonical images and alpha

- Raw and canonical PNG size ≤8 MiB, each side ≤4096, total ≤8,847,360 pixels;
  PNG/JPEG only, one nonanimated image. Reject SVG/GIF/APNG, multi-picture JPEG,
  concatenated/trailing payloads, invalid geometry/checksums/structure.
- Dimensions are checked before decode. PNG ancillary metadata and JPEG APP/COM
  private data, including EXIF/ICC/compressed text, are removed before the decoder;
  only a fixed Adobe encoding transform flag may survive. PNG decompression is
  bounded by declared geometry. JPEG is limited to 8-bit grayscale/RGB.
- A bounded local probe must confirm exactly one image packet; local FFmpeg
  re-encodes metadata-free 8-bit RGBA PNG. EXIF orientation is deliberately
  ignored; no full ICC/HDR color-managed conversion is claimed. Alpha survives
  ordinary image compositing, masks, opacity, keyframes and LUT application.
- An image clip is video/overlay only and must use `trim:0,speed:1,reverse:false,
  freeze:false,mute:true`, with default volume/pan/effect/EQ. Its positive
  `duration` is the authored still hold, within the 120-second timeline, looped at
  requested export FPS. No fake image EOF/audio/25-FPS source clock is created.
- **Alpha import is not Alpha export:** visual outputs composite over the canvas;
  the PNG output branch is RGB24. No transparent video, transparent subtitle
  video, PNG sequence or TGA sequence is implemented. Transition participants
  deliberately become opaque as documented above.

### Real 3D LUT subset

- Raw `.cube` content ≤2 MiB, UTF-8 with optional BOM; blank lines and `#`
  comments permitted. One `LUT_3D_SIZE` integer 2–33, optional quoted `TITLE`
  discarded, optional `DOMAIN_MIN 0 0 0` / `DOMAIN_MAX 1 1 1`, then exactly
  size³ RGB rows. Headers occur once before rows; other directives fail.
- RGB components are finite decimal values in [0,1], in **red-fast, then green,
  then blue** cube order, not rearranged into 1D RGB curves. Numeric tokens are
  ≤64 characters, nonzero magnitudes ≥1e-999; parser line bound 512 characters,
  total ≤50,000 lines. Canonical form uses nine significant decimal digits with
  half-up rounding, LF, unit-domain headers, no title/comment metadata.
- The server publishes `lut_` plus the first 24 SHA-256 hex characters of
  canonical bytes; full digest/size are retained in the index. The clip stores
  this `lut_id`, not a source ID, path, preset label or filter string.
- `lut3d` uses tetrahedral interpolation **after** all typed color controls on
  video/overlay/adjustment. There is no 1D LUT, non-unit domain, include/path,
  HDR transform, automatic LUT generation or AI grading. `lut_id:null` disables
  the LUT; strings such as `none` are not valid LUT IDs.

### Index, revisions and resource safety

`AssetIndex` is authoritative, not directory enumeration: strict schema/version,
duplicate JSON keys rejected, index ≤256 KiB, unique IDs and **64 images+LUTs
combined**. Loading verifies canonical files,
full hashes, byte counts and dimensions/cube size. A malformed index/content
fails closed; unindexed orphans are not source assets. Bounded reads verify
regular single-link files, open-file identity and unchanged size/mtime/ctime.
Rendering validates imported references even on hidden tracks and inactive
sequences, before audio/timed-text early returns; job publication checks imported
hashes again. The immutable snapshot also watches all declared source/LUT paths,
while expanded active leaves determine actual input use.

Admission takes the shared task/global busy slot **before** receiving/decoding,
reserves temporary space, and rechecks record/root identity, both revisions,
status, disk and private write authorization after awaited phases. Dedup is no
authorization shortcut. New data becomes visible only through the final atomic
index replacement; pre-commit failures keep the prior index and remove owned
temporary data when safe. An unremovable orphan remains charged to storage,
not permission to delete a changed task/root. Cancellation drains an already
started bounded decoder and host disk-lease cleanup before releasing ownership.
No crash/power-loss or hostile local-writer atomicity guarantee is made.

Import success does not insert a clip/apply a LUT or change the project revision.
At the 64-entry cap, valid dedup can still succeed. There is no asset-delete or
raw LUT download route. JSON backups retain references only, not portable asset
packages; a different task cannot resolve these IDs without its own authorized
matching assets and sources.

## RAW source proxy jobs and cache

`POST /proxies` takes **only** `{expected_revision,source_id}`; expected revision
is a strict nonnegative integer. No options, trim, marks, URL, media upload,
replacement source or timeline effects are accepted. The full selected catalog
video must be >0 and ≤120 seconds; selecting a shorter in/out range does not
shorten proxy input. Allowed catalog moving-video containers are MP4/MOV/MKV/AVI,
with actual video stream verification (not audio-only MP4 or attached cover art).
Original uploads, normalized video, `video_only` and `final` can qualify. **RAW
means the catalog file unchanged by Studio**, not camera-RAW encoding or only
original uploads. A final source retains its already-burned content.

The fixed **version 2** profile renders one whole source from trim/start 0, speed 1,
contain-fit: MP4/H.264, **640×360, 30 FPS, SDR BT.709**, video target 1000 kbps,
AAC target 128 kbps, stereo 48 kHz. It preserves aspect with the canvas, not
center-cover cropping. A silent source gets the renderer's silent audio bed,
not synthesized speech. No current timeline grading, LUT, transitions, crop or
mix is applied. Mandatory whole-proxy AI notice is still burned when required.
Measured output must have exactly one H.264 video and one AAC audio stream, all
three BT.709 tags, expected geometry/FPS/audio specs, duration within 150 ms of
source, correct applied profile and nonempty output <128 MiB. Bitrates are
targets, not guaranteed container bitrates.

Version 2 separates the corrected 1×/RAW renderer behavior from older proxy bytes.
An additive compatibility branch recognizes only the exact pre-sequence template
at the **same profile version**, with all current binding/key/hash checks. It does
not accept version-1 audio as corrected version-2 output, rewrite saved receipts,
accept mixed templates, or turn an edited/nested project into a RAW proxy.

### Strong verification, not a trusted saved hash

- Source SHA-256 + **pipeline revision** + complete fixed profile (including
  project defaults and disclosure implementation version) + disclosure boolean
  produce the SHA-256 cache key. Matching also requires this task's `source_id`.
  Studio project revision is checked for submission but **is not** a RAW cache
  key; later timeline edits cannot change an already captured RAW snapshot.
- Successful durable proxy jobs themselves form the bounded cache: no separate
  unbounded map, retry queue or proxy source catalog. A valid cache hit returns
  the same successful job with `cached:true`, even at the 20-job cap, with no
  extra encoder/job/audit entry. It retains the original job revision.
- Source hashing streams fixed pre-statted byte counts in ≤1 MiB chunks with
  a 60-second bound, off the event loop and cancellation-drained. Checks include
  device/inode/size/mtime/ctime and no hardlink/symlink/junction substitution.
  Source is hashed again after encoding, then the actual output is hashed and
  bound to the job/snapshot/manifest/profile. A stored digest without reading
  bytes is **not** treated as verification.
- Cache reuse and **each media/Range request** revalidate immutable RAW snapshot,
  same source path/ID, current pipeline/record/root/status, metadata/disclosure
  fences, job/manifest consistency, and full source **and** output SHA-256.
  Private authorization is checked again after hashing; cache-hit response also
  rechecks after an awaited lease release. A renamed/relabelled edited output
  cannot acquire the RAW media path.
- Verification/delivery holds the same bounded per-task/global lease until the
  FileResponse finishes (including Range, errors or disconnect), not just until
  response construction. The path-send shortcut is disabled for this route.
  These guards do not make it an OS sandbox against privileged external writes.

Proxy lists/job GETs report stored state; a stale successful record can remain
visible while `/media` returns 409. No read automatically rebuilds it or changes
the project/source catalog. A corrupt previous cache candidate is skipped on an
explicit POST only if current task invariants still hold; a new job still needs
quota. Duplicate active media operations fail 409; saturated global capacity
fails 429. Missing/wrong-kind proxy IDs return 404; unfinished, stale or invalid
bindings return 409. `/outputs/{id}` rejects proxy jobs with 409 (host output
authorization may reject first); no fallback to that route is allowed.

Proxy cancellation uses the common job DELETE, draining hash threads, codec work
and reservations before cleanup. Persisted unfinished jobs become `interrupted`
on recovery reads and are not resumed. Proxies never become clip `source_id`s;
render/export always resolve their real original/image source catalog independently.
The current client creates only on explicit click, uses the real save receipt,
does not auto-select a completed proxy, reconciles uncertain POST/DELETE with
reads only, and requires explicit refresh before another submission. Playback
quality changes try to preserve source seconds, never rewrite trims or use proxy
duration as original EOF. Precise current-frame marking requires switching back
to the original. There is no automatic proxy batch or arbitrary proxy import.

## Pipeline caption and recorded-speech preferences

These are **not fields of `StudioProject`, `Clip` or `ExportOptions`**. Definitions
and execution are in [../backend/models.py](../backend/models.py),
[../backend/workbench.py](../backend/workbench.py),
[../backend/tts_pipeline.py](../backend/tts_pipeline.py),
[../backend/subtitles.py](../backend/subtitles.py),
[../backend/pipeline.py](../backend/pipeline.py) and
[../backend/rendering.py](../backend/rendering.py). Client definitions are in
[../frontend/src/types.ts](../frontend/src/types.ts) and
[../frontend/src/lib/workbenchApi.ts](../frontend/src/lib/workbenchApi.ts).

| API location | Field / semantics |
|---|---|
| POST `/api/tasks` | Multipart `preferences` JSON object; `EditingPreferences.caption_style` defaults `news`, `enhance_speech` defaults false; unknown fields/invalid JSON/options return **400**; accepted task returns **202** |
| GET `/api/tasks/{task_id}/workbench/context` | **200**, successful revision's `preferences` alongside live task status; omitted legacy caption/speech keys normalize in the client to `news`/false, malformed present values fail |
| POST `/api/tasks/{task_id}/workbench/edit` | Strict JSON `{expected_revision,keep_sentence_ids,edits?:[],pacing?,caption_style?,enhance_speech?}`; **202** `{task_id,revision,current_revision,status:"queued"}`; revision here is the **pipeline** revision |

`CaptionStyle` is exactly **`news`, `big`, `none`**. In workbench edits, omitted
or null optional preferences mean keep the current value; `enhance_speech:false`
is an explicit change, not omission. Creation requires a genuine boolean for
`enhance_speech`; strings/0/1/null are rejected. Workbench unknown fields or
invalid/no-op requests return 422; stale revision returns 409
`{code:"stale_revision",current_revision}`. Keep IDs must be a nonempty unique
subset of the current sentences, and edits must refer to retained IDs. A valid
preference-only change can use empty edits with all current IDs retained. There
is no bare preference-patch endpoint. Accepted changes use the existing queued
reservation, new revision, QC and rollback path; there is no account approval
to invalidate or restore. See
[WORKBENCH_API.md](WORKBENCH_API.md) for the complete batch preference and
recording contract.

### Caption presentation without replacing subtitle evidence

`subtitle_burn_in_artifact()` validates canonical ASS and its existing manifest
in **all three modes**. `news` yields the unchanged original. `big` multiplies
the two body styles (`NewsDialogue2024`, `NewsSentence2024`) by **1.5**, preserving
timing and reflowing oversized lines within the safe region; if the text still
cannot fit safely it fails, rather than silently clipping glyphs/shrinking type.
`none` omits only those body dialogue events. Title, separate graphics and
required generated-media disclosure remain; a source-burned overlay is not erased.

Big/none use a unique temporary ASS linked by the canonical SHA-256, not a new
canonical template or QC baseline. Original ASS/manifest, word cues and ASR
evidence are preserved, and temporary presentation files are removed when the
render context exits, including failure/cancellation. A caption-only workbench
edit does not rebuild/re-denoise narration; it reuses existing media segments
and the finishing pipeline. Studio export `subtitles` instead uses
**`standard`, `large`, `none`**: standard keeps the already-finished picture,
including a task originally made with big/none; restyling follows the clean-picture
export contract, not a reset of `caption_style`. `text_template:"news"` is yet
another independent per-clip style, not the canonical pipeline caption template.

### Exact current `enhance_speech` DSP

Only `SentenceTiming.audio_kind == "sync"` units are eligible: source sync sound,
aligned whole-document own-voice units and per-sentence recordings.
Current TTS wins over stale recording provenance and is **never denoised**.
The effect is local deterministic DSP, not AI repair, voice separation, cloning,
speaker identification or proof of transcription. It adds no new provider call.
Existing full workflows may still call configured ASR/TTS/matching or automatic
music-selection providers; local caption/DSP support is not a promise that every
workbench edit or task is free of those existing calls.

After the **same 48,000 Hz stereo format conversion** used for the unprocessed
reference, the fixed chain is below. Steps 2–5 come from the shared
[../backend/audio_filters.py](../backend/audio_filters.py); Studio `denoise` uses
those same steps **without** step 1 or the pipeline's recording-only scope.

1. `highpass=f=70:p=2`.
2. `adelay=1200S:all=1`: exactly 1,200 samples of silent pre-roll to warm the
   denoiser's overlap-add state.
3. `apad=pad_len=1200`: exactly 1,200 EOF flush samples for its algorithmic delay.
4. `afftdn=nr=12:nf=-50:nt=w:rf=-38:tn=0:tr=0:om=o:ad=0.5:gs=0`.
5. `atrim=start_sample=2400,asetpts=PTS-STARTPTS`: remove the **2,400 synthetic
   startup samples** (pre-roll + delay), not authored speech.

At 48 kHz, each 1,200-sample component is 25 ms. Fixed floor tracking off (`tn=0`)
does not disable adaptive per-bin gains (`ad=0.5`); `om=o` is denoised output, not
noise-only. There is **no end trim, target-duration padding, silence removal or
repair of a measured size mismatch**. The derivative is checked against the
same-format decoded reference: actual PCM frame bytes, stereo/48 kHz/16-bit
specification, nonempty content, and **absolute sample-count difference ≤1**.
Source SHA-256 must remain unchanged. A violation rejects use rather than
adjusting the source or inventing word timing. Phase/amplitude can change;
sample-count equality alone is not proof of phonetic or onset/EOF preservation.

Processing happens once per eligible original unit at assembly into temporary
PCM, not in reusable sentence files, `SentenceTiming.audio_path`, TTS or the final
mixed track. Rebuild/remix reads the unprocessed units again; visual-only
replacement reuses assembled narration. Existing edge fades and TTS two-pass
loudness processing remain separate. Disabling the flag rebuilds from originals
and removes the current speech-enhancement profile receipt.

The narration profile records `speech_enhancement` with schema 1,
`algorithm:"local_highpass_afftdn_clock_compensated_v2"`, requested/applied,
scope, filter, `algorithmic_delay_samples:1200`, `preroll_samples:1200`,
`tts_filtered:false`, filtered-unit count, `sample_tolerance:1`, and per-unit
source/processed hashes, decoded/processed sample counts and source-preserved
receipt. True with no eligible units records requested=true/applied=false.
Workbench timing metadata derives optional `speech_enhancement_applied` from
the current profile; it is not `transcript_verified`. Single-sentence uploads
remain unverified-transcript recordings even when processed.

## Resource and persistence boundaries

- 120-second active timeline and 1080 maximum resolution are conservative service
  policy limits, not claims about FFmpeg hardware capabilities. No dynamic 2K/4K
  admission guard is implemented; those modes and 8K explicitly reject.
- Main plus up to four named sequences: 8 tracks/100 markers per timeline;
  32 stored tracks/64 stored clips project-wide, including nested descriptors.
  All graphs have at most two reference edges. Active expansion is separately
  bounded to 8 nonempty layers/64 leaf occurrences/16 decoder occurrences;
  references do not create independent job, storage or history allowances.
- 16 decoded media inputs per render; ≤32 full-HD
  equivalent source frames across decoders; 20,000-character filter graph.
- Source dimensions ≤4096 per axis and ≤4096×2160 pixels, source duration ≤3600s,
  source frame rate ≤120. Reverse decoded buffer estimate ≤128 MiB.
- **Two shared media-operation slots globally, one per task**, including image/LUT
  imports, render/export, proxy preparation/hash work and proxy media delivery.
  Jobs are not queued beyond that admission. Two codec threads per render
  input/output and one filter-complex thread; image ingestion uses one codec
  thread. This is not an OS CPU quota.
  Production should also apply existing service/container CPU/memory limits.
- Whole render job timeout min(300s, existing media command timeout), 30s probes.
  Proxy JSON receive ≤30s, hashes ≤60s, image/LUT body receive ≤60s, image
  decode/probe commands ≤30s each. A bounded worker may finish draining after
  caller cancellation before its resources can safely be released.
- Per-output <128 MiB; predicted bitrate budget rejects oversized work; FFmpeg
  `-fs` plus final size/duration verification prevents reporting truncated success.
- Per-task **512 MiB for all Studio storage**, including assets, staging, jobs,
  history, outputs/fonts/logs; not independent asset/proxy quotas. Maximum 20
  durable render/export/proxy jobs combined, including failures/cancellations.
  Imports do not consume this job count. Asset/index publication and atomic state
  replacement budget for temporary coexistence with prior files.
- Current render/export admission needs used storage +128 MiB output +32 MiB
  state +512 KiB metadata within 512 MiB; free-disk reservation is 256 MiB plus
  configured minimum and other active Studio reservations. Within Studio's own
  accounting, proxy creation reserves 289 MiB of free space and checks used
  +161 MiB before a new job;
  proxy media verification reserves 256 KiB. Image/LUT work reserves 84 MiB /
  4.5 MiB respectively, covering raw/sanitized/encoded staging.
- Imports/proxies also use the host's existing upload-capacity disk guard when
  supplied, so host admission can require additional headroom beyond those
  internal budgets, with cancellation-drained entry/release and continued checks.
  Render/export retains its own Studio checks rather than claiming that same
  cross-service lease. Storage-tree accounting rejects more than 10,000 entries.
  None is a distributed disk lock or a stress-test result.
- Request JSON ≤256 KiB while streaming; persisted state ≤32 MiB.
- Persistence lives beneath task-owned `studio`: one atomic state/history/audit,
  job records, and immutable output directories keyed by revision and job ID.
  Each successful job keeps project/options/source-stat snapshot, measured output
  manifest and logs. No output replaces the pipeline final/report.
- Cancellation awaits the existing media runner's process termination before
  cleanup. Failed/cancelled/interrupted work receives a removal attempt; failure
  leaves quota-counted residue and durable terminal `cleanup_pending` metadata,
  not a running/successful job. Job status/audit remains. An authorized job GET
  may retry cleanup once, never encoding. No automatic encoding retry, cloud
  call, dependency installation, or simulated successful tool exists.

## Prototype capability inventory — not 113 supported tools

The inspected prototype/catalog contains **113** entries, each with `{id,group,label,
available,classification,reason}`. `available:true` with `partial` means only the
explicit reason's subset, never the whole prototype promise. `metadata` controls
are persisted without pretending to change pixels. Machine-readable project and
export schemas describe every accepted renderer option. The current per-ID
requirements and missing subfeatures are linked in
[PROTOTYPE_CAPABILITY_MATRIX_20260925.md](PROTOTYPE_CAPABILITY_MATRIX_20260925.md).
That matrix is an implementation snapshot, not a test tally; `tlPick`, `nest`,
`camNest` and `proxy` now have explicitly bounded **partial** implementations.
The matrix's recorded category counts are historical, not proof that this
no-login version has passed regression or implemented all 113 promises. Runtime
`/capabilities` remains the authority for availability; scope removal does not
promote an unsupported renderer feature.

The complete key inventory by group is below. Classification/support detail is
the `SUPPORTED` registry and `/capabilities`; all keys absent from that registry
are explicitly `available:false,classification:"unsupported"` with a reason.

| Group | Every prototype key |
|---|---|
| timeline (11) | addTrack, trkGroup, trkColor, proxy, tc, marker, inout, snap, ripple, tlPick, nest |
| clip (14) | edit, undo, trimMode, freeze, reverse, mirror, rotate, frameCrop, speed, interp, kf, kfCurve, kfParam, adjLayer |
| cam (5) | camN, camAlign, camWave, camBatch, camNest |
| audio (11) | mixVol, mixPan, mixMute, fxAudio, recMulti, recFx, voiceLib, voiceClone, ttsBatch, wave, aiAudio |
| ai (11) | aiCut, aiNarr, aiKey, aiErase, aiFix, aiLip, aiTrans, aiColor, aiFrame, aiSub, aiTransition |
| fx (13) | trLib, trDur, trCustom, beauty, body, faceMode, faceSticker, basicColor, colorVal, proColor, zoneColor, lut, colorCopy |
| text (15) | fancy, textFx, textAlpha, textAnim, perChar, textMask, subTpl, subBatch, subOps, subShift, subIO, subLang, stickerLib, stickerCustom, stickerAnim |
| mask (12) | pip, pipLayer, blend, maskType, maskFeather, maskInv, maskTrack, bezier, chroma, chromaPro, brushKey, track |
| export (10) | expRes, expFps, expBr, expHdr, expCs, expFmt2, expBatch, expOpt, sharePlat, shareHd |
| tools (11) | libAuto, libRate, importPro, hotkey, stab, wm, splitMerge, draftOps, extract, quick, layout |

Typed keyframes/masks, manual SDR controls/RGB curves, real imported 3D LUT,
canonical PNG/JPEG stills with alpha, bounded text templates/animations, five
explicit transitions and fixed RAW proxies now have actual code paths. Manual
2–4-camera grid/switch composition is client compilation into ordinary tracks,
not a new backend camera API: explicit source sync-in, ≤12 cuts, muted camera
clips and one continuous master audio clip; no waveform/timecode auto-sync,
9/16-camera mode or automatically nested camera recipe. Ordinary camera tracks
can reside in a named sequence and be identity-nested with real second-level
editing. The current camera builder itself is disabled in children/nested main.

Cloning, rotoscoping, tracking/beauty, AI transition/color generation, arbitrary
LUT formats, automatic/imported proxies, cloud/team sync, >1080p/HDR/Alpha output
and optical flow are not thereby implemented. Standalone cloud-tool navigation
and routes have been removed; old cloud documentation is historical, not an
activation guide. Imported stills are also distinct from the create
wizard's [MEDIA_INPUT_API.md](MEDIA_INPUT_API.md) photo preparation pipeline.

## Frontend and client contract

- `StudioProps` requires `taskId`, `request:StudioRequest`, `onBack`, `onError`;
  optional `accessToken`. The task/token-keyed editor separates selected works;
  token absence is not permission unless the server establishes trusted local
  authority. No classroom prop, cloud actor receipt or account gate is required.
- The host request client owns anonymous session/CSRF handling. Known routes construct
  same-origin source/image/proxy/**output** URLs; catalog `url`/result `file` are never followed as
  arbitrary credential-bearing URLs. Use same-origin requests with no-store and
  cancellation fencing; task-token media URLs must not be logged or shared.
  The removed `sessionOnly` branch is not an alternate account authorization
  path; token-free access still depends on the server's strict local authority.
- There is no account `can_export` polling/approval request. The saved project
  receipt supplies `expected_revision`; server capability, current task/source,
  QC and job checks remain decisive for render/export. Unknown submission
  outcomes are reconciled with reads, never automatic mutation replay. RAW proxy
  reuse/delivery retains its independent current-source binding.
- Core load is capabilities/project/sources. `/assets` is fetched only if image
  or LUT availability is explicitly declared; `/proxies` only for available
  proxy capability. Auxiliary read failure does not invent data, success or a
  media job. Assets/proxies share the editor operation mutex/save-receipt flow;
  unknown LUT bindings remain visible until explicitly cleared/rebound.
- Preserve **all** typed fields and metadata on round trips. Only omitted
  additive fields get defaults; unknown project keys/invalid known values fail.
  A proxy job receipt is not a project/source receipt and cannot replace source
  IDs, EOF facts or export options. Output/source/proxy modes are visibly distinct;
  there is no live timeline composite in the source player.
- PNG `frame_time` applies only to PNG; GIF is capped at six seconds. Display
  clean/restyled warnings, overlapping-audio warnings and unreviewed semantics.
  SRT import is task-authorized/revisioned; raw ASS/SSA import remains unsupported.
- Registry reasons for `edit`, `marker`, `frameCrop` and `expCs` now describe the
  bounded clipboard/navigation/frame UI and actual matrix/range conversion.
  This does not promote a partial feature to full composite-tool support;
  sequence cloning still has no dedicated UI.

## Current validation boundary — local pass, bounded coverage

The [final backend summary](../canary_test/artifacts/workspace-validation-20260927-131111-860100/summary.json)
records **766 tests: 764 passed, 2 skipped, 0 failures/errors, 435.199335s**.
Current mounted and focused regressions cover capability/strict-local authority,
anonymous production CSRF/Origin, legacy `local_only`, post-body/post-probe
revision/source/root checks, copy/busy/drain and real QC gates. The retained
media/asset/sequence/proxy contracts include historical derivatives versus
current-source-bound proxies, Range delivery, cancellation, failed persistence,
quota-counted cleanup residue and recovery without an encoding/paid restart.
These tests are in the full total, not additional suites or a claim to have
exercised every Studio option in a browser. Opt-in historical audio replay and
one Windows symlink-privilege check remain skipped.

Frontend **286/286** contracts passed, including compositions **25**, assets
**40**, proxy **70**, sequences **51**, timeline **17**, preferences **12** and
app API **71**. Application/E2E types and the **1788-module, four-asset** build
passed; the final browser used the same current
[asset manifest](../frontend/dist/ASSET_MANIFEST.sha256). The earlier transient
proxy-script load failure is retained separately, not hidden or converted into
a pass by weaker assertions.

The [final browser run](../canary_test/artifacts/workspace-core-final-20260927-212523/summary.json)
passed **14/14, zero retries/global errors, 69.495374s** after final same-origin
media URL cleanup. WS-04 saved a real two-second timeline, trimmed it to
**source in 0.2s / output 1.5s**, and rendered the saved revision at **640×360**.
Native decoding reached ended with **45 frames**; the real UI download matched
the protected output's **828293 bytes** and SHA-256
`d0b3652fd503ddc3df74a0719c80e3ddbb101dba98b65e89ea2697415b93bb6a`.
See [the retained media evidence](../canary_test/artifacts/workspace-core-final-20260927-212523/test-results/acceptance.workspace-WS-04-23f6f-d-downloads-validated-media-workspace-msedge/studio-media.json).
QC was required and unpatched; the derivative was not represented as pipeline
QC approval. All eight responsive combinations include Studio, within
**56 total page-state samples with no overflow**.

**Not performed:** all-format/all-tool browser acceptance, live-provider/model
quality, physical microphone/hardware, full WCAG, Linux/Nginx/systemd/public
production or target-host/load/fault testing. The browser host used real FFmpeg
patterns and exact-loopback fake providers/tone TTS, with ASR, own voice and
direct video embeddings disabled. No new paid calls occurred. Opening an
existing daily-service task proved read-only access/editor availability, not a
user-media rerender or all-media hash audit.

Fresh TEMP, no-dotenv settings and provider confinement remain mandatory for
future tests; never invoke test startup cleanup on user data or historical
billing evidence. This documentation closeout only reads prior results and
checks links. Full evidence and current-host shutdown limits are in
[CORE_WORKSPACE_VALIDATION_20260927.md](CORE_WORKSPACE_VALIDATION_20260927.md).

Historical [C26](../canary_test/CANARY_C26_20260926.md),
[C25](../canary_test/CANARY_20260926.md) and
[implementation records](PROTOTYPE_IMPLEMENTATION_20260925.md) retain their
original results and failures. The earlier native Python crash high risk and
deep-host orderly-shutdown evidence gap remain unresolved; removing login does
not fix either or establish crash/power-loss atomicity.