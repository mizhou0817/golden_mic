import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import vm from 'node:vm';
import test from 'node:test';
import ts from 'typescript';

// Complete actual modules, in-memory transpilation only. The ONLY transport is
// a synthetic public origin intercepted in an offline disposable Edge context.
// No app host/build, user assets, credentials, persistence, or provider access.
const require = createRequire(import.meta.url);
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const sources = Object.fromEntries([
  ['api', '../src/lib/appApi.ts'], ['report', '../src/lib/workbenchApi.ts'],
  ['sample', '../src/components/ui/SampleView.tsx'],
].map(([key, path]) => [key, { path, text: read(path) }]));
function compile(text, fileName) {
  const result = ts.transpileModule(text, { fileName, reportDiagnostics: true,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React, esModuleInterop: true },
    transformers: { before: [context => {
      const visit = node => ts.isPropertyAccessExpression(node) && node.getText() === 'import.meta.env'
        ? ts.factory.createObjectLiteralExpression([]) : ts.visitEachChild(node, visit, context);
      return node => ts.visitNode(node, visit);
    }] } });
  assert.equal(result.diagnostics.filter(d => d.category === ts.DiagnosticCategory.Error).length, 0);
  return result.outputText;
}
const modules = Object.fromEntries(Object.entries(sources).map(([key, value]) => [key, compile(value.text, value.path)]));
const aliases = { '../../lib/appApi': 'api', '../../lib/workbenchApi': 'report' };
const denied = () => { throw Error('Forbidden sample side effect'); };
function loadParser() {
  const cache = {};
  const load = key => {
    if (cache[key]) return cache[key].exports;
    const module = cache[key] = { exports: {} };
    vm.runInNewContext(modules[key], { exports: module.exports, module, URL, URLSearchParams,
      React: require('react'), fetch: denied, localStorage: { getItem: denied, setItem: denied },
      require: name => name === 'react' ? require('react') : name.endsWith('.css') ? { default: {} }
        : aliases[name] ? load(aliases[name]) : denied(),
    });
    return module.exports;
  };
  return load('sample').parseSample;
}
const parseSample = loadParser();
const fixture = () => ({ read_only: true, task_id: 'sample-public', title: 'Synthetic public sample',
  video_url: '/api/samples/default/video', report: { task_id: 'sample-public', revision: 0, quality: null,
    rows: [0, 1, 2].map(i => ({ sentence_id: 10 + i, sentence: `Synthetic sentence ${i + 1}`,
      duration: .8, start: i * 1.2, end: i * 1.2 + .8, audio_kind: i === 1 ? 'sync' : 'tts',
      shot_id: i, thumb_url: null, description: `Verified fixture scene ${i + 1}`, confidence: .75, is_fallback: false,
      spoken_text: null, replacement_instruction: null,
      visual_beats: [{ beat_id: i, shot_id: i, text: `Beat ${i + 1}`, description: `Fixture detail ${i + 1}`,
        confidence: .6, thumb_url: null, requires_entity_coverage: false }],
    })) } });

