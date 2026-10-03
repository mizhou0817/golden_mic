# CreateWizard media input integration

> **Current no-login contract, 2026-09-27; local integration passed and deployed on daily port 8000.**
> Backend: 764 passed / 2 skipped of 766; frontend: 286/286; browser: 14/14,
> zero retries. Exact results and live/hardware exclusions are in
> [CORE_WORKSPACE_VALIDATION_20260927.md](CORE_WORKSPACE_VALIDATION_20260927.md).
> Creation is a core operation, not a classroom submission. Production requires
> signed anonymous session + `X-CSRF-Token` + Origin; every accepted creation
> returns a nonempty task capability. Local history/implicit task access uses the
> strict direct-loopback rules in [CORE_WORKSPACE_20260927.md](CORE_WORKSPACE_20260927.md).
> No account, teacher approval, class context or held dispatch is required.

## Current creation integration

`POST /api/tasks` accepts multipart `script` and `files`, optional `preferences`
and `asset_options` JSON, optional `own_voice`, and `script_format` (`auto` by
default). Success is **202 `{task_id,access_token}` with a nonempty token** in all
environments. The removed `classroom` field is rejected with 422; it is not a
compatibility path to account-backed creation. Save the receipt securely before
navigation; storage/transport failure must not automatically repeat a paid POST.

The wizard also submits multipart `script_format=headline_first`. This explicit
mode validates the actual first line as a nonempty 1–40-code-point headline,
rejects terminal `。！？.!?；;`, requires body text, and normalizes CRLF/CR with
one blank separator after the headline. Internal body content is unchanged;
the existing matching and segmentation parsers then exclude the headline from
narration even when the first body line is short. Raw/prepared length and control
characters are checked before saving uploads. Invalid content returns 400;
unknown format returns 422. The default `auto` preserves legacy parsing rather
than imposing the wizard title contract on existing API clients. See
[headline tests](../tests/test_headline_contract.py) and the isolated application
scenarios in [tests/test_workspace_access.py](../tests/test_workspace_access.py).

The async helper is `prepare_media_inputs(task_dir: Path, uploads: list[UploadedAsset], asset_options_json: str | None, own_voice: UploadFile | None, settings: Settings) -> list[UploadedAsset]` in [backend/media_input.py](../backend/media_input.py).

In [backend/main.py](../backend/main.py), `POST /api/tasks` calls it **after `save_uploads` and before `task_manager.add_task`**, replacing `uploads` with its result when `asset_options`, `own_voice`, or an image is present. Plain legacy video-only requests without these fields keep the old path. The route accepts optional multipart `asset_options` JSON and `own_voice` file; [frontend/src/lib/appApi.ts](../frontend/src/lib/appApi.ts) maps the wizard's `ownVoice` to `own_voice` and submits options in file order. Preprocessing remains inside upload/disk reservation and failure/cancellation cleanup. The aggregate multipart length includes voice; the helper also validates actual visual-plus-voice bytes. Preparation failures remove the unqueued directory, and uploaded handles are closed. Accepted tasks enter normal manager admission/queueing only after preparation; there is no teacher release queue.

Preparation makes **no cloud calls**; the later pipeline still uses configured
providers and can incur cost. The no-login mounted route passed current isolated
backend and synthetic-browser integration; this does not validate live ASR/voice.
Preparation validation errors return HTTP 422; upload byte-cap errors
return 413. Missing media tools/invalid media fail before queuing on this path.
Existing `save_uploads` count/byte limits, Origin/session admission, request
frequency, disk reservations, pending capacity and drain remain active.

## Asset options

An array in the exact upload order, one object per asset. The only fields are:

| Field | Default | Validation |
| --- | --- | --- |
| `note` | empty string | String, max 20 Unicode characters, no control characters |
| `trim_start` | 0 | JSON number, finite, nonnegative; booleans/strings rejected |
| `trim_end` | null | Null means original end; otherwise finite numeric end greater than start |

Unknown fields, wrong array length, invalid JSON, values outside source duration, NaN and infinities are rejected. Metadata cannot supply paths, FFmpeg filters or commands. All source dimensions, FPS, original byte counts, per-source and aggregate durations are checked **before trimming**, and originals are checked again at stage 1. A shorter container duration cannot hide a longer declared video-stream duration.

