> **Retired — 2026-09-27.** This is the historical classroom/account Round 2 record, not an active suite. All Round 2 specs, its Playwright/typecheck configs, account-dependent support and Python seed/host are removed. Historical commands, ports, source links and result counts below are not current execution instructions or current acceptance. Preserve original artifacts, logs, paid receipts, source media, prototypes and isolated builds; do not recreate login scenarios to make old links resolve.

# Round 2: historical local regressions and synthetic cloud contracts

## Current scope after retirement

The active no-login suite uses [playwright.workspace.config.ts](../../playwright.workspace.config.ts),
[the workspace acceptance spec](../workspace/acceptance.workspace.spec.ts) and
[the standalone workspace host](../../../tests/browser_acceptance_server.py).
See [current setup and safety boundaries](../README.md#current-no-login-workspace-suite)
and [the core workspace contract](../../../docs/CORE_WORKSPACE_20260927.md).
Only `test:e2e` / `typecheck:e2e` are the current browser-suite package scripts;
there are no legacy aliases or runnable classroom login cases.

The generic [asset generator](support/assets-fixtures.mjs),
[type declarations](support/assets-fixtures.d.mts) and
[bounded Range/media probe](support/media.mjs) stay at their existing paths.
They depend on Node built-ins, not the retired account helpers. Native product
tests and new workspace files are preserved. This cleanup ran no suites, services
or paid calls; historical C26 **69/69**, backend **1148** and Node **260** counts
are not present-suite or fresh validation claims.

## Historical C26 comprehensive audit — 2026-09-26

The latest full run passed **69/69 in 843674.39ms across 19 spec files**, with
**one Edge worker and zero automatic retries**: **58 real local-backend cases
including empty setup + 11 explicitly synthetic enabled-cloud instances**.
Those 11 instances intercept browser cloud responses; no cloud POST from them
reaches the backend. They are **not Tencent live auth, billing or media-quality
acceptance**. R2-30 retains the real default-disabled backend contract.
See the [C26 report](../../../canary_test/CANARY_C26_20260926.md) and
[evidence index](../../../canary_test/README.md); all dated C25/C24 records below
retain their original counts, failures and lifecycle claims.

**HIGH unresolved runtime risk: a prior Python 3.11.9 native host crash remains
unexplained. The latest green run is not a root-cause fix or production approval.**

| C26 final validation | Actual completed result and evidence |
| --- | --- |
| Deep | **69/69, 843674.39ms**, one complete fresh run, not accumulated retests; [summary](../../../canary_test/artifacts/round2/c26-deep-verified-20260926/summary.json). |
| Page | **37/37, 309646.872ms**; [summary](../../../canary_test/artifacts/c26-pages-final-20260926/summary.json). |
| Backend | **1148 tests: 1145 passed / 3 skipped, 623.067s, exit 0**; [log](../../../canary_test/artifacts/c26-audit-20260926/backend-full.log). **24 new Studio failure/auth contracts + 3 real-main-app auth cases are already included**, not additional. |
| Native contracts / types | **260 passed**: session 45, timeline 17, preferences 12, compositions 25, assets 40, proxy/jobs 70, sequences 51. `typecheck`, `typecheck:canary`, `typecheck:round2` passed; final logs are linked in the evidence index. |
| Build | [Final isolated manifest](../../dist-canary-c26-final-20260926/ASSET_MANIFEST.sha256): **1793 modules, 7.20s, 4 assets**; HTTP matches for [page 8768](../../../canary_test/artifacts/c26-audit-20260926/http-final.json) and [latest deep 8769/8770](../../../canary_test/artifacts/c26-audit-20260926/http-verified.json). Shared and old C25 builds unchanged; not deployed. |
| Environment / dependencies | Compile, `pip check`, environment verifier and offline uv check passed; [environment record](../../../canary_test/artifacts/c26-audit-20260926/environment-final.log). Python **76 packages / 0 known vulnerabilities / 0 skipped**, npm **0 known vulnerabilities**; no environment upgrade or crash-resolution claim. |

[audit.round2.spec.ts](audit.round2.spec.ts) adds **six real local cases** to the
retained 18-spec suite: four navigation boundaries, whole-project inactive-child
multicam IDs, and six login/result/Studio 305px-width measurements in one case.
The [coverage section](#c26-additions--6-real-local-cases) distinguishes actual
native browser observations from the usable-width model.

C26 also rechecks Studio authorization after body reads and asynchronous probes,
before durable admission. Real teacher revocation, unchecked review and logout
must leave no new job/project write. Failed/cancelled states persist independently
of `cleanup_pending` and cleanup errors; residual files still count toward quotas.
Each authorized GET retries local cleanup once; the UI warning/GET button never
re-encodes, resubmits a job or exempts quota. Result manifests commit before success
is exposed, and failed results lose success references. This does not prove
power-loss atomicity. ID allocation covers every sequence; BT.709 wording now
describes the existing SDR limited-range YUV conversion, not HDR support.

### Retained C26 failures — not overwritten by the final pass

- [First complete deep run](../../../canary_test/artifacts/round2/c26-deep-final-20260926/summary.json):
  **68/69, 806547.705ms**, only C25P-01's switch-back monitor `currentTime` differed
  by one microsecond. The [native real-byte experiment](../../../canary_test/artifacts/c26-audit-20260926/native-seek-precision.json)
  reproduced **2.007592→2.007591s** in Edge; binary floating-point multiplication
  by 10⁶ yields **2007591.9999999998**. Only that monitor-return assertion now allows
  **1e-6 seconds + floating-point noise**. Exact saved source marks, media bytes,
  cache and permission assertions remain unchanged; this is not a frame-sized
  tolerance or a product-clock change. The [focused retest](../../../canary_test/artifacts/round2/c26-proxy-clock-retest-20260926/summary.json)
  passed **1/1**, then complete runs were attempted again, not summed.
- [Subsequent closure run](../../../canary_test/artifacts/round2/c26-deep-closure-20260926/summary.json):
  **61/69, 744784.912ms**. After C25S-02's functional assertions, cleanup lost its
  connection and seven further cases lost connections after the native host died;
  **not eight independent business bugs**. [Windows events](../../../canary_test/artifacts/c26-audit-20260926/native-host-crash.json)
  identify **2026-09-26T14:23:26Z, Python 3.11.9, python311.dll, c0000005,
  offset 0x205cbe, PID 22888**. The cause is unknown. The latest full 69/69 used
  **`-X faulthandler` only for diagnostics**, not an upgraded interpreter or a fix.
  No crash dump was read/copied and no authentication locals were collected.
- The crash bypassed host finally. Its [independent source recheck](../../../canary_test/artifacts/c26-audit-20260926/crash-source-recheck.json)
  matched **259 hashes**, leaving the old manifest untouched. Unfinished duplicate
  cleanup and failed TEMP evidence remain retained; they are not erased as part
  of the latest successful run.

### C26 shutdown, integrity and acceptance boundaries

[Final shutdown evidence](../../../canary_test/artifacts/c26-audit-20260926/shutdown-verified.json)
records **no listeners on 8768/8769/8770 and no remaining acceptance Python
process**. **Only the page manifest says `stopped`.** Both latest deep redirected
diagnostic terminals exited via Ctrl-C without finalizing manifests; they remain
`startup_complete`, and `errors=[]` is **not proof of orderly lifespan cleanup**.
The [independent final deep recheck](../../../canary_test/artifacts/c26-audit-20260926/final-deep-source-recheck.json)
matched **259 source SHA-256s without rewriting manifests**. This is separate from
the prior crash's recheck and the **564 page-original hashes**, never additive.

The [verified browser/integrity record](../../../canary_test/artifacts/c26-audit-20260926/browser-integrity-verified.json)
has **163 axe reports / 0 violations**, but **1764 contrast + 45 caption incomplete
node occurrences**, not unique defects or WCAG conformance. Its **31 layout matrix
reports / 150 samples / 0 horizontal overflow** exclude the separately recorded
**six new 305px samples**. **106 case observations / 0 problems or errors** do not
erase the earlier crash. The 84 final source/dependency bindings, shared build,
C25 build, final build and paid receipt are unchanged. The initial Node `EBUSY`
hash read left an [empty retained output](../../../canary_test/artifacts/c26-audit-20260926/browser-integrity-final.json),
not evidence of tampering; the independent verified recheck matched.

All TEMP/failure evidence remains; **no new paid calls, deployment or daily
port-8000 launch**. Historical Kimi **100 fen remains held, actual bill unknown**;
Tencent live acceptance is blocked. The 113-item matrix remains **58 partial /
8 metadata / 11 renderer / 36 unsupported**, not 77 complete or all 113 implemented.
Original live QC, hardware, advanced editing and production gaps remain, alongside
the **HIGH unresolved native runtime risk**.

## Historical C25 closure — 2026-09-26

This section's counts, builds and shutdown statements describe C25 only, not C26.

The final complete run passed **63/63 cases in 855637.336ms**, across **18 spec
files**, with **one Edge worker and zero automatic retries**: **52 real
local-backend cases including empty setup + 11 explicitly simulated enabled-cloud
UI instances**. The latter are browser interceptions, **not Tencent live
authentication, billing or media-quality acceptance**. No cloud POST from those
instances reaches the backend; R2-30 still checks the real default-disabled backend.

| C25 final validation | Actual result and retained evidence |
| --- | --- |
| Deep | **63/63**, a fresh full run, not an aggregation; [summary](../../../canary_test/artifacts/round2/p25-deep-final-20260926/summary.json). |
| Page | **37/37, 322225.95ms**; [summary](../../../canary_test/artifacts/p25-pages-final-20260926/summary.json). |
| Backend | **1121 tests: 1118 passed, 3 skipped, 937.139s, exit 0**; [log](../../../canary_test/artifacts/p25-closure-20260926/backend.log). |
| Native contracts and types | **238 passed**: session 45, timeline 17, preferences 12, compositions 23, assets 40, proxy 50, sequences 51. Application and both E2E typechecks passed; these native contracts do not replace browser coverage. |
| Build | [Dedicated closure build](../../dist-canary-p25-closure-20260926/ASSET_MANIFEST.sha256), all **4 assets HTTP-hash-verified on 8768/8769/8770**; shared assets unchanged, not deployed. |

The [C25 report](../../../canary_test/CANARY_20260926.md),
[implementation](../../../docs/PROTOTYPE_IMPLEMENTATION_20260925.md) and
[113-item matrix](../../../docs/PROTOTYPE_CAPABILITY_MATRIX_20260925.md) define the
scope. The prior 42-case suite gains **21 C25 cases**: **9 prototype + 4 assets +
3 proxy + 4 sequences + 1 cold deep link**; current coverage is listed below.

Cold work/show links now start in read-only history; no creator mounts or autosaves
while opening, and legitimate creator leave still flushes. The new
[C25D-01](deep-link.round2.spec.ts) holds the real private-work GET, advances the
browser clock **1500ms before and after opening**, beyond the actual 800ms
autosave debounce, and checks
the complete canonical **nonempty step-3 draft remains unchanged with zero browser
writes**. It supplies no fake success response or auth dump. Owner-scoped API
preparation/restoration is separate from browser writes. **P25-08 retains and
passes its independent sole workbench-POST assertion**.

### Retained C25 failures and boundaries

- [Earlier deep complete](../../../canary_test/artifacts/round2/p25-deep-complete-20260926/summary.json)
  is **60/62**, with a page crash and unexpected draft POST, not green.
  The companion page **36/37** old group-locator failure remains indexed in
  [../../../canary_test/README.md](../../../canary_test/README.md).
- [Initial deep closure](../../../canary_test/artifacts/round2/p25-deep-closure-20260926/summary.json)
  is **61/63**: two `spawn ffprobe ENOENT` failures because the **Playwright
  terminal**, not the host, lacked media-tool PATH. After correcting that PATH,
  the [explicit retest](../../../canary_test/artifacts/round2/p25-media-path-retest-20260926/summary.json)
  passed **2/2**, then a fresh full run passed **63/63**. Do not add these results
  or erase the failure. The first page closure executed **0 cases** while seed
  prerequisites were not ready (**118433.971ms**), also retained.
- [Survey/integrity record](../../../canary_test/artifacts/p25-closure-20260926/browser-and-integrity-verified.json):
  **163 axe reports, 0 violations**, but **1764 contrast + 45 caption incomplete
  node occurrences** need manual review. **31 layout reports / 150 samples /
  0 overflow** are not pixel-perfect or WCAG certification. Page **564** originals
  and deep **259** allowed sources hash-match **separately, not additively**.
- [Shutdown record](../../../canary_test/artifacts/p25-closure-20260926/shutdown.json):
  owned **8768/8769/8770 stopped, errors=[]**; old **8000/8766/8767/8771 were already
  absent at resume**, not preserved old PIDs. All TEMP and failed evidence remain.
- **No new paid requests.** The historical Kimi **CNY 1 held reservation has an
  unknown actual bill** and must not be rerun; Tencent lacks approved configuration.
  The current matrix is **58 partial / 8 metadata / 11 renderer / 36 unsupported**,
  not 77 complete tools or all 113 complete. Partial subfeatures, local advanced
  editing/HDR/full multicam/tracking, hardware and production gaps are not merely
  missing credentials. The original live film's four QC blockers remain.

## Historical C24 acceptance — 2026-09-24

The following results and listener/PID statements describe C24 only, not C25
or currently running services.

The complete final run passed **42/42 cases in 290.818192 seconds**, across
**13 spec files**, with one Edge worker and zero retries: **31 real local-backend
cases including the opt-in empty setup**, plus **11 synthetic enabled-cloud
instances**. Those 11 instances intercept browser cloud responses; no cloud POST
from them reaches the backend. This is **not Tencent live authentication,
billing or quality acceptance**. R2-30 exercises the real default-disabled backend,
including deliberately rejected quote POSTs, not paid submissions.

| Final 2026-09-24 validation | Recorded result and evidence |
| --- | --- |
| Deep suite | **42/42 passed, 290.818192 seconds**; a complete run, not combined filtered passes. [../../../canary_test/artifacts/round2/c24-deep-verified/summary.json](../../../canary_test/artifacts/round2/c24-deep-verified/summary.json). |
| Page suite | **37/37 passed, 337.462241 seconds**, including SESSION-01–05 and the final login-group assertion. [../../../canary_test/artifacts/c24-pages-verified/summary.json](../../../canary_test/artifacts/c24-pages-verified/summary.json). |
| Full backend | **845 tests: 843 passed, 2 skipped, 692.988 seconds**. [../../../canary_test/artifacts/c24-validation-20260924/backend-final.log](../../../canary_test/artifacts/c24-validation-20260924/backend-final.log). |
| Native session transport | **45 passed**, controlled responses against actual client source, not real-server authentication. [../../../canary_test/artifacts/c24-validation-20260924/session-final.log](../../../canary_test/artifacts/c24-validation-20260924/session-final.log). |
| Isolated host/build regressions | **50 passed (39 page-host + 11 deep-host), 4.268 seconds**; **already included in the 845 backend tests, never added again**. [../../../canary_test/artifacts/c24-validation-20260924/isolation-final.log](../../../canary_test/artifacts/c24-validation-20260924/isolation-final.log). |

The main record is
[../../../canary_test/CANARY_20260924.md](../../../canary_test/CANARY_20260924.md).
Page/session baselines remain in [../README.md](../README.md); neither those
partial runs nor the historical 2026-09-23 acceptance below substitutes for the
complete C24 results recorded in this section.

All three final page/deep instances served the same dedicated build represented by
[../../dist-canary-c24-verified/ASSET_MANIFEST.sha256](../../dist-canary-c24-verified/ASSET_MANIFEST.sha256),
**not the shared frontend output**. Before the full browser runs, actual HTTP
fetches verified **all four asset hashes on each of 8768/8769/8770**. Earlier
baseline, fixed and intermediate-final build directories remain retained.

### Retained C24 intermediate evidence and native-play boundary

- The first deep run was **41/42**, retained in
  [../../../canary_test/artifacts/round2/c24-deep-final/summary.json](../../../canary_test/artifacts/round2/c24-deep-final/summary.json).
  R2-09 recorded trusted native **`playing` at 0.000420 seconds → `pause` at
  0.001419 seconds**, with no `ended`. This was a **test TOCTOU race**: the helper
  read a paused snapshot, then clicked the UI play/pause toggle after autoplay
  had started, thereby pausing playback. It was not a product playback defect.
- [support/screening.ts](support/screening.ts) now requests real playback/replay
  idempotently and **awaits the native `HTMLMediaElement.play()` Promise**. It does
  not replace that method, seek, change playback rate or fake `playing`/`ended`.
  The filtered **R2-09–12 retest passed 4/4 in 32.556171 seconds**:
  [../../../canary_test/artifacts/round2/c24-screening-verified/summary.json](../../../canary_test/artifacts/round2/c24-screening-verified/summary.json).
  This is supporting evidence, not a substitute for the subsequent full 42/42.
- The intermediate page run passed **37/37**, but preceded the two final ARIA
  group fixes and is **not current acceptance**, despite its `final` label:
  [../../../canary_test/artifacts/c24-pages-final/summary.json](../../../canary_test/artifacts/c24-pages-final/summary.json).

### C24 accessibility and cleanup boundaries — historical

The two product ARIA fixes give `ClassroomLogin`'s **登录身份** and
`ResultWorkbench`'s **句子选择** actual **`role=group`** semantics. The final page
login test and deep R2-23 assert the real named groups; the two invalid-label axe
findings are gone. Gradient `color-contrast` and `video-caption` incomplete
findings still require manual review: this is **not complete WCAG conformance**.
The existing teacher **班级统计** group assertion remains in R2-23.

Only owned **8768/8769/8770** were stopped. The final deep manifests record
`serverState=stopped`, `errors=[]` for seeded
`golden-mic-canary-round2-qicw5u5c` and empty
`golden-mic-canary-round2-bjr7pjox`, as recorded in the
[dated C24 report](../../../canary_test/CANARY_20260924.md).
The mutable `current`/`empty` manifest slots now represent later runs, not those C24 IDs.
The stopped page run `golden-mic-canary-b6j2g9b6` is recorded in
[../README.md](../README.md). User listeners **8766 (PID 35048), 8767 (PID 29540)
and 8771 (PID 46760) remained unchanged**; no all-ports-stopped claim is made.
The deep source recheck matched **259 allowlisted originals**; the separate final
page-original recheck matched **564 files**. These inventories are **not additive**.
All TEMP data, original media and failed/intermediate evidence remain retained.

This cycle made **no new paid requests**. The prior single Kimi smoke's
**100 fen (CNY 1) remains `held_for_reconciliation`**, unchanged, with actual bill
unknown. The original full live film's four QC blockers and all **58 unsupported
advanced catalogue items** were still gaps at that date: completing C24 did not complete every
prototype feature or Tencent live acceptance.

## Historical cloud-integration acceptance — 2026-09-23

**42/42 passed in 239.026573 seconds** in one complete fresh-process/fresh-seed run,
with one Edge worker and zero retries:
[../../../canary_test/artifacts/round2/cloud-verified-20260923/summary.json](../../../canary_test/artifacts/round2/cloud-verified-20260923/summary.json).
This is **31 real local-backend cases, including default-disabled cloud R2-30 and the
opt-in empty classroom, plus 11 synthetic enabled-cloud contract instances**. All
cloud responses in those 11 instances are browser interceptions; **no cloud POST
reaches the backend**. They are not live-cloud authentication, billing or quality
acceptance, and do not require a running live-cloud service. R2-30 separately sends
only deliberately rejected local quote POSTs, not paid job submissions.

| 2026-09-23 companion final validation | Recorded result and evidence |
|---|---|
| Backend | **795 tests: 793 passed, 2 skipped, 389.889 seconds**; [../../../canary_test/artifacts/prototype-refactor-20260923/cloud-backend-final.log](../../../canary_test/artifacts/prototype-refactor-20260923/cloud-backend-final.log). |
| Page suite | **32/32 passed, 242.14308 seconds**, one Edge worker, zero retries; [../../../canary_test/artifacts/cloud-pages-verified-20260923/summary.json](../../../canary_test/artifacts/cloud-pages-verified-20260923/summary.json). |
| Build and consistency | Application and both E2E typechecks, Vite build, `compileall`, `pip check`, environment verification and offline lock check passed; independent frozen requirements/SBOM exports matched; **4 asset hashes matched** [../../dist/ASSET_MANIFEST.sha256](../../dist/ASSET_MANIFEST.sha256). |
| Dependency audits | Python **76 packages, 0 known vulnerabilities, 0 skipped**; npm **all dependencies, 0 known vulnerabilities**. |

The authoritative final report and machine-readable record **for that dated run** are
[../../../docs/CLOUD_ACCEPTANCE_20260923.md](../../../docs/CLOUD_ACCEPTANCE_20260923.md)
and [../../../canary_test/artifacts/prototype-refactor-20260923/cloud-validation.json](../../../canary_test/artifacts/prototype-refactor-20260923/cloud-validation.json).
That dated run used **13 specs**. Its bounded **1 MiB Range-chunk GIF downloads**
and [../../playwright.round2.config.ts](../../playwright.round2.config.ts) remain;
C25 adds the five specs listed below.
The current design reference remains
[../../../金话筒新闻视频生成/金话筒 · 原型.dc.html](../../../金话筒新闻视频生成/金话筒%20·%20原型.dc.html).

R2-23 asserts the real named teacher statistics `group`: the existing UI fix
adds `role=group` to the statistics `div` with `aria-label`. That run's teacher
320px/20px axe has **0 violations; only `color-contrast` remains incomplete on 11
nodes**. The cloud-scoped 320px/20px scan has **0 violations and 0 incomplete**.
Other full-page incomplete observations remain review items, not silent passes.

At the close of **2026-09-23**, owned services **8766/8769/8770 were stopped;
8765–8770 had zero listeners**. This is not a statement about current listeners.
All **259 allowlisted original-source hashes** matched on shutdown; actual local
and production environment files were unchanged, and the daily port-8000 service
was not started or modified. TEMP data, failure evidence and the one-use paid-call
receipt remain preserved. Retained manifests do not mean the hosts are running.
The original full live film's four QC blockers remain unchanged.

The sole authorized Kimi `reference_narration` smoke remains the same one request:
9.266 seconds, 377 input + 175 output = 552 tokens; **100 fen remains
`held_for_reconciliation`, actual bill unknown**. Do not rerun it. Tencent live
acceptance remains blocked on approved account/storage/region/templates/language/
rates/budget configuration; all 58 unsupported advanced catalogue items were
recorded as gaps at that date. Those local results did not complete the full prototype.

### Preserved cloud intermediate failures

- The first **41/42** run failed R2-CE-06 because its navigation helper handled only
  the shell leave confirmation, not the initial unsaved project's discard dialog.
  Only the helper was corrected; production confirmation behavior was not changed.
  Keep [../../../canary_test/artifacts/round2/cloud-enabled-20260923/summary.json](../../../canary_test/artifacts/round2/cloud-enabled-20260923/summary.json)
  and the later **11/11 filtered retest**
  [../../../canary_test/artifacts/round2/cloud-enabled-receipt-retest/summary.json](../../../canary_test/artifacts/round2/cloud-enabled-receipt-retest/summary.json).
  That filtered result was not a new full-suite pass.
- The intermediate **41/42** run
  [../../../canary_test/artifacts/round2/cloud-final-20260923/summary.json](../../../canary_test/artifacts/round2/cloud-final-20260923/summary.json)
  failed R2-13 at `browserContext.newPage` when Edge closed, **before business
  assertions**. No production change or weakened assertion was made for that
  failure. The final 42/42 above is a complete fresh-process/fresh-seed run, not
  an aggregation of passes from different runs; both failures remain preserved.

## Earlier core prototype-refactor acceptance — historical

**30/30 passed in 228.625 seconds**, including the opt-in new empty classroom and
the new real-UI deletion regression:
[prototype-final summary](../../../canary_test/artifacts/round2/prototype-final/summary.json).
The first refactor run's 28/29 is preserved: its English-reason assertion was
updated for the intentional Chinese capability explanation while still checking
the real backend classification, technical tooltip and disabled button.
R2-29 additionally catches durable ghost rows after actual media deletion.
The companion **517 backend tests (515 passed, 2 skipped) and 32 page cases** are
also pre-cloud history, not the current acceptance or the current case target:
[../../../docs/PROTOTYPE_REFACTOR_20260923.md](../../../docs/PROTOTYPE_REFACTOR_20260923.md).

## Seed provenance and host scope

**Historical seed and host description only. The importer/hosts below are retired; the current workspace host requires no original live artifacts.**

The seeded host still requires the pinned earlier real live TEMP run
`golden-mic-canary-live-2hb49w7x`, task `22d4b7aa53a34adfbdfda4f7e887425a`, its
allowlisted media/functional artifacts, the 48 original input clips and the safe
live manifest/submission/current-state observations. Missing/mismatched source
evidence fails closed. No original task-state/authentication file, dotenv or
source database is imported, and **no paid submission is a fallback**.
The C25 final host rechecked **259 allowed source hashes unchanged at shutdown**;
the dated C24 check is preserved above. C26's latest **independent post-exit**
recheck also matched 259 hashes, but **does not prove orderly shutdown**; its
manifest was not rewritten. The separate 564-file page recheck must not be added
to this inventory. This does not permit automatic live reruns.

The retired port-8770 `--empty` host and former manual port-8765 host did
**not** require earlier real live artifacts. Their old interfaces are not the
current workspace host interface. Port 8765 fixtures were expressly synthetic
and could not replace Round 2's real-media seed or substantiate actual QC.
See [../README.md](../README.md) for prerequisites and
[../../../canary_test/CANARY_C26_20260926.md](../../../canary_test/CANARY_C26_20260926.md)
for the current audit; C25/cloud/C24 results above remain dated history. Package/build and E2E typecheck
wiring remains in place; dated passes do not establish new full acceptance.
Future reruns need fresh offline hosts; neither a new live
generation nor an enabled backend cloud provider is a prerequisite or fallback.

## Verified historical status — 2026-09-23 (before the core refactor)

**29 cases: 28 on the seeded host, plus 1 opt-in empty-host case. All 29 passed in 229.421 seconds**
in the historical final complete run, labelled `final-20260923`.
That used the then-rebuilt application, new real-media and empty hosts, one Edge
worker and zero retries. The original suite separately passed **29/29** under
`round2-regression`; its tests are not replaced by these.

The historical round-two audit recorded seven product fixes, the **487-test
backend result (485 passed, two skipped)**, intermediate failures, harness
corrections and hardware/production limits. The `final` label is a preserved
**28/29 failure**, not the passing final evidence. No additional paid generation
was executed. The original full live film's four QC blockers remain unchanged.
Both round-two hosts were stopped after that validation; retained manifests are
seed/shutdown evidence, not running servers or new-refactor acceptance.

The actual manifest schema is implemented by [the seed](../../../tests/canary_round2_seed.py)
and [the host](../../../tests/canary_round2_server.py).
Runtime prerequisites fail clearly rather than substituting another seed, inventing outputs or skipping tests.

## Operator-owned prerequisites and configuration

**Retired procedure, not current prerequisites. Do not run the removed Round 2 entrypoints or enable providers to reproduce old evidence.**

- Use the installed project Python/Node/Edge dependencies, and put **both FFmpeg
  and ffprobe on PATH in every host terminal AND the Playwright terminal**.
  New terminals lose another terminal's temporary PATH. Dynamically locate the
  current WinGet `Gyan.FFmpeg` package, not an obsolete 9.0.1 path; the reusable
  PowerShell example is in [../README.md](../README.md#dependencies-and-host-choice).
- Supply an already-built frontend and an already-running `tests.canary_round2_server` on loopback **8769**.
  The manifest slot is the server's `round2/current` evidence slot. It must report `round2-offline`,
  `seeded`, `startup_complete`, empty `errors`, restored real gates and `not_ready` providers.
  Wait for actual startup **before** launching the suite; its readiness check
  does not prepare a seed or start a server. The companion page host also hashes
  48 + 8 inputs (sample2 approximately 919 MB) and copies historical archives;
  slow OneDrive I/O must not be bypassed with fabricated readiness.
- Both seeded and `--empty` modes accept optional
  **`--frontend-dir frontend/dist-canary-<safe-label>`**, or its canonical absolute
  path. Omission retains the legacy shared-build default. For this rerun, select
  the dedicated C26 final build on **both 8769 and 8770**, or a new validated
  output for changed source; do not hot-replace assets
  used by another service. The directory must already exist as a canonical direct
  frontend child, with its complete SHA-256 manifest. Traversal, links/junctions,
  hard-linked files, missing/extra assets and hash mismatches fail closed; the host
  does not build, repair or change frontend files.
  **Never rerun a one-use build task into an existing target**, reuse result labels
  or delete claims/evidence. Reuse a completed build only unchanged with fresh hosts;
  any rebuild needs a fresh output name.
- New host manifests record **actual `baseURL`, `frontend.directory`,
  `frontend.assetHashes` and `serverState`**. The host validates every served file
  except the checksum manifest itself and rechecks the build before serving.
  Keep separate served-asset/current-source build evidence: local manifest hashes
  are not HTTP equality, successful browser tests or current-pipeline quality.
  Round 2 retains its own manifest validator and lifecycle/provenance schema, not
  the page suite's dedicated-build loader. A retained `startup_complete` manifest
  is not evidence of a live listener or completed shutdown. Record actual exit,
  source rechecks and any missing finally evidence separately; never rewrite a
  manifest to claim a clean lifecycle. `-X faulthandler` is diagnostic only and
  does not resolve C26's native crash risk or authorize environment changes.
- Backend cloud must remain **disabled**. R2-CE setup and teardown use real,
  browser-route-bypassing `context.request` GETs to require `enabled=false`,
  `can_spend=false`, six unavailable operations with `cloud_disabled`, and an empty
  real jobs list. A mismatch fails closed; do not enable/configure live cloud to
  satisfy the synthetic cases. R2-30 also validates the real disabled contract.
- The real seed is **first**, **second**, **scratch**: Alice owns all three, first/second are published,
  scratch is private with `can_export=true`. Measured clips are approximately **2.95 seconds** with **zero actual QC blockers**.
  No historical `review`/`qc`/`held`/`failed` seed assumptions are used.
- `ROUND2_RUN_LABEL` defaults to `baseline`. It accepts 1–64 ASCII alphanumeric/underscore/hyphen characters,
  beginning with an alphanumeric character. Reserved seed/history slots and Windows device names are rejected.
  All suite evidence is beneath **canary_test → artifacts → round2 → the label**.
  A nonempty prior label is refused *before* Playwright clears its output directory; use a new label for every run.
- `ROUND2_EMPTY_SETUP=1` adds the **one** optional 8770 setup case. Supply a freshly started `--empty` host
  and its separate `round2/empty` manifest slot. Reusing an already initialized empty host fails before setup.
  Exactly one successful setup request is made; this test leaves the new test-only classroom in disposable TEMP.
  Set this variable to **`1` or leave it unset, never `0`**. Full acceptance needs
  the opt-in case: **69 total**, versus **68** when intentionally omitted.
- Optional `ROUND2_BASE_URL` and `ROUND2_EMPTY_BASE_URL` can spell the corresponding loopback origin;
  they cannot redirect the suite to another host/port. The manifest, browser and context API must agree.
- Page-suite `CANARY_BASE_URL`, `CANARY_SEED_LABEL`, `CANARY_RUN_LABEL` and
  `CANARY_ARTIFACT_DIR` do **not** select Round 2 targets/evidence. Round 2 keeps its
  own `current`/`empty` slots, slot/port locks and history archival. Do not assume
  the page host's one-use seed-label/exclusive-prebind sequence applies here.
- Use `@playwright/test` 1.63.0, `@axe-core/playwright` 4.13.0, TypeScript 5.9.3 and installed Edge.
  **One worker, zero retries**, no server launcher or global seeding. Do not override the privacy settings
  with CLI options or add a raw HTML/JSON/blob/list reporter.
  The dedicated config selects only `.round2.spec.ts`; the original `.canary.spec.ts` pattern cannot select these cases.

### Reproduction order

These are operator instructions only; no commands or service calls were performed
by this documentation update.

1. From the frontend directory, with the existing test dependencies installed, run the application and both E2E
  no-emit checks: `npm.cmd run typecheck`, `npm.cmd run typecheck:canary` and
  `npm.cmd run typecheck:round2`. Optional `npm.cmd run test:session` exercises
  controlled native client-transport regressions, not server/browser acceptance.
  Build a **new dedicated output** with the optional manifest-writer `--out-dir`
  flow in [../README.md](../README.md), or use the already-completed C26 final
  build unchanged. Stop on failure. Use the project Python environment with installed requirements.
  Both FFmpeg and ffprobe must be on **host and runner PATH**; dynamically discover
  the installed Windows package. Do not rerun any retained one-use build task.
2. Confirm 8769/8770 are available for these operator-owned hosts; do not stop or
  adopt another service to free a port. From the repository root in separate
  terminals, invoke the selected interpreter with
  `-X faulthandler -m tests.canary_round2_server --port 8769 --frontend-dir frontend/dist-canary-c26-final-20260926`
  and
  `-X faulthandler -m tests.canary_round2_server --empty --port 8770 --frontend-dir frontend/dist-canary-c26-final-20260926`.
  If a new build name was chosen, substitute the **same validated directory in both**.
  These create fresh isolated TEMP data and archive previous manifest slots without overwriting run evidence.
  Diagnostics do not fix the unresolved native crash. Do not launch the live
  provider host, read/copy crash dumps or collect authentication files/locals.
3. Check both manifests report the expected origin, selected build/hash map and
  `startup_complete`, and both listeners are live, not merely `seeding`.
  Actual startup must validate that build while provider availability stays `not_ready`.
  The prerequisite check does not prepare seeds or start hosts. The empty host must still be unconfigured.
4. In the frontend runner PowerShell terminal select a **new** label, for example
  `$env:ROUND2_RUN_LABEL = 'c26-repro-deep'`, and opt into
  `$env:ROUND2_EMPTY_SETUP = '1'`. Run `npm.cmd run test:round2`, which selects
  [../../playwright.round2.config.ts](../../playwright.round2.config.ts).
  No grep/skip/retry for a complete **69-case result**: 58 real-backend cases including
  the empty host, plus 11 synthetic enabled-cloud instances. Without the opt-in empty
  host there are 68 cases, not the complete acceptance target. Reconfirm **both**
  media tools in this terminal too: retained export/C25 cases probe actual downloaded media.
5. Inspect the resulting summary and per-case observations. Stop only those owned test hosts; verify their ports closed
  and source hashes unchanged. Check process exit and manifest finalization separately;
  an independent hash check is not an orderly-shutdown claim. Retain TEMP/evidence,
  including interrupted cleanup, and original sources; do not bulk-delete temporary directories.

For an explicitly filtered Windows retest with a regex containing `|`, bypass
npm/cmd and call Node directly, for example
`node .\node_modules\@playwright\test\cli.js test --config playwright.round2.config.ts --grep 'R2-28|P25-07'`.
Use a new result label and unset `ROUND2_EMPTY_SETUP` if that focused run does not
use a fresh empty host; **never set it to `0`**. Keep the full-run result separate.

## Real local-backend cases — 58 including the opt-in empty host

### C26 additions — 6 real local cases

All six are in [audit.round2.spec.ts](audit.round2.spec.ts); they extend the prior
63-case total to 69 without changing the 37-page suite or inventing provider success.

| Case | Concrete contract |
| --- | --- |
| C26-NAV-01 | Native Back to an empty hash cancels a held real private-work open; the late GET cannot reopen the work, restore pending UI or cause browser writes. |
| C26-NAV-02 | Native Tab→Enter skip link focuses main without replacing the work hash; reload reopens the same work, with no browser mutations in the observed boundary. |
| C26-NAV-03 | Clicking the current 我的作品 navigation cancels a held private open; late delivery is ignored and the same history card can reopen normally. |
| C26-NAV-04 | 新作品 cancels the old open **before** awaiting draft clear; the real clear POST and blank autosave POST are the **only two allowed target writes**, and their canonical blank result survives the late work GET. No project/job submission. |
| C26-MC-01 | Main multicam append reserves IDs from inactive child sequences across the whole project; actual save, undo and reopen keep IDs unique and child metadata intact. Node contracts additionally cover replace, not claimed as another browser case. |
| C26-LAYOUT-01 | Real anonymous login, result and Studio at **305px usable width ×16/20px**, six geometry/masked-screen observations with no horizontal overflow. Models **320−15px**, not headless emulation of native Windows scrollbars or six extra cases. |

The separate native integrated-browser observation measured a **320px window,
305px client, 320px body and 15px overflow**; `min-width:0` made client/scroll width
305px without clipping overflow or shrinking text. The automated case requires
an actual **305px client width**, with no additional gutter, and retains method
notes. Neither this nor the full survey is pixel-perfect/WCAG or hardware approval.

### C25 additions — 21 real local cases

| Cases | Concrete contract | Spec |
| --- | --- | --- |
| P25-01–09 (9) | Warm-shell/reflow, independent FPS/timecode/marks, five trims/snapping, locks/conflicts/gates, actual color/text pixels, dissolve, bounded manual multicam, merged workbench preferences and metadata-only creator draft; P25-08 permits only its intended workbench POST. | [prototype.round2.spec.ts](prototype.round2.spec.ts) |
| C25A-01–04 (4) | PNG import/dedup/alpha pixels, actual 3D LUT binding/channel change, malformed LUT and SVG/corrupt-PNG rejection without successful asset creation. | [assets.round2.spec.ts](assets.round2.spec.ts) |
| C25P-01–03 (3) | Real proxy 202/decoding and cached 200, source authority/read-only reopen, review-gate separation/private denial, responsive controls and stale POST rejection. | [proxy.round2.spec.ts](proxy.round2.spec.ts) |
| C25S-01–04 (4) | Named sequence edits/persistence, fresh-ID cross-sequence copy and cut refusal, actual nested-parent pixels/history, explicit all-parent duration binding/transitive locks/invalid-draft retention. | [sequences.round2.spec.ts](sequences.round2.spec.ts) |
| C25D-01 (1) | Cold private-work GET held across 1500ms debounce; no mounted creator, no browser writes and unchanged complete canonical nonempty step-3 draft during and after opening. | [deep-link.round2.spec.ts](deep-link.round2.spec.ts) |

### Retained baseline contracts — 31 including the opt-in empty host

| Case | Concrete contract |
|---|---|
| R2-01a | Actual scratch PNG job, unchanged POST/held GET, same SPA result/history navigation; a different work's delete modal opens and is cancelled. |
| R2-01b | Same actual-job timing boundary; after real `context.request` polling reaches success off-Studio, scratch's delete modal must open and be cancelled. No reload that would clear the cache. |
| R2-02 | Result learning report has measurable, visible print text/table **inside the synchronous `print()` call**, then cleanup. |
| R2-03 | Real teacher roster/class report portal visible under print CSS, actual work rows, `afterprint` cancellation unmounts portal. |
| R2-04 | Newly imported one-time PINs in an isolated new class; printable geometry, masked screenshot, source and portal removed after `afterprint`. |
| R2-05 | Print throws: one-time PIN source and portal must clear **without** requiring `afterprint`. |
| R2-06 | Print returns/no-op with no `afterprint`: same credential-removal requirement. |
| R2-07 | Actual denied `getUserMedia` path when Edge supports permission override; Chinese denial + audio-upload explanation. |
| R2-08 | Denial leaves no active recording; native upload input accepts real existing narration and selection is removable, with no final edit. |
| R2-09 | Both real MP4s reach native `ended`; cancel first countdown, replay, advance exactly once, second ends without wraparound. |
| R2-10 | Real Left/Right film navigation; input-target caret keys are not stolen. |
| R2-11 | Native-ended countdown cancels on explicit hidden-tab adapter, no late advance after restoring visibility. |
| R2-12 | Close/unmount during countdown; no stale media events, navigation or remount after advancing JS time. |
| R2-13 | **Open**, re-confirmed gate: pending unchecked UI, saved/server gate unchanged before held POST; publish/export locked, then real 200 closes gate. |
| R2-14 | Open gate + one documented 503 **before forwarding**: checkbox rolls back to actual checked server value, no retry/publication. |
| R2-15 | Separate teacher context revokes while author Studio is mounted; real submission is 409, no job/audit success fabricated. |
| R2-16 | Studio permission refresh through the visible check-refresh button or focus event; submission becomes disabled after teacher revocation. |
| R2-17 | Actual UTF-8 SRT file import → exact server timing/text → browser text edit/save → reopening persistence. |
| R2-18 | Malformed and overlapping SRT receive actual 422; saved project unchanged, input retained. |
| R2-19 | SRT HTML rejected; independent project text save displays markup inertly, with no probe request or script execution. |
| R2-20 | Actual split/move/undo/redo geometry and increasing revisions; unsupported AI capability remains explicitly unavailable. |
| R2-21 | Sequential bounded MP3/WAV/PNG/SRT/ASS exports: successful real jobs, signatures/timestamps, byte counts, matching Range chunks, same-class/other-class/unauth denial. Actual JPEG poster; `jpg` export explicitly unsupported. |
| R2-22 | Browser format controls normalize inapplicable values; real PNG output decodes at 640×360 and is labelled independent export, not current timeline. |
| R2-23 | Distinct result/teacher-open-settings/Studio surveys at 1440/768/390/320px, real 20px font toggle, named sentence-selection and teacher-statistics `group` assertions, full axe violations + incomplete data, top and explicitly scrolled masked screenshots. |
| R2-24 | Optional empty setup: bad-date UI error, one real initialization/automatic teacher login, empty roster/works, logout, actual wrong-password 401, correct login, providers still `not_ready`. |
| R2-25 | Real finished PNG disappears after remote teacher revocation, while unsaved Studio text survives; real consent restoration reopens access without discarding the project. |
| R2-26 | A scoped gate GET 503 removes the previous output and preserves the draft; real recovery and save → fresh gate GET → export POST ordering. |
| R2-27 | Nonblocking print with real lifecycle signals retains PIN rows in the portal after 6000ms; early afterprint cannot clear active print media; media exit clears source/portal. |
| R2-28 | Actual MP4/MOV/MKV/AVI/GIF export jobs: authenticated ≤1 MiB Range chunks, temporary-file length and SHA-256, ffprobe dimension/duration/audio assertions plus observed 25fps; no inferred browser codec support. |
| R2-29 | Actual private duplicate plus teacher note; UI DELETE 204 removes history/sidebar entries after reload/fresh login, task/work/notes/comments 404, all seed work/report hashes unchanged. No seed deletion or publication. |
| R2-30 | Real default-disabled cloud capabilities/jobs for author and teacher; read-only refresh preserves unsaved Studio fields. Direct teacher/student quote POSTs return 403, private peer/outsider reads 404 and anonymous reads 401; no paid job submissions or UI cloud mutations. Actual work/report/project unchanged; cloud-scoped 320px/20px reflow and axe evidence retained. |

## Synthetic enabled-cloud contracts — 11 instances, not live acceptance

All R2-CE cases use the real isolated scratch work and real classroom login, but
**every browser cloud response is synthetic**. CE-02 has four parameterized
instances; the other seven case IDs each have one. They prove browser contracts,
not enabled-backend authorization, actual provider execution, billing or media quality.

| Case | Instances | Synthetic browser contract |
|---|---|---|
| R2-CE-01 | 1 | Chinese-default narration and Japanese Kimi narration/translation quotes; both declarations independently required; native reservation/region/bill-overrun warning; dismissed confirmation sends nothing, accepted confirmation produces one synthetic 202 after a durable receipt. |
| R2-CE-02 | 4 | Teacher cancellation remains available with spending disabled, provider disabled, cloud disabled, or export gate revoked. The last scenario overrides exactly one browser current-work GET to `can_export=false`; it does not revoke real approval or write classroom state. Fresh capabilities/jobs precede one synthetic cancellation; no refund/remote-stop claim. |
| R2-CE-03 | 1 | Renewed `can_cancel=false` after native consent prevents stale cancellation; no cancel POST. |
| R2-CE-04 | 1 | Real student author with synthetic enabled services but `can_spend=false`/`can_cancel=false` cannot quote, spend or cancel. |
| R2-CE-05 | 1 | Smart-subtitle language mismatch blocks quote POST; `zh-CN` quote/confirmation displays the configured synthetic MPS region; dismissed confirmation sends no job. |
| R2-CE-06 | 1 | Lost synthetic submission receipt remains query-only through real back-to-Studio unmount/remount and reload, even when the synthetic jobs list reports success; no new quote, repeated job POST or replacement key. |
| R2-CE-07 | 1 | Receipt-namespace `getItem` failure fails closed before any cloud POST without affecting classroom authentication. |
| R2-CE-08 | 1 | Receipt-namespace `setItem` failure immediately before submission blocks the job POST; classroom session remains valid. |

Spec files: [jobs.round2.spec.ts](jobs.round2.spec.ts), [print.round2.spec.ts](print.round2.spec.ts),
[microphone.round2.spec.ts](microphone.round2.spec.ts), [screening.round2.spec.ts](screening.round2.spec.ts),
[gates.round2.spec.ts](gates.round2.spec.ts), [studio.round2.spec.ts](studio.round2.spec.ts),
[exports.round2.spec.ts](exports.round2.spec.ts), [survey.round2.spec.ts](survey.round2.spec.ts),
[setup.round2.spec.ts](setup.round2.spec.ts), [hardening.round2.spec.ts](hardening.round2.spec.ts),
[deletion.round2.spec.ts](deletion.round2.spec.ts), [cloud.round2.spec.ts](cloud.round2.spec.ts),
[cloud-enabled.round2.spec.ts](cloud-enabled.round2.spec.ts), plus the five C25
specs linked above and [audit.round2.spec.ts](audit.round2.spec.ts): **19 spec files total**.

## Preserved historical baseline findings

| Test | Observed baseline and regression |
|---|---|
| R2-01b | Old cached Studio receipts blocked same-work deletion after real backend completion. [App](../../src/App.tsx) defers the busy decision to atomic server DELETE; the dialog test cancels rather than deleting seed data. |
| R2-05 / R2-06 | PINs persisted after print throw/no-event. [Teacher print lifecycle](../../src/components/TeacherDashboard.tsx) handles events/media/error/expiry without prematurely clearing an asynchronous print portal. |
| R2-07 | Native permission error lacked localized guidance. [Recorder](../../src/components/ResultWorkbench.tsx) gives Chinese upload recovery and cleans up session-owned resources. R2-08 separately proves actual file-selection recovery. |
| R2-16 | Studio controls retained stale permission. [Studio](../../src/components/Studio.tsx) refreshes the real gate, expires failures, rechecks before submission and withdraws old outputs; backend checks remain decisive. |
| R2-02 | Passed at baseline: print visibility was measured synchronously. Hidden **visibility** can be overridden; ancestor **display:none** cannot. CSS source inspection alone was not classified as a print bug. |

These are ordinary regression assertions: **no `test.fail`, `skip` or baseline suppression**.
The real-backend cases do not fabricate success responses; the separately labelled
R2-CE synthetic responses and single gate-GET exception are explicitly scoped below,
not presented as real-backend or live-cloud success.
The baseline's 19/25 result remains intact. R2-24's missing `class_id`, the first three hardening assertions,
and R2-28's option/read-size failures were test-harness issues, not additional product defects. Historical
final results retained strict permission, print, timing and output assertions after correcting those assumptions.
The historical 2026-09-23 cloud-integration run and earlier core run are listed separately above;
historical counts are not reused and failed evidence is not overwritten.

## Isolation, adapters and evidence claims

- Each browser context authenticates itself. No copied cookies, saved login state, credentials in traces, videos,
  HARs, automatic screenshots or raw HTTP transcripts. Deliberately logged-out contexts exist only for denial assertions.
- Current classroom coordination follows
  [../../../docs/CLASSROOM_API.md](../../../docs/CLASSROOM_API.md): in-memory CSRF,
  generation-fenced session observations, per-caller wait cancellation, successful
  login/logout commits as the only identity-broadcast source, actor-bound logout
  and read-only peer recovery. A fresh current anonymous GET can restore login;
  observing another actor cannot authorize adopting/logging out that actor. No
  server authentication/CSRF rule or cloud receipt exception is relaxed.
- The application's minimal **cost-submission receipt is not authentication state**
  or a Playwright `storageState` export. It lives only in `sessionStorage` under
  `golden-mic.cloud-attempt.v1:${actorScope}:${taskId}`, using the real login's
  `actorScope=${role}:${id}`; ≤4096 serialized characters contain only schema,
  opaque quote/key, revision/state and an accepted job ID when applicable. R2-CE
  checks binding and synchronous write/readback before fetch, then persistence
  through remount/reload; the evidence records flags/counts/schema field names,
  **not raw receipt identifiers/values, auth tokens, CSRF, source text or media**.
  Storage faults affect only that receipt namespace. Traces, videos, HARs and saved
  auth state remain disabled; no cross-tab/full-browser-restart recovery is claimed,
  and clearing receipts is not a safe paid retry method.
- Reuse only redaction/hash/name utilities from [the original evidence helpers](../support/io.mjs),
  **not its page-suite origin allowlist, manifest loader, artifact paths or output writer**. Round 2 uses its own actual schema/origins/sinks.
  Dynamic secrets are registered and errors redacted in the worker; the separate reporter never assumes shared secret memory.
- All browser HTTP and API-context requests stay on their own validated origin; WebSockets and generation/edit-provider
  submissions are blocked. **Real-backend cases, including R2-30, do not replace
  successful API responses with made-up 2xx, job state, media, gates or QC**;
  documented held requests and scoped 503 adapters remain unchanged.
- **Explicit R2-CE exception:** every browser cloud endpoint is intercepted and
  labelled `synthetic cloud contract; no provider/billing/live quality evidence`.
  Quotes, 202 success/job lists and cancellation responses are made-up UI contracts,
  never forwarded to the backend. Unarmed/replayed requests and output downloads
  abort without fallback. The CE-02 export-gate scenario additionally overrides
  exactly one browser-only current-work GET to `can_export=false`; no actual
  classroom approval/revocation is written or backend authorization proved. The
  real non-cloud gate/media/QC tests and their assertions are unchanged.
- After real login, R2-CE blocks other browser API mutations. It creates no
  duplicate, saves no project, changes no real classroom/media state and performs
  no real cloud POST. GET-only before/after snapshots verify identical work/approval/
  publication, report, project and protected video bytes, plus the strictly disabled,
  jobless backend. There are **no restoration writes** that could hide changes.
- Each metadata/Studio mutation case duplicates private scratch **as its real owner** using the public endpoint.
  Duplicate-reset consent/checks/confirmation are verified. Export/gate cases separately submit test-only consent/reflection,
  current gate check keys, and a real teacher confirmation. These fictional declarations are not real rights/facts approval.
  Owned copies are deleted by real API cleanup; cleanup failure fails the case.
- C25D-01 preloads a fictional owner/class draft through the real API, compares
  the complete canonical GET record (including retention) during/after the held
  work GET, and asserts **all browser mutation methods remain absent**. Its clock
  advances only application timers, not the server/media clock. After pages close,
  cleanup restores the original owner's draft payload; a previously absent draft
  becomes a normalized empty row and retention is renewed. No original database-row
  byte equality, show-link browser coverage or media-quality claim is inferred.
- C26 navigation adapters hold unchanged real requests and then release/drain
  them; native cancellation or late delivery must leave current navigation intact.
  NAV-04 separately holds the actual draft-clear POST and permits its two intended
  clear/blank-save writes, not a zero-write claim. Owner/class fixture restoration
  occurs after page close. No fake authorization response, task/job write or
  whole-application pushState history implementation is inferred. C26-LAYOUT-01
  measures a 305px usable-width model, not OS-native scrollbar emulation.
- R2-29 deletes its own duplicate in the UI. The bound `markDeleted()` verifies
  the same author, non-seed ownership and all four protected endpoints returning404
  before unregistering the copy. Teardown rechecks absence and unchanged source;
  arbitrary cleanup404 responses are not accepted as success.
- R2-01 explicitly uses **seeded scratch**: restore captured project JSON through the API after draining its real job,
  verify classroom state/report unchanged, and **never delete scratch**. Job records/audit and monotonic Studio revisions remain truthful.
  Any browser/API attempt to delete a seeded task is blocked by a test safety guard and reported as failure.
- Isolated PIN classes have no delete-class API; only their new TEMP data remains. Seed rosters are never changed.
- Print: `emulateMedia('print')` plus an explicitly replaced `window.print` captures **counts/geometry synchronously**;
  R2-27 additionally supplies documented delayed signals and checks portal retention/expiry.
  It does not patch CSS or force hidden content visible. There is no native printer/PDF claim.
  `afterprint` is dispatched only at the documented cancellation step or in final cleanup, never to make error/no-op assertions pass.
  Password inputs, sensitive forms, PIN source and portal are **MASKED**; PIN text/HTML/PDF is never attached.
- Microphone: clear permissions, attempt isolated-context CDP **denied**, and verify Permissions API state.
  A browser virtual-device argument avoids dependence on physical microphone availability; no fake-UI/permission-auto-grant flag is used.
  A transparent observer calls the original `getUserMedia`; if Edge cannot enforce/query denial, the explicit fallback rejects
  with **only `NotAllowedError`** and records that it was injected. No physical/virtual microphone success or audio capture is claimed.
  Upload recovery selects protected real narration locally; it does not submit/apply new narration.
- Screening: the helper **writes only the `muted` media property**, and also
  **invokes the real native `HTMLMediaElement.play()` method and awaits its
  Promise** for playback/replay. The method is not patched or replaced. This
  avoids the stale-paused-snapshot → UI-toggle/autoplay TOCTOU race; it is not a
  claim that playback is initiated only through a UI click. No direct changes to
  `currentTime`, `duration`, `ended` or `playbackRate`, no synthetic media events,
  source substitution, seeking-to-EOF, forced `ended`, or arbitrary sleeps.
  Native events must have `isTrusted=true`; the JS clock controls only countdown timing.
  Hidden-tab and input-target adapters are explicit; property descriptors/listeners/input are restored and the context is discarded.
- Export scope is intentionally bounded to the short seed, 360p PNG/video/animation and default audio/text settings.
  Small media reads retain their ≤8 MiB ceiling. R2-28 spools verified ≤1 MiB Range chunks to a temporary file,
  bounded by the product's 128 MiB total, then hashes/probes and removes it; the historical actual GIF was 11,549,587 bytes.
  JPEG is an actual poster, **not** a nonexistent export option. All ten supported
  formats were executed as real local exports in the **C26 final deep run**,
  as well as the historical C25, C24 and 2026-09-23 cloud-integration runs,
  not all resolution/FPS combinations or all browser codec implementations.
  Media signatures and byte/range checks are not acoustic, news-quality or pixel-effect approval.
- Axe scans include incomplete results, never quietly equating those with passes. Screenshots are viewport observations
  with method notes, not visual-diff acceptance, entire-page coverage or screen-reader/hardware validation.

## Static review notes

- Installed Playwright reloads configuration in every worker and clears output directories *before* global setup.
  The old-label guard runs in the main config before deletion, not again against its own files in replacement workers.
- Native timers must not predate clock installation; `pauseAt` must not target the past. The Screening helper installs
  before navigation and uses a prior clock epoch before pausing at the captured present.
- Textareas/selects use accessible roles and names; only actual file/password/datetime controls use label locators.
  Setup date values use canonical minute precision, avoiding the previous zero-seconds normalization trap.
- Editor diagnostics can validate syntax/types known to VS Code, **not a successful Playwright collection, TypeScript CLI
  build, browser reproduction, API run or seed readiness**. Historical successful claims are not new verification.