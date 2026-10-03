> **Retired classroom harnesses — 2026-09-27.** The page Canary and Round 2 login suites, their Python seeds/hosts/isolation tests, and legacy Playwright/typecheck configs have been removed. They are not supported entrypoints for the current no-login product. Historical commands, source links and C24/C25/C26 counts below describe the old revision only; removed source links are archival references, not files to restore. Original artifacts, logs, paid receipts, source media, prototypes and isolated builds remain untouched.

# Browser acceptance and retired Canary records

## Current no-login workspace suite

- Product and security scope: [core workspace contract](../../docs/CORE_WORKSPACE_20260927.md).
- Standalone host: [tests/browser_acceptance_server.py](../../tests/browser_acceptance_server.py), using only [tests/workspace_fixture.py](../../tests/workspace_fixture.py). Default origin is **http://127.0.0.1:8782**, with dedicated ports **8782–8799**; do not substitute an existing daily service or an old classroom host.
- Browser configuration: [playwright.workspace.config.ts](../playwright.workspace.config.ts); tests and helpers live under [the workspace acceptance spec](workspace/acceptance.workspace.spec.ts). [package.json](../package.json) exposes **`test:e2e` and `typecheck:e2e`** for this suite, with no legacy Canary/Round 2 aliases. Native product contract scripts remain independent.
- The parent/operator builds the matching frontend and explicitly starts the host. The suite requires **`WORKSPACE_MANIFEST` and a fresh `WORKSPACE_RUN_LABEL`**. `WORKSPACE_BASE_URL` defaults to **http://127.0.0.1:8782** and must exactly match the host manifest; set it explicitly for another dedicated port. Use the host's own manifest and never reuse evidence labels. There is no automatic service launcher.
- The host uses fresh system TEMP, explicit settings with dotenv disabled, synthetic inputs and loopback fake providers driving the real pipeline. It does not import historical real-data seeds, account fixtures, paid receipts or real task storage. Synthetic provider results are not live-provider, ASR, hardware or production acceptance.
- Original synthetic seed snapshots are retained; tests edit only new or explicitly duplicated TEMP tasks. Browser runs use one Edge worker, zero automatic retries and no raw authentication dumps, traces or automatic screenshots.
- Generic [asset fixtures](round2/support/assets-fixtures.mjs), [their declarations](round2/support/assets-fixtures.d.mts) and the [bounded media probe](round2/support/media.mjs) remain at their existing paths for reuse; they contain no runnable login scenarios. Product regression tests are not retired based on their historical names.

**Validation status:** this cleanup performs static dependency and disk checks only. No build, full suite, browser host, service or paid call was run here; C26 results are historical, not current acceptance.

## Historical C26 comprehensive audit — 2026-09-26

The latest complete local runs finished: **37/37 page cases, 309646.872ms**, and
**69/69 deep cases, 843674.39ms**, each with **one Edge worker and zero automatic
retries**. The deep suite now has **19 spec files: 58 real local-backend cases
including empty setup + 11 explicitly synthetic enabled-cloud instances**.
These are separate full runs, not aggregated retests or Tencent live acceptance.
See the [C26 report](../../canary_test/CANARY_C26_20260926.md),
[page summary](../../canary_test/artifacts/c26-pages-final-20260926/summary.json),
[latest deep summary](../../canary_test/artifacts/round2/c26-deep-verified-20260926/summary.json)
and [evidence index](../../canary_test/README.md).

**HIGH unresolved runtime risk: the earlier native Python host crash is not fixed
by the latest green run, and this is not production-readiness approval.**
The retained deep **61/69** run lost its Python 3.11.9 host: C25S-02 cleanup failed,
then seven further cases lost connections, not eight independent business bugs.
[Windows event evidence](../../canary_test/artifacts/c26-audit-20260926/native-host-crash.json)
records `c0000005`; the root cause is unknown. The latest run only added
`-X faulthandler` diagnostics, with **no environment upgrade**. The earlier
**68/69** one-microsecond proxy-monitor assertion and **1/1** focused retest also
remain separate; [Round 2 notes](round2/README.md) explain the narrowly bounded
test-only tolerance, with exact source marks unchanged.

- [Full backend](../../canary_test/artifacts/c26-audit-20260926/backend-full.log):
  **1148 tests, 1145 passed / 3 skipped, 623.067s, exit 0**. The **24 new Studio
  failure/auth contracts + 3 real-main-app auth cases are included**, not extra.
  Seven Node suites passed **260 contracts**; `typecheck`, `typecheck:canary` and
  `typecheck:round2` passed. Exact suite counts/logs and environment/audit results
  are in the evidence index.
- [Final isolated build](../dist-canary-c26-final-20260926/ASSET_MANIFEST.sha256):
  **1793 modules, 7.20s, 4 assets**. HTTP hashes matched on
  [page host 8768](../../canary_test/artifacts/c26-audit-20260926/http-final.json)
  and the [latest 8769/8770 hosts](../../canary_test/artifacts/c26-audit-20260926/http-verified.json).
  Shared and retained C25 builds were unchanged; **no deployment or daily
  port-8000 launch** occurred.
- [Six new audit cases](round2/audit.round2.spec.ts) belong to **Round 2**, not the
  37-page count: four navigation/focus/hash/draft boundaries, inactive-child
  multicam ID allocation, and login/result/Studio at 305px usable width ×16/20px.
  The last is a **320−15px content-width model**, not native Windows-scrollbar
  emulation. Its six measurements are separate from the regular layout matrix.
