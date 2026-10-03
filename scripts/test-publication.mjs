import assert from 'node:assert/strict';
import test from 'node:test';
import { checkLinks } from './verify-publication.mjs';

test('publication links resolve from the published set rather than local ignored files', () => {
  const docs = { 'docs/README.md': '[guide](QUICKSTART.md) [code](https://github.com/mizhou0817/golden_mic/blob/main/frontend/src/App.tsx)' };
  assert.deepEqual(checkLinks(docs, ['docs/QUICKSTART.md', 'frontend/src/App.tsx']), { checked: 2, errors: [] });
  assert.equal(checkLinks(docs, ['docs/QUICKSTART.md']).errors.length, 1);
});

test('generated outputs remain rejected even if mistakenly present in the published set', () => {
  const target = 'canary_test/artifacts/private.json';
  const result = checkLinks({ 'README.md': `[receipt](${target})` }, [target]);
  assert.equal(result.errors[0].reason, 'unpublished_generated_target');
});

test('fenced examples and external third-party references are not treated as source navigation', () => {
  const docs = { 'README.md': '```md\n[x](missing.md)\n```\n[site](https://example.org)\n[anchor](#one)' };
  assert.deepEqual(checkLinks(docs, []), { checked: 0, errors: [] });
});

test('directories, encoded paths, traversal and case sensitivity remain explicit', () => {
  assert.deepEqual(checkLinks({ 'docs/README.md': '[dir](../backend/) [space](My%20Guide.md)' },
    ['backend/main.py', 'docs/My Guide.md']), { checked: 2, errors: [] });
  assert.equal(checkLinks({ 'README.md': '[x](../outside.md)' }, ['../outside.md']).errors.length, 1);
  assert.equal(checkLinks({ 'README.md': '[x](docs/readme.md)' }, ['docs/README.md']).errors.length, 1);
});