/** Isolated v2 build. Never repairs dependencies, retries reads or reuses output.
 * node frontend/e2e/v2/build.mjs --label <fresh-v2-label>
 * Explicit reviewed alternative: append --css-engine lightningcss. The default
 * remains the original PostCSS path. See lightning-css.mjs for import limits.
 * Failed output retains V2_BUILD_DIAGNOSTIC.json, not a success receipt.
 */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire, registerHooks } from 'node:module';
import { fileURLToPath } from 'node:url';
import { exists, hashFile, ROOT, samePath, unlinked } from '../modes/io.mjs';

const args = process.argv.slice(2);
const cssEngine = args.length === 4 && args[2] === '--css-engine' && args[3] === 'lightningcss' ? 'lightningcss' : 'postcss';
if (!(args.length === 2 || (args.length === 4 && cssEngine === 'lightningcss')) || args[0] !== '--label' || !/^v2-[a-z0-9][a-z0-9-]{0,52}$/.test(args[1])) {
  throw new Error('Use --label with a fresh v2- lowercase label (4..56 characters), optionally --css-engine lightningcss.');
}
const frontend = unlinked(path.join(ROOT, 'frontend'));
const output = path.join(frontend, `dist-canary-modes-${args[1]}`);
if (!samePath(path.dirname(output), frontend) || exists(output)) {
  throw new Error('Custom output already exists or is unsafe; use a fresh label.');
}
const allowed = /^(?:SYSTEMROOT|WINDIR|PATH|PATHEXT|COMSPEC|SYSTEMDRIVE|PROGRAMFILES|PROGRAMFILES\(X86\)|LOCALAPPDATA|APPDATA|USERPROFILE|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE)$/i;
for (const name of Object.keys(process.env)) if (!allowed.test(name)) delete process.env[name];
const local = process.env.LOCALAPPDATA ?? process.env.LocalAppData;
if (!local) throw new Error('LOCALAPPDATA required for isolated Windows build TEMP.');
const temporary = fs.mkdtempSync(path.join(unlinked(path.join(local, 'Temp')), 'golden-mic-v2-build-'));
Object.assign(process.env, { TEMP: temporary, TMP: temporary, TMPDIR: temporary, NODE_ENV: 'production',
  PLAYWRIGHT_NO_COPY_PROMPT: '1', PYTHON_DOTENV_DISABLED: '1' });