Supported visual extensions: `.mp4`, `.mov`, `.avi`, `.mkv`, `.jpg`, `.jpeg`, `.png`, `.gif`. Images are bounded to 50 MiB (or the lower configured upload cap), 20 million pixels, and configured source width/height. Headers are checked before native decode; GIF frame rectangles, block structure, frame count (300) and aggregate canvas pixels (100 million) are bounded. JPG/PNG/GIF are decoded with the existing OpenCV dependency into a sanitized PNG before FFmpeg. GIF uses its **first frame**, not animation. A fixed filter generates exactly 3 seconds of 1920×1080/30fps H.264/yuv420p, without audio. Photo trimming is not supported.

Video trims retain the selected **original audio content** (AAC transcoded, not byte-identical) for stage-2 ASR, on the same zero-based timeline as the trimmed video. Video geometry/FPS are standardized; raw originals remain byte-identical. Inputs use forced demuxers and `file,pipe` protocol restrictions; no playlists/network protocols or user-controlled filter expressions are allowed.

## Persistence contract

`UploadedAsset.original_name`, `stored_name`, `path`, `size`, and `content_type` still describe the **immutable raw upload**. Display names are preserved exactly, never used as filesystem paths. `stored_name` is a random basename; `path` remains `task_dir / "raw" / stored_name`. Do not persist the ephemeral objects returned by `processing_uploads`.

Defaulted fields that [backend/task_manager.py](../backend/task_manager.py) now serializes **and restores**:

- `note` (default `""`)
- `trim_start`, `trim_end` (default null for legacy tasks)
- `prepared_stored_name` (default null; randomly named derived MP4 in the same raw directory)
- `source_duration_seconds` (default null)

Task state uses model serialization (excluding the path) and restores the optional fields explicitly, without changing the original five fields. Missing legacy metadata stays at defaults. The helper atomically writes a private media-input manifest, with original/derived names, options, original source durations, and optional voice metadata. It excludes absolute paths and credentials. This is an audit artifact, not a user-supplied path source or a public upload-manifest API.

`GET /api/config/limits` in main now sets:

- `max_video_duration_seconds = settings.max_source_duration_seconds_per_file`
- `max_total_video_duration_seconds = settings.max_total_source_duration_seconds`
- `allowed_extensions = sorted(storage.ALLOWED_EXTENSIONS)`

`GET /api/config/workspace` separately returns `{local_history,
generative_fill_available}`. These are booleans, not login roles or promises of
Provider success. `generative_fill` defaults false; a configured operator switch
does not itself opt a task into generation. Task state persists only capability
hashes, and local history never returns them. Old classroom task-state markers
remain `local_only`; media preparation does not read or migrate a classroom DB.

## Pipeline hooks

- Stage 1 validates original sources/caps and verifies derived videos.
- Stage 2 passes ephemeral prepared paths to **both** normalization and source ASR. Task persistence continues using original upload descriptors.
- Immediately after stage 4, notes are appended to `AnnotatedShot.search_text` as explicitly **unverified user retrieval context**, then annotations are saved. Vision `description`, entities, OCR, quality and verified evidence are not overwritten. Existing matching retrieval consumes this search text before stage 6.
- Stage 6 skips source-sync substitution when `own_voice.wav` exists, preserving independently chosen video shots and avoiding source-audio offsets.
- Stage 7 detects prepared own-voice audio, uses the supplied narration instead of TTS/pronunciation calls, and defensively clears stale `sync_sound` selections without changing shot IDs/beat choices. Own-voice tasks validate ASR configuration even when source sync sound is disabled, and do not require TTS configuration for that narration branch. Other pipeline models remain in use.

## Whole-document own-voice narration

Accepted extensions: `.webm`, `.ogg`, `.wav`, `.mp3`, `.m4a`. Maximum 50 MiB or lower configured per-upload cap; voice plus visual uploads must fit the aggregate upload cap. Require exactly one mono/stereo audio stream, no non-cover-art video, finite positive duration, and a maximum of `min(1800, settings.max_source_duration_seconds_per_file)` seconds. Audio lacking a duration (e.g. browser WebM) is decoded with a bounded duration and checked against measured PCM length; an overlong recording is rejected rather than silently truncated.

The full original recording is stored under a random raw basename. Local standardization produces `own_voice.wav` (16 kHz mono PCM) before queuing. ASR only runs later in stage 7 through the existing Seed provider. The configured provider's normal billable requests are used during actual task execution; tests mock them.

Alignment is deliberately fail-closed:

