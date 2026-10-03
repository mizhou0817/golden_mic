/** Pure binding checks with synthetic TEMP bytes; never import/run a builder. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import ts from 'typescript';
import * as io from '../e2e/modes/io.mjs';

const frontend = fileURLToPath(new URL('../', import.meta.url));
const helper = fs.readFileSync(path.join(frontend, 'e2e/v2/build.mjs'), 'utf8');
const environment = fs.readFileSync(path.join(frontend, 'e2e/v2/environment.ts'), 'utf8');
const anchors = ['frontend/index.html', 'frontend/package.json', 'frontend/package-lock.json',
  'frontend/vite.config.ts', 'frontend/postcss.config.cjs', 'frontend/tailwind.config.ts',
  'frontend/tsconfig.json', 'frontend/tsconfig.node.json', 'backend/mode_rules.json'];

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'gm-v2-binding-node-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true })); // exact owned synthetic root only
  const names = [...anchors, 'frontend/src/main.tsx', 'frontend/src/ui/tokens.css',
    'frontend/tsconfig.extra.json', 'frontend/vite.config.mts',
    'frontend/e2e/v2/build.mjs', 'frontend/e2e/modes/io.mjs', 'frontend/e2e/v2/lightning-css.mjs'];
  for (const name of names) {
    const file = path.join(root, name);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, `synthetic ${name}`);
  }
  // Execute ONLY the actual side-effect-free inventory function, not CLI setup,
  // environment deletion, dependency imports or Vite. Fail if boundaries move.
  const start = helper.indexOf('const configName = '), end = helper.indexOf('const describe = ');
  assert.ok(start > 0 && end > start);
  const inventory = vm.runInNewContext(`${helper.slice(start, end)}; sources`, {
    fs, path, ROOT: root, frontend: path.join(root, 'frontend'), hashFile: io.hashFile, unlinked: io.unlinked,
  });
  const sources = JSON.parse(JSON.stringify(inventory()));
  const dist = path.join(root, 'frontend/dist-canary-modes-v2-unit');
  fs.mkdirSync(path.join(dist, 'assets'), { recursive: true });
  const assets = {};
  for (const name of ['index.html', 'assets/app.js', 'assets/app.css']) {
    fs.writeFileSync(path.join(dist, name), `synthetic ${name}`);
    assets[name] = io.hashFile(path.join(dist, name));
  }
  const manifest = Object.entries(assets).map(([name, digest]) => `${digest}  ${name}\n`).join('');
  fs.writeFileSync(path.join(dist, 'ASSET_MANIFEST.sha256'), manifest);
  const helperHashes = Object.fromEntries(['frontend/e2e/v2/build.mjs', 'frontend/e2e/modes/io.mjs']
    .map(name => [name, io.hashFile(path.join(root, name))]));
  const receipt = { kind: 'synthetic-mode-custom-build', sourceHashes: sources, sourceHashesAfter: { ...sources },
    sourceUnchangedDuringBuild: true, assets, assetManifestSHA256: io.hashFile(path.join(dist, 'ASSET_MANIFEST.sha256')),
    helperHashes, cssEvidence: { engine: 'postcss' }, viteConfigLoaded: false };
  const m = { frontend: { directory: dist, hashes: { ...assets }, sourceHashes: { ...sources } },
    sourceHashes: { ...sources }, testHashes: { ...helperHashes } };
  const exports = {};
  const compiled = ts.transpileModule(environment + '\nexport { buildBinding, buildSourceName };', {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  vm.runInNewContext(compiled, { exports, URL, require(name) {
    assert.equal(name, '../modes/io.mjs');
    return { ...io, ROOT: root };
  } });
  const validate = () => {
    fs.writeFileSync(path.join(dist, 'MODE_BUILD_BINDING.json'), JSON.stringify(receipt));
    exports.buildBinding(m);
  };
  return { root, dist, inventory, receipt, m, validate, exports };
}

test('package build uses same-process writer; custom inventory matches full anchor/config policy', t => {
  assert.equal(JSON.parse(fs.readFileSync(path.join(frontend, 'package.json'), 'utf8')).scripts.build,
    'npm run typecheck && node scripts/write-manifest.mjs --build');
  const f = fixture(t);
  assert.deepEqual(Object.keys(f.receipt.sourceHashes).sort(), [...anchors,
    'frontend/src/main.tsx', 'frontend/src/ui/tokens.css', 'frontend/tsconfig.extra.json', 'frontend/vite.config.mts'].sort());
  assert.equal(f.receipt.viteConfigLoaded, false);
  f.validate();
  assert.match(helper, /assetManifestSHA256: hashFile\(path\.join\(output, 'ASSET_MANIFEST\.sha256'\)\)/);
});

test('strict source names reject query, traversal, aliases and arbitrary config directories', t => {
  const f = fixture(t);
  for (const name of ['frontend/package-lock.json?query', 'frontend/../package.json',
    'frontend/src/./main.tsx', 'frontend/src//main.tsx', 'frontend/.env', 'frontend/config/vite.config.ts',
    'frontend/scripts/unapproved.mjs', 'frontend/tsconfig.foo/bar.json', 'frontend/src/a.ts.', 'frontend/src/a\\b.ts']) {
    assert.equal(f.exports.buildSourceName(name), false, name);
    f.receipt.sourceHashes[name] = '0'.repeat(64);
    f.receipt.sourceHashesAfter[name] = '0'.repeat(64);
    assert.throws(f.validate, /safe hash entry/, name);
    delete f.receipt.sourceHashes[name]; delete f.receipt.sourceHashesAfter[name];
  }
  for (const name of ['frontend/tsconfig.unit.json', 'frontend/vite.config.cts', 'frontend/postcss.config.mjs'])
    assert.equal(f.exports.buildSourceName(name), true, name);
});

test('each required anchor and host src entry must occur in the receipt', t => {
  const f = fixture(t);
  for (const name of [...anchors, 'frontend/src/ui/tokens.css']) {
    const digest = f.receipt.sourceHashes[name];
    delete f.receipt.sourceHashes[name]; delete f.receipt.sourceHashesAfter[name]; delete f.m.frontend.sourceHashes[name];
    assert.throws(f.validate, /complete v2 build inputs/, name);
    f.receipt.sourceHashes[name] = digest; f.receipt.sourceHashesAfter[name] = digest; f.m.frontend.sourceHashes[name] = digest;
  }
});

test('bad before/after, absent after and false unchanged are rejected', t => {
  const f = fixture(t), after = f.receipt.sourceHashesAfter;
  f.receipt.sourceHashesAfter = { ...after, 'frontend/package.json': '0'.repeat(64) };
  assert.throws(f.validate, /before\/after/);
  delete f.receipt.sourceHashesAfter;
  assert.throws(f.validate, /hash map object/);
  f.receipt.sourceHashesAfter = after; f.receipt.sourceUnchangedDuringBuild = false;
  assert.throws(f.validate, /source-bound build receipt/);
});

test('current ignored standard config and actual assets cannot be stale', t => {
  const f = fixture(t), file = path.join(f.root, 'frontend/vite.config.ts'), original = fs.readFileSync(file);
  fs.writeFileSync(file, 'changed configFile:false input');
  assert.throws(f.validate, /current build input/);
  fs.writeFileSync(file, original);
  fs.writeFileSync(path.join(f.dist, 'assets/app.js'), 'stale');
  assert.throws(f.validate, /current build asset/);
});

test('asset manifest digest, inventory, duplicate and unsafe paths are strict', t => {
  const f = fixture(t), manifest = path.join(f.dist, 'ASSET_MANIFEST.sha256'), original = fs.readFileSync(manifest, 'utf8');
  fs.writeFileSync(manifest, original.replaceAll('\n', '\r\n'));
  assert.throws(f.validate, /manifest SHA256/);
  for (const bytes of [original + original.split('\n')[0] + '\n',
    original.replace('assets/app.js', 'assets/app.js?query'), original.replace('assets/app.js', 'assets/../app.js'),
    original.replace('assets/app.js', 'assets/other.js')]) {
    fs.writeFileSync(manifest, bytes); f.receipt.assetManifestSHA256 = io.hashFile(manifest);
    assert.throws(f.validate, /manifest inventory|safe hash entry/);
  }
});

test('required helper itself, stale helper and Lightning optional helper remain bound', t => {
  const f = fixture(t), helpers = { ...f.receipt.helperHashes };
  delete f.receipt.helperHashes['frontend/e2e/v2/build.mjs'];
  assert.throws(f.validate, /complete build helpers/);
  f.receipt.helperHashes = { ...helpers, 'frontend/e2e/v2/build.mjs': '0'.repeat(64) };
  assert.throws(f.validate, /current build helper/);
  f.receipt.helperHashes = helpers; f.receipt.cssEvidence.engine = 'lightningcss';
  assert.throws(f.validate, /complete build helpers/);
  const name = 'frontend/e2e/v2/lightning-css.mjs', digest = io.hashFile(path.join(f.root, name));
  f.receipt.helperHashes[name] = digest; f.m.testHashes[name] = digest;
  f.validate();
});

test('standard receipt accepted; bound returns false on later receipt drift', t => {
  const f = fixture(t);
  f.receipt.kind = 'frontend-source-build'; f.receipt.schemaVersion = 1;
  delete f.receipt.helperHashes;
  f.validate();
  f.receipt.sourceHashesAfter['frontend/package.json'] = '0'.repeat(64);
  fs.writeFileSync(path.join(f.dist, 'MODE_BUILD_BINDING.json'), JSON.stringify(f.receipt));
  assert.equal(f.exports.bound(f.m), false);
});

test('inventory discovers added top-level config, excludes arbitrary directories and checks every source', t => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.root, 'frontend/tsconfig.new.json'), '{}');
  fs.mkdirSync(path.join(f.root, 'frontend/arbitrary'));
  fs.writeFileSync(path.join(f.root, 'frontend/arbitrary/config.json'), '{}');
  const snapshot = f.inventory();
  assert.ok(snapshot['frontend/tsconfig.new.json']);
  assert.ok(!snapshot['frontend/arbitrary/config.json']);
  assert.notDeepEqual(snapshot, f.receipt.sourceHashes);
});