const envDir = path.join(temporary, 'empty-env');
fs.mkdirSync(envDir);
fs.mkdirSync(output); // Exclusive claim; preserve failures, never empty/reuse.
const json = value => JSON.stringify(value, null, 2).replace(/[\u007f-\uffff]/g,
  c => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`);
const write = (name, value) => fs.writeFileSync(path.join(output, name), json(value) + '\n', { flag: 'wx' });
// Same complete input budget as scripts/write-manifest.mjs and deploy/frontend_binding.py.
// Configs remain bound even when configFile:false deliberately does not load them.
const configName = /^(?:(?:vite|postcss|tailwind)\.config\.(?:[cm]?[jt]s)|tsconfig(?:\.[A-Za-z0-9_-]+)*\.json)$/;
const safeName = name => !/[\\\x00-\x1f\x7f<>:"|?*]/.test(name)
  && name.split('/').every(part => part && part !== '.' && part !== '..' && !/[. ]$/.test(part));
const sources = () => {
  const names = new Set(['frontend/index.html', 'frontend/package.json', 'frontend/package-lock.json',
    'frontend/vite.config.ts', 'frontend/postcss.config.cjs', 'frontend/tailwind.config.ts',
    'frontend/tsconfig.json', 'frontend/tsconfig.node.json', 'backend/mode_rules.json']);
  const visit = dir => {
    for (const entry of fs.readdirSync(unlinked(dir), { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const file = unlinked(path.join(dir, entry.name));
      if (entry.isDirectory()) visit(file);
      else if (entry.isFile()) names.add(path.relative(ROOT, file).split(path.sep).join('/'));
      else throw new Error('Unexpected frontend source entry.');
    }
  };
  visit(path.join(frontend, 'src'));
  for (const name of fs.readdirSync(frontend)) if (configName.test(name)) names.add(`frontend/${name}`);
  if (![...names].some(name => name.startsWith('frontend/src/'))
    || new Set([...names].map(name => name.toLowerCase())).size !== names.size) throw new Error('Incomplete or case-aliased source inventory.');
  const hashes = {};
  for (const name of [...names].sort()) {
    if (!safeName(name)) throw new Error('Invalid frontend source name.');
    const file = unlinked(path.join(ROOT, name)), info = fs.statSync(file);
    if (!info.isFile() || info.nlink !== 1) throw new Error('Expected single-link regular build input.');
    hashes[name] = hashFile(file);
  }
  return hashes;
};
const describe = error => ({ code: error.code, errno: error.errno, syscall: error.syscall,
  path: error.path, message: error.message, stack: error.stack });
const moduleLoadFailures = [];
const hooks = registerHooks({ load(url, context, nextLoad) {
  try { return nextLoad(url, context); }
  catch (error) { moduleLoadFailures.push({ url, ...describe(error) }); throw error; }
} });
let before, after, helperHashes;
const cssEvidence = { engine: cssEngine, explicitOptIn: cssEngine === 'lightningcss' };
try {
  before = sources();
  const helperFiles = [fileURLToPath(import.meta.url), fileURLToPath(new URL('../modes/io.mjs', import.meta.url))];
  if (cssEngine === 'lightningcss') helperFiles.push(fileURLToPath(new URL('./lightning-css.mjs', import.meta.url)));
  helperHashes = Object.fromEntries(helperFiles
    .map(file => [path.relative(ROOT, file).split(path.sep).join('/'), hashFile(file)]));
  process.chdir(frontend); // Preserve the approved Tailwind content globs.
  const require = createRequire(import.meta.url);
  const approved = require(path.join(frontend, 'postcss.config.cjs'));
  if (JSON.stringify(approved) !== JSON.stringify({ plugins: { tailwindcss: {}, autoprefixer: {} } })) {
    throw new Error('Approved PostCSS configuration changed; review explicit plugin equivalence.');
  }
  const [{ build }, { default: react }, { default: tailwindcss }, { default: autoprefixer }] = await Promise.all([
    import('vite'), import('@vitejs/plugin-react'), import('tailwindcss'), import('autoprefixer'),
  ]);
  const alternative = cssEngine === 'lightningcss'
    ? await (await import('./lightning-css.mjs')).prepareLightning({ frontend, sourceHashes: before, evidence: cssEvidence }) : null;
  const result = await build({ root: frontend, configFile: false, envDir, plugins: [react(), ...(alternative?.plugins ?? [])], publicDir: false,
    css: alternative?.css ?? { postcss: { plugins: [tailwindcss({ config: path.join(frontend, 'tailwind.config.ts') }), autoprefixer()] } },
    cacheDir: path.join(temporary, 'vite-cache'), build: { outDir: output, emptyOutDir: false, ...alternative?.build } });
  if (alternative) await alternative.verify((Array.isArray(result) ? result : [result]).flatMap(item => item.output));
  after = sources();
  if (JSON.stringify(before) !== JSON.stringify(after)) throw new Error('Source drift during build; preserve output and choose a fresh label.');
  for (const [name, digest] of Object.entries(helperHashes)) {
    if (hashFile(path.join(ROOT, name)) !== digest) throw new Error('Build helper changed during build.');
  }
  const assets = {};
  const collect = dir => {
    for (const entry of fs.readdirSync(unlinked(dir), { withFileTypes: true })) {
      const file = unlinked(path.join(dir, entry.name));
      if (entry.isDirectory()) collect(file);
      else if (entry.isFile()) {
        const name = path.relative(output, file).split(path.sep).join('/');
        if (!/^(?:index\.html|assets\/[A-Za-z0-9_./-]+)$/.test(name)) throw new Error('Unexpected custom build entry.');
        assets[name] = hashFile(file);
      } else throw new Error('Unexpected custom build entry.');
    }
  };
  collect(output);
  if (!assets['index.html'] || Object.keys(assets).length < 3) throw new Error('Incomplete custom frontend build.');
  fs.writeFileSync(path.join(output, 'ASSET_MANIFEST.sha256'), Object.entries(assets).sort(([a], [b]) => a.localeCompare(b))
    .map(([name, digest]) => `${digest}  ${name}\n`).join(''), { flag: 'wx', encoding: 'ascii' });
  write('MODE_BUILD_BINDING.json', { kind: 'synthetic-mode-custom-build', sourceHashes: before, sourceHashesAfter: after,
    sourceUnchangedDuringBuild: true, assets, helperHashes, sharedDistAccessed: false, dotenvLoaded: false,
    assetManifestSHA256: hashFile(path.join(output, 'ASSET_MANIFEST.sha256')),
    cssEvidence,
    viteConfigLoaded: false, existingPostcssTailwindConfigUsed: true, explicitPostcssPlugins: ['tailwindcss', 'autoprefixer'],
    publicDirCopied: false });
  console.log(json({ status: 'custom-build-complete', frontendDir: output, temporaryRoot: temporary,
    sources: Object.keys(before).length, assets: Object.keys(assets).length,
    manifestSHA256: hashFile(path.join(output, 'ASSET_MANIFEST.sha256')) }));
} catch (error) {
  let snapshotError;
  try { after = sources(); } catch (failure) { snapshotError = describe(failure); }
  write('V2_BUILD_DIAGNOSTIC.json', { status: 'failed', label: args[1], node: process.version,
    cssEvidence, helperHashes,
    temporaryRoot: temporary, sourceHashesBefore: before, sourceHashesAfter: after,
    sourceUnchangedDuringBuild: Boolean(before && after && JSON.stringify(before) === JSON.stringify(after)),
    snapshotError, moduleLoadFailures, error: describe(error), sharedDistAccessed: false,
    dotenvLoaded: false, viteConfigLoaded: false, dependencyRepairAttempted: false });
  console.error(json({ status: 'failed', diagnostic: path.join(output, 'V2_BUILD_DIAGNOSTIC.json'), moduleLoadFailures }));
  process.exitCode = 1;
} finally { hooks.deregister(); }