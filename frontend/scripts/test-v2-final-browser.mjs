import assert from 'node:assert/strict';
import { existsSync, readFileSync, mkdtempSync, writeFileSync, lstatSync, mkdirSync, readdirSync, rmdirSync } from 'node:fs';
import { dirname, resolve, relative, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import { createRequire } from 'node:module';
import { createHash, randomBytes } from 'node:crypto';
import nodeTest from 'node:test';
import ts from 'typescript';
import { validateOrigin, startDownloadTransport, responseHeaders, inspectOwnedFile, cleanupDownload } from './final-download-fixture.mjs';

// Independent, NOT an acceptance-host exception. No existing test/helper is
// imported; importing this file registers NOTHING, even with the browser opt-in.
// Default: units only, no skipped browser test. Explicit GM_FINAL_BROWSER=1:
// full Workspace -> ResultWorkbench TSX, real React/Edge, synthetic HTTP only.
// No server TTL/deletion/export pipeline/auth correctness claim is possible here.
const direct = !!process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url);
const test = direct ? nodeTest : () => {};
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const UNIT_ORIGIN = 'http://127.0.0.1:49152';
const A = 'final-fixture-a', B = 'final-fixture-b', JOB = 'e'.repeat(32);
const EPOCH = '2026-10-02T12:00:00.000Z';
const SRT = Buffer.from('1\r\n00:00:00,000 --> 00:00:01,000\r\nSynthetic fixture subtitle.\r\n\r\n', 'utf8');
const sha = bytes => createHash('sha256').update(bytes).digest('hex');
const check = (condition, code) => { if (!condition) throw Error(code); };

/** Exact URL/method/capability binding. No wildcard network pass-through. */
export function classify(url, method, headers, caps, origin) {
  if (!validateOrigin(origin)) return null;
  let u; try { u = new URL(url); } catch { return null; }
  const entries = Object.entries(headers), h = Object.fromEntries(entries.map(([k, v]) => [k.toLowerCase(), v]));
  if (Object.keys(h).length !== entries.length || u.origin !== origin || u.username || u.password || u.hash || h.authorization || h.cookie
    || h.host && h.host !== new URL(origin).host || h['proxy-authorization'] || h['x-forwarded-host']) return null;
  if (method === 'GET' && !u.search && !h['x-task-token'] && !h['x-csrf-token']) {
    if (['/tasks/' + A, '/tasks/' + B].includes(u.pathname)) return 'document';
    if (['/api/config/workspace', '/api/config/limits', '/api/config/availability', '/api/session'].includes(u.pathname)) return 'public';
  }
  for (const id of [A, B]) {
    const base = '/api/tasks/' + id;
    if (method === 'GET' && [base + '/video', base + '/poster', base + '/exports/' + JOB + '/file'].includes(u.pathname)) {
      const entries = [...u.searchParams];
      if (entries.length !== 2 || u.searchParams.getAll('token').length !== 1 || u.searchParams.getAll('revision').length !== 1
        || u.searchParams.get('token') !== caps[id] || !['2', '3'].includes(u.searchParams.get('revision'))
        || h['x-task-token'] || h['x-csrf-token'] || h.range && !/^bytes=0-$/.test(h.range)) return null;
      if (u.pathname.endsWith('/file')) return id === A && u.searchParams.get('revision') === '2' && !h.range ? 'download' : null;
      return 'unavailable-media';
    }
    if (u.search || h['x-task-token'] !== caps[id] || h['x-csrf-token']) continue;
    if (method === 'GET' && [base, base + '/workbench/context', base + '/checks'].includes(u.pathname)) return 'read';
    if (id === A && method === 'POST' && u.pathname === base + '/exports') return 'export';
    if (id === A && method === 'DELETE' && u.pathname === base) return 'delete';
  }
  return null;
}

/** Complete source modules, not extracted callbacks or synthetic React hooks.
 * Only import.meta.env is supplied as an explicit test/build constant. CSS
 * modules retain local names; CSS is inlined, NOT a production Vite build. */
