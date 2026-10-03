import { createHash } from 'node:crypto';
import { lstat, readdir, readFile, realpath, writeFile } from 'node:fs/promises';
import { basename, dirname, isAbsolute, resolve, relative, sep, toNamespacedPath } from 'node:path';
import { fileURLToPath } from 'node:url';

const frontend = resolve(fileURLToPath(new URL('../', import.meta.url)));
const repository = dirname(frontend);
const args = process.argv.slice(2);
// --build captures inputs BEFORE invoking Vite in this process. The old
// post-Vite invocation may validate an existing receipt, never manufacture one.
const buildRequested = args[0] === '--build';
if (buildRequested) args.shift();
const isolated = args.length > 0;
let root = resolve(frontend, 'dist');
if (isolated) {
  if (args.length !== 2 || args[0] !== '--out-dir') {
    throw new Error('Usage: node scripts/write-manifest.mjs [--build] [--out-dir dist-canary-<safe-label>]');
  }
  const input = args[1], candidate = resolve(frontend, input), name = basename(candidate);
  // Accept only a leaf name or its canonical absolute path, never arbitrary
  // relative paths, traversal, shared dist, or an output directory elsewhere.
  if (!/^dist-canary-[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(name)
    || relative(frontend, candidate) !== name
    || (input !== name && (!isAbsolute(input) || input.replaceAll('/', sep) !== candidate))) {
    throw new Error('--out-dir must select a canonical direct frontend/dist-canary-<safe-label> directory.');
  }
  root = candidate;
}

async function noLinks(path, allowMissing = false) {
  const absolute = resolve(path), chain = [];
  for (let cursor = absolute; ; cursor = dirname(cursor)) {
    chain.unshift(cursor);
    if (dirname(cursor) === cursor) break;
  }
  let info;
  for (const cursor of chain) {
    // Windows lstat('\\\\?\\C:\\') may return EISDIR, while lstat('C:\\')
    // reports the actual root metadata. Keep long-path handling for descendants.
    try { info = await lstat(dirname(cursor) === cursor ? cursor : toNamespacedPath(cursor)); }
    catch (error) {
      if (allowMissing && error.code === 'ENOENT') return undefined;
      throw new Error('Build manifest path is missing or unreadable.');
    }
    if (info.isSymbolicLink()) throw new Error('Build manifest paths may not traverse links/junctions.');
    if (cursor !== absolute && !info.isDirectory()) throw new Error('Build manifest path parent is not a directory.');
  }
  if (relative(toNamespacedPath(absolute), toNamespacedPath(await realpath(toNamespacedPath(absolute)))) !== '') {
    throw new Error('Build manifest paths must resolve to their canonical location.');
  }
  return info;
}

const sourceFiles = ['frontend/index.html', 'frontend/package.json', 'frontend/package-lock.json',
  'frontend/vite.config.ts', 'frontend/postcss.config.cjs', 'frontend/tailwind.config.ts',
  'frontend/tsconfig.json', 'frontend/tsconfig.node.json', 'backend/mode_rules.json'];
const configName = /^(?:(?:vite|postcss|tailwind)\.config\.(?:[cm]?[jt]s)|tsconfig(?:\.[A-Za-z0-9_-]+)*\.json)$/;
const safeName = name => !/[\\\x00-\x1f\x7f<>:"|?*]/.test(name)
  && name.split('/').every(part => part && part !== '.' && part !== '..' && !/[. ]$/.test(part));
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
const sameMap = (a, b) => a && b && typeof a === 'object' && !Array.isArray(a)
  && Object.keys(a).length === Object.keys(b).length
  && Object.entries(b).every(([name, value]) => Object.hasOwn(a, name) && a[name] === value);
async function stableBytes(path) {
  const before = await noLinks(path);
  if (!before?.isFile() || before.nlink !== 1) throw new Error('Expected a single-link regular build input.');
  const bytes = await readFile(toNamespacedPath(path));
  const after = await noLinks(path);
  if (!after?.isFile() || after.nlink !== 1 || before.dev !== after.dev || before.ino !== after.ino
    || before.size !== after.size || before.mtimeMs !== after.mtimeMs || bytes.length !== before.size) {
    throw new Error('Build input changed while reading.');
  }
  return bytes;
}
async function sources() {
  const names = new Set(sourceFiles);
  async function walk(dir) {
    if (!(await noLinks(dir))?.isDirectory()) throw new Error('Invalid frontend source directory.');
    for (const name of await readdir(toNamespacedPath(dir))) {
      const path = resolve(dir, name), info = await noLinks(path);
      if (info?.isDirectory()) await walk(path);
      else names.add(relative(repository, path).split(sep).join('/'));
    }
  }
  await walk(resolve(frontend, 'src'));
  for (const name of await readdir(toNamespacedPath(frontend))) {
    if (configName.test(name)) names.add(`frontend/${name}`);
  }
  const result = {};
  for (const name of [...names].sort()) {
    if (!safeName(name)) throw new Error('Invalid frontend source name.');
    result[name] = hash(await stableBytes(resolve(repository, name)));
  }
  if (!Object.keys(result).some(name => name.startsWith('frontend/src/'))
    || new Set(Object.keys(result).map(name => name.toLowerCase())).size !== names.size) {
    throw new Error('Incomplete or case-aliased frontend source inventory.');
  }
  return result;
}
const beforeSources = await sources();
if (buildRequested) {
  const existingRoot = await noLinks(root, true);
  if (existingRoot && (!existingRoot.isDirectory() || isolated)) {
    throw new Error('Isolated source-bound builds require a fresh output directory.');
  }
  // Prevent Vite emptyOutDir from traversing a pre-existing linked tree.
  if (existingRoot) {
    const inspect = async dir => {
      for (const name of await readdir(toNamespacedPath(dir))) {
        const path = resolve(dir, name), info = await noLinks(path);
        if (info?.isDirectory()) await inspect(path);
        else if (!info?.isFile() || info.nlink !== 1) throw new Error('Unsafe existing build output.');
      }
    };
    await inspect(root);
  }
  const { build } = await import('vite');
  // No dotenv or public-directory copy; the receipt covers exactly this policy.
  await build({ root: frontend, envDir: false, publicDir: false,
    build: { outDir: root, emptyOutDir: true } });
  if (!sameMap(beforeSources, await sources())) throw new Error('Source drift during build; no receipt written.');
}

// Validate the selected root and its parents BEFORE any directory walk/read.
if (!(await noLinks(root))?.isDirectory()
  || !(await noLinks(resolve(root, 'assets')))?.isDirectory()
  || !(await noLinks(resolve(root, 'index.html')))?.isFile()) {
  throw new Error('The selected build must contain index.html and an assets directory.');
}
const output = resolve(root, 'ASSET_MANIFEST.sha256');
const files = [];
async function visit(dir) {
  for (const name of await readdir(toNamespacedPath(dir))) {
    const path = resolve(dir, name), info = await lstat(toNamespacedPath(path));
    const file = relative(root, path).split(sep).join('/');
    if (info.isSymbolicLink()) throw new Error('Build output may not contain links/junctions.');
    if (/[\\\x00-\x1f\x7f<>:"|?*]/.test(file)
      || !file.split('/').every(part => part && part !== '.' && part !== '..' && !/[. ]$/.test(part))) {
      throw new Error('Build asset paths must be canonical relative POSIX paths.');
    }
    if (['asset_manifest.sha256', 'mode_build_binding.json'].includes(file.toLowerCase())) {
      if (!['ASSET_MANIFEST.sha256', 'MODE_BUILD_BINDING.json'].includes(file)) throw new Error('Build metadata must use canonical casing.');
      if (!info.isFile() || info.nlink !== 1) throw new Error('The checksum manifest must be an unlinked regular file.');
    } else if (info.isDirectory()) await visit(path);
    else {
      if (!info.isFile() || info.nlink !== 1) throw new Error('Build assets must be unlinked regular files.');
      if (file !== 'index.html' && !file.startsWith('assets/')) throw new Error('Unexpected build output file.');
      files.push(file);
    }
  }
}
await visit(root);
if (new Set(files.map(file => file.toLowerCase())).size !== files.length) throw new Error('Build contains case-aliased assets.');
if (!files.some(file => file.startsWith('assets/'))) throw new Error('A build must contain bundled assets.');
const lines = [];
for (const file of files.sort()) {
  const path = resolve(root, file);
  const before = await noLinks(path);
  if (!before?.isFile() || before.nlink !== 1) throw new Error('Build assets must be single-link regular files before reading.');
  const bytes = await readFile(toNamespacedPath(path));
  const after = await noLinks(path);
  if (!before?.isFile() || !after?.isFile() || before.nlink !== 1 || after.nlink !== 1
    || before.size !== after.size || before.mtimeMs !== after.mtimeMs || bytes.length !== before.size) {
    throw new Error('Build asset changed while generating its manifest.');
  }
  lines.push(`${createHash('sha256').update(bytes).digest('hex')}  ${file}`);
}
const existing = await noLinks(output, true);
if (existing && (!existing.isFile() || existing.nlink !== 1)) throw new Error('The checksum manifest must be an unlinked regular file.');
const manifest = lines.join('\n') + '\n';
const assets = Object.fromEntries(lines.map(line => [line.slice(66), line.slice(0, 64)]));
const afterSources = await sources();
if (!sameMap(beforeSources, afterSources)) throw new Error('Source drift during manifest generation.');
const bindingPath = resolve(root, 'MODE_BUILD_BINDING.json');
if (!buildRequested) {
  // No re-signing stale assets with today's sources. Preserve the original
  // receipt AND manifest bytes (custom helpers may use a different sort order).
  const receipt = JSON.parse((await stableBytes(bindingPath)).toString('utf8'));
  const oldManifest = await stableBytes(output);
  const oldLines = oldManifest.toString('utf8').trimEnd().split(/\r?\n/);
  const oldAssets = Object.fromEntries(oldLines.map(line => [line.slice(66), line.slice(0, 64)]));
  if (!['frontend-source-build', 'synthetic-mode-custom-build'].includes(receipt.kind)
    || receipt.sourceUnchangedDuringBuild !== true
    || !sameMap(receipt.sourceHashes, beforeSources) || !sameMap(receipt.sourceHashesAfter, afterSources)
    || !sameMap(receipt.assets, assets) || !sameMap(oldAssets, assets) || oldLines.length !== files.length
    || oldLines.some(line => !/^[a-f0-9]{64}  .+$/.test(line))
    || (receipt.kind === 'frontend-source-build' && receipt.schemaVersion !== 1)
    || ((receipt.kind === 'frontend-source-build' || Object.hasOwn(receipt, 'assetManifestSHA256'))
      && receipt.assetManifestSHA256 !== hash(oldManifest))) {
    throw new Error('Missing, incomplete or stale build binding; use --build, never re-sign existing assets.');
  }
  console.log(`Validated existing source-bound manifest: ${lines.length} assets`);
} else {
  // Deterministic receipt: no absolute paths, timestamps or random identifiers.
  const receipt = { kind: 'frontend-source-build', schemaVersion: 1,
    sourceHashes: beforeSources, sourceHashesAfter: afterSources, sourceUnchangedDuringBuild: true,
    assets, assetManifestSHA256: hash(manifest) };
  await noLinks(bindingPath, true);
  await writeFile(toNamespacedPath(output), manifest, 'utf8');
  await writeFile(toNamespacedPath(bindingPath), JSON.stringify(receipt, null, 2) + '\n', { encoding: 'utf8', flag: 'wx' });
  console.log(`Wrote source-bound build manifest: ${lines.length} assets`);
}