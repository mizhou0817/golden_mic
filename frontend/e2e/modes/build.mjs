/** Optional parent-invoked isolated build; no existing config/package edits.
 * node frontend/e2e/modes/build.mjs --label <new-safe-label>
 * The author of this harness does NOT run this build or start the host/browser.
 */
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { exists, hashFile, ROOT, samePath, unlinked } from './io.mjs';

const args = process.argv.slice(2);
if (args.length !== 2 || args[0] !== '--label' || !/^[a-z0-9][a-z0-9-]{0,55}$/.test(args[1])) {
  throw new Error('Use --label with a fresh lowercase label (1..56 characters).');
}
const frontend = unlinked(path.join(ROOT, 'frontend'));
const output = path.join(frontend, `dist-canary-modes-${args[1]}`);
if (!samePath(path.dirname(output), frontend) || path.basename(output) === 'dist' || exists(output)) {
  throw new Error('Build output must be a fresh custom dist; never reuse or touch frontend/dist.');
}
// Remove credentials, VITE_*, NODE_OPTIONS, proxies and inherited app settings
// BEFORE loading Vite/plugins. No dotenv/config loader can inspect the real .env.
const allowed = /^(?:SYSTEMROOT|WINDIR|PATH|PATHEXT|COMSPEC|SYSTEMDRIVE|PROGRAMFILES|PROGRAMFILES\(X86\)|LOCALAPPDATA|APPDATA|USERPROFILE|NUMBER_OF_PROCESSORS|PROCESSOR_ARCHITECTURE)$/i;
for (const name of Object.keys(process.env)) if (!allowed.test(name)) delete process.env[name];
const local = process.env.LOCALAPPDATA ?? process.env.LocalAppData;
if (!local) throw new Error('LOCALAPPDATA required for isolated Windows build TEMP.');
const temporary = fs.mkdtempSync(path.join(unlinked(path.join(local, 'Temp')), 'golden-mic-modes-build-'));
Object.assign(process.env, { TEMP: temporary, TMP: temporary, TMPDIR: temporary, NODE_ENV: 'production',
  PLAYWRIGHT_NO_COPY_PROMPT: '1', PYTHON_DOTENV_DISABLED: '1' });
const envDir = path.join(temporary, 'empty-env');
fs.mkdirSync(envDir);
fs.mkdirSync(output); // Exclusive claim; even a failed output is never reused.
const sources = () => {
  const hashes = {};
  const visit = dir => {
    for (const entry of fs.readdirSync(unlinked(dir), { withFileTypes: true })) {
      const file = unlinked(path.join(dir, entry.name));
      if (entry.isDirectory()) visit(file);
      else if (entry.isFile()) hashes[path.relative(ROOT, file).split(path.sep).join('/')] = hashFile(file);
    }
  };
  visit(path.join(frontend, 'src'));
  for (const file of ['frontend/index.html', 'frontend/package.json', 'frontend/postcss.config.cjs',
    'frontend/tailwind.config.ts', 'backend/mode_rules.json']) hashes[file] = hashFile(path.join(ROOT, file));
  return hashes;
};
const before = sources();
// Existing Tailwind globs are frontend-relative. This changes only this child
// process's cwd; no package script/config or parent terminal is modified.
process.chdir(frontend);
const [{ build }, { default: react }] = await Promise.all([import('vite'), import('@vitejs/plugin-react')]);
await build({ root: frontend, configFile: false, envDir, plugins: [react()], publicDir: false,
  cacheDir: path.join(temporary, 'vite-cache'), build: { outDir: output, emptyOutDir: false } });
const after = sources();
if (JSON.stringify(before) !== JSON.stringify(after)) throw new Error('Frontend sources changed during build; preserve output and use a fresh label.');
const assets = {};
const collect = directory => {
  for (const entry of fs.readdirSync(unlinked(directory), { withFileTypes: true })) {
    const file = unlinked(path.join(directory, entry.name));
    if (entry.isDirectory()) collect(file);
    else if (entry.isFile()) {
      const name = path.relative(output, file).split(path.sep).join('/');
      if (!/^(?:index\.html|assets\/[A-Za-z0-9_./-]+)$/.test(name)) throw new Error('Unexpected custom build entry.');
      assets[name] = hashFile(file);
    }
  }
};
collect(output);
if (!assets['index.html'] || Object.keys(assets).length < 3) throw new Error('Incomplete custom frontend build.');
fs.writeFileSync(path.join(output, 'ASSET_MANIFEST.sha256'), Object.entries(assets).sort(([a], [b]) => a.localeCompare(b))
  .map(([name, hash]) => `${hash}  ${name}\n`).join(''), { flag: 'wx', encoding: 'ascii' });
fs.writeFileSync(path.join(output, 'MODE_BUILD_BINDING.json'), JSON.stringify({ kind: 'synthetic-mode-custom-build',
  sourceHashes: before, sourceUnchangedDuringBuild: true, assets, sharedDistAccessed: false,
  dotenvLoaded: false, viteConfigLoaded: false, existingPostcssTailwindConfigUsed: true, publicDirCopied: false }, null, 2), { flag: 'wx', encoding: 'utf8' });
console.log(JSON.stringify({ status: 'custom-build-complete', frontendDir: output, temporaryRoot: temporary,
  sources: Object.keys(before).length, assets: Object.keys(assets).length, sharedDistAccessed: false,
  manifestSHA256: createHash('sha256').update(fs.readFileSync(path.join(output, 'ASSET_MANIFEST.sha256'))).digest('hex') })
  .replace(/[\u007f-\uffff]/g, c => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`));