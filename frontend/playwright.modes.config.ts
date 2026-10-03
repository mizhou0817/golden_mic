import { defineConfig } from '@playwright/test';
import { env, exists, joinPath, makeDirectory, newInstance, parentPath, readText, unlinked, writeJSON } from './e2e/modes/io.mjs';
import { evidenceRoot, loadManifest, runSettings } from './e2e/modes/environment';

// Parent owns the build/server. No webServer, auto-start, package or old-config changes.
env.PLAYWRIGHT_NO_COPY_PROMPT = '1';
const run = runSettings(), manifest = loadManifest(run.manifestPath);
const output = evidenceRoot(manifest, run.label), claim = output + '.claim.json';
if (env.TEST_WORKER_INDEX === undefined) {
  // Playwright 1.63 removes outputDir BEFORE globalSetup. Claim here, never there.
  if (exists(output) || exists(claim)) throw new Error('Modes evidence label already used. Choose a new label; failed evidence is retained.');
  makeDirectory(parentPath(output));
  env.MODES_RUN_INSTANCE = newInstance();
  writeJSON(claim, { run: env.MODES_RUN_INSTANCE, host: manifest.instanceId });
} else {
  // Replacement workers may reload their OWN claim; they must not reject it.
  const owner = JSON.parse(readText(unlinked(claim))) as { run?: string; host?: string };
  if (!env.MODES_RUN_INSTANCE || owner.run !== env.MODES_RUN_INSTANCE || owner.host !== manifest.instanceId) {
    throw new Error('Modes worker does not own its evidence label.');
  }
}

export default defineConfig<{ modesNoRawArtifacts: boolean }>({
  testDir: './e2e/modes', testMatch: '**/*.modes.spec.ts', tsconfig: './e2e/modes/tsconfig.json',
  fullyParallel: false, workers: 1, retries: 0, forbidOnly: true, maxFailures: 1,
  timeout: 180_000, globalTimeout: 40 * 60_000, expect: { timeout: 20_000 },
  outputDir: joinPath(output, 'test-results'), preserveOutput: 'always',
  captureGitInfo: { commit: false, diff: false },
  globalSetup: './e2e/modes/global-setup.ts', reporter: [['./e2e/modes/reporter.ts']],
  use: {
    // Enforced by this suite's _setupArtifacts override: NO_COPY_PROMPT alone
    // does not suppress Playwright 1.63's error-context file writer.
    modesNoRawArtifacts: true,
    baseURL: run.baseURL, browserName: 'chromium', channel: 'msedge', headless: true,
    viewport: { width: 1440, height: 960 }, locale: 'zh-CN', serviceWorkers: 'block', acceptDownloads: true,
    screenshot: 'off', trace: 'off', video: 'off', actionTimeout: 20_000, navigationTimeout: 30_000,
    launchOptions: { args: ['--disable-background-networking', '--disable-component-update',
      '--disable-domain-reliability', '--disable-sync', '--no-first-run', '--no-proxy-server'] },
  },
  projects: [{ name: 'modes-msedge' }],
});