import test from 'node:test';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { collect } from './test-v2-final-browser.mjs';

// Full current TSX graph, real React/ReactDOM and native Edge. Synthetic HTTP
// only; no app host, backend, provider, real config/data, media or export work.
// Raw CSS/local CSS-module names are collect()'s contract, not a Vite build.
// The shell geometry below is a fixture container, NOT full Workspace acceptance.
// 305px is a usable-width MODEL, not a native Windows scrollbar measurement.
const require = createRequire(import.meta.url), root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const ORIGIN = 'https://simple-ui.invalid', TASK = 'simple_ui_synthetic', BASE = `/api/tasks/${TASK}`;
const TOKEN = 'synthetic_only_'.padEnd(48, 'x'), UP = 'up_00000000000000000000000000000001';
const TEXT = '今天社区居民在广场共同参加现场活动', CUSTOM = '保留现场细节，不添加未经核实的信息';
const sha = value => createHash('sha256').update(value).digest('hex');
const metadata = { id: JSON.stringify(['synthetic.mp4', 3, 123]), name: 'synthetic.mp4', size: 3, lastModified: 123,
  type: 'video/mp4', kind: 'video', duration: 60, note: '保留备注', trim_start: 0, trim_end: 60 };
const binding = { file_id: metadata.id, upload_id: UP, sha256: 'a'.repeat(64), chunk_size: 8388608,
  put_url: `${BASE}/files/server_1/chunks`, draftTaskId: TASK, server_file_id: 'server_1' };
const snapshot = { id: UP, name: metadata.name, bytes: 3, sec: 60, probe_ok: true, width: 1920, height: 1080, fps: 30,
  metadata_only: false, can_materialize: true, phase: 'done', status: 'ready', has_speech: true,
  thumb_url: null, wave_url: null, asr_confidence: null, progress: 100, chunks: [0], sha256: 'a'.repeat(64),
  transcript: [{ id: 'same', text: TEXT, start: 1, end: 3, speaker_id: '', words: [] }] };
const limits = { max_files: 20, max_upload_bytes: 500 * 1024 ** 2, max_total_upload_bytes: 5 * 1024 ** 3, max_script_length: 8000 };
const task = { task_id: TASK, title: 'Synthetic result', status: 'done', mode: 'voiceover', revision: 2,
  progress: 100, stages: [], created_at: '2026-10-14T00:00:00Z' };
const report = { task_id: TASK, revision: 2, mode: 'voiceover', quality: null,
  rows: [1, 2].map(sentence_id => ({ sentence_id, sentence: `Synthetic sentence ${sentence_id}`, duration: 1,
    start: sentence_id - 1, end: sentence_id, shot_id: null, description: '', thumb_url: null, confidence: .8,
    is_fallback: false, audio_kind: 'tts', kind: 'narration', visual_beats: [] })) };
const workbench = { ...task, script: '', preferences: { pacing: 'normal' }, timings: [], shots: [], current_shots: [],
  versions: [{ revision: 2, label: 'Synthetic revision', created_at: task.created_at }], report };
const checks = { revision: 2, blocking_count: 0, pending_count: 1, passed: false,
  checks: [{ key: 'synthetic-review', code: 'manual', level: 1, message: 'Synthetic publication review',
    sentence_id: null, action: '', checked: false, confirmable: true }] };

