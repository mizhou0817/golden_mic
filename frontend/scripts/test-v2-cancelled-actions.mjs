import assert from 'node:assert/strict';
import test from 'node:test';
import { harness, status, flat, text, response } from './test-v2-result-access.mjs';

for (const errorKind of [undefined, 'network', 'qc', 'shortage', 'quote_missing']) test(`persistent cancelled status ${errorKind}: no guarded no-op edit/retry, explicit new only`, async () => {
  const h = harness('workspace', { status: 'cancelled', lifecycle_v2: true, retry_same_supported: true, errorKind });
  h.render(); await h.settle(); await h.open('A');
  const Processing = h.load('src/components/Processing.tsx').default;
  const host = flat(h.tree()).find(n => n.type === Processing); assert.equal(host.props.task.status, 'cancelled');
  let newCalls = 0, editCalls = 0, retryCalls = 0;
  const tree = Processing({ ...host.props, onNew: () => newCalls++, onEdit: () => editCalls++, onRetry: () => retryCalls++ });
  const buttons = flat(tree).filter(n => n.type === 'button');
  assert.equal(buttons.some(n => ['processing-edit-script', 'processing-edit-materials', 'processing-retry'].includes(n.props['data-control'])), false);
  const start = buttons.find(n => n.props['data-control'] === 'processing-new'); assert.ok(start); assert.match(text(tree), /暂不支持恢复为新草稿/);
  start.props.onClick(); assert.equal(newCalls, 1); assert.equal(editCalls + retryCalls, 0);
  const before = h.taskCalls().length; await h.probe().editFailedDraft(); h.probe().retryOriginal(); await h.settle(); assert.equal(h.taskCalls().length, before);
  assert.equal(typeof host.props.onNew, 'function'); host.props.onNew(); await h.settle(); assert.equal(h.probe().page, 'create');
  assert.ok(h.taskCalls().every(c => (c.init.method ?? 'GET') === 'GET')); h.stop();
});
test('cancelled optional new action is absent if unsupported, disabled when busy; failed recovery remains', () => {
  const h = harness(), Processing = h.load('src/components/Processing.tsx').default;
  const props = { task: status('A', { status: 'cancelled' }), upload: null, uploading: false, now: 0, busy: false,
    onCancel() {}, onRetry() {}, onEdit() {}, onRefresh() {} };
  assert.equal(flat(Processing(props)).some(n => n.props?.['data-control'] === 'processing-new'), false);
  assert.equal(flat(Processing({ ...props, busy: true, onNew() {} })).find(n => n.props?.['data-control'] === 'processing-new')?.props.disabled, true);
  for (const errorKind of ['network', 'qc', 'shortage', 'quote_missing']) {
    const nodes = flat(Processing({ ...props, task: status('A', { status: 'failed', errorKind, lifecycle_v2: true, retry_same_supported: true }) }));
    assert.ok(nodes.some(n => n.props?.['data-control'] === 'processing-edit-script'));
    assert.equal(nodes.some(n => n.props?.['data-control'] === 'processing-retry'), errorKind === 'network');
  }
});
test('acknowledged native cancel/delete clears selection and history without displaying stale cancelled actions', async () => {
  const h = harness('workspace', { status: 'cancelled' }); h.render(); await h.settle(); await h.open('A');
  h.handler(async (path, init) => { assert.equal(init.method, 'DELETE'); return response({ deleted: true }); });
  h.probe().deleteSelected(); await h.settle(); assert.ok(h.probe().dialog); await h.probe().dialog.action(); await h.settle();
  assert.equal(h.probe().selectedRef.current, null); assert.equal(h.probe().page, 'create');
  assert.equal(h.probe().browserRows.some(r => r.taskId === 'A'), false);
  assert.equal(h.taskCalls().filter(c => c.init.method === 'DELETE').length, 1); assert.equal(h.taskCalls().filter(c => c.init.method === 'POST').length, 0); h.stop();
});
for (const code of [404, 410]) test(`server-deleted cancelled record ${code}: inaccessible/expired view, no recovery or history purge`, async () => {
  const h = harness('workspace', { status: 'cancelled' }); h.render(); await h.settle();
  h.deny(code, { code: 'task_gone' }); await h.open('A');
  assert.equal(h.probe().page, 'gone'); assert.equal(h.probe().missingReason, code === 410 ? 'expired' : 'inaccessible');
  const Processing = h.load('src/components/Processing.tsx').default;
  assert.equal(flat(h.tree()).some(n => n.type === Processing), false); assert.equal(h.probe().browserRows.length, 2);
  assert.equal(h.taskCalls().length, 1); assert.ok(h.taskCalls().every(c => (c.init.method ?? 'GET') === 'GET')); h.stop();
});