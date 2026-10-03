# V2 final current-phase closure — 2026-09-30

## CURRENT OVERRIDE — V/W; this document's S/T body is historical

**Use [V2 current closure](V2_CURRENT_CLOSURE_20260930.md) and [sanitized machine report](../canary_test/artifacts/v2-closure-vw-20260930/final-report.json): V core 6/6 (323925.054 ms), W independent design 4/4 (34877.722 ms), zero retries/global errors.** F01–F08 implemented; release anchors include frontend-static and Windows-loop modules, and both templates cover **137/137** fields. Old “current S/T”, 589/902/40, missing-anchor/template and unfinished static-review claims below are dated history, not current blockers/results.

Reused frozen results: **629/629 V2 + selected legacy**, **1191/1191 frontend**, **43/43 root**, four typechecks exit 0; **17/17 native recording separate**, no physical microphone/ASR. U zero-test configuration failure and narrow 21-case repair remain disclosed. V first shutdown missing Origin returned **404/no mutation**, corrected identity-bound request **200**; W correct shutdown **200**. All stopped flags verified; exact exit-0/PID absence/8787 facts are parent-reported, not newly probed.

V/W use closure-U **48-input/four-asset Lightning build**, not design-L. All maps matched before these docs edits; bound browser README subsequently drifts, no re-signing. Windows correction is acceptance-only/CPython-pinned, not all crash/TLS repair. Models/quality/physical mic, sample 503, normal Vite/OneDrive, D06 explicit decisions, full historical backend safe runner and production gates remain open. **Everything below is preserved S/T history, including historical risk wording and exact original evidence links.**

**LATEST: bounded synthetic local core S 6/6 and design T 4/4 passed; all O–T acceptance hosts stopped. This supersedes J for current status, not historical evidence. Not all M0, full design, speech quality or production acceptance.**

This is a **docs-only** closure. It reads retained sanitized evidence and source, checks document links and evidence hashes, and makes no product/harness/test/dependency changes. No tests, builds, services, media probes, provider calls, installs, model downloads or deployment are run. Daily port 8000, real data and actual environment files are not accessed. The dates in evidence names are retained verbatim; UTC receipt timestamps are not rewritten to match the local closure date.

Navigation: [implementation record](V2_IMPLEMENTATION_20260929.md), [operating runbook](RUNBOOK.md), [design map](DESIGN-MAP.md), [browser procedures](../frontend/e2e/v2/README.md).

## 1. Authoritative evidence and scope

Primary evidence: [S/T final report](../canary_test/artifacts/v2-acceptance-st-final-20260930-8be4a792/final-report.json), [verification receipt](../canary_test/artifacts/v2-acceptance-st-final-20260930-8be4a792/verification-receipt.json), [evidence hash index](../canary_test/artifacts/v2-acceptance-st-final-20260930-8be4a792/evidence-hashes.json). The receipt records 22 validated JSON artifacts, 21 indexed artifact hashes, 52 unchanged original JSON reads, seven rehashed downloads and 45 recorded Range chunks. Those export probes/rechecks were **already completed**, not rerun by this documentation task. The 21 indexed hashes and receipt's report/index hashes were independently checked again before these edits.

| Run / scope | Retained result | What it establishes |
|---|---|---|
| S, `v2-browser-20260930-s` | **6/6, 322.094054s, retries 0** | Current-bound base six; 36 layouts at 20px, zero overflow; three successful initial generations, three apply actions, seven successful exports |
| T, `v2-design-20260930-t` | **4/4, 35.454555s, retries 0** | Independent design suite; two native modal samples at 320/900px, zero overflow; one short-A generation, two metadata PATCHes, one duplicate and one recover-draft |
| Backend, `v2_plus_selected_legacy` | **589/589, 371.585206s** | V2 discovery plus the runner's explicit selected legacy contracts; **not the full historical backend suite** |
| Frontend package glob | **902/902, 50.6442858s TAP duration** | Full selected frontend script glob, including opt-in native dialog check; process wall time is separately 50.7612942s |
| Root result entry | **40/40, 1.0183011s TAP duration** | Separate root script, outside frontend glob; do not count it twice |
| No-emit types | **Four exit-0 checks** | Frontend, workspace E2E, modes E2E and V2 E2E; not a workspace-wide Python typing claim |