- [Final shutdown check](../../canary_test/artifacts/c26-audit-20260926/shutdown-verified.json):
  **8768/8769/8770 had no listeners and no acceptance Python process remained**.
  Only the page manifest records `stopped`. Both latest deep diagnostic terminals
  exited via Ctrl-C without finalizing their manifests, which still say
  `startup_complete`; their `errors=[]` is **not orderly lifespan-cleanup proof**.
  The [independent deep recheck](../../canary_test/artifacts/c26-audit-20260926/final-deep-source-recheck.json)
  matched 259 source hashes without rewriting manifests. TEMP and failed evidence
  remain retained; no dump or authentication locals were collected.

No new paid requests; the historical Kimi **100-fen reservation remains held,
actual bill unknown**, and Tencent live acceptance is still blocked. The unchanged
113-item classification is **58 partial / 8 metadata / 11 renderer / 36 unsupported**,
not all tools implemented. Original live QC, hardware, accessibility-review and
production limits remain. C25/C24 records below keep their dated counts and states.

## Historical C25 closure — 2026-09-26

This section records C25 only; its results and orderly-shutdown statements do not
replace the C26 results or shutdown limitations above.

The final local acceptance is complete: **37/37 page cases, 322225.95ms**, and
**63/63 deep cases, 855637.336ms**, each with **one Edge worker and zero automatic
retries**. Deep coverage is **52 real local-backend cases + 11 explicitly simulated
enabled-cloud UI instances**, not Tencent live acceptance. These are complete
runs, not sums of filtered passes. Records:
[page summary](../../canary_test/artifacts/p25-pages-final-20260926/summary.json),
[deep summary](../../canary_test/artifacts/round2/p25-deep-final-20260926/summary.json),
[C25 report](../../canary_test/CANARY_20260926.md) and
[capability matrix](../../docs/PROTOTYPE_CAPABILITY_MATRIX_20260925.md).

- [Backend](../../canary_test/artifacts/p25-closure-20260926/backend.log):
  **1121 tests, 1118 passed / 3 skipped, 937.139s, exit 0**. Seven native Node
  suites passed **238 contracts**; application and both E2E typechecks passed.
- The [closure build](../dist-canary-p25-closure-20260926/ASSET_MANIFEST.sha256)
  had all **four assets HTTP-hash-verified on 8768/8769/8770**. Shared frontend
  assets were unchanged; this was **not a deployment**.
- Page case 23 now uses the actual all-sequences group-removal name
  **删除组并取消全部序列归属**. Round 2 adds 21 C25 cases: 9 prototype, 4 assets,
  3 proxy, 4 sequences and 1 cold-link regression. In
  [C25D-01](round2/deep-link.round2.spec.ts), a held real work GET crosses the
  1500ms autosave debounce: the canonical nonempty step-3 draft stays unchanged,
  with **zero browser writes**. Cold work/show links start in read-only history,
  without mounting the creator or autosaving while opening; legitimate creator
  leave still flushes. P25-08 independently passes its sole workbench-POST assertion.
- [Shutdown](../../canary_test/artifacts/p25-closure-20260926/shutdown.json): owned
  **8768/8769/8770 stopped, errors=[]**. Old **8000/8766/8767/8771 were already
  absent at resume**; no claim of preserving old PIDs. All TEMP and failure evidence
  remain retained.

Retained C25 failures, environment/audit results, separate source-hash inventories
and manual accessibility-review limits are indexed in
[../../canary_test/README.md](../../canary_test/README.md). Earlier failed/filtered
runs do not replace these complete results; a readiness guard does not prepare a seed.

## Historical C24 acceptance — 2026-09-24

Everything in this dated section, including listener/PID statements, describes
C24 only. It does not establish current service state or replace C25 evidence.

The complete page run passed **37/37 cases in 12 spec files**: the retained 32 page/
prototype cases plus **SESSION-01–05**. Four session cases are in
[11-session.canary.spec.ts](11-session.canary.spec.ts); SESSION-04 alone is in
[12-startup.canary.spec.ts](12-startup.canary.spec.ts). The companion Round 2
run passed **42/42 cases in 13 spec files**, including its opt-in empty host.
Both final browser runs used one Edge worker and zero retries. These are complete
runs after the final fixes, not sums of filtered or historical passes. The main
record is
[../../canary_test/CANARY_20260924.md](../../canary_test/CANARY_20260924.md).

| Final 2026-09-24 validation | Actual result and evidence |
| --- | --- |
| Page suite | **37/37 passed, 337.462241 seconds**, including SESSION-05 and the login-group assertion. [../../canary_test/artifacts/c24-pages-verified/summary.json](../../canary_test/artifacts/c24-pages-verified/summary.json). |
| Deep suite | **42/42 passed, 290.818192 seconds**: 31 real local-backend cases + 11 explicitly synthetic enabled-cloud instances, **not Tencent live acceptance**. [../../canary_test/artifacts/round2/c24-deep-verified/summary.json](../../canary_test/artifacts/round2/c24-deep-verified/summary.json). |
| Full backend | **845 tests: 843 passed, 2 skipped, 692.988 seconds**. [../../canary_test/artifacts/c24-validation-20260924/backend-final.log](../../canary_test/artifacts/c24-validation-20260924/backend-final.log). |
| Native session transport | **45 passed** against actual client source with controlled responses, not real-server authentication. [../../canary_test/artifacts/c24-validation-20260924/session-final.log](../../canary_test/artifacts/c24-validation-20260924/session-final.log). |
| Isolated host/build regressions | **50 passed (39 page-host + 11 deep-host checks), 4.268 seconds**. These checks are **already included in the 845 backend tests; do not add them again**. [../../canary_test/artifacts/c24-validation-20260924/isolation-final.log](../../canary_test/artifacts/c24-validation-20260924/isolation-final.log). |

### Retained C24 intermediate evidence

