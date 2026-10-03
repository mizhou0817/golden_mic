import { test as base, expect, request as playwrightRequest } from '@playwright/test';
import type { APIRequestContext, APIResponse, BrowserContext, Locator, Page, Response, Route, TestInfo } from '@playwright/test';
import { bytesHash, joinPath, readBytes, size, writeJSON } from './io.mjs';
import { checkedURL, hashFile, invariant, loadManifest, runSettings } from './environment';
import type { WorkspaceManifest } from './environment';

export { expect };
export interface Receipt { task_id: string; access_token: string; revision?: number; status?: string }
export interface Task {
  task_id: string; status: string; progress: number; revision: number;
  processing_started_at: string | null; processing_completed_at: string | null;
  total_elapsed_seconds: number | null; error_message: string | null;
  stages: { number: number; status: string; elapsed_seconds: number | null }[];
}
export interface Report {
  task_id: string; rows: { sentence_id: number; sentence: string; duration: number; shot_id: number }[];
  quality: { blocking_issue_count: number; warning_count: number; issues: { severity: string; code: string }[] };
}
export interface ProjectEnvelope {
  revision: number;
  project: { name: string; tracks: { id: string; type: string; name: string;
    clips: { id: string; source_id: string | null; trim: number; duration: number; start: number }[] }[] };
}
export interface Job {
  id: string; state: string; output_id: string | null; revision: number; pipeline_revision: number;
  result?: { bytes: number; duration: number }; cleanup_pending?: boolean;
}
type FetchOptions = NonNullable<Parameters<APIRequestContext['fetch']>[1]>;
const MUTATIONS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const ACCOUNT_PATH = /\/(?:classroom|accounts?|login|student-login|teacher)(?:\/|$)/i;
const confirming = new WeakSet<Page>();

export class WorkspaceRun {
  readonly owned = new Set<string>();
  readonly knownJobs = new Map<string, Set<string>>();
  readonly observations: { method: string; path: string }[] = [];
  private pageErrors = 0;
  private boundaryFailures = 0;
  private unexpectedDialogs = 0;
  private tasksPostBudget = 0;
  private responses = new Set<Promise<void>>();
  constructor(readonly manifest: WorkspaceManifest, readonly api: APIRequestContext, readonly info: TestInfo) {}