Backend source: [589-case summary](../canary_test/artifacts/v2-validation-20260929-171045-6304bb8b1aec49a780d4b6b365f0dfe5/summary.json). Combined attribution: [frozen-auth final summary](../canary_test/artifacts/frozen-auth-validation-20260930-010918-b5f499ffa67f4ea38b58275f6d716911/final-summary.json), with [sanitized retained projection](../canary_test/artifacts/v2-acceptance-st-final-20260930-8be4a792/existing-validation.json). Backend has zero failures/errors/skips/xfails/discovery errors, zero runtime guard denials or remaining owned HTTP listeners, 978 native media processes and 122 registered TEMP admission databases. Guard self-check denials are intentional negative checks, not runtime leaks. Its status remains `backend_passed_not_acceptance`; the S/T report does not rewrite it.

The combined summary also **preserves a failed focused adaptation: 33/35, two failures**. The unchanged workspace case with its original setup separately passed 1/1. Do not hide that failed diagnostic, call the combined file universally green, or reinterpret it as the 589-case result. Browser, backend, Node and typecheck totals are separately attributed, not one giant suite.

### S media, gates and actual request counts

[S summary](../canary_test/artifacts/v2-acceptance-st-final-20260930-8be4a792/S/browser-summary.json) and [seven exports](../canary_test/artifacts/v2-acceptance-st-final-20260930-8be4a792/S/seven-exports.json) retain all seven exact SHA-256 values, byte sizes and probes:

| Export | Revision | Bytes | Probe / validation |
|---|---:|---:|---|
| C MP4 | 1 | 31,586,166 | 59.566667s, 1920×1080, 30fps, H.264/AAC 48kHz |
| A MP4 | 2 | 2,353,149 | 4.366667s, 1920×1080, 30fps, H.264/AAC 48kHz |
| B MP4 | 1 | 4,666,526 | 8.3s, 1920×1080, 30fps, H.264/AAC 48kHz |
| B GIF | 1 | 2,393,041 | 6s, 480×270, 12fps |
| B MP3 | 1 | 199,916 | 8.268396s, MP3 48kHz |
| B PNG | 1 | 343,967 | 1920×1080 PNG; still image, not a playback-FPS claim |
| B SRT | 1 | 203 | Validated cue header; FFprobe not applicable |

C's MP4 SHA-256 is `9e9946f60ca6793af336b7edc09ce2095f8ddc03e1c2e8781c865ddba222ba9b`; do not substitute J's different output hash/size. C runs the eight-quote/seven-input chain once; A publishes r2 after two internal apply steps; B supplies all five formats. Independent immutable-r0 inventories **C62/A37/B37** remain unchanged. These are separate task baselines, not one combined inventory. Downloads use validated UI links and authenticated bounded Range chunks, **not native browser download-click coverage**.

Observed S requests include **four start POSTs = three successful generations + one expected 422**, and **ten export POSTs = seven successful submissions + three expected gate 409s**; five drafts and three apply POSTs. Seven logical download GET observations are not 45 wire-level Range requests. Both S/T record zero blocked requests, page errors, unexpected dialogs, missing capabilities, teardown failures and denied egress. T is not a second base-six run and exports nothing.

### Build and source bindings

S/T use the same [design-L build receipt](../frontend/dist-canary-modes-v2-20260929-design-l-9644ce17/MODE_BUILD_BINDING.json): **45 inputs (36 frontend source + nine other inputs), four assets**. [Asset manifest](../frontend/dist-canary-modes-v2-20260929-design-l-9644ce17/ASSET_MANIFEST.sha256) SHA-256:

`287126e2624eb3d3bc2747a7f6bc06c6661009422e98d8ba0a5bd098dcbe1a16`

The distinct build-receipt hash is `779db22d3ab2181c3155fcbe0ae689be9500923a1caa20f4795b8b3888cb1463`. [S bindings](../canary_test/artifacts/v2-acceptance-st-final-20260930-8be4a792/S/current-bindings.json) and [T bindings](../canary_test/artifacts/v2-acceptance-st-final-20260930-8be4a792/T/current-bindings.json) each check 100 host-source hashes, 25 test hashes, 45 build inputs, four assets and three helpers without drift. J's timer-I build is historical, not the current S/T build.

Normal Vite CSS processing remains blocked by unreadable OneDrive-backed dependencies. The explicit `--css-engine lightningcss` path is a bounded workaround, **not repaired OneDrive or a normal-build success**. Failed build labels remain preserved. The accepted isolated dist is not deployed to the shared daily dist.

**262 source files were stable during the frozen run and at post-stop verification**, and the same 262 were rechecked without drift immediately before this docs edit. The [pre-edit source/document binding](../canary_test/artifacts/v2-acceptance-st-final-20260930-8be4a792/current-source-and-doc-bindings.json) remains immutable. Subsequent changes to the four owned documents, plus this new record, are **expected docs-only drift/addition**; do not rewrite source maps, manifests or old receipts to claim that these new document bytes ran in S/T. New product/harness changes would require separately authorized, fresh matching evidence.

### Shutdown and Windows risk

