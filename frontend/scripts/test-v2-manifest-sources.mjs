import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

// Execute the actual loadManifest source-group loop and its actual helpers.
// No copied predicate, manifest fixture on disk, io module, host, or build.
const source = readFileSync(new URL('../e2e/v2/environment.ts', import.meta.url), 'utf8');
const tree = ts.createSourceFile('environment.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
const helpers = new Set(['invariant', 'configName', 'sourceFiles', 'canonicalName', 'buildSourceName', 'hashMap']);
const declarations = tree.statements.filter(node =>
  (ts.isFunctionDeclaration(node) && helpers.has(node.name?.text))
  || (ts.isVariableStatement(node) && node.declarationList.declarations.some(decl =>
    ts.isIdentifier(decl.name) && helpers.has(decl.name.text))));
assert.equal(declarations.length, helpers.size, 'extract every real source-validation helper');
const load = tree.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'loadManifest');
assert.ok(load?.body, 'actual loadManifest required');
const loops = load.body.statements.filter(node => ts.isForOfStatement(node)
  && ts.isArrayLiteralExpression(node.expression)
  && node.expression.elements.map(element => element.getText(tree)).join(',')
    === 'm.sourceHashes,m.testHashes,m.frontend.sourceHashes');
assert.equal(loops.length, 1, 'extract the actual loop over all three source maps');
const script = declarations.map(node => node.getText(tree)).join('\n')
  + `\nfunction validate(m: unknown) { ${loops[0].getText(tree)} }\nglobalThis.validate = validate;`;
const compiled = ts.transpileModule(script, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const context = vm.createContext({ exports: {} });
vm.runInContext(compiled, context, { timeout: 1000 });

const digest = 'a'.repeat(64);
const probe = 'frontend/scripts/test-v2-probe-guard.mjs';
const groups = ['sourceHashes', 'testHashes', 'frontend.sourceHashes'];
function validate(group, value) {
  const manifest = {
    sourceHashes: { 'backend/drafts.py': digest },
    testHashes: { 'frontend/e2e/v2/environment.ts': digest },
    frontend: { sourceHashes: { 'frontend/src/main.tsx': digest } },
  };
  if (group === 'frontend.sourceHashes') manifest.frontend.sourceHashes = value;
  else manifest[group] = value;
  context.validate(manifest);
}
function rejects(group, value) {
  assert.throws(() => validate(group, value), /V2 acceptance contract:/);
}

for (const group of groups) {
  test(`${group}: retains previously permitted source families`, () => {
    const names = [
      'backend/drafts.py', 'backend/providers/asr.py', 'backend/mode_rules.json',
      'frontend/src/components/CreateWizard.tsx', 'frontend/index.html',
      'frontend/package.json', 'frontend/package-lock.json', 'frontend/tsconfig.node.json',
      'frontend/vite.config.ts', 'frontend/postcss.config.cjs', 'frontend/tailwind.config.ts',
      'frontend/e2e/v2/environment.ts', 'frontend/e2e/v2/build.mjs',
      'frontend/e2e/modes/io.mjs', 'frontend/playwright.v2.config.ts',
      'tests/v2_acceptance_server.py', 'tests/mode_acceptance_server.py', 'tests/workspace_fixture.py',
    ];
    validate(group, Object.fromEntries(names.map(name => [name, digest])));
  });

  test(`${group}: permits only the exact additional probe-guard script`, () => {
    validate(group, { [probe]: digest });
  });

  test(`${group}: rejects sibling scripts, case variants, and lookalike paths`, () => {
    for (const name of [
      'frontend/scripts/test-v2-manifest-sources.mjs', 'frontend/scripts/other.mjs',
      `${probe}.bak`, `${probe}/child`, probe.replace('.mjs', '.js'),
      probe.replace('probe-guard', 'probe-guards'), probe.toUpperCase(),
      probe.replace('/scripts/', '/Scripts/'), `prefix/${probe}`, `/${probe}`,
      'tests/other.py', 'frontend/playwright.modes.config.ts', 'backend/drafts.txt',
    ]) rejects(group, { [name]: digest });
  });

  test(`${group}: rejects noncanonical paths even inside allowed families`, () => {
    for (const name of [
      'backend/../drafts.py', 'backend/./drafts.py', 'backend//drafts.py',
      'backend/folder./drafts.py', 'backend/folder /drafts.py',
      'frontend/src/../scripts/test-v2-probe-guard.mjs', 'frontend/src//file.ts',
      'frontend/src/file.ts.', 'frontend/src/file.ts ', 'frontend/src/file.ts:stream',
      'frontend/src/file\u0000.ts', 'frontend/src/file\u001f.ts', 'frontend/src/file\u007f.ts',
      'frontend/src/<file>.ts', 'frontend/src/"file.ts', 'frontend/src/file|.ts',
      'frontend/src/file?.ts', 'frontend/src/file*.ts',
      probe.replaceAll('/', '\\'), probe.replace('/scripts/', '/scripts/../scripts/'),
      `${probe} `, `${probe}.`,
    ]) rejects(group, { [name]: digest });
  });

  test(`${group}: requires a lowercase 64-character string digest without coercion`, () => {
    for (const name of ['backend/drafts.py', probe]) {
      for (const value of [
        '', 'a'.repeat(63), 'a'.repeat(65), 'A'.repeat(64), 'g'.repeat(64),
        `${digest}\n`, ` ${digest}`, null, undefined, true, 123, [digest],
        { toString: () => digest },
      ]) rejects(group, { [name]: value });
    }
  });

  test(`${group}: rejects missing, empty, primitive, and array maps`, () => {
    const disguisedArray = Object.assign([], { [probe]: digest });
    for (const value of [undefined, null, {}, [], disguisedArray, '', 'text', true, 42]) {
      rejects(group, value);
    }
  });

  test(`${group}: rejects case aliases that are individually allowed`, () => {
    const lower = 'backend/alias.py', upper = 'backend/Alias.py';
    validate(group, { [lower]: digest });
    validate(group, { [upper]: digest });
    rejects(group, { [lower]: digest, [upper]: digest });
    rejects(group, { [upper]: digest, [lower]: 'b'.repeat(64) });
  });
}