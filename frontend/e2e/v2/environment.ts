import { absolute, basename, env, hashFile, inside, joinPath, parentPath, readText, ROOT, samePath, size, tempRoot, unlinked } from '../modes/io.mjs';
import type { InputFile } from '../modes/environment';
export const BASE_URL = 'http://127.0.0.1:8787';
export const DRAFT_KEY = 'gm-modes-v1';
export interface Manifest {
  schemaVersion: number; kind: string; instanceId: string; synthetic: boolean; baseURL: string;
  serverState: string; root: string; taskRoot: string; disclosure: string;
  frontend: { directory: string; hashes: Record<string, string>; sourceHashes: Record<string, string> };
  sourceHashes: Record<string, string>; testHashes: Record<string, string>;
  tools: { ffmpeg: string; ffprobe: string }; inputs: InputFile[];
  scripts: { original: string; mixed: string; voiceover: string; shortMixed: string };
  policy: Record<string, unknown>; quotaPolicy: { session: number; ip: number; global: number; owner: string; quotaAcceptanceClaimed: boolean };
}
export function invariant(value: unknown, code: string): asserts value {
  if (!value) throw new Error(`V2 acceptance contract: ${code}`);
}
const configName = /^(?:(?:vite|postcss|tailwind)\.config\.(?:[cm]?[jt]s)|tsconfig(?:\.[A-Za-z0-9_-]+)*\.json)$/;
const sourceFiles = ['frontend/index.html', 'frontend/package.json', 'frontend/package-lock.json',
  'frontend/vite.config.ts', 'frontend/postcss.config.cjs', 'frontend/tailwind.config.ts',
  'frontend/tsconfig.json', 'frontend/tsconfig.node.json', 'backend/mode_rules.json'];
