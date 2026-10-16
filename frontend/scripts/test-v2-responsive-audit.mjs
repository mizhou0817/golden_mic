import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { collect } from './test-v2-final-browser.mjs';
import { launchBrowser } from './browser.mjs';

// Current complete TSX/React/CSS, not a replacement UI. HTTP is synthetic and
// fail-closed; no app host, real data, devices, provider or export work. Edge's
// touch emulation is not physical iOS/Android or Safari/WebKit acceptance.
const require = createRequire(import.meta.url);
const ORIGIN = 'https://responsive-audit.invalid', TASK = 'responsive-synthetic', TOKEN = 'x'.repeat(43);
const BASE = `/api/tasks/${TASK}`;
const EPOCH = '2026-10-15T00:00:00Z';
const viewports = [[305, 568], [320, 568], [360, 800], [375, 812], [390, 844], [600, 900],
  [640, 900], [768, 1024], [900, 900], [1024, 768], [1280, 900], [1440, 900], [844, 390], [320, 256]];
const source = { take_id: 'synthetic-take', upload_id: 'synthetic-upload', speaker_id: '', start: 10, end: 15,
  asr_text: 'OneTwoThreeFour', score: 1, snr_db: null, precision: 'word',
  words: [{ w: 'One', s: 10.12, e: 10.9 }, { w: 'Two', s: 11, e: 11.7 },
    { w: 'Three', s: 12, e: 12.6 }, { w: 'Four', s: 13, e: 14.8 }] };
function fixture(quote) {
  const mode = quote ? 'original' : 'voiceover';
  const task = { task_id: TASK, title: 'Synthetic responsive result', status: 'done', mode, revision: 2,
    progress: 100, stages: [], created_at: EPOCH, metadata_revision: 0, version_count: 1 };
  const rows = [1, 2].map(sentence_id => ({ sentence_id, sentence: `Synthetic sentence ${sentence_id}`,
    start: (sentence_id - 1) * 5, end: sentence_id * 5, duration: 5, shot_id: null, description: '',
    thumb_url: null, confidence: 1, is_fallback: false, visual_beats: [],
    audio_kind: quote ? 'sync' : 'tts', kind: quote ? 'quote' : 'narration',
    ...(quote ? { source, spoken_text: source.asr_text } : {}) }));
  return { task, context: { ...task, script: '', preferences: { pacing: 'normal' }, timings: [],
    shots: [], current_shots: [], versions: [{ revision: 2, label: 'Synthetic revision', created_at: EPOCH }],
    report: { task_id: TASK, revision: 2, mode, quality: null, rows } },
  checks: { revision: 2, passed: true, pending_count: 0, blocking_count: 0, checks: [] } };
}