export function collect() {
  const factories = {}, paths = {}, hashes = {}, css = new Map();
  function read(file) {
    const key = relative(root, file).replaceAll('\\', '/');
    check(key.startsWith('src/') || key === '../backend/mode_rules.json', 'source-boundary');
    const value = readFileSync(file, 'utf8'); hashes[key] = sha(value); return value;
  }
  function style(file) {
    if (css.has(file)) return;
    const text = read(file); css.set(file, '');
    const inlined = text.replace(/@import\s+["']([^"']+)["'];/g, (_, path) => {
      check(path.startsWith('.'), 'css-import-boundary'); style(resolve(dirname(file), path)); return '';
    });
    check(!/url\s*\(/i.test(inlined), 'no-css-network'); css.set(file, inlined);
  }
  function module(file) {
    const key = relative(root, file).replaceAll('\\', '/');
    if (Object.hasOwn(factories, key)) return key;
    const text = read(file); factories[key] = ''; paths[key] = {};
    if (file.endsWith('.json')) { factories[key] = `module.exports=${JSON.stringify(JSON.parse(text))};`; return key; }
    const tree = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    for (const n of tree.statements) if (ts.isImportDeclaration(n) && !n.importClause?.isTypeOnly) {
      const name = n.moduleSpecifier.text;
      if (name === 'react') continue;
      check(name.startsWith('.'), 'dependency-boundary');
      const base = resolve(dirname(file), name);
      if (name.endsWith('.css')) { style(base); continue; }
      const target = [base, base + '.ts', base + '.tsx', base + '.json'].find(p => existsSync(p) && lstatSync(p).isFile());
      check(!!target, 'dependency-missing'); paths[key][name] = module(target);
    }
    const compiled = ts.transpileModule(text, { fileName: file, reportDiagnostics: true,
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React, esModuleInterop: true },
      transformers: { before: [context => {
        const visit = n => ts.isPropertyAccessExpression(n) && ts.isMetaProperty(n.expression) && n.name.text === 'env'
          ? ts.factory.createObjectLiteralExpression() : ts.visitEachChild(n, visit, context);
        return n => ts.visitNode(n, visit);
      }] } });
    check(!compiled.diagnostics?.some(d => d.category === ts.DiagnosticCategory.Error), 'transpile-error');
    factories[key] = compiled.outputText; return key;
  }
  const entry = module(resolve(root, 'src/components/Workspace.tsx')); style(resolve(root, 'src/styles.css'));
  return { entry, factories, paths, hashes, css: [...css.values()].join('\n') };
}

function fixture(id, revision = 2) {
  const title = id === A ? 'Synthetic Alpha' : 'Synthetic Beta';
  const task = { task_id: id, title, status: 'done', mode: 'voiceover', revision, metadata_revision: 0,
    progress: 100, stages: [], created_at: EPOCH, expires_at: '2026-10-05T12:00:00Z', version_count: 1 };
  const rows = [1, 2].map(sentence_id => ({ sentence_id, sentence: `Synthetic sentence ${sentence_id}`, duration: 1,
    start: sentence_id - 1, end: sentence_id, shot_id: null, description: '', thumb_url: null, confidence: .8,
    is_fallback: false, audio_kind: 'tts', kind: 'narration', visual_beats: [] }));
  return { task, context: { ...task, script: '', preferences: { pacing: 'normal' }, timings: [], shots: [], current_shots: [], versions: [],
    report: { task_id: id, revision, mode: 'voiceover', quality: null, rows } },
  checks: { revision, checks: [], blocking_count: 0, pending_count: 0, passed: true } };
}

