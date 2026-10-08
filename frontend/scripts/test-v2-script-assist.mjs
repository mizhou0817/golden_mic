import assert from 'node:assert/strict';
import { launchBrowser } from './browser.mjs';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

// The actual ScriptAssistant + scriptAssist.ts sources run in a real browser; only the single
// /api/script/assist endpoint exists (intercepted). No server, no model, no other request.
const require = createRequire(import.meta.url);
const read = p => readFileSync(new URL(p, import.meta.url), 'utf8');
const compile = text => ts.transpileModule(text, { fileName: 'actual.tsx', compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React } }).outputText;
const plain = value => JSON.parse(JSON.stringify(value));

// ---- request layer, in Node (no browser needed)
const lib = (() => {
  const module = { exports: {} };
  vm.runInNewContext(compile(read('../src/lib/scriptAssist.ts')), { module, exports: module.exports, Error,
    require: name => { if (name === './appApi') return { createAppRequest: () => async () => { throw Error('network is not part of this test'); } }; throw Error('unexpected ' + name); } });
  return module.exports;
})();
test('assist result parser accepts only a bounded text plus string warnings', () => {
  assert.deepEqual(plain(lib.parseAssistResult({ text: '标题\n正文', warnings: ['请核对数字'] })), { text: '标题\n正文', warnings: ['请核对数字'] });
  for (const bad of [null, [], {}, { text: '' }, { text: '  ', warnings: [] }, { text: 'x', warnings: 'no' }, { text: 'x', warnings: [1] },
    { text: 'x'.repeat(3001), warnings: [] }, { text: 'x', warnings: Array(9).fill('w') }, { text: 'x', warnings: [''] }, { text: 'x', warnings: ['w'.repeat(401)] }]) {
    assert.throws(() => lib.parseAssistResult(bad), /格式不对/, JSON.stringify(bad)?.slice(0, 40));
  }
});
test('assist request validation rejects empty or oversized input before any network call', () => {
  assert.throws(() => lib.validateAssistRequest({ action: 'write', mode: 'voiceover', brief: '   ' }), /要点/);
  assert.throws(() => lib.validateAssistRequest({ action: 'edit', mode: 'voiceover', text: '' }), /稿子是空的/);
  assert.throws(() => lib.validateAssistRequest({ action: 'write', mode: 'voiceover', brief: 'x'.repeat(2001) }), /2000/);
  assert.throws(() => lib.validateAssistRequest({ action: 'edit', mode: 'mixed', text: 'x'.repeat(3001) }), /3000/);
  assert.throws(() => lib.validateAssistRequest({ action: 'edit', mode: 'mixed', text: 'x', instruction: 'x'.repeat(301) }), /300/);
  assert.doesNotThrow(() => lib.validateAssistRequest({ action: 'write', mode: 'voiceover', brief: '要点' }));
  assert.ok(lib.QUICK_EDITS.length >= 3 && lib.QUICK_EDITS.every(item => item.instruction.length <= lib.ASSIST_LIMITS.instruction));
});

// ---- the component, in a real browser
async function open(mode, script, handler) {
  const browser = await launchBrowser({ headless: true });
  const context = await browser.newContext({ offline: true, serviceWorkers: 'block', acceptDownloads: false });
  const requests = [];
  await context.route('**/*', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><meta charset="utf-8"><div id="root"></div>' });
    if (url.pathname === '/api/script/assist' && route.request().method() === 'POST') {
      const body = route.request().postDataJSON(); requests.push(body); return handler(route, body, requests.length);
    }
    return route.abort();
  });
  const page = await context.newPage(); page.setDefaultTimeout(4000);
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await page.goto('http://assist.invalid/');
  await page.addScriptTag({ path: require.resolve('react').replace(/index\.js$/, 'umd/react.development.js') });
  await page.addScriptTag({ path: require.resolve('react-dom').replace(/index\.js$/, 'umd/react-dom.development.js') });
  await page.addStyleTag({ content: read('../src/styles/modes.css').replace(/@import[^;]+;/g, '') });
  await page.addScriptTag({ content: `
    const libModule={exports:{}};(function(module,exports,require){${compile(read('../src/lib/scriptAssist.ts'))}\n})(libModule,libModule.exports,name=>{
      if(name==='./appApi')return{createAppRequest:({signal})=>async(path,init)=>{const r=await fetch(path,{...init,signal});const j=await r.json();
        if(!r.ok){throw Object.assign(new Error(typeof j.detail==='string'?j.detail:'失败'),{status:r.status});}return j;}};throw Error('x '+name);});
    const compModule={exports:{}};(function(module,exports,require){${compile(read('../src/components/ScriptAssistant.tsx'))}\n})(compModule,compModule.exports,name=>{
      if(name==='react')return React;if(name==='react/jsx-runtime')throw Error('classic jsx expected');if(name==='../lib/scriptAssist')return libModule.exports;throw Error('x '+name);});
    const Assistant=compModule.exports.default;
    window.applied=[];
    function Host(){const [script,setScript]=React.useState(${JSON.stringify(script)}),[mode,setMode]=React.useState(${JSON.stringify(mode)});
      window.setScript=setScript;window.setMode=setMode;
      return React.createElement('div',{className:'gm-create-wizard'},React.createElement('output',{id:'script'},script),
        React.createElement(Assistant,{mode,script,disabled:false,maxScript:3000,onApply:t=>{window.applied.push(t);setScript(t);}}));}
    ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(Host));
  ` });
  return { browser, context, page, requests, errors };
}
const ok = (text, warnings = []) => route => route.fulfill({ status: 200, json: { text, warnings } });