const canonicalName = (name: string) => !/[\\\x00-\x1f\x7f<>:"|?*]/.test(name)
  && name.split('/').every(part => part && part !== '.' && part !== '..' && !/[. ]$/.test(part));
const buildSourceName = (name: string) => canonicalName(name) && (sourceFiles.includes(name)
  || name.startsWith('frontend/src/') || (name.split('/').length === 2
    && name.startsWith('frontend/') && configName.test(name.slice('frontend/'.length))));
function hashMap(value: unknown, allowed: (name: string) => boolean): asserts value is Record<string, string> {
  invariant(value && typeof value === 'object' && !Array.isArray(value), 'hash map object');
  const entries = Object.entries(value);
  invariant(entries.length > 0 && new Set(entries.map(([name]) => name.toLowerCase())).size === entries.length, 'nonempty unaliased hash map');
  for (const [name, digest] of entries) invariant(canonicalName(name) && allowed(name)
    && typeof digest === 'string' && /^[a-f0-9]{64}$/.test(digest), 'safe hash entry');
}
const assetName = (name: string) => /^(?:index\.html|assets\/[A-Za-z0-9_./-]+)$/.test(name);
const sameMap = (a: Record<string, string>, b: Record<string, string>) => Object.keys(a).length === Object.keys(b).length
  && Object.entries(a).every(([name, digest]) => Object.hasOwn(b, name) && b[name] === digest);
function buildBinding(m: Manifest): void {
  const receiptPath = joinPath(m.frontend.directory, 'MODE_BUILD_BINDING.json');
  invariant(size(unlinked(receiptPath)) <= 1024 ** 2, 'bounded build receipt');
  const receipt = JSON.parse(readText(receiptPath)) as Record<string, unknown>;
  invariant(receipt && ['synthetic-mode-custom-build', 'frontend-source-build'].includes(String(receipt.kind))
    && receipt.sourceUnchangedDuringBuild === true, 'source-bound build receipt');
  hashMap(receipt.sourceHashes, buildSourceName);
  hashMap(receipt.sourceHashesAfter, buildSourceName);
  hashMap(receipt.assets, assetName);
  hashMap(m.frontend.sourceHashes, buildSourceName);
  hashMap(m.frontend.hashes, assetName);
  invariant(sourceFiles.every(name => Object.hasOwn(receipt.sourceHashes as object, name))
    && Object.keys(m.sourceHashes).filter(name => name.startsWith('frontend/src/'))
      .every(name => Object.hasOwn(receipt.sourceHashes as object, name)), 'complete v2 build inputs');
  invariant(sameMap(receipt.sourceHashes, receipt.sourceHashesAfter)
    && sameMap(receipt.sourceHashes, m.frontend.sourceHashes) && sameMap(receipt.assets, m.frontend.hashes), 'build before/after/host binding');
  invariant(Object.hasOwn(receipt.assets, 'index.html') && Object.keys(receipt.assets).some(name => name.startsWith('assets/')), 'complete build assets');
  const manifestPath = joinPath(m.frontend.directory, 'ASSET_MANIFEST.sha256');
  invariant(size(unlinked(manifestPath)) <= 1024 ** 2 && receipt.assetManifestSHA256 === hashFile(manifestPath), 'asset manifest SHA256 binding');
  const entries = readText(manifestPath).trimEnd().split(/\r?\n/).map(line => {
    invariant(/^[a-f0-9]{64}  .+$/.test(line), 'asset manifest line');
    return [line.slice(66), line.slice(0, 64)];
  });
  const manifestAssets = Object.fromEntries(entries) as Record<string, string>;
  hashMap(manifestAssets, assetName);
  invariant(entries.length === Object.keys(manifestAssets).length && sameMap(manifestAssets, receipt.assets), 'asset manifest inventory');
  if (receipt.kind === 'synthetic-mode-custom-build') {
    const css = receipt.cssEvidence as { engine?: unknown } | undefined;
    const helpers = ['frontend/e2e/v2/build.mjs', 'frontend/e2e/modes/io.mjs'];
    if (css?.engine === 'lightningcss') helpers.push('frontend/e2e/v2/lightning-css.mjs');
    hashMap(receipt.helperHashes, name => helpers.includes(name));
    invariant(Object.keys(receipt.helperHashes).length === helpers.length, 'complete build helpers');
    for (const [name, digest] of Object.entries(receipt.helperHashes)) invariant(
      m.testHashes[name] === digest && hashFile(joinPath(ROOT, name)) === digest, 'current build helper');
  } else invariant(receipt.schemaVersion === 1, 'standard build schema');
  for (const [name, digest] of Object.entries(receipt.sourceHashes)) invariant(hashFile(joinPath(ROOT, name)) === digest, 'current build input');
  for (const [name, digest] of Object.entries(receipt.assets)) invariant(hashFile(joinPath(m.frontend.directory, name)) === digest, 'current build asset');
}
export function settings() {
  const label = env.V2_RUN_LABEL, file = env.V2_MANIFEST;
  invariant(label && /^v2-[a-z0-9][a-z0-9-]{0,55}$/.test(label), 'fresh v2 label required');
  invariant(file && absolute(file), 'absolute V2_MANIFEST required');
  invariant(!env.V2_BASE_URL || env.V2_BASE_URL === BASE_URL, 'exact dedicated origin');
  return { label, file };
}
export function loadManifest(file: string): Manifest {
  invariant(inside(tempRoot(), file) && !inside(ROOT, file) && size(unlinked(file)) < 4 * 1024 ** 2, 'external bounded manifest');
  const m = JSON.parse(readText(file)) as Manifest;
  invariant(m.schemaVersion === 1 && m.kind === 'golden-mic-v2-acceptance' && m.synthetic === true
    && m.baseURL === BASE_URL && m.serverState === 'ready' && /^[a-f0-9]{32}$/.test(m.instanceId), 'ready v2 instance');
  invariant(absolute(m.root) && inside(tempRoot(), m.root) && !inside(ROOT, m.root)
    && basename(m.root).startsWith('golden-mic-v2-') && samePath(m.taskRoot, joinPath(m.root, 'tasks')), 'owned TEMP root');
  unlinked(m.root);
  invariant(samePath(parentPath(m.frontend.directory), joinPath(ROOT, 'frontend'))
    && /^dist-canary-modes-v2-[a-z0-9-]+$/.test(basename(m.frontend.directory)), 'new v2 custom dist');
  invariant(m.policy.dotenv === false && m.policy.realData === false && m.policy.paidCalls === false
    && m.policy.seededTasks === false && m.policy.qcPatched === false && m.policy.rawArtifacts === false, 'isolation policy');
  invariant(m.quotaPolicy.session === 2 && m.quotaPolicy.ip === 30 && m.quotaPolicy.global === 30
    && !m.quotaPolicy.quotaAcceptanceClaimed, 'explicit TEST-only quota settings');
  invariant(m.inputs.length === 7 && new Set(m.inputs.map(i => i.name)).size === 7, 'seven available fixtures, NOT every case uploads seven');
  for (const i of m.inputs) invariant(i.synthetic && /^synthetic-[a-z0-9-]+\.mp4$/.test(i.name)
    && samePath(i.path, joinPath(m.root, 'inputs', i.name)) && i.bytes > 0 && i.bytes <= 16 * 1024 ** 2
    && /^[a-f0-9]{64}$/.test(i.sha256), 'synthetic input binding');
  for (const group of [m.sourceHashes, m.testHashes, m.frontend.sourceHashes]) {
    hashMap(group, name => buildSourceName(name)
      || name === 'frontend/scripts/test-v2-probe-guard.mjs'
      || /^(?:backend\/[A-Za-z0-9_./-]+\.(?:py|json)|frontend\/(?:e2e\/(?:v2|modes)\/[A-Za-z0-9_./-]+|playwright\.v2\.config\.ts)|tests\/(?:v2_acceptance_server|mode_acceptance_server|workspace_fixture)\.py)$/.test(name));
  }
  invariant(m.sourceHashes['backend/v2_editing.py'] && m.sourceHashes['backend/drafts.py']
    && m.frontend.sourceHashes['frontend/src/components/CreateWizard.tsx']
    && m.frontend.sourceHashes['frontend/src/components/ResultWorkbench.tsx'], 'v2 sources bound');
  for (const [name, hash] of Object.entries(m.frontend.hashes)) invariant(
    /^(?:index\.html|assets\/[A-Za-z0-9_./-]+)$/.test(name) && !name.split('/').includes('..') && /^[a-f0-9]{64}$/.test(hash), 'asset entry');
  buildBinding(m);
  return m;
}
export function bound(m: Manifest): boolean {
  try { buildBinding(m); } catch { return false; }
  return [m.sourceHashes, m.testHashes, m.frontend.sourceHashes].every(group =>
    Object.entries(group).every(([name, hash]) => hashFile(joinPath(ROOT, name)) === hash))
    && Object.entries(m.frontend.hashes).every(([name, hash]) => hashFile(joinPath(m.frontend.directory, name)) === hash)
    && m.inputs.every(i => hashFile(i.path) === i.sha256);
}
export const evidenceRoot = (m: Manifest, label: string) => joinPath(m.root, 'browser-runs', label);
export function checkedURL(value: string): URL {
  invariant(value.startsWith('/') && !value.startsWith('//') && !/[\\\r\n]/.test(value), 'local relative URL');
  const url = new URL(value, BASE_URL);
  invariant(url.origin === BASE_URL && !url.username && !url.password, 'exact origin');
  return url;
}