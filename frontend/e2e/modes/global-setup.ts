import { request } from '@playwright/test';
import { bytesHash, hashFile, joinPath, makeDirectory, ROOT, writeJSON } from './io.mjs';
import { evidenceRoot, invariant, loadManifest, runSettings } from './environment';

export default async function setup() {
  const run = runSettings(), manifest = loadManifest(run.manifestPath);
  const api = await request.newContext({ baseURL: run.baseURL, timeout: 30_000, ignoreHTTPSErrors: false });
  try {
    const identify = await api.get('/api/test/modes', { maxRedirects: 0 });
    invariant(identify.status() === 200 && identify.headers()['x-modes-acceptance'] === manifest.instanceId, 'live host identity');
    const body = await identify.json() as { instanceId: string; serverState: string; synthetic: boolean; deniedEgress: number };
    invariant(body.instanceId === manifest.instanceId && body.synthetic === true && body.serverState === 'ready'
      && body.deniedEgress === 0, 'live isolated state');
    const ready = await api.get('/health/ready', { maxRedirects: 0 });
    invariant(ready.status() === 200, 'actual readiness required, no patched readiness');
    invariant(Object.keys(manifest.frontend.hashes).length >= 3 && manifest.frontend.hashes['index.html'], 'complete custom build');
    for (const [name, hash] of Object.entries(manifest.frontend.hashes)) {
      invariant(hashFile(joinPath(manifest.frontend.directory, name)) === hash, 'build drift');
      const response = await api.get(name === 'index.html' ? '/' : '/' + name, { maxRedirects: 0 });
      invariant(response.status() === 200 && response.headers()['x-modes-acceptance'] === manifest.instanceId
        && bytesHash(await response.body()) === hash, 'served build differs from selected custom assets');
      await response.dispose();
    }
    for (const input of manifest.inputs) invariant(hashFile(input.path) === input.sha256, 'fixture file drift');
    for (const artifact of manifest.sourceArtifacts) invariant(hashFile(artifact.path) === artifact.sha256, 'ASR label artifact drift');
    for (const [name, hash] of Object.entries(manifest.sourceHashes)) invariant(hashFile(joinPath(ROOT, name)) === hash,
      'product source changed after host import; stop this host and build/start a fresh settled version');
    for (const [name, hash] of Object.entries(manifest.frontend.sourceHashes)) invariant(hashFile(joinPath(ROOT, name)) === hash,
      'frontend source changed after the custom build; a fresh settled build is required');
    for (const [name, hash] of Object.entries(manifest.testHashes)) invariant(hashFile(joinPath(ROOT, name)) === hash,
      'acceptance source changed since host startup');
    const output = evidenceRoot(manifest, run.label);
    makeDirectory(output);
    writeJSON(joinPath(output, 'binding.json'), { kind: 'synthetic-mode-acceptance-binding', host: manifest.instanceId,
      sourceHashes: manifest.sourceHashes, testHashes: manifest.testHashes, frontend: manifest.frontend,
      inputHashes: manifest.inputs.map(i => ({ name: i.name, sha256: i.sha256 })),
      asrLabelsAreSynthetic: true, speechEvidence: false, retries: 0, sharedDistRead: false });
  } finally { await api.dispose(); }
}