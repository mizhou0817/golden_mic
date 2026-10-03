import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import test from 'node:test';
import ts from 'typescript';
import postcss from 'postcss';

// Focused actual JSX/CSS runtime, not a full wizard or screenshot acceptance.
// No host/build or requests; all dependencies already installed, no downloads.
const require = createRequire(import.meta.url);
const read = p => readFileSync(new URL(p, import.meta.url), 'utf8');
const source = read('../src/components/CreateWizard.tsx'), css = read('../src/styles/modes.css');
const tree = ts.createSourceFile('CreateWizard.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const print = ts.createPrinter();
function find(predicate) { let found; const visit = n => { if (predicate(n)) { assert.equal(found, undefined); found = n; } ts.forEachChild(n, visit); }; visit(tree); assert.ok(found); return found; }
const grid = find(n => ts.isJsxElement(n) && n.openingElement.attributes.properties.some(p => ts.isJsxAttribute(p) && p.name.text === 'className' && p.initializer?.text === 'gm-mode-cards'));
const more = find(n => ts.isJsxElement(n) && n.openingElement.attributes.properties.some(p => ts.isJsxAttribute(p) && p.name.text === 'className' && p.initializer?.text === 'gm-small-button gm-mode-more'));
const icon = find(n => ts.isFunctionDeclaration(n) && n.name?.text === 'Icon');
const emit = n => print.printNode(ts.EmitHint.Unspecified, n, tree);
const compile = text => ts.transpileModule(text, { fileName: 'actual.tsx', compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React,
} }).outputText;
test('640px media rule changes the mode grid alone; 600px general mobile rules remain', () => {
  const ast = postcss.parse(css), small = ast.nodes.filter(n => n.type === 'atrule' && n.params === '(max-width:640px)');
  assert.equal(small.length, 1); assert.equal(small[0].nodes.length, 1);
  assert.equal(small[0].nodes[0].selector, '.gm-create-wizard .gm-mode-cards');
  assert.deepEqual(small[0].nodes[0].nodes.map(n => [n.prop, n.value]), [['grid-template-columns', 'minmax(0,1fr)']]);
  const mobile = ast.nodes.find(n => n.type === 'atrule' && n.params === '(max-width:600px)');
  assert.ok(mobile.nodes.length > 10); assert.ok(!mobile.nodes.some(n => n.selector.includes('.gm-mode-cards')));
});
test('offline Edge runtime: expanded actual mode cards at 601/620/640/641px and 16/20px; bounds and native focus', async () => {
  let browser, context, stage = 'launch', errors = 0, requests = 0;
  const evidence = [];
  try {
    const { chromium } = require('playwright');
    browser = await chromium.launch({ channel: 'msedge', headless: true,
      args: ['--disable-background-networking', '--disable-component-update', '--no-first-run'] });
    context = await browser.newContext({ offline: true, serviceWorkers: 'block', acceptDownloads: false });
    await context.route('**/*', route => { requests++; return route.abort(); });
    const page = await context.newPage(); page.setDefaultTimeout(5000); page.on('pageerror', () => errors++);
    await page.setContent('<!doctype html><html><body><div id="root"></div></body></html>');
    const actualCSS = read('../src/components/ui/tokens.css') + read('../src/styles.css') + css.replace('@import "../components/ui/tokens.css";', '');
    assert.doesNotMatch(actualCSS, /@import|url\(/i); await page.addStyleTag({ content: actualCSS });
    await page.addScriptTag({ path: require.resolve('react').replace(/index\.js$/, 'umd/react.development.js') });
    await page.addScriptTag({ path: require.resolve('react-dom').replace(/index\.js$/, 'umd/react-dom.development.js') });
    const modeCode = compile(read('../src/lib/productionModes.ts'));
    const iconCode = compile(read('../src/components/ui/Icon.tsx'));
    await page.addScriptTag({ content: `
      const rules=${read('../../backend/mode_rules.json')};
      const modeExports={};(function(exports,require){${modeCode}\n})(modeExports,name=>{if(name.endsWith('mode_rules.json'))return{default:rules};throw Error('Unapproved import');});
      const iconExports={};(function(exports,require){${iconCode}\n})(iconExports,()=>({default:{icon:'icon'}}));
      const {MODE_CARDS}=modeExports,SharedIcon=iconExports.default;
      ${compile(`const {useState}=React; ${emit(icon)}
        function Fixture(){const [modeMore,setModeMore]=useState(false),[mode,setMode]=useState('voiceover');
          const id='actual',audioBusy=false,chooseMode=setMode;
          return <section className="gm-create-wizard"><div className="gm-wizard-card">${emit(grid)}${emit(more)}</div></section>;}
        const root=ReactDOM.createRoot(document.getElementById('root'));
        window.mount=()=>ReactDOM.flushSync(()=>root.render(<Fixture key={Math.random()}/>)); window.mount();`)}
    ` });
    for (const font of [16, 20]) for (const width of [601, 620, 640, 641]) {
      stage = `${width}/${font}`; await page.setViewportSize({ width, height: 900 });
      await page.evaluate(font => { document.documentElement.style.fontSize = font + 'px'; window.mount(); }, font);
      const toggle = page.locator('.gm-mode-more'), cards = page.locator('.gm-mode-card');
      assert.equal(await cards.count(), 1);
      stage = `${width}/${font}/expand`; await toggle.focus(); await page.keyboard.press('Space');
      assert.equal(await cards.count(), 3); assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
      stage = `${width}/${font}/focus`;
      await page.keyboard.press('Shift+Tab');
      assert.equal(await cards.nth(2).evaluate(e => e === document.activeElement && e.matches(':focus-visible')), true);
      await page.keyboard.press('Shift+Tab');
      assert.equal(await cards.nth(1).evaluate(e => e === document.activeElement), true);
      await page.keyboard.press('Enter'); assert.equal(await cards.nth(1).getAttribute('aria-pressed'), 'true');
      await page.keyboard.press('Tab'); assert.equal(await cards.nth(2).evaluate(e => e === document.activeElement), true);
      await page.keyboard.press('Space'); assert.equal(await cards.nth(2).getAttribute('aria-pressed'), 'true');
      stage = `${width}/${font}/geometry`;
      const facts = await page.evaluate(() => {
        const grid = document.querySelector('.gm-mode-cards'), bounds = grid.getBoundingClientRect();
        const cards = [...grid.children].map(e => { const r = e.getBoundingClientRect(); return { x: r.x, y: r.y, width: r.width, height: r.height, right: r.right }; });
        const overflow = [...grid.querySelectorAll('*')].filter(e => e.checkVisibility()).filter(e => {
          const r = e.getBoundingClientRect(), card = e.closest('.gm-mode-card').getBoundingClientRect();
          return r.left < bounds.left - 1 || r.right > bounds.right + 1 || r.left < card.left - 1 || r.right > card.right + 1;
        }).length;
        const focus = getComputedStyle(document.activeElement);
        return { columns: getComputedStyle(grid).gridTemplateColumns.split(' ').length, cards, overflow,
          pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
          padding: getComputedStyle(document.querySelector('.gm-wizard-card')).paddingLeft,
          focusVisible: document.activeElement.matches(':focus-visible'), outlineWidth: parseFloat(focus.outlineWidth), outlineStyle: focus.outlineStyle };
      });
      console.log(JSON.stringify({ width, font, facts }));
      assert.equal(facts.columns, width <= 640 ? 1 : 3); assert.equal(facts.overflow, 0); assert.equal(facts.pageOverflow, false);
      assert.equal(facts.padding, `${font * 1.5}px`, '601+ does not activate 600px general padding');
      assert.equal(facts.focusVisible, true); assert.ok(facts.outlineWidth >= 3); assert.equal(facts.outlineStyle, 'solid');
      if (width <= 640) for (let i = 1; i < 3; i++) { assert.equal(facts.cards[i].x, facts.cards[0].x); assert.ok(facts.cards[i].y >= facts.cards[i-1].y + facts.cards[i-1].height); }
      else { assert.equal(facts.cards[0].y, facts.cards[2].y); assert.ok(facts.cards[1].x >= facts.cards[0].right); }
      evidence.push({ width, font, ...facts });
    }
    assert.equal(errors, 0); assert.equal(requests, 0); assert.equal(read('../src/styles/modes.css'), css); assert.equal(read('../src/components/CreateWizard.tsx'), source);
    console.log(JSON.stringify({ runtime: 'offline native Edge', version: browser.version(), evidence, errors, requests,
      cssSHA256: createHash('sha256').update(css).digest('hex'), fullWizardAcceptance: false }));
  } catch (failure) {
    console.log(JSON.stringify({ assertion: failure.code === 'ERR_ASSERTION' ? { actual: typeof failure.actual === 'number' || typeof failure.actual === 'boolean' ? failure.actual : null,
      expected: typeof failure.expected === 'number' || typeof failure.expected === 'boolean' ? failure.expected : null } : null }));
    assert.fail(`Mode breakpoint runtime failed at ${stage}; errors=${errors}, requests=${requests}; raw browser output suppressed`);
  }
  finally { await context?.close(); await browser?.close(); }
});