test('offline native simple UI', { timeout: 150000 }, async t => {
  let browser, watchdog, stage = 'collect', failures = 0, layouts = 0, contexts = 0, cleanupErrors = 0, timedOut = false;
  const totals = { gets: 0, alignPosts: 0, denied: 0, pageErrors: 0, downloads: 0, deviceDenials: 0, submissions: 0, keyboardToggles: 0 };
  const check = (ok, code) => { if (!ok) { stage = code; throw new Error(code); } };
  const safeError = () => { const e = new Error(`failure-stage:${stage}`); e.stack = ''; return e; };
  let graph;
  try {
    graph = collect();
    for (const name of ['CreateWizard', 'Processing', 'ResultWorkbench']) check(!!graph.factories[`src/components/${name}.tsx`], 'complete-graph');
    check(!!graph.factories['src/lib/workbenchApi.ts'], 'unchanged-parser');
    const scripts = ['react', 'react-dom'].map(name => readFileSync(require.resolve(name).replace(/index\.js$/, `umd/${name}.development.js`), 'utf8'));
    const bundle = `const factories={${Object.entries(graph.factories).map(([k, v]) => `${JSON.stringify(k)}:function(module,exports,require){${v}\n}`).join(',')}},paths=${JSON.stringify(graph.paths)},cache={};
      window.load=function load(key){if(cache[key])return cache[key].exports;const m=cache[key]={exports:{}};
      factories[key](m,m.exports,name=>name==='react'?React:name.endsWith('.css')?{__esModule:true,default:new Proxy({},{get:(_,k)=>k})}:load(paths[key][name]));return m.exports;};`;
    stage = 'launch';
    const { chromium } = require('playwright');
    browser = await chromium.launch({ channel: 'msedge', headless: true, timeout: 8000,
      env: { ...process.env, DEBUG: '', PWDEBUG: '0' },
      proxy: { server: 'http://egress-disabled.invalid:9', bypass: '<-loopback>' },
      args: ['--disable-background-networking', '--disable-component-update', '--no-first-run', '--disable-quic',
        '--force-webrtc-ip-handling-policy=disable_non_proxied_udp', '--host-resolver-rules=MAP * ~NOTFOUND'] });
    watchdog = setTimeout(() => { timedOut = true; void browser.close().catch(() => cleanupErrors++); }, 130000);

    async function scenario(name, options, body) {
      await t.test(name, { timeout: 20000 }, async () => {
        let context, page, failed = false;
        const m = { gets: 0, alignPosts: 0, denied: 0, pageErrors: 0, downloads: 0, submits: 0, layouts: 0,
          layoutFailures: 0, strictLocatorFailure: false, timeoutFailure: false };
        let firstLayoutFailure = '';
        const boundary = { alignBudget: 0, refreshFailure: false };
        stage = `${name}-setup`;
        try {
          context = await browser.newContext({ viewport: { width: 1440, height: 950 }, serviceWorkers: 'block',
            acceptDownloads: false, permissions: [], locale: 'zh-CN' }); contexts++;
          await context.routeWebSocket('**/*', socket => { m.denied++; socket.close(); });
          await context.addInitScript(() => {
            window.boundaryDenials = 0;
            const deny = () => { window.boundaryDenials++; throw new DOMException('Offline fixture denial', 'NotAllowedError'); };
            for (const key of ['WebSocket', 'WebTransport', 'EventSource', 'RTCPeerConnection', 'webkitRTCPeerConnection',
              'Worker', 'SharedWorker', 'open', 'showOpenFilePicker', 'showSaveFilePicker', 'showDirectoryPicker'])
              Object.defineProperty(window, key, { configurable: true, value: deny });
            if (navigator.mediaDevices) for (const key of ['getUserMedia', 'getDisplayMedia', 'enumerateDevices'])
              Object.defineProperty(navigator.mediaDevices, key, { configurable: true, value: deny });
            for (const key of ['getUserMedia', 'webkitGetUserMedia', 'sendBeacon'])
              Object.defineProperty(navigator, key, { configurable: true, value: deny });
            document.addEventListener('click', event => {
              if (event.target instanceof Element && event.target.closest('a[download]')) { event.preventDefault(); deny(); }
            }, true);
          });
          await context.route('**/*', async route => {
            try {
              const request = route.request(), u = new URL(request.url()), h = request.headers(), method = request.method();
              const exact = u.origin === ORIGIN && !u.search && !u.hash && !u.username && !u.password;
              const clean = !h.authorization && !h.cookie && !h['x-csrf-token'];
              if (exact && clean && method === 'GET' && u.pathname === '/' && request.isNavigationRequest())
                return await route.fulfill({ contentType: 'text/html', headers: {
                  'Permissions-Policy': 'camera=(), microphone=(), display-capture=(), geolocation=(), usb=(), serial=(), bluetooth=()',
                  'Content-Security-Policy': `default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'; connect-src ${ORIGIN}; media-src 'none'; img-src 'none'; worker-src 'none'; frame-src 'none'; form-action 'none'`,
                }, body: '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><div class="shell-layout"><main class="shell-main"><div id="root"></div></main></div></html>' });
              // Real appApi bootstraps its browser session before an explicit
              // write, even for task-scoped capabilities. No auth is fabricated.
              if (exact && clean && method === 'GET' && u.pathname === '/api/session' && !h['x-task-token'] && boundary.alignBudget === 1) {
                m.gets++; return await route.fulfill({ json: { access_mode: 'development', csrf_token: null, expires_at: null } });
              }
              const scoped = exact && clean && h['x-task-token'] === TOKEN;
              if (scoped && method === 'GET' && [BASE, `${BASE}/files`, `${BASE}/files/server_1`].includes(u.pathname) && !options.result) {
                check(u.pathname.endsWith('/server_1') ? h['x-upload-token'] === TOKEN : !h['x-upload-token'], 'get-header-scope'); m.gets++;
                if (boundary.refreshFailure && u.pathname.endsWith('/server_1')) return await route.fulfill({ status: 503, json: { detail: 'Synthetic unavailable' } });
                return await route.fulfill({ json: u.pathname === BASE ? { task_id: TASK, status: 'draft' }
                  : u.pathname.endsWith('/files') ? { files: [] } : { ...snapshot, ...options.snapshot } });
              }
              if (scoped && method === 'GET' && options.result && [BASE, `${BASE}/workbench/context`, `${BASE}/checks`].includes(u.pathname)) {
                m.gets++;
                return await route.fulfill({ json: u.pathname === BASE ? task : u.pathname.endsWith('/context') ? workbench : checks });
              }
              // The sole write exception is consumed BEFORE responding, and armed
              // only immediately before the user's native explicit recheck click.
              if (scoped && method === 'POST' && u.pathname === `${BASE}/align` && boundary.alignBudget === 1) {
                boundary.alignBudget = 0; m.alignPosts++;
                const value = request.postDataJSON();
                check(value.mode === options.mode && value.script === options.script && Array.isArray(value.sentences)
                  && value.sentences.length > 0 && Object.keys(value).sort().join() === 'mode,script,sentences', 'align-body');
                return await route.fulfill({ json: { matches: value.sentences.map(s => ({ idx: s.idx, kind: s.kind, score: s.kind === 'narration' ? 0 : 1,
                  source: s.kind === 'narration' ? null : { take_id: 'synthetic_take', upload_id: UP, start: 1, end: 3, speaker_id: '', asr_text: options.unsafe ? '今年我们还请了舞狮队' : s.text,
                    score: 1, snr_db: null, words: [], precision: 'segment' }, alt_takes: [] })) } });
              }
              m.denied++; await route.abort();
            } catch { m.denied++; await route.abort().catch(() => {}); }
          });
          page = await context.newPage(); page.setDefaultTimeout(2200); page.setDefaultNavigationTimeout(3000);
          page.on('pageerror', () => m.pageErrors++); page.on('crash', () => m.pageErrors++);
          page.on('download', download => { m.downloads++; void download.cancel(); });
          page.on('dialog', dialog => { m.denied++; void dialog.dismiss(); });
          page.on('filechooser', chooser => { m.denied++; void chooser.setFiles([]); });
          await page.goto(ORIGIN + '/');
          await page.addStyleTag({ content: graph.css });
          for (const content of scripts) await page.addScriptTag({ content });
          await page.addScriptTag({ content: bundle });
          await page.evaluate(({ options, limits, TASK, TOKEN, UP, metadata, binding, task }) => {
            window.observed = { draft: null, submissions: [], errors: 0, callbacks: 0 };
            const load = window.load, R = window.React, fail = () => window.observed.errors++, count = () => window.observed.callbacks++;
            let component, props;
            if (options.result) {
              component = load('src/components/ResultWorkbench.tsx').default;
              props = { taskId: TASK, accessToken: TOKEN, title: 'Synthetic result', isOwner: true,
                request: load('src/lib/appApi.ts').createAppRequest({ taskId: TASK, token: TOKEN }),
                onProcessing: count, onChanged: count, onDelete: count, onNew: count, onRename: count, onDuplicate: count, onError: fail };
            } else if (options.processing) {
              component = load('src/components/Processing.tsx').default;
              props = { task: { ...task, status: options.processing, progress: 40, current_stage: 3, revision: 0,
                  errorKind: options.processing === 'failed' ? 'quote_missing' : undefined, badRows: [1] },
                upload: null, uploading: false, now: 0, title: 'Synthetic processing', busy: false,
                onCancel: count, onRetry: count, onEdit: count, onRefresh: count };
            } else {
              component = load('src/components/CreateWizard.tsx').CreateWizard;
              const preferences = load('src/lib/productionModes.ts').defaultModePreferences(options.mode);
              // Seed generative=true even when unavailable to prove sanitization.
              preferences.generative_fill = true;
              props = { limits, generativeAllowed: !!options.generative, busy: false, serviceReady: true,
                initialDraft: { draftTaskId: TASK, draftTaskToken: TOKEN, mode: options.mode, script: options.script, step: 2,
                  preferences, source_voice_preferred: false, files: [metadata], uploadBindings: [binding],
                  uploadIds: [UP], uploadTokens: { [UP]: TOKEN }, elements: {}, sentenceChecks: {} },
                onDraft: draft => { window.observed.draft = draft; }, onError: fail,
                onSubmit: async value => { window.observed.submissions.push(value); if (options.submitFailure) throw Error('Synthetic submit boundary'); } };
            }
            document.querySelector('main').dataset.page = options.result ? 'result' : options.processing ? 'processing' : 'create';
            if (!options.result && !options.processing) document.getElementById('root').className = 'shell-wizard-host';
            window.fixtureRoot = ReactDOM.createRoot(document.getElementById('root'));
            ReactDOM.flushSync(() => window.fixtureRoot.render(R.createElement(component, props)));
          }, { options, limits, TASK, TOKEN, UP, metadata, binding, task });

          const button = name => page.getByRole('button', { name, exact: true });
          const visible = async locator => check(await locator.evaluate(e => e.checkVisibility({ checkVisibilityCSS: true, checkOpacity: true })), 'visible-control');
          async function toggle(selector, key, open) {
            stage = `${name}-keyboard-${key}-${open}`;
            const detail = page.locator(selector), summary = detail.locator(':scope > summary');
            await summary.focus(); await summary.press(key);
            check(await summary.evaluate(e => e === document.activeElement && e.matches(':focus-visible') && getComputedStyle(e).outlineStyle !== 'none'), 'summary-native-focus');
            check(await detail.evaluate(e => e.open) === open, `${name}-native-${key}`); totals.keyboardToggles++;
          }
          async function layout(label) {
            for (const font of [16, 20]) for (const width of [1440, 900, 640, 390, 320, 305]) {
              stage = `${name}-${label}-${width}-${font}`;
              await page.setViewportSize({ width, height: 950 });
              const metric = await page.evaluate(font => {
                document.documentElement.style.fontSize = `${font}px`;
                const doc = document.documentElement, controls = [...document.querySelectorAll('button,input,textarea,select,summary,a[href]')]
                  .filter(e => e.checkVisibility({ checkVisibilityCSS: true, checkOpacity: true }));
                const boxes = controls.map(e => e.getBoundingClientRect());
                return { client: doc.clientWidth, scroll: doc.scrollWidth, controls: controls.length,
                  outside: boxes.filter(r => r.left < -1 || r.right > doc.clientWidth + 1).length,
                  outsideStoryboard: controls.filter((e, i) => (boxes[i].left < -1 || boxes[i].right > doc.clientWidth + 1) && !!e.closest('.gm-rw-strip')).length,
                  outsideDisclosure: controls.filter((e, i) => (boxes[i].left < -1 || boxes[i].right > doc.clientWidth + 1) && !!e.closest('details')).length,
                  left: Math.min(...boxes.map(r => r.left)), right: Math.max(...boxes.map(r => r.right)) };
              }, font);
              layouts++; m.layouts++;
              if (!(metric.scroll <= metric.client + 1 && metric.outside === 0 && metric.controls > 0)) {
                // Collect all samples, then FAIL the scenario. No clipping,
                // scrollbar exemption or weakened bounds to conceal a defect.
                m.layoutFailures++; firstLayoutFailure ||= stage;
                console.log(JSON.stringify({ failureStage: stage, ...metric }));
              }
            }
            await page.setViewportSize({ width: 1440, height: 950 });
            await page.evaluate(() => { document.documentElement.style.fontSize = '16px'; });
          }
          async function recheck() {
            stage = `${name}-explicit-recheck`;
            check(m.alignPosts === 0 && boundary.alignBudget === 0, 'no-auto-align'); boundary.alignBudget = 1;
            await button('重新核对').click();
            await page.locator('.gm-match-row[data-state="ok"]').first().waitFor();
            check(m.alignPosts === 1 && boundary.alignBudget === 0, 'explicit-align-only');
          }
          await body({ page, button, toggle, layout, visible, check, m, boundary, recheck });
          stage = `${name}-safety`;
          const observed = await page.evaluate(() => ({ denials: window.boundaryDenials, submissions: window.observed.submissions.length, errors: window.observed.errors }));
          totals.deviceDenials += observed.denials; totals.submissions += observed.submissions; m.submits = observed.submissions;
          check(observed.errors === (options.submitFailure ? 1 : 0), 'unexpected-error-callback');
          check(observed.denials === 0 && m.denied === 0 && m.pageErrors === 0 && m.downloads === 0 && boundary.alignBudget === 0, 'offline-boundary');
          check(m.layoutFailures === 0, firstLayoutFailure);
        } catch (error) {
          m.strictLocatorFailure = typeof error?.message === 'string' && error.message.includes('strict mode violation');
          m.timeoutFailure = error?.name === 'TimeoutError';
          failed = true; failures++; throw safeError();
        }
        finally {
          for (const k of ['gets', 'alignPosts', 'denied', 'pageErrors', 'downloads']) totals[k] += m[k];
          try { await context?.close(); } catch { cleanupErrors++; }
          console.log(JSON.stringify({ scenario: name, passed: !failed, failureStage: failed ? stage : null, ...m }));
        }
      });
    }

    for (const mode of ['voiceover', 'mixed', 'original']) {
      const script = `社区新闻\n${mode === 'mixed' ? `${TEXT}\n同期：${TEXT}` : TEXT}`;
      await scenario(mode, { mode, script, generative: mode === 'mixed', submitFailure: mode === 'voiceover' }, async h => {
        const { page, button, toggle, layout, visible, m, recheck } = h;
        stage = `${mode}-restore`;
        await page.getByText('有人说话 · 转写 1 句', { exact: true }).waitFor();
        check(m.gets === 3 && m.alignPosts === 0, 'task-scoped-get-only');
        if (mode !== 'voiceover') { check(await button('下一步：选效果').isDisabled(), 'quote-gate-before-recheck'); await recheck(); }
        if (mode !== 'original') {
          await layout('upload-closed'); await toggle('.gm-sentence-review', 'Enter', true);
          await page.locator('.gm-check-row').first().click();
          await layout('upload-expanded'); await toggle('.gm-sentence-review', 'Space', false);
          await toggle('.gm-sentence-review', 'Enter', true);
          check((await page.locator('.gm-check-row').first().textContent()).includes('拍到了'), 'sentence-check-preserved');
        }
        await button('下一步：选效果').click(); await page.getByRole('heading', { name: '选效果', exact: true }).waitFor();
        const detail = page.locator('.gm-advanced-settings');
        check(!await detail.evaluate(e => e.open), 'settings-default-closed');
        check(await page.locator('[role="switch"]').count() === (mode === 'mixed' ? 8 : 7), 'all-effects-mounted');
        check(await page.locator('[role="switch"]').evaluateAll(es => es.every(e => e.closest('details.gm-advanced-settings'))), 'effects-inside');
        await visible(button('大字清晰')); await button('大字清晰').click();
        if (mode !== 'original') {
          await visible(button('我自己读')); await button('我自己读').click();
          check(await page.locator('.gm-voice-panel').count() === 0 && await button('开始制作').isEnabled(), 'm0-own-voice-no-fake-file-gate');
        } else check(await button('我自己读').count() === 0, 'original-no-voice-control');
        if (mode === 'mixed') await visible(page.getByText('本次已开启 AI 示意画面补充', { exact: false }));
        await layout('effects-closed'); await toggle('.gm-advanced-settings', 'Enter', true);
        const switches = detail.getByRole('switch');
        for (let i = 0; i < await switches.count(); i++) await visible(switches.nth(i));
        for (const label of ['背景音乐', '画面慢慢推近', '新闻台标和片尾板', '开头结尾淡入淡出', '颜色统一', '现场原声降噪']) {
          const control = page.getByRole('switch', { name: label, exact: true }), before = await control.getAttribute('aria-checked');
          await control.click(); check(await control.getAttribute('aria-checked') !== before, 'effect-native-change');
        }
        if (mode !== 'original') {
          await button('快一点').click();
          const gen = page.getByRole('switch', { name: 'AI 示意画面补充', exact: true });
          if (mode === 'voiceover') { check(await gen.isDisabled(), 'disabled-capability'); await gen.press('Space');
            check(await gen.getAttribute('aria-checked') === 'false', 'disabled-capability-not-toggleable'); }
        }
        await page.getByLabel('想对 AI 剪辑师说的话', { exact: false }).fill(CUSTOM);
        const before = await page.evaluate(() => JSON.stringify(window.observed.draft.preferences));
        await layout('effects-expanded'); await toggle('.gm-advanced-settings', 'Space', false);
        await toggle('.gm-advanced-settings', 'Enter', true);
        check(await page.getByLabel('想对 AI 剪辑师说的话', { exact: false }).inputValue() === CUSTOM, 'custom-reopen');
        await button('← 上一步').click(); await button('← 上一步').click();
        if (mode !== 'original') {
          await toggle('.gm-writing-tools', 'Enter', true);
          await page.locator('.gm-element-chip').first().click();
          await toggle('.gm-evidence-details', 'Enter', true);
          await layout('writing-expanded'); await toggle('.gm-evidence-details', 'Space', false);
          await toggle('.gm-writing-tools', 'Space', false); await layout('writing-closed');
          await toggle('.gm-writing-tools', 'Enter', true);
          check(await page.locator('.gm-element-chip').first().getAttribute('aria-pressed') === 'true', 'writing-self-check-preserved');
        }
        await button('下一步：传素材').click(); await button('下一步：选效果').click();
        check(await page.evaluate(before => JSON.stringify(window.observed.draft.preferences) === before, before), 'navigation-preferences');
        check(!await detail.evaluate(e => e.open), 'navigation-native-default');
        check(await page.evaluate(() => window.observed.submissions.length) === 0 && m.alignPosts === (mode === 'voiceover' ? 0 : 1), 'no-automatic-submit');
        if (mode === 'voiceover') await page.setViewportSize({ width: 320, height: 568 });
        await button('开始制作').click();
        await page.waitForFunction(() => window.observed.submissions.length === 1);
        check(await page.evaluate(({ before, TOKEN, UP, mode, CUSTOM }) => {
          const s = window.observed.submissions[0];
          return JSON.stringify(s.preferences) === before && s.preferences.custom_instructions === CUSTOM && s.mode === mode
            && s.draftTaskToken === TOKEN && s.uploadIds.length === 1 && s.uploadIds[0] === UP && s.source_voice_preferred === false
            && !Object.hasOwn(s, 'ownVoice') && s.files.length === 0 && s.asset_options[0].note === '保留备注';
        }, { before, TOKEN, UP, mode, CUSTOM }), 'submit-payload');
        if (mode === 'voiceover') {
          await visible(page.getByRole('alert'));
          const alert = await page.getByRole('alert').evaluate(e => {
            const r = e.getBoundingClientRect(); return { inView: r.top >= 0 && r.bottom <= innerHeight,
              focused: e === document.activeElement };
          });
          console.log(JSON.stringify({ submitFailureAlertInViewport: alert.inView, submitFailureAlertFocused: alert.focused }));
          check(alert.inView && alert.focused, 'submit-failure-offscreen-or-unfocused');
          check(!await detail.evaluate(e => e.open), 'failure-outside-collapsed-settings');
        }
      });
    }

    for (const kind of ['file-gate', 'no-speech', 'unsafe-quote']) {
      const mode = kind === 'file-gate' ? 'voiceover' : 'original', script = `社区新闻\n${kind === 'unsafe-quote' ? '今年我们请舞狮队' : TEXT}`;
      await scenario(kind, { mode, script, unsafe: kind === 'unsafe-quote', snapshot: kind === 'file-gate' ? { probe_ok: false }
        : kind === 'no-speech' ? { has_speech: false, transcript: [] } : {} }, async ({ page, button, recheck, layout }) => {
        if (kind === 'unsafe-quote') { await page.getByText('有人说话 · 转写 1 句', { exact: true }).waitFor(); await recheck(); }
        else await page.getByRole('alert').first().waitFor();
        check(await button('下一步：选效果').isDisabled(), `${kind}-blocked`);
        await layout('gate');
        check(await page.evaluate(() => window.observed.submissions.length === 0), 'gate-no-submit');
      });
    }

    for (const status of ['running', 'failed']) await scenario(`processing-${status}`, { processing: status }, async ({ page, toggle, layout, visible }) => {
      const detail = '[data-testid="v2-processing"] details';
      check(await page.locator(detail).evaluate(e => e.open) === (status === 'failed'), 'processing-default');
      check(await page.locator('[data-testid="processing-stages"] > li').count() === 10, 'ten-real-stages');
      if (status === 'failed') { await visible(page.getByRole('alert')); await toggle(detail, 'Space', false); }
      await layout('closed'); await toggle(detail, 'Enter', true); await layout('expanded'); await toggle(detail, 'Space', false);
      if (status === 'failed') await visible(page.getByRole('alert'));
      check(await page.evaluate(() => window.observed.callbacks === 0), 'processing-no-mutation');
    });

    await scenario('result-controls', { result: true }, async ({ page, button, toggle, layout, visible }) => {
      const review = page.locator('.gm-rw-check-list').getByText('Synthetic publication review', { exact: true });
      await review.waitFor(); await visible(review);
      for (const selector of ['.gm-rw-more-actions', '.gm-rw-version-details']) {
        check(!await page.locator(selector).evaluate(e => e.open), 'result-default-closed'); await toggle(selector, 'Enter', true);
      }
      await button('重命名').click(); check(await page.evaluate(() => window.observed.callbacks === 1), 'owner-callback-only');
      check(await button('版本 2').isEnabled(), 'versions-accessible');
      await layout('expanded');
      for (const selector of ['.gm-rw-more-actions', '.gm-rw-version-details']) await toggle(selector, 'Enter', false);
      await layout('closed'); await button('导出 / 分享').click();
      const dialog = page.getByRole('dialog'); await dialog.waitFor();
      check(await dialog.getByRole('button', { name: '视频 MP4', exact: true }).getAttribute('aria-pressed') === 'true', 'default-mp4');
      check(!await page.locator('.gm-rw-export-settings').evaluate(e => e.open), 'export-settings-closed');
      check((await page.locator('.gm-rw-export-summary').textContent()).includes('1080p · 横屏 · 标准字幕'), 'default-export-summary');
      check(await button('检查通过后才能导出').isDisabled(), 'publication-export-gate');
      await layout('export-closed'); await toggle('.gm-rw-export-settings', 'Enter', true);
      stage = 'result-export-select-options';
      // These implicit labels also contain their option text. Match the actual
      // select elements, asserting all three associated labels independently.
      const exportSelects = dialog.locator('.gm-rw-export-grid select');
      check(await exportSelects.count() === 3 && await exportSelects.evaluateAll(es => es.every((e, i) =>
        e.labels.length === 1 && e.labels[0].textContent.startsWith(['清晰度', '画幅', '字幕'][i]))), 'export-associated-labels');
      await exportSelects.nth(0).selectOption('720'); await exportSelects.nth(1).selectOption('9:16');
      await layout('export-expanded'); await toggle('.gm-rw-export-settings', 'Enter', false); await toggle('.gm-rw-export-settings', 'Enter', true);
      check(await exportSelects.nth(0).inputValue() === '720' && await exportSelects.nth(1).inputValue() === '9:16', 'export-choice-preserved');
      stage = 'result-five-formats';
      for (const label of ['动图 GIF', '音频 MP3', '字幕 SRT', '封面图', '视频 MP4']) {
        await dialog.getByRole('button', { name: label, exact: true }).click();
        check(await dialog.getByRole('button', { name: label, exact: true }).getAttribute('aria-pressed') === 'true', 'five-formats');
      }
      await dialog.getByRole('button', { name: '关闭', exact: true }).click();
      stage = 'result-native-pending';
      await page.locator('.gm-rw-metrics select').selectOption('fast');
      await visible(page.getByRole('region', { name: '待保存修改', exact: true }));
      await visible(button('应用修改 → 生成新版本')); await visible(review);
      await layout('pending');
      check(await page.evaluate(() => window.observed.callbacks === 1), 'no-actual-result-mutations');
    });

    await scenario('result-native-space', { result: true }, async ({ page, button, toggle }) => {
      await page.locator('.gm-rw-check-list').getByText('Synthetic publication review', { exact: true }).waitFor();
      // Keep every native Space assertion even when another disclosure fails.
      // Enter is verified separately above; never replace Space with clicks.
      const states = [];
      for (const selector of ['.gm-rw-more-actions', '.gm-rw-version-details']) {
        const summary = page.locator(`${selector} > summary`); await summary.focus(); await summary.press('Space');
        states.push(await summary.evaluate(e => e === document.activeElement && e.parentElement.open));
      }
      await button('导出 / 分享').click();
      await toggle('.gm-rw-export-settings', 'Space', true);
      console.log(JSON.stringify({ resultSpaceMore: states[0], resultSpaceVersions: states[1] }));
      check(states.every(Boolean), 'result-summary-space-intercepted');
    });
    stage = 'source-integrity';
    check(Object.entries(graph.hashes).every(([file, hash]) => sha(readFileSync(resolve(root, file), 'utf8')) === hash), 'source-drift');
  } catch { failures++; throw safeError(); }
  finally {
    clearTimeout(watchdog);
    try { await browser?.close(); } catch { cleanupErrors++; }
    console.log(JSON.stringify({ failures, layouts, contexts, cleanupErrors, timedOut, ...totals, sourceHashes: graph?.hashes,
      savedFile: existsSync(fileURLToPath(import.meta.url)), syntheticOnly: true, builtAppAcceptance: false }));
  }
  check(cleanupErrors === 0 && !timedOut, 'bounded-cleanup');
});