test('full source graph includes unchanged Workspace, Workbench and real API decoders', () => {
  const graph = collect();
  for (const file of ['src/components/Workspace.tsx', 'src/components/ResultWorkbench.tsx', 'src/lib/appApi.ts', 'src/lib/workbenchApi.ts']) {
    assert.ok(graph.factories[file]); assert.equal(graph.hashes[file], sha(readFileSync(resolve(root, file))));
  }
  assert.ok(graph.css.includes('.gm-rw')); assert.ok(graph.css.includes('.shell-layout'));
});
test('fixture guard rejects wrong authority, method, duplicate capability and arbitrary API', () => {
  const caps = { [A]: 'a'.repeat(43), [B]: 'b'.repeat(43) }, base = `${UNIT_ORIGIN}/api/tasks/${A}`;
  assert.equal(classify(base, 'GET', { 'x-task-token': caps[A] }, caps, UNIT_ORIGIN), 'read');
  assert.equal(classify(base, 'GET', { 'x-task-token': caps[A] }, caps), null);
  assert.equal(classify(base, 'GET', { 'x-task-token': caps[A], Host: 'wrong.invalid' }, caps, UNIT_ORIGIN), null);
  for (const [url, method, headers] of [[base, 'PUT', { 'x-task-token': caps[A] }], [base, 'GET', { 'x-task-token': caps[B] }],
    [base + '/apply', 'POST', { 'x-task-token': caps[A] }], [base.replace(UNIT_ORIGIN, 'http://127.0.0.1:8000'), 'GET', {}],
    [base + '/exports/' + JOB + '/file?token=' + caps[A] + '&token=' + caps[A] + '&revision=2', 'GET', {}],
    [base + '/video?token=' + caps[A] + '&revision=2&extra=1', 'GET', {}], [base, 'GET', { 'x-task-token': caps[A], cookie: 'no' }]])
    assert.equal(classify(url, method, headers, caps, UNIT_ORIGIN), null);
});
test('bounded SRT fixture has actual cues and deterministic bytes, not a video/export quality claim', () => {
  assert.ok(SRT.length < 4096); assert.match(SRT.toString('utf8'), /^1\r\n00:00:00,000 --> 00:00:01,000\r\n[^\r\n]+\r\n\r\n$/);
  assert.equal(sha(SRT), sha(Buffer.from(SRT.toString('utf8'))));
});