test('write: the brief is sent as typed, the result is only a preview until adopted, and adoption can be undone', async () => {
  const draft = '回家看看\n这个周末我们一家人回到老家。';
  const h = await open('voiceover', '旧稿子\n旧内容。', ok(draft, ['稿中的数字 120 不在你给的要点里，可能是 AI 编的，请核对或删掉。']));
  try {
    const { page } = h;
    assert.equal(await page.locator('[data-control=assist-write]').isDisabled(), true, 'no brief yet');
    await page.getByLabel(/写几句要点/).fill('周末回江西老家看望爷爷奶奶'); await page.getByLabel(/中（约 300 字）/).check();
    await page.locator('[data-control=assist-write]').click();
    await page.locator('[data-testid=script-assist-result]').waitFor();
    assert.deepEqual(plain(h.requests), [{ action: 'write', mode: 'voiceover', brief: '周末回江西老家看望爷爷奶奶', instruction: '', length: 'normal' }]);
    assert.equal(await page.locator('#script').textContent(), '旧稿子\n旧内容。', 'nothing replaced before the user adopts');
    assert.match(await page.locator('.gm-assist-preview').textContent(), /回家看看/);
    assert.match(await page.locator('.gm-assist-warnings').textContent(), /120/);
    await page.locator('[data-control=assist-adopt]').click();
    assert.equal(await page.locator('#script').textContent(), draft);
    assert.equal(await page.locator('[data-testid=script-assist-result]').count(), 0);
    await page.locator('[data-control=assist-undo]').click();
    assert.equal(await page.locator('#script').textContent(), '旧稿子\n旧内容。', 'undo restores exactly the previous manuscript');
    assert.equal(await page.locator('[data-control=assist-undo]').count(), 0);
    assert.deepEqual(h.errors, []);
  } finally { await h.context.close(); await h.browser.close(); }
});

test('edit: quick edits and custom requests send the current text; undo disappears once the text is edited by hand', async () => {
  const h = await open('mixed', '标题\n旁白一。\n同期：真实原话。', (route, body) => ok('标题\n更顺的旁白。\n同期：真实原话。')(route));
  try {
    const { page } = h;
    await page.getByRole('button', { name: '精简一些' }).click(); await page.locator('[data-testid=script-assist-result]').waitFor();
    assert.equal(h.requests[0].action, 'edit'); assert.equal(h.requests[0].mode, 'mixed');
    assert.equal(h.requests[0].text, '标题\n旁白一。\n同期：真实原话。'); assert.match(h.requests[0].instruction, /精简/);
    assert.match(await page.locator('[data-testid=script-assist]').textContent(), /同期”原声句会原样保留/);
    await page.locator('[data-control=assist-retry]').click(); await page.waitForFunction(() => window.applied !== undefined);
    await page.waitForTimeout(150); assert.equal(h.requests.length, 2); assert.match(h.requests[1].instruction, /精简/, 'retry repeats the same request');
    await page.locator('[data-control=assist-adopt]').click();
    await page.locator('[data-control=assist-undo]').waitFor();
    await page.evaluate(() => window.setScript('用户又手动改了'));
    await page.locator('[data-control=assist-undo]').waitFor({ state: 'detached' });  // undo would clobber later manual edits, so it is withdrawn
    await page.getByLabel('自定义修改要求').fill('缩短到 100 字'); await page.locator('[data-control=assist-edit]').click();
    await page.locator('[data-testid=script-assist-result]').waitFor();
    assert.deepEqual(plain({ ...h.requests[2], text: '' }), { action: 'edit', mode: 'mixed', text: '', instruction: '缩短到 100 字' });
    assert.equal(h.requests[2].text, '用户又手动改了');
  } finally { await h.context.close(); await h.browser.close(); }
});

