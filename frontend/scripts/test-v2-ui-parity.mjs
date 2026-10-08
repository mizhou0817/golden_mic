import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import postcss from 'postcss';

// Actual TSX rendered with React SSR. Initial state alone is seeded for loaded
// screens; no effects, network, browser, build, media or real storage. These
// assertions prove markup/CSS contracts, NOT rendered layout or accessibility.
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const read = path => readFileSync(resolve(root, path), 'utf8');
const denied = () => assert.fail('Unexpected side effect in parity SSR');
function loader(seeds = {}, storage = {}) {
  const cache = new Map();
  function load(path) {
    path = resolve(root, path);
    if (cache.has(path)) return cache.get(path).exports;
    if (path.endsWith('.css')) return { __esModule: true, default: new Proxy({}, { get: (_, k) => k }) };
    if (path.endsWith('.json')) return JSON.parse(readFileSync(path, 'utf8'));
    if (/[/\\](ResultWorkbench|Processing|SampleView)\.tsx$/.test(path)) return { __esModule: true, default: () => null };
    const state = seeds[path] ?? {};
    const output = ts.transpileModule(readFileSync(path, 'utf8'), {
      fileName: path, reportDiagnostics: true,
      compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
      transformers: { before: [context => source => {
        const visit = node => {
          if (ts.isPropertyAccessExpression(node) && ts.isMetaProperty(node.expression) && node.name.text === 'env') return ts.factory.createIdentifier('__viteEnv');
          if (ts.isVariableDeclaration(node) && ts.isArrayBindingPattern(node.name) && node.initializer && ts.isCallExpression(node.initializer)
              && node.initializer.expression.getText(source) === 'useState') {
            const name = node.name.elements[0]?.name?.getText(source);
            if (Object.hasOwn(state, name)) return ts.factory.updateVariableDeclaration(node, node.name, node.exclamationToken, node.type,
              ts.factory.updateCallExpression(node.initializer, node.initializer.expression, node.initializer.typeArguments,
                [ts.factory.createElementAccessExpression(ts.factory.createIdentifier('__seeds'), ts.factory.createStringLiteral(name))]));
          }
          return ts.visitEachChild(node, visit, context);
        };
        return ts.visitNode(source, visit);
      }] },
    });
    assert.equal(output.diagnostics.filter(d => d.category === ts.DiagnosticCategory.Error).length, 0);
    const module = { exports: {} }; cache.set(path, module);
    new vm.Script(output.outputText, { filename: path }).runInNewContext({
      module, exports: module.exports, __seeds: state, __viteEnv: {}, console,
      URL, URLSearchParams, Headers, AbortController, DOMException,
      fetch: denied, setTimeout: denied, setInterval: denied, navigator: {},
      location: { origin: 'https://synthetic.invalid', pathname: '/', hash: '' },
      localStorage: { getItem: key => storage[key] ?? null, setItem: denied, removeItem: denied },
      require(name) {
        if (name === 'react') return React;
        if (name === 'react/jsx-runtime') return jsxRuntime;
        assert.ok(name.startsWith('.'), `Unbound dependency: ${name}`);
        const full = resolve(dirname(path), name);
        const target = [full, `${full}.ts`, `${full}.tsx`].find(existsSync);
        assert.ok(target, name); return load(target);
      },
    }, { timeout: 5000 });
    return module.exports;
  }
  return load;
}
const wizardPath = resolve(root, 'src/components/CreateWizard.tsx');
const workspacePath = resolve(root, 'src/components/Workspace.tsx');
const limits = { max_files: 20, max_upload_bytes: 500 * 1024 ** 2, max_total_upload_bytes: 5 * 1024 ** 3, max_script_length: 8000 };
const render = (component, props) => renderToStaticMarkup(React.createElement(component, props));
function wizard(patch = {}, state = {}) {
  const load = loader({ [wizardPath]: state });
  const { CreateWizard, defaultPreferences } = load(wizardPath);
  const Intro = load('src/components/ui/IntroCard.tsx').default;
  const initialDraft = { script: '', step: 1, mode: 'voiceover', preferences: defaultPreferences(), files: [], elements: {}, sentenceChecks: {}, ...patch };
  return render(CreateWizard, { limits, initialDraft, intro: React.createElement(Intro, { onStart: denied, onSample: denied }),
    busy: false, generativeAllowed: false, onDraft: denied, onSubmit: denied, onError: denied });
}
function workspace(state = {}, storage = {}) {
  const load = loader({ [workspacePath]: { limits, configResolved: true, config: { local_history: true, generative_fill_available: false }, healthReady: true, healthBusy: false, ...state } }, storage);
  return render(load(workspacePath).default);
}
test('SSR empty A is StepBar → Intro → step card with exact opening copy and soft counter', () => {
  const html = wizard();
  assert.ok(html.indexOf('aria-label="制作步骤"') < html.indexOf('aria-label="使用引导"'));
  assert.ok(html.indexOf('aria-label="使用引导"') < html.indexOf('class="gm-wizard-card"'));
  assert.equal((html.match(/<h1\b/g) ?? []).length, 1);
  assert.match(html, /第一行是标题。/); assert.match(html, /直接粘贴新闻稿到这里，或自己写/);
  assert.match(html, /0 \/ 3000 字/); assert.doesNotMatch(html, /0 \/ 8000/);
  // All three modes are shown from the start, nothing is collapsed; AI voiceover is the pre-selected default.
  assert.equal((html.match(/class="gm-mode-card"/g) ?? []).length, 3);
  assert.doesNotMatch(html, /data-collapsed|gm-mode-more|更多制作方式/);
  assert.match(html, /<button type="button" aria-pressed="true" class="gm-mode-card"[^>]*>(?:(?!<\/button>)[\s\S])*AI 配音/);
  assert.equal((html.match(/aria-pressed="true" class="gm-mode-card"/g) ?? []).length, 1);
  const tip = html.match(/<details class="gm-tip">[\s\S]*?<\/details>/)?.[0];
  assert.ok(tip); assert.equal((tip.match(/<li>/g) ?? []).length, 4);
  assert.match(tip, /制作后请核对实际读音/); assert.doesNotMatch(tip, /AI 会读对/);
});
test('SSR B/C preserve mode copy; step two cannot show intro and keeps visible privacy brief', () => {
  assert.match(wizard({ mode: 'mixed' }), /第一行是标题；人说的话写成「同期 姓名（身份）：…」。/);
  assert.match(wizard({ mode: 'original' }), /第一行是标题；下面每行一句原话。/);
  const html = wizard({ step: 2 });
  assert.doesNotMatch(html, /aria-label="使用引导"/);
  const brief = html.match(/class="gm-cloud-notice"[\s\S]*?<details>/)?.[0];
  assert.match(brief, /选择文件即开始上传和转写/); assert.match(brief, /第三方 AI 并产生费用/);
  assert.match(brief, /已获授权、不含敏感隐私/);
  assert.match(html, /<details><summary>隐私与处理说明/);
});
test('SSR soft overflow is advisory; hard overflow remains invalid and explicit at 8000', () => {
  const soft = wizard({ script: '标题\n' + '完整的新闻句子。'.repeat(400) });
  assert.match(soft, /服务器硬上限 8000 字/); assert.match(soft, /aria-invalid="false"/);
  const hard = wizard({ script: '标题\n' + '完整的新闻句子。'.repeat(1100) });
  assert.match(hard, /稿件不能超过 8000 字/); assert.match(hard, /aria-invalid="true"/);
});
test('SSR self-check evidence defaults closed; speaker name/title limits are 8/12', () => {
  const html = wizard({ script: '标题\n今天社区在广场举行活动，居民了解现场新闻。' });
  assert.match(html, /class="gm-element-chip"[^>]*aria-pressed="false"/);
  assert.match(html, /class="gm-evidence-details">/); assert.doesNotMatch(html, /class="gm-evidence-details" open/);
  const speakers = wizard({ mode: 'mixed', step: 2, speakers: [{ id: 'speaker_a', name: '', title: '', auto_label: '说话人 1', appearances: 1, seconds: 2 }] });
  assert.match(speakers, /maxLength="8" placeholder="姓名"/);
  assert.match(speakers, /maxLength="12" placeholder="身份，如：主办方"/);
});
test('SSR healthy shell has left brand/font/help, right truthful service/save, post-form closed draft tools', () => {
  const html = workspace(); const header = html.match(/<header class="shell-header">[\s\S]*?<\/header>/)[0];
  const order = ['shell-brand', 'shell-font-toggle', 'shell-help', 'shell-header-actions', 'shell-ready', 'shell-saved'].map(s => header.indexOf(s));
  assert.deepEqual([...order].sort((a, b) => a - b), order);
  assert.match(header, />大字<\/button>/); assert.match(header, /服务正常/); assert.doesNotMatch(header, /制作服务就绪|大字 关/);
  assert.match(html, /<details class="shell-draft-details"><summary>草稿操作/);
  assert.ok(html.indexOf('shell-draft-details') > html.indexOf('gm-wizard-actions'));
  assert.match(html, />保存草稿<\/button>/); assert.doesNotMatch(html, /shell-history-panel|shell-sidebar-footer|class="shell-footer"/);
  assert.match(workspace({ font: 20 }), />常规字号<\/button>/);
});
test('SSR shell errors remain outside collapsed tools; history revision is never a modification count', () => {
  const html = workspace({ draftProblem: '保存失败，请勿刷新', healthReady: false, healthError: '服务连接失败',
    localRows: [{ taskId: 'synthetic', title: '测试作品', createdAt: '2026-09-29T00:00:00Z', status: 'done', revision: 2, mode: 'voiceover' }] });
  assert.match(html, /role="alert">保存失败，请勿刷新/); assert.match(html, /服务连接失败/);
  assert.ok(html.indexOf('保存失败，请勿刷新') < html.indexOf('shell-draft-details'));
  assert.match(html, /版本 2/); assert.doesNotMatch(html, /改了 2 次|还剩 \d+ 天|样片保留 3 天/);
  assert.match(html, /<h2>我的作品<\/h2>/); assert.match(html, /按时间 ↕/);
});
test('SSR shell keeps sorted history presentation bounded to 100 without a duplicate history page', () => {
  const localRows = Array.from({ length: 105 }, (_, i) => ({ taskId: `work_${i}`, title: `TITLE_${String(i).padStart(3, '0')}`,
    createdAt: '2026-09-29T00:00:00Z', status: 'done', revision: 0, mode: 'voiceover' }));
  const html = workspace({ localRows, sort: 'title' });
  const sidebar = html.match(/<aside class="shell-sidebar">[\s\S]*?<\/aside>/)[0];
  assert.equal((sidebar.match(/<strong>TITLE_/g) ?? []).length, 100);
  assert.match(sidebar, /按标题 ↕/); assert.doesNotMatch(sidebar, /TITLE_100/);
});
test('CSS parsed contracts retain 960px canvas, rem font inheritance, one-third folded A and gold focus', () => {
  const shell = postcss.parse(read('src/styles.css')), modes = postcss.parse(read('src/styles/modes.css'));
  const module = postcss.parse(read('src/components/ui/Primitives.module.css'));
  const value = (css, selector, property) => { let result; css.walkRules(selector, rule => rule.walkDecls(property, d => { result = d.value; })); return result; };
  assert.equal(value(shell, '.shell-creator-heading,.shell-wizard-host', 'max-width'), '960px');
  assert.equal(value(modes, '.gm-create-wizard', 'max-width'), '960px');
  assert.equal(value(module, '.intro', 'max-width'), '960px');
  assert.equal(value(modes, '.gm-create-wizard', 'font'), 'inherit');
  assert.doesNotMatch(read('src/styles/modes.css'), /data-collapsed=true.*grid-template-columns/);
  const grid = []; modes.walkRules('.gm-create-wizard .gm-mode-cards', r => { if (r.parent.type === 'root') r.walkDecls('grid-template-columns', d => grid.push(d.value)); });
  assert.deepEqual(grid, ['repeat(3,minmax(0,1fr))']);
  assert.match(read('src/styles/modes.css'), /focus-visible \{ outline: .1875rem solid var\(--gm-gold\)/);
  assert.doesNotMatch(read('src/styles.css'), /--(?:ink|gm-ink):/);
});