1. Compare the entire body and ASR transcript after the existing NFKC/alphanumeric normalization plus casefold; require **100% normalized coverage**, in order, without omitted or repeated material. Titles are not narrated. Numeric readings that do not normalize identically fail with a re-record/AI-voice instruction.
2. Require definite, ordered, non-overlapping ASR intervals inside actual recording duration.
3. Sentence boundaries must coincide with complete ASR utterance boundaries and at least 120 ms of observed separation. If a screen unit cuts through one ASR utterance, **reject it**; do not fabricate character/word timing or proportional alignment.
4. Use observed inter-utterance midpoints to split audio, retaining the whole recording (including leading/trailing audio). Existing narration rebuilding adds the established 120 ms sentence gap; thus assembled length can exceed recording length by these added gaps.

The current Seed adapter exposes **utterance** timestamps, not individual word timestamps. `SentenceTiming.words` contains those real, unsplit ASR utterance spans with unit-relative times. The narration sidecar explicitly reports `timestamp_granularity: "asr_utterance"`; it does not claim word-level accuracy. Supporting screen boundaries within an utterance would require an upstream word-timing/forced-alignment extension that is not implemented.

Every sentence has `audio_kind="sync"`, `tts_group_id=None`, real timing spans,
an audio-content fingerprint and reusable task-local audio. Historical internal
`student-` fingerprints/media naming are compatibility labels only, **not student
accounts or verified speaker identity**. Existing narration assembly creates
timings/narration/profile outputs. The sidecar stores alignment policy/coverage,
source ranges, transcript and audio profile, not an account identity or credential.
A failed alignment occurs before timing/narration/final replacement; it never
silently falls back to a synthetic voice. Existing per-sentence workbench
recording behavior remains distinct and its transcript is unverified.

## Validation — local pass, live speech excluded

[tests/test_media_input.py](../tests/test_media_input.py) covers strict options, preserved names/raw bytes, path escapes, bomb headers, original limits before trim, safe first-frame photo conversion, audio-preserving trim and stage-2 path integration, actual Seed-ASR-shaped timestamps, ambiguous/mismatched narration rejection, no TTS or sync override, channel/size/duration restrictions, durationless decode, and untouched final/timing artifacts on failed alignment. Tests use synthetic local media and mocked providers; they do not import `backend.main`. Regression runs that include existing API tests must set `DATA_DIR` to a fresh temporary directory **before importing** the application.

These tests and the current mounted scenarios in
[tests/test_workspace_access.py](../tests/test_workspace_access.py) passed within
the [final backend run](../canary_test/artifacts/workspace-validation-20260927-131111-860100/summary.json):
**766 tests, 764 passed / 2 skipped, no failures/errors, 435.199335s**. Coverage
includes nonempty creation capabilities, rejected classroom fields, production
Origin/session/CSRF, local authority, rate limits, failed-upload cleanup and no
paid starts on restart. Opt-in historical audio replay and unavailable Windows
symlink privilege are explicitly skipped, not counted as passes. The earlier
optional-token assertion was updated to the required-token contract, not a
loosening of the product response schema.

The [final browser run](../canary_test/artifacts/workspace-core-final-20260927-212523/summary.json)
passed **14/14, retry 0, global errors 0, 69.495374s**. WS-02 selected and uploaded
four actual FFmpeg-generated pattern MP4s through the wizard and executed all
ten real stages with exact-loopback fake model responses/tone TTS. It observed
intermediate progress, a nonempty token, persisted history, real QC and native
decoding of a **3.8s, 1920×1080, 114-frame** result. WS-06 verified metadata-only
draft restoration and a held cold deep-link without unsolicited writes; the
eight width/font combinations produced 56 layout samples without overflow.
Frontend **286/286** contracts, application/E2E typechecks and build passed.

**ASR, whole-document own voice and direct video embeddings were not enabled in
that synthetic browser host.** Their unit/adapter/alignment coverage above does
not constitute live Seed ASR, real speech/voice quality or physical-microphone
acceptance. Tone TTS is not speech; no new live-provider or paid calls occurred.
Linux/public-production, hardware and full WCAG remain unperformed. The earlier
invalid synthetic stored basename was corrected to UUID form before successful
hosts; its failed TEMP was retained, without relaxing product path checks.

The [current validation report](CORE_WORKSPACE_VALIDATION_20260927.md) separates
these results from retained failures and prior
[refactor acceptance](PROTOTYPE_REFACTOR_20260923.md). This documentation-only
closeout reruns no tests/builds/providers and changes no media or machine evidence.

Photo preparation here is separate from [STUDIO_API.md](STUDIO_API.md): Studio
can use authorized prepared/normalized videos and has its own stricter raw
PNG/JPEG import route with canonicalization. Old raw image uploads do not
automatically become Studio still assets; GIF first-frame preparation is not
animated-image timeline support.