[Process observation](../canary_test/artifacts/v2-acceptance-st-final-20260930-8be4a792/process-observation.json) and the verification receipt establish O/P/Q/R/S/T manifest PIDs absent and **8787 clear**. All six hosts record stopped/shutdown/lifespan/provider/input/source/test flags true; S/T denied egress is zero. This means the scoped acceptance hosts are stopped, **not that every unrelated user service was enumerated or stopped**.

Exact S/T launcher exit codes are not independently available in this final report; terminal-return/identity-shutdown facts are [parent observations](../canary_test/artifacts/v2-acceptance-st-final-20260930-8be4a792/parent-observations.json). Do not transplant J's exact-handle exit-0 claim into S/T. **S observed four Windows 10054 callbacks; T observed zero. The cause remains unresolved; T's zero does not establish a repair.** Historical native-crash, Linux and load risks are also not cleared by these passes.

## 2. Current-phase implementation summary

These are implemented/tested slices, not an assertion that every design item is complete:

| Slice | Source and evidence boundary |
|---|---|
| Display metadata CAS | [task_metadata.py](../backend/task_metadata.py), [Workspace.tsx](../frontend/src/components/Workspace.tsx): independent display title/metadata revision, expected content + metadata revisions, stale 409 and read reconciliation; no burned-headline rewrite or TTL extension. T exercises one commit and one stale conflict with pending edits retained. |
| Gone proof and truthful history | [main.py](../backend/main.py), [admission.py](../backend/admission.py): explicit `410 task_gone` requires proof; absent/invalid access remains 404, conflicting/repeated capability never falls back. T covers actual invalid-link 404 and read-only retry, **not real expiry/delete/410**. Historical QC summaries are not authoritative: history says **“发布前检查尚未读取”** until actual current checks are read; never infer publication eligibility from old history. |
| Copy and recovery | [task_operations.py](../backend/task_operations.py), [drafts.py](../backend/drafts.py): same-mode completed copy vs changed-mode recovered draft, fresh capability/file bindings, creation inputs and source-voice intent preserved, original retention/lifecycle retained. Staging thumbnail URLs are not copied into immutable reports. T independently checks source/copy r0, no repeat ASR or copy generation. |
| Mine intent and own voice | [CreateWizard.tsx](../frontend/src/components/CreateWizard.tsx), [mode_pipeline.py](../backend/mode_pipeline.py), [own-voice tests](../tests/test_v2_own_voice.py): `voice=mine` is sentence-recording intent after an initial AI version, not first-generation cloning/whole-track capture; `source_voice_preferred` survives submission/recovery. T verifies the initial-AI notice, not a live microphone. |
| Progressive design and focus | [progressive tests](../frontend/scripts/test-v2-progressive-design.mjs), [dialog tests](../frontend/scripts/test-v2-dialog-focus.mjs): collapsed options/native transcript disclosure, source-bound selections and start gates; native modal Tab/Shift-Tab containment, busy dialog behavior, Escape/restoration. T adds native disclosure/keyboard/modal checks at two widths; no exhaustive visual/WCAG claim. |
| SPA deep links | [frontend_static.py](../backend/frontend_static.py), [route tests](../tests/test_v2_frontend_routes.py): exact task/sample routes serve the index, while API/health/static/method/path safety boundaries remain; T's real reload now succeeds. Not a blanket fallback swallowing missing assets or APIs. |
| SNR and matching | [speech_analysis.py](../backend/speech_analysis.py), [production_modes.py](../backend/production_modes.py): exact-energy uncertainty handling makes equal-power adjacent SNR unknown without inventing evidence; 18dB policy preserved. Matching retains formula/thresholds, contiguous-coverage tie ordering, bounded perfect-span fast path and pre-admission C continuity rejection. Selected semantic/upload regressions are included in 589; historical failed baselines remain. |
| Speech preflight | [readiness.py](../backend/readiness.py), [preflight tests](../tests/test_v2_speech_preflight.py): when `local_speech_required=True`, both speaker/alignment prerequisites report safe reason codes for missing runtime/path/license without inference/importing acoustic runtimes. `inference_verified=False` is explicit. **`local_speech_required=False` is still allowed/default: required policy is not globally forced.** |
| Release/build binding | [frontend_binding.py](../deploy/frontend_binding.py), [manifest writer](../frontend/scripts/write-manifest.mjs), [release tests](../tests/test_v2_release_integrity.py): current builder checks complete source/config/lock/rules inputs, before/after freshness and actual asset/manifest bytes; supplied receipts are never ignored. Legacy receipt-free archive compatibility is not a current-build exemption. No production archive/install was run here. |

### Export authentication regression and historical attribution

