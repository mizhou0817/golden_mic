import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import { readFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import React from 'react';
import ts from 'typescript';

// Actual AST-selected presentation blocks and the complete Processing component.
// Only enclosing state/HTTP/media boundaries are synthetic. No source rewriting,
// host, build, storage, downloads, provider work, or raw browser artifacts.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const deny = () => assert.fail('Unexpected external effect');
const read = p => readFileSync(resolve(root, p), 'utf8');
const parse = p => ts.createSourceFile(p, read(p), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const resultAST = parse('src/components/ResultWorkbench.tsx');
const wizardAST = parse('src/components/CreateWizard.tsx');
const printer = ts.createPrinter();
function find(tree, predicate) {
  const found = [];
  function visit(n) { if (predicate(n)) found.push(n); ts.forEachChild(n, visit); }
  visit(tree); assert.equal(found.length, 1, 'Unique actual source node'); return found[0];
}
const print = (n, tree) => printer.printNode(ts.EmitHint.Unspecified, n, tree);
function declaration(tree, name) {
  const n = find(tree, n => (ts.isVariableDeclaration(n) || ts.isFunctionDeclaration(n))
    && (ts.isArrayBindingPattern(n.name) ? n.name.elements[0].name.getText(tree) : n.name?.getText(tree)) === name);
  return (ts.isVariableDeclaration(n) ? 'const ' : '') + print(n, tree) + ';';
}
const candidateJSX = print(find(resultAST, n => ts.isJsxElement(n) && n.openingElement.attributes.properties.some(a =>
  ts.isJsxAttribute(a) && a.name.text === 'className' && a.initializer?.text === 'gm-rw-candidates')), resultAST);
const candidateCode = `function Candidates({shots, initialPending = {}}) {
  const {useState} = React;
  ${declaration(resultAST, 'candidateMore')}
  const [selected,setSelected] = useState(1);
  const [pending,setPending] = useState(initialPending);
  const context = {shots}, row = {sentence_id:selected}, root = '/synthetic';
  const recording=false, micStarting=false, editText=null, instruction='', historical=false;
  const rows=[], cells={current:new Map()}, editorPanel={current:null}, video={current:null};
  const setEditText=()=>{}, setInstruction=()=>{}, setNotice=()=>{}, rowStart=()=>null;
  const media=()=> 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
  const patchSentence=(id,patch)=>setPending(old=>({...old,...patch}));
  ${declaration(resultAST, 'candidates')}
  ${declaration(resultAST, 'visibleCandidates')}
  ${declaration(resultAST, 'selectRow')}
  return <section>${candidateJSX}<button type="button" id="same" onClick={()=>selectRow(selected)}>Same</button>
    <button type="button" id="next" onClick={()=>selectRow(selected+1)}>Next</button></section>;
}`;
const transcriptCode = `function Transcript({mode, upload, initialRows=[]}) {
  const {useState}=React;
  const [rows,setRows]=useState(initialRows), analysis={rows};
  const speakers=[{id:'sp1',name:'测试说话人',auto_label:'说话人 1'}];
  const [expandedTranscripts,setExpandedTranscripts]=useState({});
  const clock=seconds=>String(seconds);
  const pickTranscript=(id,segment,checked)=>setRows(checked
    ? [...rows,{source_hint:{upload_id:id,seg_id:segment.id}}]
    : rows.filter(r=>r.source_hint.seg_id!==segment.id));
  ${declaration(wizardAST, 'transcriptList')}
  return transcriptList(upload);
}`;
function compile(code, module = ts.ModuleKind.None) {
  const output = ts.transpileModule(code, { fileName: 'actual.tsx', reportDiagnostics: true,
    compilerOptions: { target: ts.ScriptTarget.ES2022, module, jsx: ts.JsxEmit.React, esModuleInterop: true } });
  assert.equal(output.diagnostics.filter(d => d.category === ts.DiagnosticCategory.Error).length, 0);
  return output.outputText;
}
const text = n => n == null || typeof n === 'boolean' ? '' : Array.isArray(n) ? n.map(text).join('')
  : typeof n === 'object' ? text(n.props?.children) : String(n);
const nodes = n => Array.isArray(n) ? n.flatMap(nodes) : !n || typeof n !== 'object' ? [] : [n, ...nodes(n.props?.children)];
function component(code, name, props) {
  const hooks = []; let cursor = 0, tree;
  const react = { ...React, useState(seed) { const i = cursor++;
    if (!hooks[i]) hooks[i] = { value: typeof seed === 'function' ? seed() : seed };
    return [hooks[i].value, next => { hooks[i].value = typeof next === 'function' ? next(hooks[i].value) : next; }];
  } };
  const fn = vm.runInNewContext(compile(code) + `;${name}`, { React: react, window: { matchMedia: () => ({ matches: false }) } });
  const render = () => { cursor = 0; tree = fn(props); return tree; }; render();
  return { render, get tree() { return tree; }, all: () => nodes(tree), click(n) { n.props.onClick(); render(); } };
}
const shot = (id, patch = {}) => ({ shot_id: id, description: `synthetic shot ${id}`, selectable: true, unused: true, media_origin: 'source', ...patch });
const choices = w => w.all().filter(n => n.type === 'button' && Object.hasOwn(n.props, 'aria-pressed'));
const disclosure = w => w.all().find(n => n.type === 'button' && Object.hasOwn(n.props, 'aria-expanded'));
for (const count of [0, 3, 4, 5]) test(`candidate ${count}: first three, exact remainder, native disclosure and fold`, () => {
  const w = component(candidateCode, 'Candidates', { shots: Array.from({ length: count }, (_, i) => shot(i)) });
  assert.equal(choices(w).length, Math.min(3, count));
  if (count <= 3) { assert.equal(disclosure(w), undefined); return; }
  const button = disclosure(w);
  assert.equal(text(button), `还有 ${count - 3} 个镜头 ▾`);
  assert.equal(button.props.type, 'button'); assert.equal(button.props['aria-expanded'], false);
  for (const key of ['role', 'tabIndex', 'onKeyDown', 'onKeyUp']) assert.equal(button.props[key], undefined);
  w.click(button); assert.equal(choices(w).length, count); assert.equal(disclosure(w).props['aria-expanded'], true);
  assert.equal(text(disclosure(w)), '收起 ▴'); w.click(disclosure(w)); assert.equal(choices(w).length, 3);
});
test('candidate eligibility and missing-description disabled behavior remain intact', () => {
  const shots = [shot(1), shot(2, { unused: false }), shot(3, { selectable: false }), shot(4, { media_origin: 'generated' }),
    shot(5, { media_origin: undefined }), shot(6, { description: '' }), shot(7), shot(8)];
  const before = JSON.stringify(shots), w = component(candidateCode, 'Candidates', { shots });
  assert.deepEqual(choices(w).map(n => n.key), ['1', '6', '7']);
  assert.equal(choices(w)[1].props.disabled, true); assert.equal(text(disclosure(w)), '还有 1 个镜头 ▾');
  w.click(disclosure(w)); assert.deepEqual(choices(w).map(n => n.key), ['1', '6', '7', '8']); assert.equal(JSON.stringify(shots), before);
});
test('candidate selection marker survives folding; same row keeps expansion, another row resets', () => {
  const w = component(candidateCode, 'Candidates', { shots: [1, 2, 3, 4, 5].map(id => shot(id)) });
  w.click(disclosure(w)); w.click(choices(w)[4]); assert.equal(choices(w)[4].props['aria-pressed'], true);
  w.click(disclosure(w)); w.click(disclosure(w)); assert.equal(choices(w)[4].props['aria-pressed'], true);
  w.click(w.all().find(n => n.props.id === 'same')); assert.equal(disclosure(w).props['aria-expanded'], true);
  w.click(w.all().find(n => n.props.id === 'next')); assert.equal(disclosure(w).props['aria-expanded'], false);
  w.click(disclosure(w)); w.click(choices(w)[4]); assert.ok(choices(w).every(n => !n.props['aria-pressed']));
});

function load(input, cache = new Map()) {
  const path = resolve(root, input);
  if (cache.has(path)) return cache.get(path).exports;
  if (path.endsWith('.css')) return { default: new Proxy({}, { get: (_, key) => String(key) }), __esModule: true };
  if (path.endsWith('.json')) return JSON.parse(readFileSync(path, 'utf8'));
  const output = ts.transpileModule(readFileSync(path, 'utf8'), { fileName: path,
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, esModuleInterop: true },
    transformers: { before: [ctx => source => {
      function visit(n) {
        if (ts.isPropertyAccessExpression(n) && ts.isMetaProperty(n.expression) && n.name.text === 'env') return ts.factory.createIdentifier('__env');
        return ts.visitEachChild(n, visit, ctx);
      }
      return ts.visitNode(source, visit);
    }] },
  });
  const module = { exports: {} }; cache.set(path, module);
  vm.runInNewContext(output.outputText, { module, exports: module.exports, React, __env: {}, fetch: deny,
    require(name) { if (name === 'react') return React; assert.ok(name.startsWith('.'));
      const p = resolve(dirname(path), name), target = [p, p + '.ts', p + '.tsx'].find(existsSync);
      assert.ok(target); return load(target, cache); },
  }, { timeout: 5000 }); return module.exports;
}
const Processing = load('src/components/Processing.tsx').default;
const processTree = patch => Processing({ task: { status: 'failed', mode: 'original', revision: 0, stages: [],
  current_stage: 6, errorKind: 'quote_missing', ...patch }, upload: null, uploading: false, now: 0, busy: false,
  onCancel: deny, onRetry: deny, onRefresh: deny, onEdit: deny });
const failure = tree => nodes(tree).find(n => n.props.role === 'alert');
for (const [label, badRows, count] of [
  ['known', [2, 4], 2], ['duplicates-invalid', [2, 2, 4, 0, -1, 1.5, NaN, Infinity, '3', null, true, 100001], 2],
  ['upper-bound', [100000], 1], ['absent', undefined, 0], ['null', null, 0], ['empty', [], 0],
  ['wrong-shape', '2,4', 0], ['invalid-only', [0, -1, NaN, '1'], 0],
]) test(`quote missing ${label}: count only valid distinct known rows in headline AND failure`, () => {
  const tree = processTree({ badRows });
  const expected = count ? `有 ${count} 句原话在素材里没找到` : '有原话在素材里没找到';
  assert.ok(text(nodes(tree).find(n => n.props.role === 'status')).includes(expected));
  if (count) assert.ok(text(failure(tree)).includes(expected));
  else assert.doesNotMatch(text(tree), /有 \d+ 句原话/);
  assert.equal(failure(tree).props['data-error-kind'], 'quote_missing');
});
for (const kind of ['quote_unverified', undefined, 'qc', 'shortage']) test(`stage six ${kind}: do not infer missing quotes or counts from stage/badRows`, () => {
  const tree = processTree({ errorKind: kind, badRows: [1, 3] });
  assert.doesNotMatch(text(tree), /有 \d+ 句原话在素材里没找到/); assert.ok(failure(tree));
});
test('nonfailure stage six and unknown stages never acquire a missing-quote count', () => {
  for (const status of ['running', 'queued', 'done']) {
    const tree = processTree({ status, badRows: [2], current_stage: null });
    assert.doesNotMatch(text(tree), /句原话在素材里没找到/); assert.equal(failure(tree), undefined);
    assert.equal(nodes(tree).filter(n => n.type === 'li' && n.props['data-state'] === 'unknown').length, 10);
  }
});
const upload = { id: 'synthetic_upload', transcript: Array.from({ length: 10 }, (_, i) => ({ id: `s${i}`, text: `原话 ${i}`, start: i, end: i + 1, speaker_id: 'sp1' })) };
for (const mode of ['voiceover', 'mixed', 'original']) test(`transcript ${mode}: semantic rows, eight-row fold, source checkbox and speaker badge`, () => {
  const w = component(transcriptCode, 'Transcript', { mode, upload });
  const rows = () => w.all().filter(n => n.props.className === 'gm-transcript-line');
  assert.equal(rows().length, 8);
  for (const row of rows()) {
    assert.equal(row.type, mode === 'original' ? 'label' : 'div');
    const controls = nodes(row).filter(n => ['input', 'select', 'textarea', 'button'].includes(n.type));
    assert.equal(controls.length, mode === 'original' ? 1 : 0);
    assert.ok(text(row).includes('测试说话人'));
    if (mode === 'original') { assert.equal(controls[0].props.type, 'checkbox'); assert.ok(controls[0].props['aria-label'].startsWith('选入原话：')); }
  }
  const more = () => w.all().find(n => n.type === 'details');
  assert.equal(more().props.open, false);
  more().props.onToggle({ currentTarget: { open: true } }); w.render(); assert.equal(rows().length, 10);
  more().props.onToggle({ currentTarget: { open: false } }); w.render(); assert.equal(rows().length, 8);
});

test('offline native Edge: Enter/Space disclosure, selection, row reset, C label and checkbox keyboard', async () => {
  let browser, context, requests = 0, errors = 0, stage = 0;
  try {
    const { chromium, expect } = require('@playwright/test');
    browser = await chromium.launch({ channel: 'msedge', headless: true,
      args: ['--disable-background-networking', '--disable-component-update', '--no-first-run'] });
    context = await browser.newContext({ offline: true, serviceWorkers: 'block', acceptDownloads: false });
    await context.route('**/*', route => { requests++; return route.abort(); });
    const page = await context.newPage(); page.setDefaultTimeout(5000); page.on('pageerror', () => errors++);
    await page.setContent('<!doctype html><html><body><div id="root"></div></body></html>');
    await page.addScriptTag({ path: require.resolve('react').replace(/index\.js$/, 'umd/react.development.js') });
    await page.addScriptTag({ path: require.resolve('react-dom').replace(/index\.js$/, 'umd/react-dom.development.js') });
    await page.addScriptTag({ content: compile(candidateCode + transcriptCode + `
      const root=ReactDOM.createRoot(document.getElementById('root'));
      window.showCandidates=shots=>ReactDOM.flushSync(()=>root.render(React.createElement(Candidates,{shots})));
      window.showTranscript=props=>ReactDOM.flushSync(()=>root.render(React.createElement(Transcript,{...props,key:props.mode})));
    `) });
    stage = 1; await page.evaluate(shots => window.showCandidates(shots), [1, 2, 3, 4].map(id => shot(id)));
    const choices = page.locator('.gm-rw-candidates button[aria-pressed]'), more = page.locator('.gm-rw-candidates button[aria-expanded]');
    await expect(choices).toHaveCount(3); await expect(more).toHaveText('还有 1 个镜头 ▾');
    await more.focus(); await page.keyboard.press('Enter'); await expect(choices).toHaveCount(4);
    stage = 2; await choices.nth(3).focus(); await page.keyboard.press('Space'); await expect(choices.nth(3)).toHaveAttribute('aria-pressed', 'true');
    await more.focus(); await page.keyboard.press('Space'); await expect(choices).toHaveCount(3);
    await expect(more).toBeFocused(); await page.keyboard.press('Enter'); await expect(choices.nth(3)).toHaveAttribute('aria-pressed', 'true');
    await page.locator('#next').click(); await expect(choices).toHaveCount(3); await expect(more).toHaveAttribute('aria-expanded', 'false');
    stage = 3;
    for (const mode of ['voiceover', 'mixed', 'original']) {
      await page.evaluate(props => window.showTranscript(props), { mode, upload });
      const rows = page.locator('.gm-transcript-line'); await expect(rows).toHaveCount(8);
      assert.equal(await rows.first().evaluate(e => e.tagName), mode === 'original' ? 'LABEL' : 'DIV');
      await expect(page.locator('.gm-transcript-line input')).toHaveCount(mode === 'original' ? 8 : 0);
      if (mode === 'original') {
        await rows.first().locator('span').last().click(); await expect(rows.first().locator('input')).toBeChecked();
        await rows.first().locator('input').focus(); await page.keyboard.press('Space'); await expect(rows.first().locator('input')).not.toBeChecked();
      }
      const summary = page.locator('summary'); await summary.focus(); await page.keyboard.press('Enter'); await expect(rows).toHaveCount(10);
      await summary.focus(); await page.keyboard.press('Space'); await expect(rows).toHaveCount(8);
    }
    assert.equal(requests, 0); assert.equal(errors, 0);
    console.log(JSON.stringify({ nativeDesignDetails: 'passed', browser: browser.version(), requests, pageErrors: errors, rawArtifacts: false }));
  } catch { assert.fail(`Offline native contract failed at stage ${stage}; no raw DOM/error retained`); }
  finally { await context?.close(); await browser?.close(); }
});