test('real sample + report parsers preserve sparse IDs, clocks and supplied scene evidence', () => {
  const input = fixture(), original = structuredClone(input), data = parseSample(input);
  assert.deepEqual(Array.from(data.report.rows, r => [r.sentence_id, r.start, r.end]), [[10, 0, .8], [11, 1.2, 2], [12, 2.4, 3.2]]);
  assert.equal(data.report.rows[1].description, 'Verified fixture scene 2');
  assert.equal(data.report.rows[1].visual_beats[0].text, 'Beat 2');
  assert.deepEqual(input, original);
});
for (const [label, mutate] of [
  ['wrong report identity', x => { x.report.task_id = 'other'; }],
  ['non-readonly', x => { x.read_only = false; }],
  ['duplicate sentence', x => { x.report.rows[1].sentence_id = 10; }],
  ['malformed actual report', x => { x.report.rows[0].audio_kind = 'made-up'; }],
  ['foreign video', x => { x.video_url = 'https://foreign.invalid/video'; }],
  ['credential in video', x => { x.video_url += '?token=untrusted'; }],
  ['wrong revision', x => { x.video_url += '?revision=1'; }],
  ['wrong task thumbnail', x => { x.report.rows[0].thumb_url = '/api/tasks/other/thumbs/0.jpg'; }],
  ['credential in beat thumbnail', x => { x.report.rows[0].visual_beats[0].thumb_url = '/api/tasks/sample-public/thumbs/0.jpg?token=untrusted'; }],
  ['foreign thumbnail', x => { x.report.rows[0].thumb_url = '//foreign.invalid/image.jpg'; }],
  ['missing end', x => { delete x.report.rows[0].end; }],
  ['nonfinite clock', x => { x.report.rows[0].start = NaN; }],
  ['overlapping clock', x => { x.report.rows[1].start = .1; }],
]) test(`strict public sample rejects ${label}`, () => { const x = fixture(); mutate(x); assert.throws(() => parseSample(x)); });
test('tokenless bound report references stay relative; missing media/timing never fabricated', () => {
  const x = fixture(); x.report.rows[0].thumb_url = '/api/tasks/sample-public/thumbs/0.jpg';
  x.video_url = null; delete x.report.rows[0].start; delete x.report.rows[0].end;
  const data = parseSample(x);
  assert.equal(data.report.rows[0].thumb_url, x.report.rows[0].thumb_url);
  assert.equal(data.video, null); assert.equal(data.report.rows[0].start, undefined);
});

