import { request } from '@playwright/test';
import { bytesHash, joinPath, makeDirectory, writeJSON } from '../modes/io.mjs';
import { BASE_URL, bound, evidenceRoot, invariant, loadManifest, settings } from './environment';
export default async function setup() {
  const run = settings(), m = loadManifest(run.file);
  invariant(bound(m), 'source/build/input drift; parent must settle source and restart fresh');
  const api = await request.newContext({ baseURL: BASE_URL, timeout: 30_000 });
  try {
    const response = await api.get('/api/test/v2', { maxRedirects: 0 });
    invariant(response.status() === 200 && response.headers()['x-v2-acceptance'] === m.instanceId, 'live instance');
    const identity = await response.json();
    invariant(identity.instanceId === m.instanceId && identity.synthetic && identity.serverState === 'ready' && identity.deniedEgress === 0, 'isolated host');
    await response.dispose();
    const ready = await api.get('/health/ready', { maxRedirects: 0 });
    invariant(ready.status() === 200, 'real readiness'); await ready.dispose();
    for (const [name, hash] of Object.entries(m.frontend.hashes)) {
      const asset = await api.get(name === 'index.html' ? '/' : '/' + name, { maxRedirects: 0 });
      invariant(asset.status() === 200 && asset.headers()['x-v2-acceptance'] === m.instanceId && bytesHash(await asset.body()) === hash, 'served build binding');
      await asset.dispose();
    }
    const output = evidenceRoot(m, run.label); makeDirectory(output);
    writeJSON(joinPath(output, 'binding.json'), { host: m.instanceId, sourceHashes: m.sourceHashes,
      testHashes: m.testHashes, frontend: m.frontend, synthetic: true, speechEvidence: false, quotaPolicy: m.quotaPolicy });
  } finally { await api.dispose(); }
}