[Current authorization](../backend/main.py#L701-L731) restores the established contract: **one identical header token + one query token** is a valid matching pair (successful authenticated Range 206), whereas a conflict or repeated value **within either channel**, even equal repeated values, remains **404**. Explicit bad credentials cannot gain owner/local fallback; revision/output/publication tampering retains 409; Gone disclosure retains its proof boundary. [Auth regressions](../tests/test_v2_export_download_auth.py) are included in the current 589. No header/query/Range gate was loosened merely for browser success.

The [O/P/Q/R appendix](../canary_test/artifacts/v2-acceptance-st-final-20260930-8be4a792/historical-opqr-appendix.json) preserves:

- **O failed** at recovered-script comparison. It compared pre-input text with the normalized **submitted snapshot**, which intentionally inserts a blank headline/body separator. Product behavior did not change for this expectation repair. [Design spec](../frontend/e2e/v2/design.v2.spec.ts) retains the original strict `SCRIPT` literal and an independently authored `SUBMITTED_SCRIPT` expected value; source draft, recovered payload and textarea must all equal that value exactly, rather than deriving expected text from the response. [Recovery regression](../frontend/scripts/test-v2-recovered-mode-script.mjs) retains the original-input failure diagnostic.
- **P passed 4/4 before the auth repair**; historical design evidence, not current-product acceptance.
- **Q failed** in V2-03 Range retrieval after two passes; **its exact HTTP status is unknown**, not proven 404.
- **R failed** in V2-03: first Range offset 0 returned **actual 404**, identity matched. `response-206` names the expected gate, not the response status. This led to the matching-header/query regression repair.
- **S/T are current**. J, H/I, K–N and earlier failed build/backend labels are not deleted, retried under the same label or retrospectively made green. Later passes do not repair historical evidence.

## 3. Remaining blockers and concrete next actions

1. **Release-owner review before packaging:** [REQUIRED_MEMBERS](../deploy/verify_release_archive.py#L18-L54) includes V2 core, metadata, three existing docs and the binding helper, but **does not explicitly require the new [frontend_static.py](../backend/frontend_static.py)**. [The builder](../deploy/build_release.py#L20-L40) includes the backend directory recursively, so this is an explicit verifier-anchor gap, not proof the ordinary archive omits the module. Separately authorize adding the required anchor and a missing-member regression; then inspect actual archive contents. This new validation record is also not in the builder's explicit docs list; decide whether to include it or ship a durable summary in the already-included docs. No release-code change or archive generation occurs here.
2. **Speech/operator gate:** obtain compatible private speaker ONNX and CTC weights/tokenizer, checksums, language/privacy/license review, and explicitly approved runtime provisioning (`sherpa_onnx`, `torch`, `transformers`). They remain outstanding, not installed or downloaded here. Decide/enforce the required policy operationally; then separately validate real inference, speaker identity, alignment, human listening and live microphone permission/recording/error cleanup. Synthetic ASR/speaker labels/even clocks/tone TTS plus real FFmpeg prove wiring/media contracts, **not real speech or factual quality**.
3. **Sample and build environment:** provide a reviewed packaged sample and verify the actual sample endpoint; current absence is legitimate **503**. Recover the OneDrive dependency-read problem before claiming the normal Vite path; retain the explicit Lightning workaround and all failure labels meanwhile.
4. **Design review:** map and review **26 hooks, 357 actual vs 311 declared view keys, 609 unique bindings / 892 occurrences, 105 copy entries, 17 check codes** in [DESIGN-MAP.md](DESIGN-MAP.md). These are inventory/candidate mappings, not full semantic/pixel acceptance. Record per-item evidence or deliberate deviations. Expand coverage for real Gone/expiry/delete, browser retry-same, live recording, remaining responsive/overlay states and accessibility with separate authorization.
5. **Configuration and historical regression:** review the **26 missing development-template fields** against 137 Settings fields; production-template inventory has **zero missing/unknown/duplicate fields**, not 26 missing production variables. Real production credentials/domains remain operator-only. Preserve historical failures and decide a separately guarded full-historical-suite run if needed; **589 selected legacy is not that run**.
6. **Operations:** investigate Windows 10054 callbacks and historical native-crash risk without assuming a green rerun fixed either. Separately plan Linux dependency/model compatibility, load/capacity, TLS/Nginx/systemd, signed traceable release, drain/backup and rollback/fault drills. None of production/Linux/load/TLS/rollback is verified by S/T. No daily-service access, real-data migration, deployment or new paid activity is authorized by this record.

**Closure:** current bounded synthetic core/design phase is complete on its retained bindings; the above gates remain open. Docs-only post-run drift is disclosed, not a reason to re-sign history or claim all M0 finished.