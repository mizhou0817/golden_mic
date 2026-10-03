# V2 design-to-implementation map

> **2026-10-02 implementation override:** [Product progress](V2_PRODUCT_PROGRESS_20261002.md) records implemented/tested 12-second estimation, known speech one shot and separately counted unknown speech. Whole-quote warning/50 MiB remain. Later “pending implementation” below is the earlier decision-phase state, not current code; no new full design/browser acceptance is claimed.

## D06 decision update — 2026-10-02

Under delegated product authority, [the decision record](V2_MODEL_SELECTION_20261002.md#5-d06-产品决策已作出而非等待用户选择) resolves D06: retain whole-quote continuity warnings/server gates, retain 50 MiB image safety limit, and adopt **12 seconds per estimated shot / one for known speech interviews**. The warning and image limit accept existing behavior; the estimate is **not yet implemented or tested**. Six seconds estimates more available shots, not a more conservative supply. Models are selected in the same record, not installed. Historical mapping and acceptance evidence below remain unchanged.

## CURRENT OVERRIDE — 2026-09-30, V/W closure

**V core 6/6 and independent W design 4/4 passed on current pre-doc bindings; not full pixel/semantic acceptance.** [Current closure](V2_CURRENT_CLOSURE_20260930.md) and [machine report](../canary_test/artifacts/v2-closure-vw-20260930/final-report.json) supersede old S/T/J status and open F01–F08 wording below.

F01–F08 are implemented with scoped evidence. [Full static review](V2_DESIGN_REVIEW_20260930.md) assigns **1114 entries to 16 groups**, not 1114 passes. D06 whole-quote warning versus changed-word underline, 6s versus 12s estimate and image 50 MiB cap still require explicit decision/disclosure, not presumed approval. Selected-scope history checks avoid GET fanout; candidate picker is three then remaining; `badRows` counts and read-only A/B transcripts are current.

Frozen evidence reused: backend **629/629 selected scope**, frontend **1191/1191**, root **43/43**, four types exit 0; native recording **17/17 separate**, no physical mic/ASR. Release anchors and both **137/137** templates are corrected. Models/quality, actual sample 503, normal Vite/OneDrive, exhaustive browser/accessibility and production gates remain. Windows correction is acceptance-only/pinned, not universal crash/TLS repair. Pre-edit hashes matched; bound README now has disclosed docs-only drift, no re-signing.

**Historical boundary:** the S/T/J “latest”, release/template gaps and unreviewed-inventory wording below describe their earlier snapshots. Keep original inventory statuses/evidence unchanged; current grouping is review, not blanket acceptance.

## LATEST STATUS — 2026-09-30

**Bounded core S 6/6 (322.094054s) and separate design T 4/4 (35.454555s) passed, superseding J for current status. No full semantic/pixel/M0 acceptance claim.** Scoped O–T acceptance hosts are stopped, 8787 clear. [Final report](../canary_test/artifacts/v2-acceptance-st-final-20260930-8be4a792/final-report.json), [verification receipt](../canary_test/artifacts/v2-acceptance-st-final-20260930-8be4a792/verification-receipt.json) and [current-phase record](V2_VALIDATION_20260930.md) retain the exact evidence and limits; historical J and O/P/Q/R results remain preserved.

S covers 36 zero-overflow 20px layouts, three generations/three apply actions/seven exports and stable independent r0 C62/A37/B37. T adds two native modal samples at 320/900px, metadata CAS/real stale 409, copy/recovery and invalid-link 404. T is not base-six or real expiry/410/microphone coverage. Current frozen regression is **589/589 V2 + selected legacy**, **902/902 frontend**, **40/40 root**, four typechecks exit 0; **not a full historical backend-suite pass**. See [combined evidence](../canary_test/artifacts/frozen-auth-validation-20260930-010918-b5f499ffa67f4ea38b58275f6d716911/final-summary.json).

The same design-L build binds 45 inputs/four assets, manifest `287126e2624eb3d3bc2747a7f6bc06c6661009422e98d8ba0a5bd098dcbe1a16`. 262 files were stable during the frozen run/pre-edit verification; this documentation update is **expected later docs-only drift**, not permission to regenerate historical bindings. No tests/builds/services/product changes are made here.

**Still open:** full per-item mapping, real speech/models/runtimes/license review and microphones, sample package (503), normal Vite/OneDrive recovery (Lightning workaround only), Windows 10054 (S four/T zero, unresolved), production Linux/load/TLS/rollback. `local_speech_required=False` remains allowed. Release review must address the new [frontend_static.py](../backend/frontend_static.py) missing from explicit [required archive anchors](../deploy/verify_release_archive.py#L18-L54); no code change is included. Concrete actions: [remaining blockers](V2_VALIDATION_20260930.md#3-remaining-blockers-and-concrete-next-actions).

The **2026-09-29 inventory below remains valid as inventory**, not complete-product acceptance. Static counts or literal candidate matches do not become accepted semantic mappings merely because S/T passed.

The normative input is the [UI handoff](../金话筒%20·%20新闻视频生成工具%20-%20UI%20设计交接文档（GPT-6%20Astra）.md), particularly its full Appendix A HTML and Appendix B JavaScript. The [standalone prototype](../金话筒%20·%20新闻视频生成工具（独立版）.html) is a design reference, not a backend or a source of real speech evidence. M0 decisions in [V2_IMPLEMENTATION_20260929.md](V2_IMPLEMENTATION_20260929.md) override conflicting simulation behavior.

## Reproducible machine inventory

[The shared-safe runner](../tests/run_v2_validation.py) generates a fresh `design-map.json` inside its exclusively created V2 evidence directory. Run from the repository root:

```powershell
& .\.venv\Scripts\python.exe -B tests/run_v2_validation.py --static-only --compile-ast
```

This parses, never evaluates, the handoff's JavaScript using the **already installed** TypeScript compiler API. Python uses `ast.parse` for Settings/test declarations; neither phase imports Settings, the application, dotenv or test modules. No npm install, build, host, runtime package installation, model inference or model download is performed. The runner's help path uses only the Python standard library and writes nothing.

| Dimension | Appendix C heading | Full-source extraction | Interpretation |
|---|---:|---:|---|
| Responsive `data-r` hooks | 26 | 26 | HTML attributes only, not CSS selector references |
| `renderVals()` view keys | 311 | **357** | Direct properties of the method's returned object, not keys in nested callbacks |
| Template binding paths | 609 | 609 unique / 892 occurrences | Retains all occurrence source lines, including literal true/false and loop aliases |
| Static-copy index | 105 | 105 | Appendix C-6 authored list; not every dynamic sentence/string |
| Check codes | 17 | 17 | AST string prefixes in `checksFor`; cross-reference canonical rule definitions |

**311 is a stale heading, not a passing assertion.** Read-only AST inspection found 357 distinct direct keys. Appendix C-3 and C-4 are also literally truncated in the handoff; copying those lines cannot produce complete indexes. The generator retains `declared`, `observed` and `mismatches` instead of deleting the 46 additional keys or pretending to have 311. Count equality for other dimensions is inventory evidence only.

Each generated item contains:

- its exact key/copy, handoff source line(s), `status: pending_integration`, and an initially empty `evidence` array;
- literal candidate file/line locations in current frontend source, or a JSON pointer into [mode_rules.json](../backend/mode_rules.json) for a check code;
- handoff SHA-256 plus the companion before/after source inventories for binding the run to exact sources.

Literal matches are **not semantic mappings or proof of behavior**. No match means unresolved, not unsupported; a match does not change an item to complete. Loop-local bindings need manual tracing to their collection producer. Repeated short literals can have many candidates. CSS-only selectors such as `cols3` are not counted as actual template hooks. `static_copy` includes historical developer/demo notes that should be explicitly classified rather than shipped as customer text by accident.

## Implementation responsibility map — updated 2026-09-30

The following slices now have bounded current evidence. **No whole row is declared exhaustively accepted**; the remaining column names the missing evidence rather than repeating the obsolete claim that no browser validation exists.

| Design area | Current implementation / bounded evidence | Remaining closure evidence |
|---|---|---|
| Four screens, history/drawer, gone, navigation | [Workspace.tsx](../frontend/src/components/Workspace.tsx), [v2Persistence.ts](../frontend/src/lib/v2Persistence.ts), [frontend_static.py](../backend/frontend_static.py): S/T capability-bound flows, T reload/real invalid-link 404, native disclosure | All navigation races/states; real browser expiry/delete/410; not false expiry from bare 404/410 |
| Metadata CAS, copy, mine intent | [task_metadata.py](../backend/task_metadata.py), [task_operations.py](../backend/task_operations.py): T commit + stale 409, stable title/TTL/r0 boundaries, independent copy/recovery and initial-AI notice | Real microphone and sentence recording; broader failure/uncertain network UX; no voice-cloning claim |
| Three-step wizard and progressive controls | [CreateWizard.tsx](../frontend/src/components/CreateWizard.tsx), [draftTasks.ts](../frontend/src/lib/draftTasks.ts), [productionModes.ts](../frontend/src/lib/productionModes.ts): S scoped upload/refresh/start and C rejection; T native fold/mode selection | Complete binding/copy review and remaining progressive states; genuine acoustic source marks |
| Draft lifecycle, queue, accepted starts | [drafts.py](../backend/drafts.py), [admission.py](../backend/admission.py), [task_manager.py](../backend/task_manager.py): selected backend exactly-once/retry/retention contracts included in 589 | Production capacity/load/restart drills and browser retry-same; not quota acceptance from test-host limits |
| Processing weights/stages/failures | [Processing.tsx](../frontend/src/components/Processing.tsx), [mode_rules.json](../backend/mode_rules.json): actual S C/A/B pipeline polling | All failure actions/stages, real-provider timings and performance; no simulated completion |
| Storyboard/pending edits/trim/take | [ResultWorkbench.tsx](../frontend/src/components/ResultWorkbench.tsx), [QuoteEditor.tsx](../frontend/src/components/QuoteEditor.tsx), [pendingEdits.ts](../frontend/src/lib/pendingEdits.ts): S three applies, T dirty-dialog/pending preservation | Genuine microphone/word-clock quality and all edit/error states; synthetic clocks remain labelled |
| Apply/restore/revisions | [v2_editing.py](../backend/v2_editing.py), [revisions.py](../backend/revisions.py), [workbench.py](../backend/workbench.py): S C r1/A r2/B r1 and independent immutable-r0 hashes | Full crash/power-loss/rollback/restore coverage; two internal steps do not imply two published versions |
| Publication and exports | [publication.py](../backend/publication.py), [quality.py](../backend/quality.py), [studio_render.py](../backend/studio_render.py): S seven verified exports/five formats/three expected gate 409s; matching-pair auth regression covered | Native download clicks, real speech/factual QC; history summaries are not authoritative and UI says “发布前检查尚未读取” |
| Speech, SNR, matching, preflight | [local_speech.py](../backend/providers/local_speech.py), [speech_analysis.py](../backend/speech_analysis.py), [readiness.py](../backend/readiness.py): exact-energy SNR/matching/prerequisite tests included in 589 | Reviewed weights/runtimes/language/quality; required=False remains allowed; prerequisite pass is not inference verification |
| Responsive/keyboard/focus parity | [ui components](../frontend/src/components/ui), [frontend source](../frontend/src): S36 zero-overflow layouts, T two native modals; opt-in native dialog test in 902 | All breakpoints/overlays/contrast/WCAG and independent pixel review; sampled layout is not exhaustive parity |

The static **26 missing fields concern the development template**, not production variables; production inventory has zero missing/unknown/duplicate fields. These configuration counts are separate from the **26 responsive hooks**. O's recovered-script expectation was corrected against the independently specified normalized submitted script, not by changing product code or making expected text equal to the response; [historical attribution](V2_VALIDATION_20260930.md#export-authentication-regression-and-historical-attribution) preserves O failure, P pre-auth pass, Q unknown status and R actual 404.

## The 26 hooks

`app`, `menu`, `side`, `narrow-hide`, `main`, `steps`, `line`, `stack`, `wrap`, `h1`, `modes`, `tline`, `narrow-only`, `frow`, `mrow`, `spk`, `toggles`, `startbar`, `rtitle`, `ractions`, `rgrid`, `strip`, `checks`, `panel`, `pendbtns`, `drawer`.

## The 17 design check codes

`QUOTE_NOT_FOUND`, `QUOTE_MATCH_LOW`, `QUOTE_TOO_LONG`, `QUOTE_AUDIO_NOISY`, `QUOTE_TEXT_DIFFERS`, `SPEAKER_UNNAMED`, `JUMP_CUT_UNCOVERED`, `MIXED_NO_NARRATION`, `MATCH_FALLBACK`, `EXPLICIT_ENTITY_NOT_COVERED`, `generated_media`, `LOW_MATCH_CONFIDENCE`, `FREEZE_PAD_EXCESSIVE`, `VISUAL_CLIP_TOO_LONG`, `NARRATION_SPEAKING_RATE`, `CONTEXTUAL_BROLL_OVERLAY`, `FACT_CHECK`.

The canonical rules also contain separate safety checks; the 17-code design index is not the universe of backend errors. Level 2 is informational, level 1 requires confirmation, and an automatic level-0 blocker cannot be cleared by a checkbox. `generated_media` is explicitly acknowledgeable; that does not waive missing-disclosure or factual-truth blockers. **`QUALITY_GATE_MODE=warn` permits a reviewable render, not a passed publication/export gate.**

## Completion discipline

An item becomes accepted only after a reviewer records exact source/build hashes, implementation location, current test evidence and any deliberate M0 deviation. Missing runtime/model/visual evidence stays pending or blocked. Preserve failed runs. **Source drift during validation invalidates that run; later disclosed docs-only edits do not rewrite its frozen evidence or imply those new bytes were tested.** Product/harness drift needs fresh authorized matching evidence, never reuse an old label. See [RUNBOOK.md](RUNBOOK.md) for reference procedures and [current closure](V2_VALIDATION_20260930.md) for scope exclusions and concrete next actions.