| Earlier 2026-09-24 check | Actual result and boundary |
| --- | --- |
| First page baseline | **35 cases: 32 passed, 3 failed; 470.52768 seconds**. The original 32 passed; SESSION-01–03 failed. [../../canary_test/artifacts/c24-pages-baseline/summary.json](../../canary_test/artifacts/c24-pages-baseline/summary.json). |
| Filtered session retest | **SESSION-01–04: 4/4 passed; 30.266574 seconds total**. This predates the SESSION-05 fix and is not a full page run. [../../canary_test/artifacts/c24-session-fixed/summary.json](../../canary_test/artifacts/c24-session-fixed/summary.json). |
| Anonymous-recovery baseline | **SESSION-05 failed**: 19.736 seconds for the case, **23.303932 seconds for the run**. The failure is retained; the fixed real-cookie/403/fresh-anonymous-GET path passed in the final 37/37 run without an automatic POST retry. [../../canary_test/artifacts/c24-expired-session-baseline/summary.json](../../canary_test/artifacts/c24-expired-session-baseline/summary.json). |
| Intermediate page run | **37/37 passed, 321.859393 seconds**, but **before the two final ARIA group fixes**. Its `final` label is not the current acceptance result. [../../canary_test/artifacts/c24-pages-final/summary.json](../../canary_test/artifacts/c24-pages-final/summary.json). |

The first deep run's **41/42** screening-helper failure and the later **R2-09–12
4/4** native-play retest are retained separately in [round2/README.md](round2/README.md).
Neither replaces the complete final deep run above.

### C24 build and instance cleanup — historical

All three final instances served the same completed dedicated build represented by
[../dist-canary-c24-verified/ASSET_MANIFEST.sha256](../dist-canary-c24-verified/ASSET_MANIFEST.sha256),
**not the shared frontend output**. Before the full browser runs, **all four
assets were fetched over actual HTTP and their hashes matched on each of
8768/8769/8770**. Earlier baseline, fixed and intermediate-final build directories
remain retained; none is relabelled as the current build.

The recorded final listener check left user-owned **8766 (PID 35048), 8767
(PID 29540) and 8771 (PID 46760) unchanged**. Only this cycle's owned
**8768/8769/8770** hosts were stopped; all three final manifests record
`serverState=stopped` and `errors=[]`. The dated report preserves C24 provenance;
Round 2's mutable `current`/`empty` slots now refer to later runs:

| Owned host | C24 final run ID | Retained C24 evidence |
| --- | --- | --- |
| Page, 8768 | `golden-mic-canary-b6j2g9b6` | [../../canary_test/artifacts/c24-pages-verified-seed/manifest.json](../../canary_test/artifacts/c24-pages-verified-seed/manifest.json) |
| Deep seeded, 8769 | `golden-mic-canary-round2-qicw5u5c` | [../../canary_test/CANARY_20260924.md](../../canary_test/CANARY_20260924.md) |
| Deep empty, 8770 | `golden-mic-canary-round2-bjr7pjox` | [../../canary_test/CANARY_20260924.md](../../canary_test/CANARY_20260924.md) |

The final integrity recheck matched **564 page-original files**, separately from
the deep host's **259 allowlisted original-source files**. These inventories are
not additive. TEMP data, source media and all intermediate evidence remain
preserved; the results and shutdown state above are the recorded final run.

## Historical prototype-refactor acceptance — 2026-09-23

**32/32 passed in 288.061 seconds** on the then-final refactored application and a
fresh real-data seed: [summary](../../canary_test/artifacts/prototype-refactor-final/summary.json).
The original 29 cases were extended by three [prototype-contract cases](10-prototype.canary.spec.ts):
title/assignment instructions, shortage confirmation, and independently scoped roster search.
The deep suite separately passed **30/30**, and backend **517 tests (515 passed, two skipped)**.
See [the dated acceptance report](../../docs/PROTOTYPE_REFACTOR_20260923.md) for retained failures,
deletion/title fixes, cleanup and capability limits. The later 2026-09-23 cloud
**32-page / 42-deep / 795-backend** results are recorded separately in
[round2/README.md](round2/README.md); neither set validates later C25/C26 changes.
The design reference is
[the current prototype](../../金话筒新闻视频生成/金话筒%20·%20原型.dc.html);
tests never load its demo database, identities, progress or success responses.

## Historical suite scope (retired)

The former scope comprised twelve page-suite spec files (37 cases), nineteen Round 2 spec files
(58 real local-backend cases including empty setup + 11 synthetic cloud instances),
both support trees/configs, six offline Python seed/host modules, the frontend
build-manifest writer and seven native Node contract runners.
The five classroom seed/host modules and their two isolation tests are now removed;
the former manual host is now the independent no-login workspace host above.
The build-manifest writer, native product tests and generic asset/media helpers
remain. No current suite imports the retired workflow host or real-artifact importer.

## Dependencies and host choice

**Historical instructions only; the removed hosts/configs and aliases below must not be run. Use the current workspace entrypoints above.**

- Frontend: existing TypeScript 5.9.3, `@playwright/test` 1.63.0,
  `@axe-core/playwright` 4.13.0, compatible Node.js and installed Microsoft Edge.
  [../package.json](../package.json) and its lock declare the dependencies and
  `test:canary`, `test:round2`, `typecheck:canary`, `typecheck:round2` scripts.
  Optional **`npm run test:session`** runs
  [../scripts/test-classroom-session.mjs](../scripts/test-classroom-session.mjs)
  with Node's native test runner and controlled transport responses, without a
  browser/server/provider. It does not replace real browser/backend regressions.
  The other native scripts are `test:timeline`, `test:preferences`,
  `test:compositions`, `test:assets`, `test:proxy` and `test:sequences`.
  `build` typechecks and writes the shared build manifest automatically; use the
  separate-output reproduction below when existing services must keep that build.
