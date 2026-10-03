# V2 current-phase sanitized closure — 2026-09-30

> **2026-10-02 后续实施状态：** [本轮产品进展](V2_PRODUCT_PROGRESS_20261002.md)覆盖下面决策阶段的现状：12 秒估算已实现；后端 735/735、前端 1256/1256、根工作台 43/43、四项类型检查通过；正常 Vite 构建成功。CAMPPlus 独立环境合成输入 smoke 通过，但 CTC/真实质量/原生下载/新核心浏览器验收仍未完成。旧 V/W 结果不升级为当前源码验收。

> **2026-10-02 决策更新（不改旧验收）：** 用户已授权自行选型，见[模型与产品决策](V2_MODEL_SELECTION_20261002.md)。选定中文 CAMPPlus ONNX 与中文 XLSR-53 CTC（待受控 safetensors 打包）；D06 确定保留整句警示、采用 12 秒估一镜、保留图片 50 MiB。**12 秒估算尚未实现，模型尚未安装/推理验收**；下文“待选择/待决定”仅保留其当时含义，旧测试结果不因此升级。

**Current: bounded synthetic core V 6/6 and independent design W 4/4 passed. F01–F08 are implemented, not still-open product defects. This supersedes S/T/J “latest/current” wording without rewriting their dated evidence. It is not full design, speech-quality or production acceptance.**

Machine record: [sanitized final report](../canary_test/artifacts/v2-closure-vw-20260930/final-report.json). Navigation: [implementation](V2_IMPLEMENTATION_20260929.md), [runbook](RUNBOOK.md), [design map](DESIGN-MAP.md), [static design review](V2_DESIGN_REVIEW_20260930.md), [historical S/T closure](V2_VALIDATION_20260930.md), [browser procedures](../frontend/e2e/v2/README.md).

## 1. Evidence, attribution and frozen scope

This closure only reads source and allowlisted projections of selected evidence, hashes files and checks links. It runs no tests/builds/services/product modules, touches no actual environment/data/live task, and makes no product/harness change. All writes use patches. Retained results below were **reused, not rerun**; distinct scopes must not be summed into a new suite.

| Scope | Verified retained result | Attribution / limit |
|---|---|---|
| V base six | **6/6; 323925.054 ms; retries 0; global errors 0** | Complete base suite; `baseSixPassed=true`. Stopped TEMP manifest and manifest-root-relative browser summary independently projected and hashed. |
| W independent design | **4/4; 34877.722 ms; retries 0; global errors 0** | Complete design suite; **`baseSixPassed=false` is correct**, not failure or a second base-six pass. |
| [Backend frozen summary](../canary_test/artifacts/v2-validation-20260929-194544-34a6f3ff3e9f44c1b1ae45872dbb7f2c/summary.json) | **629/629; 371.637188 s; zero failures/errors/skips** | `v2_plus_selected_legacy`, `backend_passed_not_acceptance`; 283 sources stable at run. Independently compared **63 backend source-map entries now: zero drift**. Not full historical backend discovery. |
| [Frontend frozen summary](../canary_test/artifacts/missing-frontend-frozen-20260930-8fa247c19b43495eac021f304b8e68de/summary.json) | **1191/1191; 89075.0837 ms; zero failures/skips/cancellations** | Retained frontend glob. Separate **root 43/43; 1342.2083 ms**; four frontend/workspace/modes/V2 typechecks exit 0. 325 sources stable at that run. |
| Native recording, separate focused run | **17/17 = 15 default + 2 opt-in; exit 0; exact-handle wait; 24.7819346 s wall** | No physical microphone, ASR or real provider; never add 17 to 1191 or label V/W microphone coverage. |
| U source-map repair | **21/21; V2 typecheck exit 0; Problems 0** | Parent-reported focused result, later than frozen 1191; not added to that total. Current repair source hashes verified. |