test('offline native responsive Workspace, drawer, export and quote touch controls', { timeout: 150000 }, async t => {
  const graph = collect();
  const scripts = ['react', 'react-dom'].map(name => readFileSync(require.resolve(name)
    .replace(/index\.js$/, `umd/${name}.development.js`), 'utf8'));
  const bundle = `const factories={${Object.entries(graph.factories).map(([key, value]) => `${JSON.stringify(key)}:function(module,exports,require){${value}\n}`).join(',')}},paths=${JSON.stringify(graph.paths)},cache={};
    function load(key){if(cache[key])return cache[key].exports;const m=cache[key]={exports:{}};
      factories[key](m,m.exports,name=>name==='react'?React:name.endsWith('.css')?{__esModule:true,default:new Proxy({},{get:(_,k)=>k})}:load(paths[key][name]));return m.exports;}
    window.fixtureRoot=ReactDOM.createRoot(document.getElementById('root'));
    ReactDOM.flushSync(()=>fixtureRoot.render(React.createElement(React.StrictMode,null,React.createElement(load(${JSON.stringify(graph.entry)}).default))));`;
  let browser, watchdog, stage = 'launch', timedOut = false, cleanupErrors = 0, failures = 0;
  const samples = [], interactions = [];
  const totals = { requests: 0, blocked: 0, pageErrors: 0, dialogs: 0, downloads: 0, devices: 0 };
  const check = (value, code) => { if (!value) { stage = code; const e = new Error(code); e.stack = ''; throw e; } };
  try {
    // Edge, Chrome or WebKit, whichever is installed (GM_BROWSER); same offline launch options.
    browser = await launchBrowser({ headless: true, timeout: 8000,
      proxy: { server: 'http://egress-disabled.invalid:9', bypass: '<-loopback>' },
      args: ['--disable-background-networking', '--disable-component-update', '--no-first-run', '--disable-quic',
        '--force-webrtc-ip-handling-policy=disable_non_proxied_udp', '--host-resolver-rules=MAP * ~NOTFOUND'] });
    watchdog = setTimeout(() => { timedOut = true; void browser.close().catch(() => cleanupErrors++); }, 135000);
    for (const kind of ['create', 'result', 'quote']) await t.test(kind, { timeout: 45000 }, async () => {
      let context, page, failed = false;
      const counts = { requests: 0, blocked: 0, pageErrors: 0, dialogs: 0, downloads: 0, devices: 0 };
      try {
        stage = `${kind}-setup`;
        const f = fixture(kind === 'quote');
        context = await browser.newContext({ viewport: { width: 1440, height: 900 }, hasTouch: true,
          serviceWorkers: 'block', acceptDownloads: false, permissions: [], locale: 'zh-CN' });
        await context.routeWebSocket('**/*', socket => { counts.blocked++; socket.close(); });
        await context.addInitScript(() => {
          window.deviceAttempts = 0;
          const deny = () => { window.deviceAttempts++; throw new DOMException('Offline fixture denial', 'NotAllowedError'); };
          for (const key of ['WebSocket', 'WebTransport', 'EventSource', 'RTCPeerConnection', 'Worker', 'SharedWorker', 'open',
            'showOpenFilePicker', 'showSaveFilePicker', 'showDirectoryPicker']) Object.defineProperty(window, key, { configurable: true, value: deny });
          if (navigator.mediaDevices) for (const key of ['getUserMedia', 'getDisplayMedia', 'enumerateDevices'])
            Object.defineProperty(navigator.mediaDevices, key, { configurable: true, value: deny });
          Object.defineProperty(navigator, 'sendBeacon', { configurable: true, value: deny });
        });
        await context.route('**/*', async route => {
          try {
            const request = route.request(), u = new URL(request.url()), h = request.headers(); counts.requests++;
            check(u.origin === ORIGIN && !u.username && !u.password && !u.hash && request.method() === 'GET'
              && !h.authorization && !h.cookie && !h['x-csrf-token'], 'exact-offline-get');
            if (request.isNavigationRequest() && !u.search && ['/', `/tasks/${TASK}`, '/samples/default'].includes(u.pathname))
              return await route.fulfill({ contentType: 'text/html', body: '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Synthetic responsive fixture</title><div id="root"></div></html>' });
            if (!u.search && !h['x-task-token'] && u.pathname.startsWith('/api/config/')) {
              const value = u.pathname === '/api/config/workspace' ? { local_history: false, generative_fill_available: false }
                : u.pathname === '/api/config/availability' ? { status: 'ready' }
                  : u.pathname === '/api/config/limits' ? { max_files: 20, max_upload_bytes: 500 * 1024 ** 2,
                    max_total_upload_bytes: 5 * 1024 ** 3, max_script_length: 8000 } : null;
              check(value !== null, 'exact-public-path'); return await route.fulfill({ json: value });
            }
            if (!u.search && !h['x-task-token'] && u.pathname === '/api/samples/default')
              return await route.fulfill({ status: 503, json: { detail: 'Synthetic sample unavailable' } });
            if (!u.search && h['x-task-token'] === TOKEN && [BASE, `${BASE}/workbench/context`, `${BASE}/checks`].includes(u.pathname))
              return await route.fulfill({ json: u.pathname === BASE ? f.task : u.pathname.endsWith('/context') ? f.context : f.checks });
            if (!u.search && kind === 'quote' && h['x-task-token'] === TOKEN && /^\/api\/tasks\/responsive-synthetic\/quotes\/[12]\/takes$/.test(u.pathname))
              return await route.fulfill({ json: { takes: [] } });
            if (!u.search && kind === 'quote' && h['x-task-token'] === TOKEN && /^\/api\/tasks\/responsive-synthetic\/waves\/[12]\.json$/.test(u.pathname))
              return await route.fulfill({ json: { start: 10, end: 15, interval_ms: 20,
                samples: Array.from({ length: 250 }, (_, i) => Math.sin(i * .5) * .4) } });
            if ([`${BASE}/video`, `${BASE}/poster`].includes(u.pathname) && !h['x-task-token']
                && [...u.searchParams].length === 2 && u.searchParams.getAll('token').length === 1
                && u.searchParams.get('token') === TOKEN && u.searchParams.getAll('revision').length === 1
                && u.searchParams.get('revision') === '2') return await route.fulfill({ status: 503, body: '' });
            counts.blocked++; await route.abort();
          } catch { counts.blocked++; await route.abort().catch(() => {}); }
        });
        page = await context.newPage(); page.setDefaultTimeout(5000); page.setDefaultNavigationTimeout(5000);
        page.on('pageerror', () => counts.pageErrors++); page.on('crash', () => counts.pageErrors++);
        page.on('dialog', dialog => { counts.dialogs++; void dialog.dismiss(); });
        page.on('download', download => { counts.downloads++; void download.cancel(); });
        await page.goto(ORIGIN + (kind === 'create' ? '/' : `/tasks/${TASK}`));
        await page.evaluate(({ TASK, TOKEN, EPOCH }) => {
          localStorage.setItem('golden-mic.history.v1', JSON.stringify(Array.from({ length: 25 }, (_, i) => ({
            taskId: i ? `synthetic-history-${i}` : TASK, accessToken: TOKEN, title: `Synthetic history ${i}`,
            status: 'done', revision: 2, createdAt: EPOCH }))));
        }, { TASK, TOKEN, EPOCH });
        await page.addStyleTag({ content: graph.css });
        for (const content of scripts) await page.addScriptTag({ content });
        await page.addScriptTag({ content: bundle });
        if (kind === 'create') {
          await page.getByRole('heading', { name: '写稿', exact: true }).waitFor();
          // Writing helpers intentionally do not exist for an empty manuscript.
          await page.getByRole('textbox', { name: '新闻稿（第一行标题，下一行正文）', exact: true })
            .fill('合成界面检查\n这是用于验证手机平板和电脑排版的合成新闻稿件。');
        }
        else await page.locator('.gm-rw-next').getByRole('button', { name: '开始导出', exact: true }).waitFor();

        async function geometry(label, font, sizes = viewports, scope = 'body') {
          for (const [width, height] of sizes) {
            stage = `${kind}-${label}-${width}-${height}-${font}`;
            await page.setViewportSize({ width, height });
            const metric = await page.evaluate(scope => {
              const doc = document.documentElement, root = document.querySelector(scope);
              const visible = e => e.checkVisibility({ visibilityProperty: true, opacityProperty: true });
              const controls = [...root.querySelectorAll('button,input,textarea,select,summary,a[href]')].filter(visible);
              const outside = controls.filter(e => {
                const r = e.getBoundingClientRect(); if (r.left >= -1 && r.right <= doc.clientWidth + 1) return false;
                // A real bounded filmstrip is intentional local scrolling, not a
                // document-overflow exemption or blanket overflow:hidden fix.
                const strip = e.closest('.gm-rw-strip'); if (!strip) return true;
                const s = strip.getBoundingClientRect(), css = getComputedStyle(strip);
                return !['auto', 'scroll'].includes(css.overflowX) || s.left < -1 || s.right > doc.clientWidth + 1;
              });
              const r = root.getBoundingClientRect();
              return { client: doc.clientWidth, scroll: doc.scrollWidth, controls: controls.length, outside: outside.length,
                rootLeft: r.left, rootRight: r.right, font: getComputedStyle(doc).fontSize };
            }, scope);
            samples.push({ kind, label, width, height, requestedFont: font, ...metric });
            check(metric.scroll <= metric.client + 1 && metric.outside === 0 && metric.controls > 0, stage);
            // Regular mobile text is intentionally 17px at <=600px. The real
            // header control, not a forced CSS override, supplies 20px large text.
            check(metric.font === `${font === 16 && width <= 600 ? 17 : font}px`, 'actual-large-text');
          }
        }
        for (const font of [16, 20]) {
          if (font === 20) await page.locator('.shell-font-toggle').click();
          await geometry('main', font);
          if (kind === 'create') {
            for (const [width, height] of viewports.filter(([w]) => w <= 900)) {
              stage = `drawer-${width}-${height}-${font}`; await page.setViewportSize({ width, height });
              const menu = page.getByRole('button', { name: '打开导航菜单', exact: true }); await menu.click();
              const drawer = page.getByRole('dialog', { name: '工作区导航菜单', exact: true });
              const close = drawer.getByRole('button', { name: '关闭导航菜单', exact: true });
              await close.focus(); await page.keyboard.press('Shift+Tab');
              check(await drawer.locator('.shell-receipt-note > summary').evaluate(e => e === document.activeElement), 'drawer-last-summary');
              await page.keyboard.press('Tab'); check(await close.evaluate(e => e === document.activeElement), 'drawer-tab-wrap');
              await drawer.evaluate(e => { e.scrollTop = e.scrollHeight; });
              const closeFacts = await close.evaluate(e => { const r = e.getBoundingClientRect(); return {
                width: r.width, height: r.height, visible: r.top >= -1 && r.bottom <= innerHeight + 1,
                hit: e.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)),
              }; });
              check(closeFacts.visible && closeFacts.hit && closeFacts.width >= 44 && closeFacts.height >= 44, 'drawer-close-reachable');
              interactions.push({ kind: 'drawer', width, height, font, ...closeFacts });
              await close.click(); check(await menu.evaluate(e => e === document.activeElement), 'drawer-focus-return');
            }
            await page.setViewportSize({ width: 1440, height: 900 });
            await page.locator('.gm-writing-tools > summary').press('Enter');
            await geometry('writing-open', font);
            await page.locator('.gm-writing-tools > summary').press('Space');
          } else if (kind === 'result') {
            await page.setViewportSize({ width: 1440, height: 900 });
            await page.locator('.gm-rw-selected').evaluate(e => window.scrollTo(0, e.getBoundingClientRect().top + scrollY + 180));
            await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
            const sticky = await page.evaluate(() => {
              const panel = document.querySelector('.gm-rw-selected'), r = panel.getBoundingClientRect(),
                grid = panel.parentElement.getBoundingClientRect(), header = document.querySelector('.shell-header').getBoundingClientRect();
              return { panel: r.top, height: r.height, gridBottom: grid.bottom, viewport: innerHeight,
                topOffset: parseFloat(getComputedStyle(panel).top), header: header.bottom };
            });
            // The current design deliberately lets a tall editor scroll with the
            // document. Sticky cannot pin its top once its grid bottom is reached.
            // Require the real pinned position only when geometry permits it.
            check(sticky.topOffset >= sticky.header, 'sticky-offset-clears-header');
            if (sticky.height <= sticky.viewport - sticky.topOffset && sticky.gridBottom >= sticky.topOffset + sticky.height)
              check(sticky.panel >= sticky.header - 1, 'sticky-editor-not-under-header');
            interactions.push({ kind: 'sticky', font, ...sticky });
            const opener = page.getByRole('button', { name: '导出 / 分享', exact: true }); await opener.click();
            const dialog = page.getByRole('dialog', { name: '导出 / 分享', exact: true });
            await dialog.locator('.gm-rw-export-settings > summary').focus(); await page.keyboard.press('Tab');
            check(await dialog.getByRole('button', { name: '开始导出', exact: true }).evaluate(e => e === document.activeElement), 'export-summary-native-tab');
            await geometry('export', font, viewports, '.gm-rw-export');
            await dialog.locator('.gm-rw-export-settings > summary').press('Space');
            await geometry('export-expanded', font, viewports, '.gm-rw-export');
            const start = dialog.getByRole('button', { name: '开始导出', exact: true });
            await start.scrollIntoViewIfNeeded();
            const reachable = await start.evaluate(e => { const r = e.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; });
            check(reachable, 'short-export-submit-reachable');
            await start.focus(); await page.keyboard.press('Tab');
            check(await dialog.getByRole('button', { name: '关闭', exact: true }).evaluate(e => e === document.activeElement), 'export-tab-wrap');
            // Native details remain mounted across dialog close. Restore the
            // state explicitly, rather than assuming Escape recreates the node.
            await dialog.locator('.gm-rw-export-settings > summary').press('Space');
            check(!await dialog.locator('.gm-rw-export-settings').evaluate(e => e.open), 'export-native-collapse');
            await page.keyboard.press('Escape'); check(await opener.evaluate(e => e === document.activeElement), 'export-focus-return');
            interactions.push({ kind: 'export', font, reachable, nativeTab: true, restoredFocus: true });
          } else {
            await page.setViewportSize({ width: 390, height: 844 });
            const words = page.locator('.gm-quote-words button'); check(await words.count() === 4, 'complete-word-evidence');
            const sizes = await words.evaluateAll(es => es.map(e => { const r = e.getBoundingClientRect(); return { w: r.width, h: r.height }; }));
            check(sizes.every(s => s.w >= 44 && s.h >= 44), 'quote-touch-target');
            await words.nth(1).tap(); await words.nth(2).tap();
            check((await words.evaluateAll(es => es.map(e => e.getAttribute('aria-pressed')))).join() === 'false,true,true,false', 'native-touch-contiguous-selection');
            await page.getByRole('button', { name: '记下剪短', exact: true }).tap();
            await page.getByRole('region', { name: '待保存修改', exact: true }).waitFor();
            await page.getByRole('button', { name: '撤销这句的待应用修改', exact: true }).click();
            interactions.push({ kind: 'quote-touch', font, targets: sizes.length, contiguousSelection: true, noServerWrite: true });
          }
        }
        stage = `${kind}-text-contrast`;
        const contrast = await page.evaluate(kind => {
          const luminance = color => {
            const components = color.match(/[\d.]+/g).slice(0, 3).map(Number).map(v => {
              const c = v / 255; return c <= .04045 ? c / 12.92 : ((c + .055) / 1.055) ** 2.4;
            });
            return components[0] * .2126 + components[1] * .7152 + components[2] * .0722;
          };
          return [...document.querySelectorAll(kind === 'create' ? '.gm-tip-more,.shell-work-status[data-status=done]'
            : '.gm-rw-badge[data-tone=olive],.gm-rw-cell-text,.shell-work-status[data-status=done]')]
            .filter(e => e.checkVisibility()).map(e => {
              const style = getComputedStyle(e), bg = e.matches('.gm-tip-more') ? getComputedStyle(e.closest('.gm-tip')).backgroundColor : style.backgroundColor;
              const a = luminance(style.color), b = luminance(bg);
              return { ratio: (Math.max(a, b) + .05) / (Math.min(a, b) + .05), opaqueBackground: !bg.startsWith('rgba') };
            });
        }, kind);
        check(contrast.length > 0 && contrast.every(c => c.opaqueBackground && c.ratio >= 4.5), 'readable-text-contrast');
        interactions.push({ kind: 'text-contrast', screen: kind, samples: contrast.length, minimum: Math.min(...contrast.map(c => c.ratio)) });
        stage = `${kind}-boundaries`;
        counts.devices = await page.evaluate(() => window.deviceAttempts);
        check(Object.entries(counts).every(([key, value]) => key === 'requests' || value === 0), 'offline-boundaries');
      } catch { failed = true; failures++; const e = new Error(`responsive-audit:${stage}`); e.stack = ''; throw e; }
      finally {
        for (const [key, value] of Object.entries(counts)) totals[key] += value;
        try { await context?.close(); } catch { cleanupErrors++; }
        console.log(JSON.stringify({ scenario: kind, passed: !failed, failureStage: failed ? stage : null, ...counts }));
      }
    });
    stage = 'source-integrity';
    const current = collect();
    check(JSON.stringify(current.hashes) === JSON.stringify(graph.hashes), 'responsive-source-drift');
  } finally {
    clearTimeout(watchdog); try { await browser?.close(); } catch { cleanupErrors++; }
    console.log(JSON.stringify({ responsiveAudit: { failures, timedOut, cleanupErrors, samples, interactions, ...totals,
      browserClosed: !browser?.isConnected(), syntheticHTTP: true, builtAcceptance: false,
      usable305Model: true, physicalDevices: false, webkit: false, axeAudit: 'separate-not-part-of-layout-run' } }));
  }
  check(!timedOut && cleanupErrors === 0 && failures === 0, 'responsive-cleanup-or-case-failure');
});