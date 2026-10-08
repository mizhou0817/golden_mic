import assert from 'node:assert/strict';
import { launchBrowser } from './browser.mjs';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

// Unit tests are default. GM_DIALOG_BROWSER=1 explicitly opts into an owned,
// offline Edge process; no host, pipeline, build, trace, screenshot or DOM dump.
// GM_DIALOG_BASELINE=1 is a diagnostic of the unmodified native-only component.
const require = createRequire(import.meta.url);
const read = p => readFileSync(new URL(p, import.meta.url), 'utf8');
const source = read('../src/components/Workspace.tsx');
const ast = ts.createSourceFile('Workspace.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const api = ts.createSourceFile('appApi.ts', read('../src/lib/appApi.ts'), ts.ScriptTarget.Latest, true);
const printer = ts.createPrinter();
function declaration(name, tree = ast) {
  let found;
  function visit(n) {
    if ((ts.isFunctionDeclaration(n) || ts.isVariableDeclaration(n)) && n.name?.getText(tree) === name) {
      assert.equal(found, undefined); found = n;
    }
    ts.forEachChild(n, visit);
  }
  visit(tree); assert.ok(found, 'required actual declaration');
  return ((ts.isVariableDeclaration(found) ? 'const ' : '') + printer.printNode(ts.EmitHint.Unspecified, found, tree)).replace(/^export /, '') + ';';
}
const compile = code => ts.transpileModule(code, { fileName: 'actual.tsx', compilerOptions: {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None, jsx: ts.JsxEmit.React,
} }).outputText;
const baseline = process.env.GM_DIALOG_BASELINE === '1';
const hasHandler = ast.statements.some(n => ts.isFunctionDeclaration(n) && n.name?.text === 'containDialogTab');

if (!baseline) {
  test('all three dialogs share only a local handler and negative fallback tabindex', () => {
    for (const name of ['RenameDialog', 'DuplicateDialog', 'ConfirmDialog']) {
      const code = declaration(name);
      assert.match(code, /onKeyDown=\{event => containDialogTab\(event\)\}/);
      assert.match(code, /tabIndex=\{-1\}/);
      assert.match(code, /previous\?\.isConnected/);
      assert.doesNotMatch(code, /addEventListener|onKeyUp|tabIndex=\{[0-9]/);
    }
  });
  test('actual shared handler only intercepts boundary Tab in an open modal', () => {
    const { handler } = vm.runInNewContext(compile(declaration('containDialogTab') + ';({handler:containDialogTab})'));
    let active, prevented = 0;
    const item = (patch = {}) => ({ tabIndex: 0, type: 'button', name: '', form: null,
      matches: () => false, closest: () => null, checkVisibility: () => true,
      getClientRects: () => [1], focus() { active = this; }, ...patch });
    const a = item(), b = item(), disabled = item({ matches: () => true });
    const hidden = item({ checkVisibility: () => false }), inert = item({ closest: () => ({}) });
    const negative = item({ tabIndex: -1 }), noBox = item({ getClientRects: () => [] });
    let controls = [hidden, disabled, inert, negative, noBox, a, b], modal = true;
    const dialog = { open: true, matches: () => modal, querySelectorAll: () => controls,
      ownerDocument: { get activeElement() { return active; } }, focus() { active = this; } };
    const press = (patch = {}) => { prevented = 0; handler({ currentTarget: dialog, key: 'Tab', shiftKey: false,
      preventDefault() { prevented++; }, ...patch }); return prevented; };
    active = b; assert.equal(press(), 1); assert.equal(active, a);
    assert.equal(press({ shiftKey: true }), 1); assert.equal(active, b);
    active = a; assert.equal(press(), 0); assert.equal(active, a);
    for (const patch of [{ key: 'Escape' }, { key: 'Enter' }, { ctrlKey: true }, { altKey: true }, { metaKey: true }, { defaultPrevented: true }]) {
      active = b; assert.equal(press(patch), 0); assert.equal(active, b);
    }
    dialog.open = false; assert.equal(press(), 0); dialog.open = true;
    modal = false; assert.equal(press(), 0); modal = true;
    active = {}; assert.equal(press(), 1); assert.equal(active, a);
    active = {}; assert.equal(press({ shiftKey: true }), 1); assert.equal(active, b);
    controls = [hidden, disabled]; assert.equal(press(), 1); assert.equal(active, dialog);
    controls = [a]; active = a; assert.equal(press(), 1); assert.equal(active, a);
  });
  test('radio boundary follows the enabled checked member, recalculated each key', () => {
    const { handler } = vm.runInNewContext(compile(declaration('containDialogTab') + ';({handler:containDialogTab})'));
    let active;
    const radio = checked => ({ type: 'radio', name: 'group', form: null, checked, tabIndex: 0,
      matches: () => false, closest: () => null, checkVisibility: () => true, getClientRects: () => [1],
      focus() { active = this; } });
    const a = radio(false), b = radio(true), last = { ...radio(false), type: 'button' };
    const dialog = { open: true, matches: () => true, querySelectorAll: () => [a, b, last],
      ownerDocument: { get activeElement() { return active; } } };
    const press = shiftKey => handler({ currentTarget: dialog, key: 'Tab', shiftKey, preventDefault() {} });
    active = last; press(false); assert.equal(active, b);
    press(true); assert.equal(active, last);
    b.checked = false; active = last; press(false); assert.equal(active, a);
    a.checked = true; a.matches = () => true; active = last; press(false); assert.equal(active, b);
  });
}

if (process.env.GM_DIALOG_BROWSER === '1') test('actual extracted React dialogs: native offline Edge keyboard contract', async () => {
  // Catch all browser errors locally: never emit Playwright's raw error/DOM text.
  let browser, context, stage = 0, requests = 0, pageErrors = 0;
  const evidence = [];
  try {
    const { chromium } = require('playwright');
    browser = await launchBrowser({ headless: true,
      args: ['--disable-background-networking', '--disable-component-update', '--no-first-run'] });
    context = await browser.newContext({ offline: true, serviceWorkers: 'block', acceptDownloads: false, viewport: { width: 320, height: 900 } });
    await context.route('**/*', route => { requests++; return route.abort(); });
    const page = await context.newPage(); page.on('pageerror', () => pageErrors++);
    page.setDefaultTimeout(5000);
    await page.setContent('<!doctype html><html><head></head><body><button id="opener">Open</button><div id="root"></div><button id="after">After</button></body></html>');
    const css = read('../src/components/ui/tokens.css') + read('../src/styles.css');
    assert.doesNotMatch(css, /@import|url\(/i);
    await page.addStyleTag({ content: css + '\nhtml{font-size:20px}' });
    await page.addScriptTag({ path: require.resolve('react').replace(/index\.js$/, 'umd/react.development.js') });
    await page.addScriptTag({ path: require.resolve('react-dom').replace(/index\.js$/, 'umd/react-dom.development.js') });
    const actual = (hasHandler ? declaration('containDialogTab') : '')
      + declaration('validMetadataTitle', api)
      + ['RenameDialog', 'DuplicateDialog', 'ConfirmDialog'].map(n => declaration(n)).join('\n');
    await page.addScriptTag({ content: compile(`
      const {useState,useEffect,useRef,useId}=React;
      const MODE_LABELS={voiceover:'A',mixed:'B',original:'C'};
      const errorText=()=> 'Synthetic failure';
      ${actual}
      const root=ReactDOM.createRoot(document.getElementById('root'));
      let state, release;
      const base={task_id:'synthetic',title:'Original',revision:0,metadata_revision:0};
      window.mountDialog=(kind)=>{
        state={closes:0,saves:0,reads:0,actions:0,choices:0};
        const onClose=()=>{state.closes++;ReactDOM.flushSync(()=>root.render(null));};
        const hold=()=>new Promise((resolve,reject)=>{release={resolve,reject};});
        const props=kind==='RenameDialog'?{onClose,spec:{baseline:base,
          save:()=>{state.saves++;return hold();},read:()=>{state.reads++;return hold();}}}
          :kind==='ConfirmDialog'?{onClose,onError:()=>{},spec:{title:'Synthetic confirm',text:'Synthetic only',confirm:'Continue',action:()=>{state.actions++;return hold();}}}
          :{onClose,onChoose:()=>{state.choices++;onClose();}};
        ReactDOM.flushSync(()=>root.render(React.createElement({RenameDialog,DuplicateDialog,ConfirmDialog}[kind],props)));
      };
      window.receipt=()=>({...state});
      window.settle=(fail)=>fail?release.reject(Error('Synthetic')):release.resolve({...base,title:'Reconciled',metadata_revision:1});
    `) });
    const snapshot = () => page.evaluate(() => {
      const d = document.querySelector('dialog'), a = document.activeElement;
      return { tag: a?.tagName ?? '', index: d ? [...d.querySelectorAll('input,button')].indexOf(a) : -1,
        inside: !!d?.contains(a), modal: !!d?.matches(':modal'), body: a === document.body,
        background: a === document.getElementById('opener') || a === document.getElementById('after'),
        documentFocus: document.hasFocus(), dialog: a === d };
    });
    // Safari/WebKit does not focus a button on mouse click and a click would blur it, so focus the opener as a keyboard user would.
    const open = async kind => { await page.locator('#opener').focus(); await page.evaluate(kind => window.mountDialog(kind), kind); };
    const press = async key => { await page.keyboard.press(key); return snapshot(); };
    const sequence = async (keys, contained = true) => {
      const result = [await snapshot()];
      for (const key of keys) result.push(await press(key));
      if (contained) for (const entry of result) assert.equal(entry.inside, true);
      assert.equal(result.some(entry => entry.background), false);
      return result;
    };
    const restore = async () => {
      await page.keyboard.press('Escape'); assert.equal(await page.locator('dialog').count(), 0);
      assert.equal(await page.locator('#opener').evaluate(e => e === document.activeElement), true);
    };
    for (const width of [320, 900]) {
      stage = width;
      await page.setViewportSize({ width, height: 900 }); await open('RenameDialog');
      const seq = await sequence(['Tab', 'Tab', 'Tab', 'Tab'], !baseline);
      evidence.push({ width, case: 'rename-unchanged', sequence: seq });
      assert.equal(seq[0].index, 0); assert.equal(seq[1].index, 1);
      if (baseline) {
        assert.equal(hasHandler, false); assert.equal(seq[2].body, true); assert.equal(seq[2].background, false);
        // A background DOM focus attempt must still fail while the modal is open.
        const inert = await page.evaluate(() => { const b=document.getElementById('after');b.focus();return document.activeElement!==b; });
        assert.equal(inert, true); await restore(); continue;
      }
      assert.deepEqual(seq.map(s => s.index), [0, 1, 0, 1, 0]);
      evidence.push({ width, case: 'rename-reverse', sequence: await sequence(['Shift+Tab', 'Shift+Tab']) });
      await page.locator('dialog input').fill('Changed');
      const changed = await sequence(['Tab', 'Tab', 'Tab']); assert.deepEqual(changed.map(s => s.index), [0, 1, 2, 0]);
      evidence.push({ width, case: 'rename-changed', sequence: changed });
      await restore(); assert.deepEqual(await page.evaluate(() => window.receipt()), { closes: 1, saves: 0, reads: 0, actions: 0, choices: 0 });
    }
    if (!baseline) {
      stage = 1001; await open('RenameDialog');
      await page.locator('dialog input').fill('Changed'); await page.keyboard.press('Enter');
      await page.waitForFunction(() => [...document.querySelectorAll('dialog input,dialog button')].every(e => e.disabled) && document.activeElement === document.querySelector('dialog'));
      evidence.push({ case: 'rename-busy', sequence: await sequence(['Tab', 'Shift+Tab', 'Enter', 'Escape']) });
      assert.equal((await page.evaluate(() => window.receipt())).saves, 1); assert.equal((await page.evaluate(() => window.receipt())).closes, 0);
      await page.evaluate(() => window.settle(true));
      await page.waitForFunction(() => document.querySelectorAll('dialog button').length === 3 && !document.querySelector('dialog button').disabled);
      const uncertain = await sequence(['Tab', 'Tab', 'Tab', 'Shift+Tab']);
      assert.deepEqual(uncertain.slice(1).map(s => s.index), [1, 2, 1, 2]); evidence.push({ case: 'rename-uncertain', sequence: uncertain });
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => window.receipt().reads === 1 && document.activeElement === document.querySelector('dialog'));
      await sequence(['Tab', 'Shift+Tab', 'Escape']);
      await page.evaluate(() => window.settle(false));
      await page.waitForFunction(() => document.querySelectorAll('dialog button').length === 2 && !document.querySelector('dialog input').disabled);
      const reconciled = await sequence(['Tab', 'Tab', 'Tab']);
      assert.deepEqual(reconciled.slice(1).map(s => s.index), [0, 1, 0]); evidence.push({ case: 'rename-reconciled', sequence: reconciled });
      await restore(); assert.equal((await page.evaluate(() => window.receipt())).saves, 1);
      stage = 1002; await open('RenameDialog');
      await page.locator('dialog input').fill('Changed');
      // Constructed hidden/disabled states on actual controls, never a focus patch.
      for (const mode of ['hidden', 'visibility', 'inert', 'fieldset']) {
        await page.evaluate(mode => {
          const b=document.querySelector('dialog button');
          if(mode==='hidden') b.hidden=true;
          if(mode==='visibility') b.style.visibility='hidden';
          if(mode==='inert') b.inert=true;
          if(mode==='fieldset'){const f=document.createElement('fieldset');f.disabled=true;b.before(f);f.append(b);}
        }, mode);
        const seq = await sequence(['Tab', 'Tab', 'Shift+Tab', 'Shift+Tab']);
        assert.deepEqual(seq.map(s => s.index), [0, 2, 0, 2, 0]);
        await page.evaluate(() => {const b=document.querySelector('dialog button');b.hidden=false;b.style.visibility='';b.inert=false;const f=b.closest('fieldset');if(f){f.before(b);f.remove();}});
      }
      await restore();
      stage = 1003; await open('DuplicateDialog');
      const dup = await sequence(['Tab', 'Tab', 'Tab', 'Shift+Tab']);
      assert.deepEqual(dup.map(s => s.index), [0, 4, 5, 0, 5]); evidence.push({ case: 'duplicate', sequence: dup });
      await page.keyboard.press('Tab'); await page.keyboard.press('ArrowRight');
      assert.equal((await snapshot()).index, 1);
      const radio = await sequence(['Shift+Tab', 'Tab', 'Tab', 'Tab', 'Tab']);
      assert.deepEqual(radio.map(s => s.index), [1, 5, 1, 4, 5, 1]); evidence.push({ case: 'duplicate-radio', sequence: radio });
      await restore(); assert.equal((await page.evaluate(() => window.receipt())).choices, 0);
      stage = 1004; await open('ConfirmDialog');
      const confirm = await sequence(['Shift+Tab', 'Tab', 'Tab']);
      assert.deepEqual(confirm.map(s => s.index), [0, 1, 0, 1]); evidence.push({ case: 'confirm', sequence: confirm });
      await page.keyboard.press('Enter');
      await page.waitForFunction(() => window.receipt().actions === 1 && document.activeElement === document.querySelector('dialog'));
      await sequence(['Tab', 'Shift+Tab', 'Escape']); assert.equal((await page.evaluate(() => window.receipt())).closes, 0);
      await page.evaluate(() => window.settle(true));
      await page.waitForFunction(() => !document.querySelector('dialog button').disabled);
      const failed = await sequence(['Tab', 'Tab', 'Shift+Tab']);
      assert.deepEqual(failed.slice(1).map(s => s.index), [0, 0, 0]); evidence.push({ case: 'confirm-failed', sequence: failed });
      await restore();
      stage = 1005;
      // Outside a modal no global trap exists: native Tab reaches the next button.
      await page.keyboard.press('Tab'); assert.equal(await page.locator('#after').evaluate(e => e === document.activeElement), true);
      stage = 1006; await open('RenameDialog');
      await page.locator('dialog input').fill('Changed'); await page.keyboard.press('Enter');
      await page.waitForFunction(() => window.receipt().saves === 1 && document.activeElement === document.querySelector('dialog'));
      await page.evaluate(() => window.settle(true));
      await page.waitForFunction(() => document.querySelectorAll('dialog button').length === 3 && !document.querySelector('dialog button').disabled);
      await restore(); // Uncertain is not busy: Escape closes, with no read/retry.
      assert.deepEqual(await page.evaluate(() => window.receipt()), { closes: 1, saves: 1, reads: 0, actions: 0, choices: 0 });
      for (const kind of ['RenameDialog', 'ConfirmDialog']) {
        stage = 1007; await open(kind);
        if (kind === 'RenameDialog') { await page.locator('dialog input').fill('Changed'); await page.keyboard.press('Enter'); }
        else { await page.keyboard.press('Tab'); await page.keyboard.press('Enter'); }
        await page.waitForFunction(() => document.activeElement === document.querySelector('dialog'));
        await page.evaluate(() => window.settle(false));
        await page.waitForFunction(() => !document.querySelector('dialog'));
        assert.equal(await page.locator('#opener').evaluate(e => e === document.activeElement), true);
        assert.equal((await page.evaluate(() => window.receipt())).closes, 1);
      }
    }
    assert.equal(requests, 0); assert.equal(pageErrors, 0);
    assert.equal(read('../src/components/Workspace.tsx'), source);
    console.log(JSON.stringify({ dialogFocus: baseline ? 'baseline' : 'fixed', workspaceSHA256: createHash('sha256').update(source).digest('hex'),
      browser: browser.version(), requests, pageErrors, rawArtifacts: false }));
    for (const row of evidence) console.log(JSON.stringify(row));
  } catch {
    // Only a static stage and allowlisted booleans/numeric focus indices survive.
    console.log(JSON.stringify({ dialogFocus: 'failed', stage, requests, pageErrors, rawArtifacts: false, evidence }));
    assert.fail(`Dialog keyboard contract failed at static stage ${stage}; raw error suppressed`);
  } finally {
    let cleanupFailed = false;
    try { await context?.close(); } catch { cleanupFailed = true; }
    try { await browser?.close(); } catch { cleanupFailed = true; }
    assert.equal(cleanupFailed, false, 'Owned browser cleanup failed; raw error suppressed');
  }
});