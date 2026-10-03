import { defineConfig } from '@playwright/test';
import { env, exists, joinPath, makeDirectory, newInstance, parentPath, readText, writeJSON } from './e2e/workspace/io.mjs';
import { loadManifest, runSettings, unlinked } from './e2e/workspace/environment';

// No webServer/global launcher: the parent builds and explicitly starts the host.
// No dependency on the retired canary/account harness or package scripts.
env.PLAYWRIGHT_NO_COPY_PROMPT = '1';
const run = runSettings();
const manifest = loadManifest(run.manifestPath, run.baseURL);
const claim = `${run.outputRoot}.claim.json`;
const worker = env.TEST_WORKER_INDEX !== undefined;

if (!worker) {
  // Playwright clears outputDir BEFORE globalSetup. Refuse reuse here, not there.
  if (exists(run.outputRoot) || exists(claim)) throw new Error('Workspace run label already consumed; choose a new label.');
  makeDirectory(parentPath(run.outputRoot));
  unlinked(parentPath(run.outputRoot));
  env.WORKSPACE_RUN_INSTANCE = newInstance();
  writeJSON(claim, { run: env.WORKSPACE_RUN_INSTANCE, host: manifest.instanceId });
} else {
  // Replacement workers reload this config. They may use ONLY their own claim.
  const owner = JSON.parse(readText(unlinked(claim))) as { run?: string; host?: string };
  if (!env.WORKSPACE_RUN_INSTANCE || owner.run !== env.WORKSPACE_RUN_INSTANCE || owner.host !== manifest.instanceId) {
    throw new Error('Workspace worker does not own this evidence label.');
  }
}

export default defineConfig({
  testDir: './e2e/workspace', testMatch: '**/*.workspace.spec.ts',
  tsconfig: './e2e/workspace/tsconfig.json',
  fullyParallel: false, workers: 1, retries: 0, forbidOnly: true,
  timeout: 90_000, globalTimeout: 25 * 60_000, expect: { timeout: 15_000 },
  outputDir: joinPath(run.outputRoot, 'test-results'),
  captureGitInfo: { commit: false, diff: false },
  preserveOutput: 'always',
  globalSetup: './e2e/workspace/global-setup.ts',
  reporter: [['./e2e/workspace/reporter.ts']],
  use: {
    baseURL: run.baseURL, browserName: 'chromium', channel: 'msedge',
    headless: true, viewport: { width: 1440, height: 960 }, locale: 'zh-CN',
    serviceWorkers: 'block', acceptDownloads: true,
    trace: 'off', video: 'off', screenshot: 'off',
    actionTimeout: 15_000, navigationTimeout: 30_000,
    launchOptions: { args: ['--disable-background-networking', '--disable-component-update',
      '--disable-domain-reliability', '--disable-sync', '--no-first-run'] },
  },
  projects: [{ name: 'workspace-msedge' }],
});