  allowOneGeneration() { invariant(this.tasksPostBudget === 0, 'one generation admission at a time'); this.tasksPostBudget = 1; }
  authorize(method: string, pathname: string): boolean {
    if (ACCOUNT_PATH.test(pathname)) return false;
    if (!MUTATIONS.has(method)) return ['GET', 'HEAD', 'OPTIONS'].includes(method);
    if (method === 'POST' && pathname === '/api/tasks') {
      if (!this.tasksPostBudget) return false;
      this.tasksPostBudget--; return true;
    }
    if (pathname === `/api/tasks/${this.manifest.seed.taskId}/duplicate` && method === 'POST') return true;
    const task = /^\/api\/tasks\/([a-f0-9]{32})(\/.*)?$/.exec(pathname);
    return !!task && this.owned.has(task[1]);
  }
  async fetch(pathname: string, options: FetchOptions = {}): Promise<APIResponse> {
    const url = checkedURL(this.manifest.baseURL, pathname);
    const method = (options.method ?? 'GET').toUpperCase();
    invariant(this.authorize(method, new URL(url).pathname), 'API write is not owned or explicitly admitted');
    const response = await this.api.fetch(url, { ...options, method, maxRedirects: 0,
      headers: { ...(MUTATIONS.has(method) ? { Origin: this.manifest.baseURL } : {}), ...options.headers } });
    invariant(response.headers()['x-workspace-acceptance'] === this.manifest.instanceId, 'response came from a different acceptance instance');
    return response;
  }
  async json<T>(pathname: string, options: FetchOptions = {}, status = 200): Promise<T> {
    const response = await this.fetch(pathname, options);
    invariant(response.status() === status, `HTTP ${response.status()} instead of ${status}`);
    return await response.json() as T;
  }
  receipt(value: unknown): Receipt {
    const receipt = value as Receipt;
    invariant(receipt && /^[a-f0-9]{32}$/.test(receipt.task_id) && receipt.task_id !== this.manifest.seed.taskId,
      'invalid/new task identifier');
    // Add ownership before checking the capability so a malformed receipt does
    // not leave a known accepted synthetic task without scoped cleanup.
    this.owned.add(receipt.task_id);
    invariant(typeof receipt.access_token === 'string' && receipt.access_token.length >= 32,
      'creation/copy must return a nonempty capability immediately');
    return receipt; // Never logged, attached, included in assertion values or printed.
  }
  async duplicate(): Promise<string> {
    const data = await this.json<unknown>(`/api/tasks/${this.manifest.seed.taskId}/duplicate`,
      { method: 'POST', data: { expected_revision: 0 } }, 201);
    return this.receipt(data).task_id;
  }
  async status(id: string): Promise<Task> { return this.json(`/api/tasks/${id}`); }
  async completed(id: string, revision = 0): Promise<Task> {
    let result: Task | undefined;
    await expect.poll(async () => {
      result = await this.status(id);
      invariant(!['failed', 'cancelled'].includes(result.status), 'task failed; see owned TEMP artifacts, no raw response dumped');
      invariant(result.task_id === id && !(result.status === 'done' && result.error_message), 'task identity or edit outcome failed');
      return result.status === 'done' && result.revision === revision && !result.error_message;
    }, { timeout: 600_000, intervals: [100, 300, 1000, 1500], message: 'Real synthetic task must finish at the expected revision' }).toBe(true);
    return result!;
  }
  async job(id: string, jobId: string): Promise<Job> {
    const jobs = this.knownJobs.get(id) ?? new Set<string>(); jobs.add(jobId); this.knownJobs.set(id, jobs);
    let result: Job | undefined;
    await expect.poll(async () => {
      result = await this.json<Job>(`/api/tasks/${id}/studio/jobs/${jobId}`);
      invariant(!['failed', 'cancelled', 'interrupted'].includes(result.state), 'real Studio job failed');
      return result.state;
    }, { timeout: 330_000, intervals: [200, 500, 1500] }).toBe('succeeded');
    invariant(result?.output_id && result.result && result.result.bytes > 0 && !result.cleanup_pending, 'validated Studio output required');
    return result;
  }
  async guard(context: BrowserContext) {
    await context.route('**/*', async (route: Route) => {
      const request = route.request(); const url = new URL(request.url());
      const pathName = url.pathname;
      const local = url.origin === this.manifest.baseURL && !url.username && !url.password;
      const headers = request.headers();
      const taskCapability = pathName.startsWith('/api/tasks/') && (url.searchParams.has('token')
        || 'x-task-token' in headers || 'authorization' in headers || 'x-classroom-csrf' in headers);
      if (!local || taskCapability || !this.authorize(request.method(), pathName)) {
        this.boundaryFailures++; await route.abort('blockedbyclient'); return;
      }
      // Never retain query parameters/headers/body (media URLs can hold tokens).
      this.observations.push({ method: request.method(), path: pathName });
      await route.continue();
    });
    await context.routeWebSocket(/.*/, socket => { this.boundaryFailures++; socket.close(); });
    const observe = (page: Page) => {
      page.on('pageerror', () => { this.pageErrors++; });
      // Only individual test actions may accept a confirmation. New dialogs
      // are dismissed instead of auto-accepting arbitrary writes.
      page.on('dialog', dialog => {
        if (dialog.type() === 'beforeunload') void dialog.accept();
        else if (!confirming.has(page)) { this.unexpectedDialogs++; void dialog.dismiss(); }
      });
      page.on('response', response => {
        const url = new URL(response.url());
        if (url.origin !== this.manifest.baseURL || response.request().method() !== 'POST') return;
        const creation = url.pathname === '/api/tasks' || url.pathname === `/api/tasks/${this.manifest.seed.taskId}/duplicate`;
        const studio = /^\/api\/tasks\/([a-f0-9]{32})\/studio\/(?:render|export)$/.exec(url.pathname);
        if (!creation && !studio) return;
        const observation = (async () => {
          try {
            if (creation && [201, 202].includes(response.status())) this.receipt(await response.json());
            if (studio && response.status() === 202) {
              const job = await response.json() as Job;
              const jobs = this.knownJobs.get(studio[1]) ?? new Set<string>(); jobs.add(job.id); this.knownJobs.set(studio[1], jobs);
            }
          } catch { this.boundaryFailures++; }
        })();
        this.responses.add(observation); void observation.finally(() => this.responses.delete(observation));
      });
    };
    context.pages().forEach(observe); context.on('page', observe);
  }
  async evidence(name: string, value: unknown) {
    invariant(/^[a-z0-9-]+$/.test(name), 'safe evidence identifier');
    // Callers provide projections of geometry/counts/hashes, never raw data.
    writeJSON(this.info.outputPath(`${name}.json`), value);
  }
  async finish() {
    await Promise.all([...this.responses]);
    const errors: string[] = [];
    for (const id of this.owned) {
      try {
        for (const jobId of this.knownJobs.get(id) ?? []) {
          const job = await this.json<Job>(`/api/tasks/${id}/studio/jobs/${jobId}`);
          if (['queued', 'running'].includes(job.state)) await this.json(`/api/tasks/${id}/studio/jobs/${jobId}`, { method: 'DELETE' });
        }
        const deleted = await this.fetch(`/api/tasks/${id}`, { method: 'DELETE' });
        invariant(deleted.status() === 204, 'owned task cleanup not confirmed');
        invariant((await this.fetch(`/api/tasks/${id}`)).status() === 404, 'owned task survived deletion');
      } catch { errors.push('owned-task-cleanup-unconfirmed'); }
    }
    const identity = await this.json<{ deniedEgress: number; providerCalls: Record<string, number> }>('/api/test/workspace');
    for (const [name, digest] of Object.entries(this.manifest.seed.artifactHashes)) {
      invariant(hashFile(joinPath(this.manifest.taskRoot, this.manifest.seed.taskId, name)) === digest, 'original seed artifact changed');
    }
    await this.evidence('safety', { syntheticOnly: true, ownedTaskCount: this.owned.size,
      cleanupErrors: errors, pageErrors: this.pageErrors, blockedRequests: this.boundaryFailures,
      unexpectedDialogs: this.unexpectedDialogs, deniedProviderEgress: identity.deniedEgress,
      unexpectedProviderCalls: identity.providerCalls.unexpected ?? 0, requests: this.observations,
      immutableSeedArtifactHashesMatched: true, rawTracesOrTokensRecorded: false });
    invariant(errors.length === 0 && this.pageErrors === 0 && this.boundaryFailures === 0
      && this.unexpectedDialogs === 0 && identity.deniedEgress === 0 && !(identity.providerCalls.unexpected),
    'acceptance safety/cleanup check failed; only projected evidence retained');
  }
}