test('changing the production mode discards any pending AI draft, a running request and the undo', async () => {
  let release;
  const h = await open('mixed', '标题\n旁白。\n同期：真实原话。', route => new Promise(resolve => { release = () => { route.fulfill({ status: 200, json: { text: '标题\n新旁白。\n同期：真实原话。', warnings: [] } }).catch(() => {}); resolve(); }; }));
  try {
    const { page } = h;
    await page.getByRole('button', { name: '润色，更通顺' }).click(); await page.locator('[data-control=assist-cancel]').waitFor();
    await page.evaluate(() => window.setMode('voiceover'));
    await page.locator('[data-control=assist-cancel]').waitFor({ state: 'detached' });   // the running request was aborted
    release?.(); await page.waitForTimeout(250);
    assert.equal(await page.locator('[data-testid=script-assist-result]').count(), 0, 'a late answer for the old mode never appears');
    assert.equal(await page.locator('[data-testid=script-assist-error]').count(), 0);
    assert.equal(h.requests[0].mode, 'mixed');
  } finally { await h.context.close(); await h.browser.close(); }
  const kept = await open('mixed', '标题\n旁白。', ok('标题\n更顺的旁白。'));
  try {
    await kept.page.getByRole('button', { name: '润色，更通顺' }).click(); await kept.page.locator('[data-testid=script-assist-result]').waitFor();
    await kept.page.evaluate(() => window.setMode('voiceover'));
    await kept.page.locator('[data-testid=script-assist-result]').waitFor({ state: 'detached' });
    assert.equal(await kept.page.locator('#script').textContent(), '标题\n旁白。', 'the manuscript itself is untouched by the mode change');
    await kept.page.evaluate(() => window.setMode('mixed')); await kept.page.waitForTimeout(100);
    assert.equal(await kept.page.locator('[data-testid=script-assist-result]').count(), 0, 'and it does not come back');
  } finally { await kept.context.close(); await kept.browser.close(); }
});

test('edit controls need a manuscript; original-sound mode shows an explanation and no AI controls at all', async () => {
  const empty = await open('voiceover', '', ok('x\ny'));
  try {
    for (const label of ['润色，更通顺', '精简一些', '更适合口播', '语气更庄重']) assert.equal(await empty.page.getByRole('button', { name: label }).isDisabled(), true, label);
    assert.equal(await empty.page.locator('[data-control=assist-edit]').isDisabled(), true);
    assert.match(await empty.page.locator('[data-testid=script-assist]').textContent(), /先在上面的稿子框里写或粘贴内容/);
    assert.equal(empty.requests.length, 0);
  } finally { await empty.context.close(); await empty.browser.close(); }
  const original = await open('original', '标题\n原话。', ok('x\ny'));
  try {
    assert.equal(await original.page.locator('[data-testid=script-assist-unavailable]').count(), 1);
    assert.equal(await original.page.locator('button').count(), 0);
    assert.match(await original.page.locator('[data-testid=script-assist-unavailable]').textContent(), /不能由 AI 写或改/);
  } finally { await original.context.close(); await original.browser.close(); }
});

test('failures are explained, the manuscript is untouched, and a held request can be cancelled without a result', async () => {
  const failing = await open('voiceover', '标题\n稿子内容在这里。', route => route.fulfill({ status: 422, json: { detail: 'AI 没有写出完整的稿件（需要一行标题加正文），请换个说法再试一次。' } }));
  try {
    await failing.page.getByRole('button', { name: '润色，更通顺' }).click();
    await failing.page.locator('[data-testid=script-assist-error]').waitFor();
    assert.match(await failing.page.locator('[data-testid=script-assist-error]').textContent(), /没有写出完整的稿件.*稿子没有被改动/);
    assert.equal(await failing.page.locator('#script').textContent(), '标题\n稿子内容在这里。');
    assert.equal(await failing.page.locator('[data-testid=script-assist-result]').count(), 0);
  } finally { await failing.context.close(); await failing.browser.close(); }
  let release;
  const held = await open('voiceover', '标题\n稿子内容在这里。', route => new Promise(resolve => { release = () => { route.fulfill({ status: 200, json: { text: 'a\nb', warnings: [] } }).catch(() => {}); resolve(); }; }));
  try {
    await held.page.getByRole('button', { name: '精简一些' }).click();
    await held.page.locator('[data-control=assist-cancel]').waitFor();
    for (const label of ['润色，更通顺', '精简一些']) assert.equal(await held.page.getByRole('button', { name: label }).isDisabled(), true, 'no second request while one is running');
    await held.page.locator('[data-control=assist-cancel]').click(); release?.(); await held.page.waitForTimeout(250);
    assert.equal(await held.page.locator('[data-testid=script-assist-result]').count(), 0, 'a cancelled request never shows a late result');
    assert.equal(await held.page.locator('[data-control=assist-cancel]').count(), 0);
    assert.equal(held.requests.length, 1);
    assert.equal(await held.page.locator('#script').textContent(), '标题\n稿子内容在这里。');
  } finally { await held.context.close(); await held.browser.close(); }
});
