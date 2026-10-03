import { defineConfig } from '@playwright/test';
import { env, exists, joinPath, makeDirectory, newInstance, parentPath, readText, writeJSON } from './e2e/modes/io.mjs';
import { BASE_URL, evidenceRoot, invariant, loadManifest, settings } from './e2e/v2/environment';
env.PLAYWRIGHT_NO_COPY_PROMPT = '1';
invariant(env.V2_DESIGN_ONLY === undefined || env.V2_DESIGN_ONLY === '1', 'V2_DESIGN_ONLY must be absent or 1');
invariant(!(env.V2_DESIGN_ONLY === '1' && env.V2_NAV_ONLY === '1'), 'select exactly one explicit suite');
const run = settings(), manifest = loadManifest(run.file), output = evidenceRoot(manifest, run.label), claim = output + '.claim.json';
if (env.TEST_WORKER_INDEX === undefined) {
  invariant(!exists(output) && !exists(claim), 'label already used; preserve failed evidence and choose a new label');
  makeDirectory(parentPath(output));
  env.V2_RUN_INSTANCE = newInstance();
  writeJSON(claim, { run: env.V2_RUN_INSTANCE, host: manifest.instanceId });
} else {
  const owner = JSON.parse(readText(claim)) as { run: string; host: string };
  invariant(env.V2_RUN_INSTANCE && owner.run === env.V2_RUN_INSTANCE && owner.host === manifest.instanceId, 'worker claim ownership');
}
export default defineConfig<{ v2NoRawArtifacts: boolean }>({
  // Explicit opt-in keeps the six product acceptance cases/report contract intact.
  testDir: './e2e/v2', testMatch: env.V2_DESIGN_ONLY === '1' ? '**/design.v2.spec.ts'
    : env.V2_NAV_ONLY === '1' ? '**/navigation.v2.spec.ts' : '**/acceptance.v2.spec.ts', tsconfig: './e2e/v2/tsconfig.json',
  workers: 1, retries: 0, fullyParallel: false, forbidOnly: true, maxFailures: 1,
  timeout: 45 * 60_000, globalTimeout: 45 * 60_000, expect: { timeout: 20_000 },
  outputDir: joinPath(output, 'test-results'), preserveOutput: 'always',
  captureGitInfo: { commit: false, diff: false }, globalSetup: './e2e/v2/global-setup.ts',
  reporter: [['./e2e/v2/reporter.ts']],
  use: { v2NoRawArtifacts: true, baseURL: BASE_URL, browserName: 'chromium', channel: 'msedge',
    headless: true, locale: 'zh-CN', viewport: { width: 1280, height: 900 },
    serviceWorkers: 'block', acceptDownloads: false, screenshot: 'off', trace: 'off', video: 'off',
    actionTimeout: 20_000, navigationTimeout: 30_000,
    launchOptions: { args: ['--disable-background-networking', '--disable-component-update', '--disable-domain-reliability', '--disable-sync', '--no-first-run', '--no-proxy-server'] } },
  projects: [{ name: 'v2-msedge' }],
});