export const test = base.extend<{ workspace: WorkspaceRun }>({
  workspace: async ({}, use, info) => {
    const run = runSettings(); const manifest = loadManifest(run.manifestPath, run.baseURL);
    const api = await playwrightRequest.newContext({ baseURL: run.baseURL, timeout: 60_000 });
    const workspace = new WorkspaceRun(manifest, api, info);
    try {
      const identity = await workspace.json<{ instanceId: string; synthetic: boolean; serverState: string }>('/api/test/workspace');
      invariant(identity.instanceId === manifest.instanceId && identity.synthetic && identity.serverState === 'ready', 'host identity changed');
      await use(workspace);
    } finally { try { await workspace.finish(); } finally { await api.dispose(); } }
  },
  context: async ({ browser, workspace }, use) => {
    // Fresh context per test; never an existing user profile, cookies or account.
    const context = await browser.newContext({ baseURL: workspace.manifest.baseURL,
      locale: 'zh-CN', viewport: { width: 1440, height: 960 }, serviceWorkers: 'block', acceptDownloads: true });
    context.setDefaultTimeout(15_000); context.setDefaultNavigationTimeout(30_000);
    await workspace.guard(context);
    try { await use(context); } finally { await context.close(); }
  },
});

export async function confirm(page: Page, trigger: () => Promise<unknown>, pattern: RegExp) {
  let matches = false;
  confirming.add(page);
  const next = page.waitForEvent('dialog').then(async dialog => {
    matches = dialog.type() === 'confirm' && pattern.test(dialog.message());
    if (matches) await dialog.accept(); else await dialog.dismiss();
  });
  try { await Promise.all([trigger(), next]); } finally { confirming.delete(page); }
  invariant(matches, 'unexpected confirmation; action was not accepted');
}
export function responseFor(page: Page, pathname: string, method = 'POST'): Promise<Response> {
  return page.waitForResponse(response => new URL(response.url()).pathname === pathname && response.request().method() === method);
}
let coldNavigation = 0;
export async function openResult(page: Page, id: string) {
  // Changed query forces a real document navigation, not a guarded hash change.
  await page.goto(`/?workspace-view=${++coldNavigation}#work=${id}`, { waitUntil: 'domcontentloaded' });
  await expect(page.locator('main')).toHaveAttribute('data-page', 'result');
  await expect(page.getByRole('region', { name: '作品结果工作台', exact: true })).toHaveAttribute('aria-busy', 'false');
  await expect(page.getByRole('group', { name: '句子选择', exact: true }).getByRole('button').first()).toBeVisible();
}
export async function decodeMedia(video: Locator, expectedSeconds?: number) {
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.readyState >= 2 && element.videoWidth > 0)).toBe(true);
  // Real native decode + play/ended, no seek/rate changes or synthetic events.
  const measured = await video.evaluate(async (element: HTMLVideoElement) => {
    element.muted = true;
    const decoded = new Promise<boolean>(resolve => element.requestVideoFrameCallback(() => resolve(true)));
    const end = new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Native synthetic playback deadline')), 30_000);
      element.addEventListener('ended', () => { clearTimeout(timer); resolve(); }, { once: true });
      element.addEventListener('error', () => { clearTimeout(timer); reject(new Error('Native media decode failed')); }, { once: true });
    });
    await element.play(); await decoded; await end;
    return { duration: element.duration, width: element.videoWidth, height: element.videoHeight,
      ended: element.ended, currentTime: element.currentTime, frames: element.getVideoPlaybackQuality().totalVideoFrames };
  });
  expect(measured.ended).toBe(true); expect(measured.frames).toBeGreaterThan(0);
  if (expectedSeconds !== undefined) expect(Math.abs(measured.duration - expectedSeconds)).toBeLessThan(0.15);
  return measured;
}
export async function downloadedBytes(page: Page, link: Locator, expectedHash: string) {
  const downloadEvent = page.waitForEvent('download'); await link.click(); const download = await downloadEvent;
  invariant(await download.failure() === null, 'native download failed');
  const file = await download.path(); invariant(file, 'native download missing');
  invariant(size(file) > 0 && size(file) <= 32 * 1024 * 1024, 'bounded synthetic download');
  const content = readBytes(file);
  invariant(bytesHash(content) === expectedHash, 'download bytes differ from real response');
  // Playwright owns/removes its TEMP download when this disposable context closes.
  return { bytes: content.length, sha256: expectedHash };
}
export async function noAccountUI(page: Page) {
  const forbidden = await page.locator('body').evaluate(body => {
    const text = (body as HTMLElement).innerText;
    return /登录|注册|账号|账户|班级|课堂|老师台|教师/.test(text)
      || [...body.querySelectorAll<HTMLAnchorElement>('a[href]')].some(a => /\/(?:login|classroom|account)(?:\/|$)/i.test(new URL(a.href).pathname));
  });
  expect(forbidden, 'No account navigation or visible identity gate').toBe(false);
}