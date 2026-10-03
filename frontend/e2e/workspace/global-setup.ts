import { request } from '@playwright/test';
import { bytesHash, joinPath, resolvePath } from './io.mjs';
import { hashFile, inside, invariant, loadManifest, runSettings, unlinked } from './environment';

export default async function setup() {
  const run = runSettings(); const manifest = loadManifest(run.manifestPath, run.baseURL);
  const api = await request.newContext({ baseURL: run.baseURL, timeout: 20_000, ignoreHTTPSErrors: false });
  try {
    const identity = await api.get('/api/test/workspace', { maxRedirects: 0 });
    invariant(identity.status() === 200 && identity.headers()['x-workspace-acceptance'] === manifest.instanceId, 'live host identity');
    const body = await identity.json() as { instanceId: string; synthetic: boolean; serverState: string; seedTaskId: string; deniedEgress: number };
    invariant(body.instanceId === manifest.instanceId && body.synthetic && body.serverState === 'ready'
      && body.seedTaskId === manifest.seed.taskId && body.deniedEgress === 0, 'live synthetic state');
    const ready = await api.get('/health/ready', { maxRedirects: 0 });
    invariant(ready.status() === 200, 'actual preflight must pass; never fake readiness');
    const assets = Object.entries(manifest.frontend.hashes);
    invariant(assets.length >= 3 && assets.some(([name]) => name === 'index.html'), 'complete built assets');
    for (const [name, expected] of assets) {
      invariant(/^(?:index\.html|assets\/[a-zA-Z0-9_.\/-]+)$/.test(name)
        && !name.split('/').includes('..') && /^[a-f0-9]{64}$/.test(expected), 'unsafe build manifest entry');
      const local = joinPath(manifest.frontend.directory, name);
      invariant(hashFile(local) === expected, 'build changed since host startup');
      const response = await api.get(name === 'index.html' ? '/' : `/${name}`, { maxRedirects: 0 });
      invariant(response.status() === 200 && bytesHash(await response.body()) === expected,
        'served bytes must match the selected build');
    }
    for (const item of manifest.inputs) invariant(hashFile(item.path) === item.sha256, 'synthetic input drift');
    for (const [name, digest] of Object.entries(manifest.seed.snapshotHashes)) {
      const file = resolvePath(manifest.seed.snapshotDirectory, name);
      invariant(inside(manifest.seed.snapshotDirectory, file) && hashFile(unlinked(file)) === digest, 'original synthetic snapshot drift');
    }
  } finally { await api.dispose(); }
}