- Python: project Python 3.11–3.12 environment and
  [../../requirements.txt](../../requirements.txt), including FastAPI/Starlette,
  Uvicorn, Pydantic Settings, httpx, aiohttp (via project dependencies) and
  websockets. **Both FFmpeg and ffprobe must be on PATH in every host terminal
  AND the Playwright terminal**: browser-side downloaded-output probes also spawn
  ffprobe. A new terminal does not inherit another terminal's temporary PATH.
- All hosts use fresh TEMP, explicit settings with dotenv disabled and no
  inherited credentials, loopback-only listeners, disabled access logs and
  HTTP/WebSocket transport blocking. Build and launch remain operator-owned.

On Windows, resolve the current installation in **each** host/runner PowerShell
terminal; do not pin an obsolete WinGet 9.0.1 directory. This operator example
uses an available executable or dynamically finds the current Gyan package,
then fails if either tool is unavailable:

```powershell
$ffmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
$ffmpegPath = if ($ffmpeg) { $ffmpeg.Source } else {
  Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages" -Directory -Filter 'Gyan.FFmpeg*' -ErrorAction SilentlyContinue |
    ForEach-Object { Get-ChildItem $_.FullName -File -Filter ffmpeg.exe -Recurse -ErrorAction SilentlyContinue } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $ffmpegPath) { throw 'Install FFmpeg and ffprobe before starting a host or suite.' }
$env:Path = (Split-Path -Parent $ffmpegPath) + ';' + $env:Path
Get-Command ffmpeg.exe, ffprobe.exe -ErrorAction Stop | Select-Object Name, Source
```

| Host | Purpose | Previous real live artifacts required? |
| --- | --- | --- |
| Former manual host, 8765 (replaced; see current workspace entrypoints above) | Historical manual acceptance used empty setup or explicitly synthetic local film/accounts. Its old `--fixtures` interface is retired and is not the current workspace host interface. | **No.** Historical fixture mode used `_Scenario` and a workbench fixture; synthetic QC was not news/media-quality approval. |
| [../../tests/canary_server.py](../../tests/canary_server.py), 8766 legacy default; explicit 8767 or 8768 | Current 37-case page/prototype/session suite; [archive seed](../../tests/canary_seed.py). Nondefault ports require a dedicated build and an explicit unused seed slot. | Not the prior live TEMP run, but **both historical sample1 archives**, 48 + 8 real input clips and both DOCX manuscripts are required. |
| [../../tests/canary_workflow_server.py](../../tests/canary_workflow_server.py), historical 8768 | Restricted one-copy offline workflow host/importer, not launched by a suite. Cannot share 8768 with the current page host or replace its seed. | **Yes**, pinned prior live functional artifacts and safe live evidence. |
| [../../tests/canary_round2_server.py](../../tests/canary_round2_server.py), 8769 | [Round 2 real seed](../../tests/canary_round2_seed.py): actual local deletion and two duplicates; three independent works. | **Yes**, same pinned prior live source; missing/mismatched evidence fails closed, never triggers regeneration. |
| Same Round 2 host with `--empty`, 8770 | Optional once-only initial setup. | **No.** No source imports, fixture accounts or jobs are seeded. |

The pinned source is TEMP run `golden-mic-canary-live-2hb49w7x`, task
`22d4b7aa53a34adfbdfda4f7e887425a`. The importer requires the three safe live
observations (`manifest`, `ui-submission`, `current-state`), original sample1
input hashes, the upload manifest and allowlisted functional/media files. It
does **not** read original authentication state, dotenv or the original database.
Their presence/content was verified by the refactor's actual seeded run; shutdown
also verified 259 allowed original file hashes unchanged. Future missing evidence
must not trigger a paid live submission to replace a prerequisite.

The original archive names remain `improved_pipeline_20260724_200612` and
`enhanced_pipeline_20260804_151229` under the sample1 evaluation-work directory.
No compatibility repair, invented QC, replacement media or readiness override
is allowed. Round 2's latest verified 1 MiB Range-chunk GIF helper is preserved.

The manifest writer [../scripts/write-manifest.mjs](../scripts/write-manifest.mjs)
accepts optional **`--out-dir dist-canary-<safe-label>`**: the argument must be a
leaf name resolved against the frontend root, or that directory's canonical
absolute path, not an arbitrary relative path. Without the flag it keeps
the shared-build behavior; it does not infer an alternate Vite output directory.
For an explicit isolated build it inventories **every file except the checksum
manifest itself**, using SHA-256 and canonical relative POSIX names. Linked,
noncanonical or case-aliased paths are rejected. The hosts validate the complete
existing build; they never build, repair or overwrite it.

During the historical refactor, one verified prototype locator needed adjustment: the recommended-example
button is now outside the sidebar's `nav`, so the original helper scopes the
whole sidebar. Semantic labels and all substantive assertions remain intact.
Other deviations at that restoration stage were safety/static-only: the
manual host shares the comprehensive offline guards and does not log fixture
credentials; the original suite rejects reused evidence, blocks unintended
generation/WebSockets, redacts errors in the worker and uses only its safe
reporter. Python annotations/narrowing retain the existing fail-closed checks.

## Recorded historical results — 2026-09-22 (not a refactor pass)

The full **release-candidate run passed 29/29**. The later **gold-track run
passed 1/1, case 22 only**, after the Studio gold-clip contrast patch.
Evidence: [../../canary_test/artifacts/release-candidate/summary.json](../../canary_test/artifacts/release-candidate/summary.json)
and [../../canary_test/artifacts/gold-track/summary.json](../../canary_test/artifacts/gold-track/summary.json).
Earlier failures remain in the retained canary artifacts;
run labels such as final or verified are not acceptance claims.

The separate real-media workflow xtj0kpzu passed all **21 functional steps**,
reported `missingCoverage=[]`, and restored v0 as v2. Its overall outcome remains
**failed** because the saved Studio text/mask project exposed gold-clip label
contrast failures on **two targets**. The patch and case-22 retest do not rewrite
that original result. A subsequent read-only scan of the SAME three-clip project
has zero Axe violations and confirms the r1 project is unchanged; the screenshot
and full scan are retained in the workflow run directory. That historical backend
run had 450 tests, OK with two skips; these counts are separate from browser tests.

