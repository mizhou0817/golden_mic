// Read-only, in-memory TS contracts over this run's captured main ASGI JSON.
// No browser/server/providers. Fetch is a rejecting-by-default local adapter.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const vm = require('node:vm');
const crypto = require('node:crypto');
const test = require('node:test');
const ts = require('../frontend/node_modules/typescript');
const root = path.resolve(__dirname, '..');
const evidence = path.resolve(process.argv[2] || '');
assert.equal(path.dirname(evidence), path.resolve(os.tmpdir()));
assert.match(path.basename(evidence), /^gm-v2-editing-validation-[a-z0-9_]+$/);
const summary = JSON.parse(fs.readFileSync(path.join(evidence, 'summary.json'), 'utf8'));
assert.equal(summary.success, true);
const contracts = JSON.parse(fs.readFileSync(path.join(evidence, 'contracts.json'), 'utf8'));
const exportsCaptured = contracts.filter(value => value.kind === 'export');
assert.equal(exportsCaptured.length, 5);
const applyCaptured = contracts.find(value => value.kind === 'apply');
assert.ok(applyCaptured);

function load(name, bindings = {}) {
  const relative = `frontend/src/lib/${name}.ts`;
  const source = fs.readFileSync(path.join(root, relative), 'utf8');
  assert.equal(crypto.createHash('sha256').update(source).digest('hex'),
    summary.source_hashes[relative.replaceAll('/', '\\')] ?? summary.source_hashes[relative]);
  const compiled = ts.transpileModule(source, {
    fileName: name + '.ts', reportDiagnostics: true,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    transformers: { before: [context => tree => {
      const visit = node => ts.isPropertyAccessExpression(node) && node.name.text === 'env'
        && ts.isMetaProperty(node.expression) ? ts.factory.createIdentifier('__viteEnv')
          : ts.visitEachChild(node, visit, context);
      return ts.visitNode(tree, visit);
    }] },
  });
  assert.equal(compiled.diagnostics.filter(value => value.category === ts.DiagnosticCategory.Error).length, 0);
  const moduleValue = { exports: {} };
  vm.runInNewContext(compiled.outputText, { module: moduleValue, exports: moduleValue.exports,
    URL, Headers, Response, FormData, Blob, AbortController, DOMException, setTimeout, clearTimeout,
    location: { origin: 'https://contracts.invalid' }, __viteEnv: {},
    require() { throw Error('Unexpected runtime dependency'); }, ...bindings }, { timeout: 2000 });
  return moduleValue.exports;
}
const api = load('workbenchApi');
const plain = value => JSON.parse(JSON.stringify(value));
const token = 'synthetic-only-task-token-'.padEnd(48, 't');
const csrf = 'synthetic-only-csrf-'.padEnd(48, 'c');

test('all five actual main POST and GET export responses parse without legacy ambiguity', () => {
  for (const capture of exportsCaptured) {
    for (const response of [capture.receipt, capture.job]) {
      assert.equal(Object.hasOwn(response, 'id'), false);
      const parsed = api.parseJob(response);
      assert.equal(parsed.id, response.export_id);
      assert.equal(parsed.pipeline_revision, 2);
      assert.equal(parsed.options.format, response.options.fmt);
      assert.equal(parsed.options.subtitles, 'standard');
    }
    assert.equal(api.parseJob(capture.job).state, 'succeeded');
    assert.ok(Number.isFinite(api.parseJob(capture.job).result.duration));
  }
});

test('actual createAppRequest + workbench export/poll preserve task token, CSRF and current revision', async () => {
  let requests = 0;
  for (const capture of exportsCaptured) {
    const prefix = '/api/tasks/' + capture.task_id;
    const app = load('appApi', { fetch: async (url, init) => {
      requests++;
      if (url === '/api/session') return Response.json({ access_mode: 'anonymous', csrf_token: csrf,
        expires_at: new Date(Date.now() + 3600000).toISOString() });
      assert.equal(init.headers.get('X-Task-Token'), token);
      assert.equal(init.credentials, 'include');
      assert.equal(init.redirect, 'error');
      if (url === prefix + '/exports' && init.method === 'POST') {
        assert.equal(init.headers.get('X-CSRF-Token'), csrf);
        const body = JSON.parse(init.body);
        assert.equal(body.expected_revision, 2);
        assert.equal(body.sub, 'std');
        return Response.json(capture.receipt, { status: 202 });
      }
      assert.equal(url, prefix + '/exports/' + capture.job.export_id);
      return Response.json(capture.job);
    } });
    const workbench = api.createWorkbenchApi(capture.task_id,
      app.createAppRequest({ taskId: capture.task_id, token, signal: new AbortController().signal }));
    const options = api.parseJob(capture.job).options;
    const posted = await workbench.export(options, 2);
    assert.equal(posted.id, capture.job.export_id);
    const polled = await workbench.job(posted.id, undefined, 2);
    assert.equal(polled.state, 'succeeded');
    await assert.rejects(workbench.job(posted.id, undefined, 1));
  }
  assert.equal(requests, 20); // session + POST + GET + rejected stale-revision GET per format
});

test('actual apply client accepts base+N receipt and last-observed pending/terminal steps', async () => {
  const capture = applyCaptured;
  const workbench = api.createWorkbenchApi(capture.task_id, async (url, init) => {
    if (url.endsWith('/apply')) {
      const body = JSON.parse(init.body);
      assert.equal(body.expected_revision, 0);
      assert.equal(Object.keys(body.replaces).length, 1);
      return capture.receipt;
    }
    return capture.operation;
  });
  const receipt = await workbench.apply({ expected_revision: 0, keep_sentence_ids: [0],
    edits: [{ sentence_id: 0, text: 'Synthetic narration', instruction: 'Different source' }] }, [0], 'voiceover');
  assert.equal(receipt.current_revision, 0);
  assert.equal(receipt.revision, 2);
  assert.deepEqual(plain(receipt.plan.steps), capture.pending.steps);
  assert.equal(capture.pending.expected_revision, 0);
  const observed = await workbench.operation(receipt.operation_id, receipt.revision, undefined, receipt.plan.steps);
  assert.equal(observed.complete, true);
  assert.deepEqual(observed.steps.map(s => s.state).join(','), 'succeeded,succeeded');
  await assert.rejects(workbench.operation(receipt.operation_id, 1));
});

test('task request rejects foreign scope and query credentials; replaces caller-supplied tokens', async () => {
  const capture = exportsCaptured[0];
  let calls = 0;
  const app = load('appApi', { fetch: async (url, init) => {
    calls++;
    assert.equal(init.headers.get('X-Task-Token'), token);
    assert.equal(init.headers.has('Authorization'), false);
    return Response.json(capture.job);
  } });
  const request = app.createAppRequest({ taskId: capture.task_id, token, signal: new AbortController().signal });
  await assert.rejects(request('https://foreign.invalid/api/tasks/' + capture.task_id));
  await assert.rejects(request('/api/tasks/' + 'b'.repeat(32)));
  await assert.rejects(request('/api/tasks/' + capture.task_id + '?token=injected'));
  assert.equal(calls, 0);
  await request('/api/tasks/' + capture.task_id, { headers: { 'X-Task-Token': 'injected', Authorization: 'injected' } });
  assert.equal(calls, 1);
});