The [test-name comparison](../canary_test/artifacts/missing-frontend-frozen-20260930-8fa247c19b43495eac021f304b8e68de/test-name-comparison.json) and [frontend closure](../canary_test/artifacts/missing-frontend-frozen-20260930-8fa247c19b43495eac021f304b8e68de/closure.json) preserve the initial **1186 = 1164 pass / 22 fail**, root **40 = 34 pass / 6 fail**, fixture-only repair and subsequent pass. [Initial frozen report](../canary_test/artifacts/frozen-full-validation-20260930-034030-17479d71d13c4ce091e311f12e5f62dc/final-report.json) remains unchanged. TAP totals, not sanitized top-level row counts (1189), are authoritative. No claim that every old failure or native crash was repaired.

### Build and pre-edit integrity

V/W reuse the [closure-U build receipt](../canary_test/artifacts/v2-20260930-closure-u-93b5ffdd/receipt.json) and [build binding](../frontend/dist-canary-modes-v2-20260930-closure-u-93b5ffdd/MODE_BUILD_BINDING.json): **48 inputs = 39 source + 9 other; four assets**. [Asset manifest](../frontend/dist-canary-modes-v2-20260930-closure-u-93b5ffdd/ASSET_MANIFEST.sha256) SHA-256 is `bf15be6231db9d9cc7044d98f7b7a8674a9fa550726a55fd5aab53a0dfa4777a`. The build was verified current before V by the parent; this closure independently rechecked its current inputs/assets. Explicit **Lightning CSS workaround**, not a normal Vite success or OneDrive repair.

Before edits, both stopped V/W maps matched current bytes: **104 source entries, 26 test entries, 48 build inputs and 4 assets each**, zero drift; repository union **138 distinct entries**, not the sum of overlapping maps. Observation timestamp **2026-09-29T20:30:03.598Z** is retained as UTC, not relabelled to the local closure date. Digest algorithm, map digests and evidence hashes are in the machine report.

**Post-test documentation drift is intentional:** [the bound browser README](../frontend/e2e/v2/README.md) changes after V/W and must now differ from its original test hash. Five other existing docs receive top additions; this report and the machine artifact are new. No old manifest/source map/build receipt is re-signed. These new document bytes did not run in V/W. Future product/harness changes need fresh appropriately scoped evidence.

## 2. U failure, V/W shutdown and Windows scope

U is **failed configuration / zero tests**, not a partial pass. The parent identified exactly one rejected key, [the probe-guard regression](../frontend/scripts/test-v2-probe-guard.mjs), under `safe source entry`. The existing [environment validator](../frontend/e2e/v2/environment.ts) now permits **that exact path**, with canonical-name, case-alias and digest checks; [manifest-source regressions](../frontend/scripts/test-v2-manifest-sources.mjs) cover the repair. This is not a whole scripts-directory allowance. Only these two harness files were changed/added in the parent U→V/W continuation; **this documentation task changes neither** and makes no product edit.

Stopped U/V/W manifests independently confirm `serverState=stopped`, shutdown/lifespan/fake-provider completion, input/source/test stability and denied egress **0** (the stopped state plus six true flags). Exact process/HTTP facts below are **parent-provided receipts**, not reacquired by live requests or process enumeration here:

| Host | Launcher / worker, both absent | Exact launcher wait | Wall seconds | stdout / stderr chars | Windows 10054 |
|---|---|---|---:|---:|---:|
| U | 56656 / 51352 | exit 0 | 31.9824072 | 70 / 0 | 0 |
| V | 36440 / 17040 | exit 0 | 382.9484308 | 70 / 0 | 0 |
| W | 53108 / 42900 | exit 0 | 62.5747129 | 70 / 0 | 0 |

Parent observed **8787 free** after each. This is not an assertion that all unrelated services were stopped. **V's first shutdown POST omitted Origin and returned 404 without state mutation.** The corrected exact-Origin request with instance header/body verified shutdown **200**. This procedural failure is retained, not omitted because the suite passed. W used identity-bound correct-Origin shutdown **200**. No shutdown request was sent by this closure.

[Windows transport correction](../backend/windows_asyncio.py) is opt-in **only in the acceptance host**, pinned to **CPython 3.11.9 and the checked private layout**. It narrowly handles terminal socket-shutdown `winerror=10054` and preserves cleanup/error behavior; daily/production launchers do not enable it. Parent's independent native fixtures retained **stock 12 / corrected 0** resets and intentional error cases; [fixture source](../tests/test_v2_windows_asyncio.py) remains. V/W zero counts are additional bounded evidence, **not a fix for all Node/native crashes or TLS resets**. Historical S four and J three callbacks remain historical facts.

