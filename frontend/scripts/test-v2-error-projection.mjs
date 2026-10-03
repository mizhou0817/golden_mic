import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../e2e/v2/diagnostics.ts', import.meta.url), 'utf8');
const module = { exports: {} };
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText,
  { module, exports: module.exports }, { timeout: 1000 });
const facts = value => JSON.parse(JSON.stringify(module.exports.pageErrorFacts(value)));
test('page error projection keeps only known kind and bound asset coordinates', () => {
  assert.deepEqual(facts({ name: 'TypeError', message: 'Illegal invocation', stack: 'TypeError: Illegal invocation\n    at Object.secretMethod (http://127.0.0.1:8787/assets/index-Test_1.js:21:99)' }),
    { name: 'TypeError', category: 'illegal-invocation', frames: [{ asset: 'index-Test_1.js', line: 21, column: 99 }] });
});
test('raw secrets, foreign URLs, query strings and dynamic identifiers are discarded', () => {
  const result = facts({ name: 'Bearer private', message: 'token=private', stack: [
    'Error token=private', ' at private (http://127.0.0.1:8787/api/tasks/private/video?token=private:1:2)',
    ' at private (http://127.0.0.1:8787/assets/index.js?token=private:1:2)',
    ' at private (https://foreign.invalid/assets/index.js:1:2)',
    ' at private (file:///private/source.ts:1:2)',
  ].join('\n') });
  assert.deepEqual(result, { name: 'other', category: 'other', frames: [] });
  assert.equal(JSON.stringify(result).includes('private'), false);
});
test('projection is bounded and does not serialize arbitrary error properties', () => {
  const result = facts({ name: 'ReferenceError', message: 'private is not defined', token: 'private',
    stack: Array(20).fill(' at private (http://127.0.0.1:8787/assets/index.js:1:2)').join('\n') });
  assert.equal(result.frames.length, 8); assert.equal(result.category, 'other');
  assert.deepEqual(Object.keys(result), ['name', 'category', 'frames']);
});