if (direct && process.env.GM_FINAL_BROWSER === '1') test('native Edge: independent final client states and UI SRT download (mock HTTP, no server TTL)', { timeout: 35000 }, async () => {
  let browser, context, page, temp, downloadDir, transport, download, stage = 'collect', failed = false, cleanupErrors = 0, timedOut = false;
  let errors = 0, blocked = 0, routeErrors = 0, downloads = 0, dialogs = 0, approvedConfirm = false, approvedUnload = false;
  let postBudget = 0, deleteBudget = 0, posts = 0, deletes = 0, routedDownloadGets = 0, requests = 0;
  let revision = 2, denial = null, hold = false, held = null;
  const cases = [], caps = { [A]: randomBytes(32).toString('base64url'), [B]: randomBytes(32).toString('base64url') };
  const started = performance.now(), graph = collect();
  const receipt = { scope: 'independent-native-client-fixture', network: 'in-memory-UI-plus-owned-rejecting-native-download-proxy', serverTTL: false,
    backendDeletion: false, backendExportPipeline: false, backendAuthorization: false, core6: 'separate-not-run', design4: 'separate-not-run',
    rawErrors: false, rawDOM: false, screenshots: false, traces: false, syntheticCapabilities: true, cases };
  let watchdog;
  try {
    stage = 'transport'; temp = mkdtempSync(join(tmpdir(), 'gm-final-browser-'));
    downloadDir = join(temp, 'downloads'); mkdirSync(downloadDir);
    transport = await startDownloadTransport({ task: A, job: JOB, token: caps[A], revision: 2, bytes: SRT });
    const ORIGIN = transport.origin;
    stage = 'launch';
    const { chromium } = require('playwright');
    browser = await chromium.launch({ channel: 'msedge', headless: true, timeout: 6000, downloadsPath: downloadDir,
      proxy: { server: ORIGIN, bypass: '<-loopback>' },
      args: ['--disable-background-networking', '--disable-component-update', '--no-first-run', '--disable-quic',
        '--force-webrtc-ip-handling-policy=disable_non_proxied_udp', '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1'] });
    // Deadline closes ONLY this browser; no process-name kills or daily hosts.
    watchdog = setTimeout(() => { timedOut = true; void browser.close().catch(() => { cleanupErrors++; }); }, 25000);
    context = await browser.newContext({ serviceWorkers: 'block', acceptDownloads: true, viewport: { width: 1280, height: 900 } });
    await context.routeWebSocket('**/*', socket => { blocked++; socket.close(); });
    await context.route('**/*', async route => {
      try {
        const request = route.request(), url = new URL(request.url()), method = request.method();
        const kind = classify(request.url(), method, request.headers(), caps, ORIGIN); requests++;
        if (!kind || requests > 200) { blocked++; await route.abort(); return; }
        if (kind === 'document') return await route.fulfill({ contentType: 'text/html', body: '<!doctype html><meta charset="utf-8"><div id="root"></div>' });
        if (kind === 'public') {
          const body = url.pathname === '/api/config/workspace' ? { local_history: false, generative_fill_available: false }
            : url.pathname === '/api/config/availability' ? { status: 'ready' }
              : url.pathname === '/api/session' ? { access_mode: 'development', csrf_token: null, expires_at: null }
                : { max_files: 20, max_upload_bytes: 1024, max_total_upload_bytes: 2048, max_script_length: 8000 };
          return await route.fulfill({ json: body });
        }
        const id = url.pathname.split('/')[3], f = fixture(id, revision);
        if (kind === 'unavailable-media') return await route.fulfill({ status: 503, body: '' }); // Deliberately no substitute media.
        if (kind === 'read') {
          if (url.pathname.endsWith('/checks') && id === A) {
            if (hold) { hold = false; held = route; return; }
            if (denial) return await route.fulfill({ status: denial.status, json: { detail: denial.detail } });
          }
          return await route.fulfill({ json: url.pathname.endsWith('/context') ? f.context : url.pathname.endsWith('/checks') ? f.checks : f.task });
        }
        if (kind === 'export') {
          check(postBudget-- === 1 && posts++ === 0, 'export-budget');
          const body = request.postDataJSON();
          check(JSON.stringify(Object.keys(body).sort()) === JSON.stringify(['aspect', 'expected_revision', 'fmt', 'res', 'sub'])
            && body.fmt === 'srt' && body.expected_revision === revision && body.aspect === '16:9' && body.res === '1080p' && body.sub === 'std', 'export-body');
          return await route.fulfill({ status: 202, json: { export_id: JOB, revision, state: 'succeeded', output_id: JOB,
            options: body, result: { bytes: SRT.length, duration: 1 } } });
        }
        if (kind === 'download') {
          routedDownloadGets++; blocked++;
          return await route.abort(); // No fallback: this must be the actual native transport GET.
        }
        check(kind === 'delete' && deleteBudget-- === 1 && deletes++ === 0 && request.postData() === null, 'delete-budget');
        return await route.fulfill({ status: 204, body: '' });
      } catch { routeErrors++; await route.abort().catch(() => {}); }
    });
    page = await context.newPage(); page.setDefaultTimeout(2500); page.setDefaultNavigationTimeout(3000);
    page.on('pageerror', () => errors++); page.on('crash', () => errors++);
    page.on('download', () => downloads++);
    page.on('dialog', async dialog => {
      dialogs++;
      if (approvedConfirm && dialog.type() === 'confirm') { approvedConfirm = false; await dialog.accept(); }
      else if (approvedUnload && dialog.type() === 'beforeunload') { approvedUnload = false; await dialog.accept(); }
      else { errors++; await dialog.dismiss(); }
    });
    const bundle = `const factories={${Object.entries(graph.factories).map(([k, v]) => `${JSON.stringify(k)}:function(module,exports,require){${v}\n}`).join(',')}},paths=${JSON.stringify(graph.paths)},cache={};
      function load(key){if(cache[key])return cache[key].exports;const m=cache[key]={exports:{}};factories[key](m,m.exports,name=>name==='react'?React:name.endsWith('.css')?{__esModule:true,default:new Proxy({},{get:(_,k)=>k})}:load(paths[key][name]));return m.exports;}
      window.fixtureDenials=0;const deny=()=>{window.fixtureDenials++;throw Error('fixture-denied');};
      if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=deny;window.open=deny;window.showSaveFilePicker=deny;
      const appRoot=ReactDOM.createRoot(document.getElementById('root'));
      ReactDOM.flushSync(()=>appRoot.render(React.createElement(React.StrictMode,null,React.createElement(load(${JSON.stringify(graph.entry)}).default))));`;
    const scripts = ['react', 'react-dom'].map(name => readFileSync(require.resolve(name).replace(/index\.js$/, `umd/${name}.development.js`), 'utf8'));
    async function mount(seed = true) {
      await page.goto(ORIGIN + '/tasks/' + A);
      await page.clock.setFixedTime(new Date(EPOCH)); // Date ONLY; real timers/RAF/native events. Not server TTL.
      if (seed) await page.evaluate(({ caps, A, B, EPOCH }) => {
        localStorage.clear();
        localStorage.setItem('golden-mic.history.v1', JSON.stringify([A, B].map(id => ({ taskId: id, accessToken: caps[id],
          title: id === A ? 'Synthetic Alpha' : 'Synthetic Beta', status: 'done', revision: 2, createdAt: EPOCH }))));
      }, { caps, A, B, EPOCH });
      await page.addStyleTag({ content: graph.css });
      for (const content of scripts) await page.addScriptTag({ content });
      await page.addScriptTag({ content: bundle });
    }
    async function ready(title = 'Synthetic Alpha') {
      await page.getByRole('heading', { name: title, exact: true }).waitFor();
      await page.locator('.gm-rw-next').getByRole('button', { name: '开始导出', exact: true }).waitFor();
    }
    const gone = page.locator('.shell-gone h1');
    const main = page.locator('main');
    const sidebar = title => page.locator('.shell-sidebar .shell-recent').getByRole('button').filter({ has: page.getByText(title, { exact: true }) });
    const historyIntact = () => page.evaluate(({ A, B, caps }) => {
      const rows = JSON.parse(localStorage.getItem('golden-mic.history.v1'));
      return [A, B].every(id => rows.some(r => r.taskId === id && r.accessToken === caps[id]));
    }, { A, B, caps });

    for (const [status, detail, expired] of [[404, { code: 'task_gone' }, false], [410, '', false],
      [410, { code: 'other' }, false], [410, { code: 'task_gone' }, true]]) {
      stage = `checks-${status}-${expired ? 'structured' : typeof detail === 'string' ? 'bare' : detail.code}`;
      denial = { status, detail }; await mount(); await gone.waitFor();
      check(await gone.textContent() === (expired ? '作品已到期或已清理' : '作品不存在或暂不可访问'), 'gone-classification');
      check(await main.getAttribute('data-page') === 'gone' && await page.locator('.gm-rw').count() === 0, 'gone-editor-removed');
      check(await historyIntact(), 'gone-history-preserved');
      check(await main.getByRole('button', { name: '重新读取作品', exact: true }).isEnabled(), 'readonly-retry-control');
      denial = null; await main.getByRole('button', { name: '重新读取作品', exact: true }).click(); await ready();
      check(posts === 0 && deletes === 0, 'no-auto-write');
      cases.push(stage + '-client-only-retry');
    }

    stage = 'held-stale-read'; hold = true; await mount();
    await page.getByRole('heading', { name: 'Synthetic Alpha', exact: true }).waitFor();
    // Browser-observed pending UI precedes releasing the old response.
    await page.getByText('正在读取检查结果…', { exact: true }).waitFor();
    check(!!held, 'held-check-request');
    approvedConfirm = true; await sidebar('Synthetic Beta').click(); await ready('Synthetic Beta');
    await held.fulfill({ status: 410, json: { detail: { code: 'task_gone' } } }); held = null;
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    check(await main.getAttribute('data-page') === 'result' && await gone.count() === 0 && await historyIntact(), 'stale-eviction');
    check(await page.getByRole('heading', { name: 'Synthetic Beta', exact: true }).count() === 1, 'stale-selection');
    cases.push('held-A-checks-410-after-B-native-navigation-abort-fence');

    stage = 'deleted-sentence'; await mount(); await ready();
    await page.getByLabel('删掉第 1 句', { exact: true }).check();
    check(await page.locator('.gm-rw-cell.is-deleted').count() === 1, 'deleted-row');
    check(!await page.getByRole('button', { name: '我来读', exact: true }).isEnabled(), 'deleted-recording-disabled');
    check(await page.getByLabel('或上传音频').count() === 0, 'deleted-upload-hidden');
    await page.getByLabel('删掉第 1 句', { exact: true }).uncheck();
    await page.getByRole('button', { name: '改字', exact: true }).click();
    await page.getByLabel('改这句话', { exact: true }).fill('Synthetic retained pending');
    await page.getByRole('button', { name: '记下修改', exact: true }).click();
    await page.getByRole('region', { name: '待保存修改', exact: true }).waitFor();
    cases.push('native-sentence-delete-disables-recording-and-undo');
    stage = 'stale-draft'; revision = 3; approvedUnload = true; await mount(false);
    await page.getByText('草稿基于 v2，当前为 v3。已保留草稿，禁止自动覆盖。', { exact: true }).waitFor();
    check(!await page.getByRole('button', { name: '应用修改 → 生成新版本', exact: true }).isEnabled(), 'stale-apply-disabled');
    check(!await page.getByRole('button', { name: '我来读', exact: true }).isEnabled(), 'stale-recording-disabled');
    check(await page.getByLabel('或上传音频').count() === 0, 'stale-upload-hidden');
    await page.getByRole('button', { name: '导出 / 分享', exact: true }).click();
    check(!await page.getByRole('dialog', { name: '导出 / 分享', exact: true }).getByRole('button', { name: '检查通过后才能导出', exact: true }).isEnabled(), 'stale-export-disabled');
    check(await page.evaluate(A => JSON.parse(localStorage.getItem('gm-modes-v1')).pend.resultV2[A].draft.sentences[1].text === 'Synthetic retained pending', A), 'durable-pending-preserved');
    check(posts === 0 && deletes === 0, 'stale-no-write'); cases.push('native-edit-reload-new-revision-stale-durable-draft');

    stage = 'native-download'; revision = 2; approvedUnload = true; await mount(); await ready();
    await page.getByRole('button', { name: '导出 / 分享', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '导出 / 分享', exact: true });
    await dialog.getByRole('button', { name: '字幕 SRT', exact: true }).click(); postBudget = 1;
    await dialog.getByRole('button', { name: '开始导出', exact: true }).click();
    const link = dialog.getByRole('link', { name: '下载已完成的 SRT 文件（第 2 版）', exact: true }); await link.waitFor();
    check(await link.getAttribute('href') === `/api/tasks/${A}/exports/${JOB}/file?token=${caps[A]}&revision=2`, 'bound-native-link');
    check(await link.getAttribute('download') === '', 'unchanged-native-download-attribute');
    transport.arm({ posts, revision });
    stage = 'native-download-event';
    [download] = await Promise.all([page.waitForEvent('download'), link.click()]);
    stage = 'native-download-file';
    check(await download.failure() === null && download.suggestedFilename() === 'synthetic-final.srt', 'native-download-result');
    const file = await download.path();
    const inspected = inspectOwnedFile(file, downloadDir, SRT);
    stage = 'native-download-transport-headers';
    assert.deepEqual(transport.observation, { status: 200, headers: responseHeaders(SRT), browser_header_observation: false, observation: 'transport-response' });
    receipt.download = { nativeEvent: true, fromActualUI: true, failureNull: true, filenameExact: true,
      ...inspected, transportResponse: transport.observation, browser_header_observation: false };
    receipt.downloadCleanup = await cleanupDownload(download); check(!existsSync(file), 'download-removed'); download = null;
    cases.push('UI-export-button-mock-success-native-anchor-SRT-transport-headers-exact-bytes-hash');

    stage = 'acknowledged-delete'; await dialog.getByRole('button', { name: '关闭', exact: true }).click();
    approvedConfirm = true; await page.getByRole('button', { name: '删除', exact: true }).click();
    const confirm = page.getByRole('dialog', { name: '取消并永久删除作品？', exact: true });
    await confirm.waitFor(); deleteBudget = 1; await confirm.getByRole('button', { name: '永久删除', exact: true }).click();
    await page.getByText('服务器已确认删除作品。', { exact: false }).waitFor();
    check(await main.getAttribute('data-page') === 'create' && await page.locator('.gm-rw').count() === 0, 'delete-leaves-result');
    check(await sidebar('Synthetic Alpha').count() === 0 && await sidebar('Synthetic Beta').count() === 1, 'delete-history-projection');
    check(await page.evaluate(({ A, B, caps }) => { const h = JSON.parse(localStorage.getItem('golden-mic.history.v1'));
      return !h.some(r => r.taskId === A) && h.some(r => r.taskId === B && r.accessToken === caps[B]); }, { A, B, caps }), 'delete-exact-history');
    cases.push('native-confirm-then-UI-dialog-mocked-DELETE-204-client-history-only');
    stage = 'integrity';
    check(await page.evaluate(() => window.fixtureDenials) === 0, 'device-or-popup-attempt');
    check(posts === 1 && deletes === 1 && transport.counters.gets === 1 && routedDownloadGets === 0 && downloads === 1 && dialogs === 4, 'exact-side-effects');
    check(errors === 0 && blocked === 0 && routeErrors === 0 && !timedOut, 'browser-boundaries');
    for (const [path, hash] of Object.entries(graph.hashes)) check(sha(readFileSync(resolve(root, path))) === hash, 'source-drift');
    receipt.browser = browser.version(); receipt.sourceHashes = graph.hashes;
  } catch { failed = true; }
  finally {
    clearTimeout(watchdog);
    if (held) await held.abort().catch(() => { cleanupErrors++; });
    if (download) try { receipt.downloadCleanup = await cleanupDownload(download); } catch { cleanupErrors++; }
    try { await context?.close(); } catch { cleanupErrors++; }
    try { await browser?.close(); } catch { cleanupErrors++; }
    try { await transport?.close(); } catch { cleanupErrors++; }
    if (downloadDir) try {
      receipt.downloadEntriesAfterClose = readdirSync(downloadDir).length;
      check(receipt.downloadEntriesAfterClose === 0, 'download-cleanup-residue'); rmdirSync(downloadDir);
      receipt.downloadDirectoryRemoved = !existsSync(downloadDir);
    } catch { cleanupErrors++; }
    receipt.transport = transport?.counters; receipt.transportCleanup = transport?.cleanup;
    if (transport && (!transport.cleanup.closed || transport.cleanup.listening || transport.cleanup.sockets !== 0)) cleanupErrors++;
    Object.assign(receipt, { passed: !failed && !cleanupErrors && !timedOut, stage, errors, blocked, routeErrors, requests,
      posts, deletes, downloadGets: transport?.counters.gets ?? 0, routedDownloadGets, downloads, dialogs, cleanupErrors, ownedBrowserClosed: !browser?.isConnected(),
      timedOut, durationMs: Math.round(performance.now() - started) });
    // Static keys/labels, counts, hashes only. No capabilities, URLs, exceptions,
    // DOM, console, downloads or browser traces enter retained evidence.
    if (temp) writeFileSync(join(temp, 'receipt.json'), JSON.stringify(receipt, null, 2), { flag: 'wx' });
    console.log(JSON.stringify({ finalBrowser: receipt, evidence: temp }));
  }
  check(receipt.passed, `final-browser-failed-at-${stage}-raw-error-suppressed`);
});