## 3. Current implementation overrides for F01–F08

The [static review's original findings](V2_DESIGN_REVIEW_20260930.md) remain the discovery record. Its “open / parent decision pending / not implemented” wording for these eight findings is superseded by this table. Implemented plus bounded tests does not mean every behavior was exercised in V/W or full pixel parity.

| Finding | Current implementation and evidence source |
|---|---|
| **F01 implemented** | [Speaker validation](../frontend/src/lib/workbenchApi.ts) and [QuoteEditor](../frontend/src/components/QuoteEditor.tsx) use **8/12 Unicode code points** at input/submission boundaries, preserve invalid drafts for correction and do not truncate identities. [Boundary tests](../frontend/scripts/test-v2-speaker-limits.mjs). |
| **F02 implemented** | [Workspace](../frontend/src/components/Workspace.tsx) / [ResultWorkbench](../frontend/src/components/ResultWorkbench.tsx): structured **401/403/404/410** read handling, only **410 + `task_gone`** means expiry; captured selection/generation/abort and mutation-receipt fences prevent stale responses/held writes from evicting current state. [Access tests](../frontend/scripts/test-v2-result-access.mjs). Real browser TTL expiry remains unaccepted. |
| **F03 implemented** | [Processing](../frontend/src/components/Processing.tsx) cancelled state offers **new work**, not no-op retry or unsupported recover-draft. [Cancelled-action tests](../frontend/scripts/test-v2-cancelled-actions.mjs). |
| **F04 implemented** | [ResultWorkbench](../frontend/src/components/ResultWorkbench.tsx) provides idle editable-narration upload and truthful wait/countdown/recording cancellation, keeps previous pending edits and closes late-granted resources. [Recording tests](../frontend/scripts/test-v2-recording-affordance.mjs); native scope below. |
| **F05 implemented** | [SampleView](../frontend/src/components/ui/SampleView.tsx) is a strict **read-only** storyboard/detail/player using supplied final-video times, no inferred clock/fake assets/task authority. [Sample tests](../frontend/scripts/test-v2-sample-review.mjs). Actual reviewed sample pack still absent/503. |
| **F06 implemented** | [Wizard](../frontend/src/components/CreateWizard.tsx), [drafts](../backend/drafts.py), [uploads](../backend/uploads.py): task-scoped probe-only fallback, then **explicit completion** after all selected original durations and per-file/total budgets are known; `before_asr` rechecks before provider work. A valid probed **ASR-failed peer** does not block other metadata-only completions or silently retry itself. [Fallback tests](../frontend/scripts/test-v2-server-probe.mjs), [continuation](../frontend/scripts/test-v2-probe-continuation.mjs), [backend probe guards](../tests/test_v2_probe_fallback.py). Synthetic acceptance host is not independent proof of real ASR cost behavior. |
| **F07 implemented** | [Quality summary](../frontend/src/lib/qualitySummary.ts), [saved QC target](../backend/quality.py), [safe report projection](../backend/workbench.py): accurate scoped counts, saved target/window belongs to **displayed revision**, history immutable, old missing window unknown rather than recomputed from today's preferences. [Rate tests](../frontend/scripts/test-v2-rate-summary.mjs), [backend rate tests](../tests/test_v2_rate_summary.py). |
| **F08 implemented** | [Mode CSS](../frontend/src/styles/modes.css) single-column at **≤640px**; [breakpoint tests](../frontend/scripts/test-v2-mode-breakpoint.mjs) address boundary widths, not just old 320/900 layouts. |

Additional current slices: [selected-scope history checks](../frontend/src/lib/historyChecks.ts) relay actual authoritative checks without per-history-row GET fanout; unvisited rows remain unknown. Candidate picker shows **three then remaining**, [root result tests](../scripts/test-v2-result.mjs); known `badRows` are counted accurately and A/B transcripts stay read-only, [design-detail tests](../frontend/scripts/test-v2-design-details.mjs). These are not retrospective changes to S/T evidence.

[Release required members](../deploy/verify_release_archive.py) now explicitly include **both** [frontend_static](../backend/frontend_static.py) and [windows_asyncio](../backend/windows_asyncio.py); the old missing-anchor finding is superseded. The retained 629 static inventory records **137/137 fields in both templates**, zero missing/unknown/duplicates, replacing the old 26-development-field gap. Actual environment files were not read; schema coverage is not deployability. No archive was built or deployment verified here.

## 4. Native recording receipt, independently scoped

Selected sanitized TEMP receipts from the recording-verification run were projected; their locators and hashes are archived in the machine report, **not raw manifests, media, errors, DOM or logs**. Native permission/cancel case **6881.3947 ms**; stop/save/discard case **11749.7209 ms**. The latter produced **one in-memory multipart upload**, native **Opus 57600 frames at 48 kHz / 1.2 s**, 19529 bytes; parent reports nonzero decoded frames. Native recorder unchanged, pending edits retained, tracks/contexts closed; retained final receipt reports **zero evidence-bound processes/profile directories** and **273 hashes stable during the run**. This is run-time stability, not a claim to have newly rehashed that full recording inventory in this closure.

All network blocked; synthetic AudioContext input, no physical microphone, backend auth/ASR, real provider, apply/start or media-quality acceptance. The **17 focused cases remain separate from the frozen 1191 total**. Prior incomplete recording attempt remains preserved, not green.

**Artifact disclosure:** Edge created a **246-byte diagnostic file**. It is preserved **unread and unprinted**, as confirmed by the sanitized disclosure receipt. Thus “no deliberate raw log capture” must not become the false claim “no browser-created diagnostic artifact exists.” This closure did not read its contents.

## 5. Remaining decisions and acceptance limits

- **Real speech/models:** approved compatible runtimes, weights, checksums/licenses/privacy, operator listening and factual/semantic quality; physical microphone and actual ASR/recorded-speech pipeline. No model download, dependency install or paid call here. `local_speech_required=False` remains allowed; prerequisite checks are not inference proof.
- **Sample:** reviewed actual package still unavailable/503; implemented read-only UI and synthetic fixtures do not supply a production sample. No live endpoint was queried here.
- **Normal build:** OneDrive **0x80070194 / UNKNOWN -4094** affects two installed Vite chunks; Lightning is a bounded workaround, not repaired dependencies or permission to change them.
- **D06 explicit decision still required:** whole-quote continuity warning is **not changed-word underline**; **6s versus 12s** footage estimate and **image 50 MiB cap** are disclosed differences, **not user-approved parity**. Do not silently waive them when closing F01–F08.
- **Design:** [static grouping evidence](../canary_test/artifacts/v2-design-review-20260930-static-a5191ade/coverage.json) assigns **1114 entries to 16 groups** (26 hooks + 357 views + 609 unique bindings + 105 copies + 17 checks); 892 binding occurrences and stale heading 311 remain distinct. Complete inventory review is not exhaustive semantic/pixel/WCAG acceptance.
- **Broader browser/media:** real expiry/410/delete transitions, same-browser retry, native download-click completion and all responsive/accessibility states are not established by V/W. Export Range validation is not native download UX; synthetic ASR/tone TTS + real FFmpeg is not real speech quality.
- **Operations:** no production Linux/load/TLS/signed release/deploy/rollback acceptance; Windows fix remains opt-in/pinned and does not close historical native-crash risk. **Full historical backend safe runner is not implemented**; 629 covers V2 plus selected legacy only.

**Closure boundary:** only the two new closure artifacts and top status additions in six owned documents. No services/tests/builds/models/dependencies/environment/data/live task reads or daily port 8000 access; no historical evidence rewritten. Old S/T/J bodies and failed labels remain dated history; the new top statuses and this record govern the current phase.

**Documentation verification:** 67 new Markdown link occurrences / 51 distinct targets and seven machine-report relative references resolve, zero broken links. All six historical document bodies reconstruct to their pre-edit SHA-256; retained evidence hashes and the recording-test hash remain unchanged. The only V/W-bound drift is the disclosed browser README. An initial Unicode text-transport comparison was inconclusive; byte-hash reconstruction, not altered history, resolved it.