test('offline Edge runtime: actual SampleView + parsers + publicRequest; native playback and keyboard, no mutable surface', async () => {
  const { chromium } = require('playwright');
  let browser, context, stage = 'launch', errors = 0, unexpected = 0, downloads = 0;
  const calls = [], evidence = [];
  const temp = mkdtempSync(join(tmpdir(), 'gm-sample-review-'));
  try {
    browser = await chromium.launch({ channel: 'msedge', headless: true,
      args: ['--disable-background-networking', '--disable-component-update', '--no-first-run'] });
    context = await browser.newContext({ offline: true, serviceWorkers: 'block', acceptDownloads: false });
    let response = fixture(), status = 200, missingVideo = false, videoBytes;
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      const headers = request.headers();
      if (url.origin !== 'https://sample.invalid' || request.method() !== 'GET'
        || headers.authorization || headers.cookie || headers['x-task-token'] || headers['x-csrf-token'] || url.search) {
        unexpected++; return route.abort();
      }
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><div id="root"></div>' });
      calls.push(url.pathname);
      if (url.pathname === '/api/samples/default') return route.fulfill({ status, json: response });
      if (url.pathname === '/api/samples/default/video') {
        if (missingVideo) return route.fulfill({ status: 503, body: '' });
        const range = /^bytes=(\d+)-(\d*)$/.exec(headers.range ?? '');
        const start = range ? Number(range[1]) : 0;
        const end = range?.[2] ? Math.min(Number(range[2]), videoBytes.length - 1) : videoBytes.length - 1;
        assert.ok(start <= end && end < videoBytes.length);
        return route.fulfill({ status: range ? 206 : 200, contentType: 'video/mp4',
          headers: { 'Accept-Ranges': 'bytes', 'Content-Length': String(end - start + 1),
            ...(range ? { 'Content-Range': `bytes ${start}-${end}/${videoBytes.length}` } : {}) },
          body: videoBytes.subarray(start, end + 1) });
      }
      unexpected++; return route.abort();
    });
    const page = await context.newPage(); page.setDefaultTimeout(6000);
    page.on('pageerror', () => errors++); page.on('download', () => downloads++);
    await page.goto('https://sample.invalid/');
    stage = 'synthetic-native-media';
    // Installed encoder, lavfi only, exclusive TEMP output. Unlike MediaRecorder
    // WebM this has a finalized seek index; never patch currentTime or events.
    const media = join(temp, 'synthetic-public.mp4');
    const encoded = spawnSync(process.env.GM_SAMPLE_FFMPEG || 'ffmpeg', ['-nostdin', '-v', 'error', '-n',
      '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=12:duration=4', '-an', '-c:v', 'libx264',
      '-g', '12', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', media], { windowsHide: true, timeout: 15000 });
    assert.equal(encoded.status, 0, 'TEMP synthetic encoder must succeed');
    videoBytes = readFileSync(media);
    writeFileSync(join(temp, 'synthetic-report.json'), JSON.stringify(response));
    assert.ok(videoBytes.length > 0);
    await page.addStyleTag({ content: read('../src/components/ui/tokens.css') + read('../src/components/ui/Primitives.module.css') + read('../src/components/ui/uiSampleView.css') });
    await page.addScriptTag({ path: require.resolve('react').replace(/index\.js$/, 'umd/react.development.js') });
    await page.addScriptTag({ path: require.resolve('react-dom').replace(/index\.js$/, 'umd/react-dom.development.js') });
    const bundle = Object.entries(modules).map(([key, code]) => `${JSON.stringify(key)}:function(module,exports,require){${code}\n}`).join(',');
    await page.addScriptTag({ content: `
      const factories={${bundle}}, aliases=${JSON.stringify(aliases)}, cache={};
      function requireActual(name){
        if(name==='react') return React;
        if(name.endsWith('.css')) return {__esModule:true,default:new Proxy({}, {get:(_,key)=>key})};
        const key=aliases[name]||name;
        if(cache[key])return cache[key].exports;
        if(!factories[key])throw Error('Unapproved dependency');
        const module=cache[key]={exports:{}}; factories[key](module,module.exports,requireActual);return module.exports;
      }
      window.denials=0; const deny=()=>{window.denials++;throw Error('Forbidden sample side effect');};
      for(const key of ['localStorage','sessionStorage'])Object.defineProperty(window,key,{get:deny});
      if(navigator.mediaDevices)navigator.mediaDevices.getUserMedia=deny;
      window.open=deny; window.showSaveFilePicker=deny;
      window.backCount=0; const root=ReactDOM.createRoot(document.getElementById('root'));
      const Sample=requireActual('sample').default;
      window.mountSample=()=>ReactDOM.flushSync(()=>root.render(React.createElement(Sample,{key:Math.random(),onBack:()=>window.backCount++})));
      window.mountSample();
    ` });
    stage = 'loaded';
    const cards = page.locator('.gm-sample-storyboard button'), detail = page.locator('#gm-sample-sentence');
    await cards.nth(2).waitFor(); stage = 'native-metadata';
    await page.waitForFunction(() => document.querySelector('video')?.readyState >= 2);
    console.log(JSON.stringify({ sampleMedia: await page.locator('video').evaluate(e => ({ readyState: e.readyState, duration: Number.isFinite(e.duration) ? e.duration : null, error: e.error?.code ?? 0 })) }));
    assert.equal(await detail.getByText('Verified fixture scene 1', { exact: true }).count(), 1);
    stage = 'click-second';
    await cards.nth(1).click();
    await page.waitForFunction(() => Math.abs(document.querySelector('video').currentTime - 1.2) < .001);
    assert.equal(await cards.nth(1).getAttribute('aria-pressed'), 'true');
    assert.equal(await detail.getByText('Fixture detail 2', { exact: true }).count(), 1);
    await page.keyboard.press('Tab');
    assert.equal(await cards.nth(2).evaluate(e => e === document.activeElement && e.matches(':focus-visible')), true);
    await page.keyboard.press('Enter');
    await page.waitForFunction(() => Math.abs(document.querySelector('video').currentTime - 2.4) < .001);
    await page.keyboard.press('Shift+Tab'); await page.keyboard.press('Space');
    assert.equal(await cards.nth(1).getAttribute('aria-pressed'), 'true');
    stage = 'native-playback';
    await cards.nth(0).click();
    // Idempotent native play(), not synthetic timeupdate or patched media clocks.
    await page.locator('video').evaluate(e => { e.muted = true; return e.play(); });
    await page.waitForFunction(() => document.querySelectorAll('.gm-sample-storyboard button')[2].getAttribute('aria-pressed') === 'true');
    assert.equal(await detail.getByText('Synthetic sentence 3', { exact: true }).count(), 1);
    await page.locator('video').evaluate(e => e.pause());
    assert.equal(await page.locator('input,textarea,select,dialog,a,[download],[contenteditable=true]').count(), 0);
    assert.equal(await page.locator('button').count(), 5);
    assert.equal(await page.locator('video').getAttribute('controlslist'), 'nodownload noremoteplayback');
    await page.getByRole('button', { name: '\u8fd4\u56de\u5199\u7a3f', exact: true }).click();
    assert.equal(await page.evaluate(() => window.backCount), 1);
    evidence.push({ case: 'valid', nativePlaybackSelection: true, keyboard: true });
    stage = 'missing-assets'; missingVideo = true;
    await page.evaluate(() => window.mountSample());
    await page.getByRole('alert').waitFor();
    assert.equal(await cards.count(), 3);
    assert.match(await page.getByRole('alert').textContent(), /\u672a\u4f7f\u7528\u66ff\u4ee3\u5a92\u4f53/);
    await cards.nth(1).click(); assert.equal(await cards.nth(1).getAttribute('aria-pressed'), 'true');
    evidence.push({ case: 'missing-assets', truthful: true });
    stage = 'missing-clock'; response = fixture(); response.video_url = null;
    for (const row of response.report.rows) { delete row.start; delete row.end; }
    await page.evaluate(() => window.mountSample()); await cards.nth(2).waitFor();
    await cards.nth(2).click(); assert.equal(await page.locator('video').count(), 0);
    assert.match(await detail.textContent(), /\u4e0d\u6839\u636e\u53e5\u957f\u63a8\u7b97/);
    stage = 'invalid'; response = fixture(); response.video_url = '/api/tasks/foreign/video';
    const mediaBefore = calls.filter(x => x.endsWith('/video')).length;
    await page.evaluate(() => window.mountSample()); await page.getByRole('alert').waitFor();
    assert.equal(await cards.count(), 0); assert.equal(await page.locator('video').count(), 0);
    assert.equal(calls.filter(x => x.endsWith('/video')).length, mediaBefore);
    stage = '503'; status = 503; response = { code: 'sample_unavailable', read_only: true };
    await page.evaluate(() => window.mountSample()); await page.getByRole('alert').waitFor();
    assert.match(await page.getByRole('alert').textContent(), /\u4e0d\u4f1a\u7528\u6f14\u793a\u6570\u636e/);
    assert.equal(await cards.count(), 0);
    const beforeRetry = calls.length;
    await page.getByRole('button', { name: '\u91cd\u65b0\u8bfb\u53d6\u8303\u4f8b', exact: true }).click();
    await page.getByRole('alert').waitFor(); assert.equal(calls.length, beforeRetry + 1);
    assert.equal(await page.evaluate(() => window.denials), 0);
    assert.equal(errors, 0); assert.equal(unexpected, 0); assert.equal(downloads, 0);
    for (const source of Object.values(sources)) assert.equal(read(source.path), source.text, 'source drift');
    console.log(JSON.stringify({ runtime: 'offline native Edge', version: browser.version(), evidence,
      requests: calls.length, unexpected, errors, downloads, sourceSHA256: Object.fromEntries(Object.entries(sources)
        .map(([key, value]) => [key, createHash('sha256').update(value.text).digest('hex')])), temp, syntheticOnly: true }));
  } catch (failure) {
    console.log(JSON.stringify({ assertion: failure.code === 'ERR_ASSERTION' ? { actual: typeof failure.actual === 'number' || typeof failure.actual === 'boolean' ? failure.actual : null,
      expected: typeof failure.expected === 'number' || typeof failure.expected === 'boolean' ? failure.expected : null } : null }));
    assert.fail(`Sample runtime failed at ${stage}; errors=${errors}, unexpected=${unexpected}; raw browser output suppressed`);
  } finally { await context?.close(); await browser?.close(); }
});