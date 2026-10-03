import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

// Execute the actual modules with a receiver-sensitive timer boundary. Native
// Edge also rejects scheduler.set = setTimeout; Node timers alone do not.
const task = 'a'.repeat(32);
const compile = name => ts.transpileModule(readFileSync(new URL('../src/lib/' + name, import.meta.url), 'utf8'), {
  fileName: name, compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
const apiCode = compile('workbenchApi.ts'), pendingCode = compile('pendingEdits.ts');
function fixture() {
  let nextId = 0, raw = JSON.stringify({ schemaVersion: 1, draft: { retained: true } }), writes = 0;
  const callbacks = new Map(), calls = [];
  function setTimeout(callback, delay) {
    if (this != null) throw new TypeError('Illegal invocation');
    const id = ++nextId; callbacks.set(id, callback); calls.push({ kind: 'set', id, delay }); return id;
  }
  function clearTimeout(id) {
    if (this != null) throw new TypeError('Illegal invocation');
    callbacks.delete(id); calls.push({ kind: 'clear', id });
  }
  function load(code, api) {
    const module = { exports: {} };
    vm.runInNewContext(code, { module, exports: module.exports, setTimeout, clearTimeout,
      require(name) { assert.equal(name, './workbenchApi'); return api; },
      fetch() { assert.fail('No network in pending draft tests'); },
    }, { timeout: 1000 });
    return module.exports;
  }
  const pending = load(pendingCode, load(apiCode));
  const storage = { getItem: () => raw, setItem: (_, value) => { writes++; raw = value; } };
  const draft = start => ({ ...pending.emptyPending(), base: 0, pendTrim: { 1: { start, end: 4.123456789 } } });
  return { pending, storage, draft, calls, callbacks, setTimeout, clearTimeout,
    get writes() { return writes; }, root: () => JSON.parse(raw),
    fire(id) { const callback = callbacks.get(id); assert.ok(callback); callbacks.delete(id); callback(); },
  };
}

test('receiver model rejects native timers called as arbitrary object methods', () => {
  const f = fixture(), invalid = { set: f.setTimeout, clear: f.clearTimeout };
  assert.throws(() => invalid.set(() => {}, 300), /Illegal invocation/);
  assert.throws(() => invalid.clear(1), /Illegal invocation/);
});

test('default scheduler saves a precise quote trim without rebinding native timers', () => {
  const f = fixture(), writer = f.pending.pendingWriter(task, f.storage);
  writer.schedule(f.draft(1.234567891));
  assert.equal(f.writes, 0);
  assert.deepEqual(f.calls, [{ kind: 'set', id: 1, delay: 300 }]);
  f.fire(1);
  assert.equal(f.writes, 1);
  const saved = f.pending.readPending(task, f.storage);
  assert.equal(saved.base, 0); assert.equal(saved.pendTrim[1].start, 1.234567891);
  assert.equal(saved.pendTrim[1].end, 4.123456789);
  assert.deepEqual(f.root().draft, { retained: true });
});

test('default debounce cancels the previous timer and persists only the latest edit', () => {
  const f = fixture(), writer = f.pending.pendingWriter(task, f.storage);
  writer.schedule(f.draft(1)); writer.schedule(f.draft(2));
  assert.deepEqual([...f.callbacks.keys()], [2]);
  assert.deepEqual(f.calls, [{ kind: 'set', id: 1, delay: 300 }, { kind: 'clear', id: 1 }, { kind: 'set', id: 2, delay: 300 }]);
  f.fire(2); assert.equal(f.pending.readPending(task, f.storage).pendTrim[1].start, 2);
  assert.equal(f.writes, 1);
});

test('explicit lifecycle flush clears the native timer and never duplicates a write', () => {
  const f = fixture(), writer = f.pending.pendingWriter(task, f.storage);
  writer.schedule(f.draft(1)); writer.flush(); writer.flush();
  assert.equal(f.callbacks.size, 0); assert.equal(f.writes, 1);
  assert.equal(f.pending.readPending(task, f.storage).pendTrim[1].start, 1);
  writer.schedule(null); writer.flush();
  assert.equal(f.pending.readPending(task, f.storage), null);
  assert.deepEqual(f.root().draft, { retained: true });
});

test('injected schedulers retain their own method receiver and exact 300 ms delay', () => {
  const f = fixture();
  const scheduler = { callback: null,
    set(callback, delay) { assert.equal(this, scheduler); assert.equal(delay, 300); this.callback = callback; return 7; },
    clear(id) { assert.equal(this, scheduler); assert.equal(id, 7); this.callback = null; },
  };
  const writer = f.pending.pendingWriter(task, f.storage, scheduler);
  writer.schedule(f.draft(1)); assert.equal(typeof scheduler.callback, 'function');
  writer.flush(); assert.equal(scheduler.callback, null); assert.equal(f.writes, 1);
  assert.equal(f.calls.length, 0);
});