## Scope and ownership

The suite has **37 cases**: original cases 01–29, prototype cases 30–32 and
SESSION-01–05. It does not start a server, build the application, install
dependencies, call providers, or modify application code.

The operator owns the matching frontend build and the isolated
[../../tests/canary_server.py](../../tests/canary_server.py) process. Only the exact
origins **http://127.0.0.1:8766**, **http://127.0.0.1:8767** and
**http://127.0.0.1:8768** are allowed. `CANARY_BASE_URL` defaults to the first;
trailing slashes, whitespace, localhost aliases, paths, credentials, queries and
other ports are rejected, not normalized. A nondefault origin is not permission
to use an already-running service on that port.

### Origin, seed and build binding

| Setting | Contract |
| --- | --- |
| Host `--port` | Exactly 8766, 8767 or 8768; default 8766. `--live` is always refused. |
| Host `--frontend-dir` | Existing canonical repository child `frontend/dist-canary-<safe-label>` (repository-relative or canonical absolute path). Required for 8767/8768. Shared output, traversal, links/junctions and arbitrary external directories are refused. |
| Host `CANARY_ARTIFACT_DIR` | For any dedicated build/nondefault port, an explicit unused direct child `canary_test/artifacts/<seed-label>`. A nonempty directory or retained claim is never reused, even after failure. Legacy 8766 without a dedicated build defaults to `current` but also refuses nonempty seed evidence. |
| Runner `CANARY_SEED_LABEL` | Names that same seed directory, not a path. Default `current` is permitted only at 8766; 8767/8768 require an explicit nonreserved label. |
| Runner `CANARY_RUN_LABEL` | A different, unused evidence label; default `baseline`. It must differ from the seed label **case-insensitively**. |

Safe labels are **1–64 ASCII letters/digits/underscores/hyphens, starting
alphanumeric**. Reserved `current`, `live`, `workflow`, `round2`, `history` and
Windows device names are rejected, apart from the explicit legacy `current`
seed exception. The host and runner check canonical paths without following
links/junctions; ordinary build/evidence files must not be hard-link aliases.

The page host exclusively **binds the requested loopback socket before claiming
evidence or creating TEMP data**, then seeds before that socket listens. A busy
port fails closed: no connecting to, adopting, killing or retrying its owner.
The durable one-use seed claim and failed evidence remain for inspection, never
removed to make a retry possible. Choose a new seed and run label instead.

New manifests record the **actual `baseURL`, `frontend.directory`, complete
`frontend.assetHashes` and `serverState`**. Dedicated seed validation requires
`startup_complete`, an empty `errors` array and the matching isolated local build;
selecting a non-`current` seed on 8766 also requires that dedicated build.
Legacy 8766/`current` manifests may omit the newly introduced fields. A present
state must still be `startup_complete`. Build validation uses the real readiness
validator against the selected build, not a made-up ready result. Provider
availability remains **`not_ready`**. These local hashes alone do not prove HTTP
asset equality, equality to current source or a current pipeline generation.

The runtime seed manifest must exist in the selected seed directory. Its schema,
offline status, TEMP root, account/class IDs,
all 48 + 8 original files, sizes, DOCX/text hashes, derived JPG/WAV hashes and
historical archive declarations are validated before test use. The two full
selection cases also SHA-256-check every original video. Missing prerequisites
fail explicitly; they do not skip tests or create replacement samples.

Wait for **`startup_complete` before launching the suite**. Input hashing includes
all 48 + 8 videos (sample2 alone is approximately **919 MB**), followed by historical
archive copying; OneDrive hydration/I/O can exceed the runner's readiness window.
The readiness guard neither prepares the seed nor makes `inputs`/`seeding` usable.
Quiet output is not evidence of a deadlock; do not replace data, relax readiness
or rerun generation to bypass these prerequisites.

### Isolated build and page-suite reproduction

**Retired reproduction procedure, retained only to explain historical evidence. Do not execute it against the current product or reuse its isolated outputs.**

These are **operator instructions**, not commands executed by this documentation
update. Use the project interpreter and already-installed dependencies, with
FFmpeg/ffprobe on PATH in **both host and runner terminals**. Leave existing
services and the shared build unchanged. Build task targets and seed/result
labels are one-use: never rerun a previously used task into its existing output
or delete evidence to reuse a label; choose fresh names.

1. From the frontend directory, run `npm.cmd run typecheck`,
  `npm.cmd run typecheck:canary` and `npm.cmd run typecheck:round2`; stop on any
  failure. Optionally run `npm.cmd run test:session` for the native transport
  regression before browser testing.
2. Build into a **new** directory rather than invoking the shared-output `build`
  script. This example refuses to overwrite an earlier build:

  ```powershell
  $build = 'dist-canary-c26-repro'
  if (Test-Path -LiteralPath $build) { throw 'Choose a new isolated build name.' }
  npm.cmd exec -- vite build --outDir $build
  if ($LASTEXITCODE -ne 0) { throw 'Isolated Vite build failed.' }
  node scripts/write-manifest.mjs --out-dir $build
  if ($LASTEXITCODE -ne 0) { throw 'Isolated build manifest failed.' }
  ```

  The recorded C26 final selection is
  [../dist-canary-c26-final-20260926/ASSET_MANIFEST.sha256](../dist-canary-c26-final-20260926/ASSET_MANIFEST.sha256);
  the retained C25 final selection is
  [../dist-canary-p25-closure-20260926/ASSET_MANIFEST.sha256](../dist-canary-p25-closure-20260926/ASSET_MANIFEST.sha256).
  Do not rebuild either retained directory or rerun its one-use build task. The writer and
  every host for the run must select the same completed, unchanged output.
3. In a separate terminal at the repository root, choose an unused seed label,
  for example set `$env:CANARY_ARTIFACT_DIR = 'canary_test/artifacts/c26-repro-seed'`
  in PowerShell, and launch
  the selected project interpreter with
  `-m tests.canary_server --port 8768 --frontend-dir frontend/dist-canary-c26-repro`.
  Substitute the chosen build name if different, still with an
  **explicit new** artifact directory. Stop on an occupied port; do not disturb
  another service or remove old evidence.
4. In the frontend runner PowerShell terminal set
  `$env:CANARY_BASE_URL = 'http://127.0.0.1:8768'`,
  `$env:CANARY_SEED_LABEL = 'c26-repro-seed'` and a distinct unused
  `$env:CANARY_RUN_LABEL = 'c26-repro-pages'`. Confirm both media executables in
  this terminal. Wait for the selected manifest's `startup_complete`, empty
  `errors` and that host's **live** endpoint **before starting Playwright**;
  global setup's up-to-120-second prerequisite/liveness guard never prepares
  a seed or starts a host.
5. Run `npm.cmd run test:canary` without filtering for the complete **37-case**
  result. Check the actual summary, retained failures and separately recorded
  served-asset/build evidence; do not aggregate filtered passes. Stop only the
  owned host after its run and retain TEMP/evidence. Verify ports/processes and
  whether shutdown finalization actually ran; stale manifests are not proof of
  orderly cleanup. Independently recheck source hashes rather than rewriting a
  manifest to make it appear stopped.

For a deliberately filtered Windows retest whose regex contains `|`, bypass npm's
cmd parsing and invoke Node directly, for example
`node .\node_modules\@playwright\test\cli.js test --config playwright.canary.config.ts --grep 'SESSION-01|SESSION-02'`.
Use another unused result label; a filtered result is not the full acceptance.
For the companion full deep run, `ROUND2_EMPTY_SETUP=1` is required; leave it
**unset**, never `0`, when intentionally omitting that case.

Select the dedicated [../playwright.canary.config.ts](../playwright.canary.config.ts)
when the outer runner is ready. It uses installed **Microsoft Edge**, one worker,
zero retries, fresh contexts, and continues after failures. Do not override these
settings, enable traces, or enable context reuse. Edge is intentional: a
codec-deficient headless browser must not be mistaken for a broken MP4 input.

## Coverage

| Cases | Independent scenarios | Implementation |
| --- | --- | --- |
| 01–02 | Actual teacher/student login forms, empty/invalid validation, logout, storage keys; anonymous/Bob/Carol private-route denial | [01-auth.canary.spec.ts](01-auth.canary.spec.ts) |
| 03–06 | Actual teacher roster/ID-based statistics, filters, class switch; scoped class create/edit and duplicate/date rejection; roster edit/PIN reset; task create/edit/end/history | [02-teacher.canary.spec.ts](02-teacher.canary.spec.ts) |
| 07–11 | Exact DOCX-extracted manuscripts; all 48 news clips and 8 interviews; real thumbnails/duration; derived image/audio; invalid extension/duplicate/trim; draft reload/reselection/save retry; effect and submission gates | [03-create.canary.spec.ts](03-create.canary.spec.ts) |
| 12–15 | Real personal history/sorting, mobile drawer/focus, empty wall, empty recommendations and explicit isolated legacy entry | [04-browsing.canary.spec.ts](04-browsing.canary.spec.ts) |
| 16–19 | Decoded historical video frames and actual playback time, sentence keyboard/seek, real report/version data, context failure/retry, discard unsent edits, QC/export denials, teacher feedback/owner acknowledgement | [05-results.canary.spec.ts](05-results.canary.spec.ts) |
| 20–21 | Held sample2 queue, author-name delete protection, actual release to the documented local running harness, cancel; known initial failure, revision rejection, original-source retry and cancel | [06-processing.canary.spec.ts](06-processing.canary.spec.ts) |
| 22–25 | Actual source-library playback and two-second trim, project save/reload, tags/ratings, all-sequences group removal, masks, keyframes, lock enforcement, conflict/undo/redo/import validation and honest render/export refusal | [07-studio.canary.spec.ts](07-studio.canary.spec.ts) |
| 26–28 | Held A→B→A autosave with real writes; initial draft GET 503 without empty overwrite; context GET 503 recovery without stale parent alert | [08-recovery.canary.spec.ts](08-recovery.canary.spec.ts) |
| 29 | Synchronous visual checkbox state while POST is held; real 200 persistence; injected 503 rollback; unchanged historical QC and closed gate | [09-checks.canary.spec.ts](09-checks.canary.spec.ts) |
| 30–32 | Prototype title/instructions, shortage confirmation through both step controls, independent roster search and ID-based assignment scope | [10-prototype.canary.spec.ts](10-prototype.canary.spec.ts) |
| SESSION-01 | A second tab only reads the same Alice cookie; no native identity broadcast or first-tab lock | [11-session.canary.spec.ts](11-session.canary.spec.ts) |
| SESSION-02 | Real peer logout/login locks the old tab; its read-only recovery cannot adopt or log out Teacher, broadcast back or alter the teacher-owned draft | [11-session.canary.spec.ts](11-session.canary.spec.ts) |
| SESSION-03 | Real same-actor cookie rotation causes actual logout 403; held/failed logout is not labelled successful, refresh does not replay POST, explicit retry reaches 200 | [11-session.canary.spec.ts](11-session.canary.spec.ts) |
| SESSION-04 | Hold only the first real availability response; after acknowledged logout and fresh anonymous startup, late delivery or actual abort must not restore private DOM | [12-startup.canary.spec.ts](12-startup.canary.spec.ts) |
| SESSION-05 | Actual server invalidation leaves the old page's logout at 403; a fresh real anonymous GET restores login without a different-actor lock or automatic POST replay | [11-session.canary.spec.ts](11-session.canary.spec.ts) |

Companion **C26-NAV-01–04, C26-MC-01 and C26-LAYOUT-01** are six real local
**deep** cases in [round2/audit.round2.spec.ts](round2/audit.round2.spec.ts).
Their [coverage and adapter boundaries](round2/README.md#c26-additions--6-real-local-cases)
include real Back/current-history cancellation, hash-preserving skip focus,
intentional new-blank saves, global inactive-child IDs and six 305px samples;
they do not inflate the page-suite count.

The app has a manuscript **textarea**, not a DOCX importer. Tests insert the
seed's exact ZIP/XML-extracted DOCX text there; DOCX selected as visual media is
correctly rejected. Choosing local files is not a server upload. No test submits
new generation while availability is `not_ready`.

The effect/audio case uses clearly identified test-only declaration inputs in
a disposable draft. It does **not** assert real subjects consented; the “nobody”
checkbox stays false, no work is submitted/approved/published, and the draft is
cleared at cleanup. The real-source eight-second WAV is an audio-control probe,
not a verified complete narration.

Session behavior follows
[../../docs/CLASSROOM_API.md](../../docs/CLASSROOM_API.md): generation-fenced
observations, per-caller cancellation, broadcasts only after successful auth
commits, actor-bound logout and read-only peer recovery. These are frontend
coordination rules, not changed server authentication/CSRF permissions.
SESSION-04 is a startup/logout return-boundary regression, **not a claimed new
baseline failure** or proof of the separate transport supersession races.

## Layout, accessibility and evidence

[support/survey.ts](support/survey.ts) surveys each main page at
**1440×1000, 768×1024, 390×844 and 320×800**, each at 16px and **20px via the
existing large-font toggle**. Related error/secondary states get an additional
current-viewport scan. Every scan waits for the page's semantic readiness and
fonts/layout frames, not arbitrary sleeps or perpetual network idle.

The survey records document overflow plus separately identified nested scrollers,
screenshots, and full Axe results for `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`,
`wcag22aa`. No rules or elements are excluded. It collects every viewport's
findings before asserting that violations/overflow/runtime errors are empty.
There are no expected-failure annotations or accepted-bug baselines.
Incomplete Axe results are preserved for human review, not called WCAG compliance.

C26's [final browser record](../../canary_test/artifacts/c26-audit-20260926/browser-integrity-verified.json)
contains **163 axe reports / 0 violations**, but **1764 contrast + 45 caption
incomplete node occurrences**, not unique defects or WCAG approval. The regular
matrix is **31 reports / 150 samples / 0 horizontal overflow**; the new deep
305px model supplies **six separate samples**. The distinct native integrated-
browser observation was a 320px window with 305px client width, 320px body width
and 15px overflow; `min-width:0` reduced scroll width to 305px. A headless 305px
viewport models that available width, **not OS scrollbar behavior**. The latest
**106 case observations / 0 problems or errors** do not erase the prior host crash.
The same record verifies **564 page originals**, 84 final source/dependency
bindings, retained builds and the paid receipt unchanged; the deep 259-file
inventory is separate and must not be added.

The final product fixes give `ClassroomLogin`'s **登录身份** and
`ResultWorkbench`'s **句子选择** containers actual **`role=group`** semantics.
The page login test and deep R2-23 assert those real named groups; the final axe
reports no longer contain the two invalid-label findings. Remaining gradient
`color-contrast` and `video-caption` incomplete findings still require manual
review. Removing those ARIA findings is **not zero outstanding accessibility work
or complete WCAG conformance**.

`CANARY_RUN_LABEL` selects the run directory below the repository's canary
artifacts directory, default **baseline**, distinct from `CANARY_SEED_LABEL`.
Reserved seed/evidence labels
**current**, **live**, **workflow**, **round2**, **history**, and Windows device
names are rejected. A **nonempty reused label fails before Playwright clears its
output directory**; replacement workers may use this run's own output. The run gets:

- A redacted run summary containing the selected origin/seed/run labels, planned cases (37 for a full run), statuses, durations,
  assertions, attachment paths and explicit untested boundaries. No raw list reporter.
- Per-case JSON/text attachments: real receipts/provenance, layout/Axe results,
  all collected findings, and sanitized browser/HTTP logs (paths/statuses only).
- Viewport/frame evidence and **masked failure screenshots**, including failures
  discovered in fixture teardown. Automatic unmasked screenshots are disabled.

**Raw authentication data is never saved as evidence**: no passwords/PINs,
Cookie/CSRF/task tokens, login bodies, raw request headers or authentication-state
exports. Ordinary HTTP logs contain paths/statuses only. **Selected test-only
business mutation bodies and receipts are saved after redaction**; this is not a
raw network capture. Traces, browser video recordings, HAR, storage-state files
and Git diffs are disabled. Password/PIN inputs and sensitive roster areas are
masked. CSRF stays in client memory; session cookies are browser-managed and
HttpOnly, never exported. The existing minimal opaque cloud-cost receipt is not
authentication state; its separate storage/evidence exception remains as documented
in [round2/README.md](round2/README.md). The pinned Playwright version's
automatic AI page snapshot is disabled; residual errors are redacted in the
worker that knows dynamic secrets. Reporter evidence failures fail the run.
Unexpected external browser HTTP/WebSockets and new generation/pipeline-edit
submissions are blocked and recorded as failing findings, not silently ignored.

## Isolation and limitations

- No serial suite or login shared **between tests**. Failure of one test does not
  skip the remaining pages. Each test has an independent browser context;
  additional personas normally use additional independent contexts and same-origin
  cookie/CSRF APIs. SESSION-01/02 deliberately open two tabs inside their own fresh
  context to exercise the real shared cookie and native BroadcastChannel; no
  cookies/auth state are copied. Different ports alone do not isolate cookies
  (see the already-recorded research in
  [../../canary_test/CANARY_20260924.md](../../canary_test/CANARY_20260924.md)).
- Real API mutations affect **only the validated TEMP seed**. Extra classes are
  uniquely named; assignment/roster edits never change Alice/Bob/Carol identities.
  Every creation case clears Alice's disposable server draft after closing its
  editor to stop autosave, including on failure; fresh contexts alone do not
  isolate server drafts. Failure screenshots are captured before cleanup.
  Studio tests restore the previous project while retaining real revision/audit
  history. Cleanup errors are attached and fail the case.
- **Use a fresh server seed and unused label for each complete run/retest.**
  Cases 20–21 delete their own held/failed fixture tasks. Teacher feedback and
  newly created classes remain as auditable TEMP state; source datasets and
  historical media/QC remain unchanged. Reused evidence is never overwritten.
- Fault injection is explicit and endpoint-scoped: draft-save/context-read 503s,
  initial draft-read 503 in case 27, and a held manual-check write followed by
  a non-forwarded 503 in case 29. Case 26 holds and forwards the real B write.
  Recovery reaches the real server; no successful API, media, availability,
  report, QC, gate or provider response is fabricated. Case 29 proves an
  already-closed historical gate stays closed, not every eligible-gate busy state.
- Session fault adapters only hold delivery of an unchanged real logout response
  or the first real availability response. Cookie rotation/invalidation and the
  resulting 403/anonymous refresh are real local operations; no fake auth success,
  injected identity message or automatic protected-write replay is used. Owned
  drafts are restored through separate cleanup authority after closing editors,
  never by logging the changed shared context back in as its former actor.
- Historical zero-blocker QC does **not** establish current publication/export
  eligibility. The tests preserve missing newer metrics and all blockers, and
  verify `can_export=false` / actual HTTP 409 instead of signing fake approvals.

## Historical separate real-media workflow — port 8768

The standalone offline UI driver is intentionally not part of this restoration.
The required [workflow host](../../tests/canary_workflow_server.py) imports one
allowlisted copy of the completed live task into a fictional TEMP classroom,
preserving original four-blocker QC. It allows no model calls, no original live
service/database access and no synthetic replacement media or fabricated
approvals. It is also the source importer used by Round 2.

The historical xtj0kpzu workflow recorded real UI deletion of 14 sentences,
keeping original sentence ID 3 as v1. That **one-sentence exercise**, not the
original full film, measured zero QC blockers/warnings. Test-only declarations,
checks, reflection and teacher confirmation allowed **author** publication in
the isolated classroom. Peer playback/comments, teacher recommendation/retention,
PNG-at-EOF and MP4 exports, and a two-second Studio text/mask render with real
decoding passed. The original sentence audio hash stayed unchanged.

Restoring v0 as new v2 returned the original four blockers, revoked approval and
export eligibility, and left the copy unpublished. All 21 steps passed, including
integrity/cleanup; `missingCoverage=[]` corresponds to `coverage.notPassed=[]`.
The saved result still records the two contrast targets and overall failure.
Earlier checkbox and teacher-recommendation failures remain in their own reports.

## Remaining boundaries

- The sole authorized live run uploaded all 48 sample1 clips and completed ten
  stages, but its original **four blockers and eleven warnings remain**. No
  automatic live rerun, host restart, budget reset or cloud re-synthesis is allowed.
- C26, like the preserved C25 cycle, made **no new paid requests**. The prior single Kimi smoke's
  **100 fen (CNY 1) remains `held_for_reconciliation`**, unchanged; actual billing
  is still unknown. There is no Tencent live acceptance or permission to retry.
- The historical local TTS replay passed one test: rate outliers **7→0**, measured
  unit loudness spread **2.9→0.6 LU**. This is local audio replay, not a final
  full-video rerender or a new QC approval.
- Earlier 432/450/487/517/795/845/1121 backend-test counts and previous canary/audit artifacts
  are historical, not cumulative and not substitutes for current C26 validation.
- Original suite: one unpublished historical seed cannot prove successful
  publication/export or positive inter-work countdown. [Round 2](round2/README.md)
  separately covers real short-media exports, setup and two-film screening.
- Microphone hardware, OS printing, cloud text/shot changes and re-synthesis,
  all Studio effects, and manual visual/keyboard/screen-reader acceptance are
  not fully covered. Axe `incomplete` remains a review item; screenshots are
  evidence, not approval assertions.
- Completion is for the documented **canary scope**, not every prototype feature.
  The 113-item classification is **58 partial / 8 metadata / 11 renderer /
  36 unsupported**, not 77 complete tools. Partial entries retain missing
  subfeatures; local advanced editing/HDR/full multicam/tracking gaps are not
  just missing cloud credentials. See the current capability matrix above.
- **HIGH unresolved native runtime risk remains despite the latest green run**;
  diagnostic-only `-X faulthandler` is not a root-cause fix or production approval.

Static syntax/editor checks do not execute tests or establish seed readiness.
The current full results are **37/37 page, 69/69 deep and 1148 backend
tests (1145 passed, 3 skipped)** above, not combined partial or historical passes.
Both suites require rebuilt assets and fresh operator-owned hosts for future
source changes. C26's final check found no owned 8768/8769/8770 listener or acceptance
Python process; only the page manifest records orderly shutdown, not the two deep
manifests. C25's stopped hosts and old 8000/8766/8767/8771 already absent at that
resume remain historical, as do C24/2026-09-23 listener/PID statements.
Preserved manifests are not proof of a live server or completed finalization. Retain
failures as well as successes; do not touch existing services or deploy implicitly.