import { test as base, expect, request as playwrightRequest } from '@playwright/test';
import type { APIRequestContext, APIResponse, BrowserContext, Locator, Page, TestInfo } from '@playwright/test';
import { bytesHash, hashFile, joinPath, makeDirectory, parentPath, probeMedia, readBytes, readText, snapshotHashes, writeJSON } from './io.mjs';
import { BASE_URL, checkedURL, DRAFT_KEY, invariant, loadManifest, runSettings } from './environment';
import type { InputFile, ModeManifest, Sentence } from './environment';

export { expect, invariant, DRAFT_KEY };
export type Mode = 'voiceover' | 'mixed' | 'original';
export interface Word { w: string; s: number; e: number }
export interface Take {
  take_id: string; upload_id: string; start: number; end: number; asr_text: string;
  speaker_id: string; score: number; precision: 'word' | 'segment'; words: Word[];
}
export interface Match { idx: number; kind: 'narration' | 'quote'; score: number; source: Take | null; alt_takes: Take[] }
export interface Upload {
  id: string; name: string; status: string; sec: number; has_speech: boolean | null; progress: number;
  chunks: number[]; received_bytes: number; transcript: { precision: string; segments: { id: string; text: string; words: Word[]; speaker_id: string }[] };
  speakers: { id: string; name: string; title: string; auto_label: string; appearances: number; seconds: number }[];
}
export interface Task {
  task_id: string; mode: Mode; status: string; revision: number; progress: number;
  current_stage: number | null; error_message: string | null; errorKind: string | null; badRows: number[];
  stages: { number: number; status: string; weight: number; fraction: number; elapsed_seconds: number | null }[];
}
export interface ReportRow {
  sentence_id: number; kind: 'narration' | 'quote'; sentence: string; spoken_text: string | null;
  audio_kind: 'tts' | 'sync'; duration: number; source: Take | null; trim: { start: number; end: number } | null;
}
export interface Report {
  task_id: string; mode: Mode; rows: ReportRow[];
  quality: { blocking_issue_count: number; warning_count: number; issues: { code: string; severity: string }[] };
}
export interface Checks {
  revision: number; blocking_count: number; pending_count: number; passed: boolean;
  checks: { key: string; code: string; level: number; checked: boolean }[];
}
export interface Job {
  id: string; export_id?: string; pipeline_revision: number; state: string; output_id: string | null;
  result?: { bytes: number; duration: number };
}
export interface Identity {
  instanceId: string; serverState: string; synthetic: boolean; deniedEgress: number;
  providerCalls: Record<string, number>; uploadASRCalls: Record<string, number>;
}
type FetchOptions = NonNullable<Parameters<APIRequestContext['fetch']>[1]>;
const WRITE = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const ACCOUNT = /\/(?:classroom|accounts?|login|student-login|teacher|cloud)(?:\/|$)/i;

export class ModesRun {
  readonly tasks = new Set<string>();
  readonly uploads = new Map<string, string>(); // Capabilities stay in worker RAM, never evidence.
  readonly jobs = new Map<string, string>();
  readonly observations: { method: string; path: string }[] = [];
  readonly checkpoints: string[] = [];
  readonly confirmations = new WeakMap<Page, RegExp>();
  private uploadBudget = 0;
  private taskBudget = 0;
  private boundaryFailures = 0;
  private pageErrors = 0;
  private unexpectedDialogs = 0;
  private captures = new Set<Promise<void>>();

  constructor(readonly manifest: ModeManifest, readonly api: APIRequestContext, readonly info: TestInfo) {}
  checkpoint(value: string) {
    invariant(/^[a-z0-9-]{1,80}$/.test(value), 'static checkpoint identifier');
    this.checkpoints.push(value);
  }
  allowUploads(count: number) {
    invariant(this.uploadBudget === 0 && count > 0 && count <= 7, 'explicit one-shot upload admission');
    this.uploadBudget = count;
  }
  allowTask() { invariant(this.taskBudget === 0, 'only one explicit task admission'); this.taskBudget = 1; }
  authorize(method: string, path: string): boolean {
    if (ACCOUNT.test(path) || path.startsWith('/api/test/') && path !== '/api/test/modes') return false;
    // The manifest proves that ALL history belongs to this fresh synthetic
    // host. Read-only history thumbnails may refer to earlier retained cases;
    // only mutations require this test's own creation receipt.
    if (!WRITE.has(method)) return ['GET', 'HEAD', 'OPTIONS'].includes(method);
    const upload = /^\/api\/uploads\/(up_[a-f0-9]{32})(?:\/.*)?$/.exec(path);
    const task = /^\/api\/tasks\/([a-f0-9]{32})(?:\/.*)?$/.exec(path);
    if (upload && !this.uploads.has(upload[1]) || task && !this.tasks.has(task[1])) return false;
    if (method === 'POST' && path === '/api/uploads') {
      if (!this.uploadBudget) return false;
      this.uploadBudget--; return true;
    }
    if (method === 'POST' && path === '/api/tasks') {
      if (!this.taskBudget) return false;
      this.taskBudget--; return true;
    }
    if (method === 'POST' && path === '/api/match/preview') return true; // Real side-effect-free product parser/alignment.
    return !!upload || !!task;
  }
  uploadReceipt(value: unknown): string {
    const receipt = value as { upload_id: string; access_token: string; chunk_size: number };
    invariant(receipt && /^up_[a-f0-9]{32}$/.test(receipt.upload_id) && /^[A-Za-z0-9_-]{43}$/.test(receipt.access_token)
      && receipt.chunk_size === 8_388_608, 'real upload capability/chunk contract');
    this.uploads.set(receipt.upload_id, receipt.access_token);
    return receipt.upload_id;
  }
  taskReceipt(value: unknown): string {
    const receipt = value as { task_id: string; access_token: string };
    invariant(receipt && /^[a-f0-9]{32}$/.test(receipt.task_id), 'new task identity');
    this.tasks.add(receipt.task_id);
    invariant(typeof receipt.access_token === 'string' && receipt.access_token.length >= 32, 'immediate task capability required');
    return receipt.task_id;
  }
  async fetch(pathname: string, options: FetchOptions = {}): Promise<APIResponse> {
    const url = checkedURL(pathname), method = (options.method ?? 'GET').toUpperCase();
    invariant(this.authorize(method, new URL(url).pathname), 'unowned or unadmitted request');
    const response = await this.api.fetch(url, { ...options, method, maxRedirects: 0, maxRetries: 0,
      headers: { ...(WRITE.has(method) ? { Origin: BASE_URL } : {}), ...options.headers } });
    invariant(response.headers()['x-modes-acceptance'] === this.manifest.instanceId, 'response belongs to a different host');
    this.observations.push({ method, path: new URL(url).pathname });
    return response;
  }
  async json<T>(pathname: string, options: FetchOptions = {}, status = 200): Promise<T> {
    const response = await this.fetch(pathname, options);
    try {
      invariant(response.status() === status, `HTTP-${response.status()}-expected-${status}`);
      const bytes = await response.body();
      invariant(bytes.length <= 8 * 1024 * 1024, 'bounded JSON response');
      return JSON.parse(new TextDecoder().decode(bytes)) as T;
    } finally { await response.dispose(); }
  }
  identity(): Promise<Identity> { return this.json('/api/test/modes'); }
  uploadHeader(id: string) {
    const token = this.uploads.get(id); invariant(token, 'owned upload capability required');
    return { 'X-Upload-Token': token };
  }
  async uploadStatus(id: string): Promise<Upload> { return this.json(`/api/uploads/${id}`, { headers: this.uploadHeader(id) }); }
  async readyUpload(id: string): Promise<Upload> {
    let result: Upload | undefined;
    await expect.poll(async () => {
      result = await this.uploadStatus(id);
      invariant(!['failed', 'asr_failed', 'interrupted'].includes(result.status), 'real upload preprocessing failed');
      return result.status;
    }, { timeout: 180_000, intervals: [100, 300, 750, 1500] }).toBe('ready');
    return result!;
  }
  async upload(input: InputFile): Promise<Upload> {
    invariant(hashFile(input.path) === input.sha256, 'upload bytes changed');
    const bytes = readBytes(input.path, 16 * 1024 * 1024);
    invariant(bytes.length === input.bytes && bytesHash(bytes) === input.sha256, 'bounded source bytes');
    this.allowUploads(1);
    const id = this.uploadReceipt(await this.json('/api/uploads', {
      method: 'POST', data: { name: input.name, bytes: bytes.length, sha256: input.sha256, content_type: 'video/mp4' },
    }, 201));
    for (let offset = 0, index = 0; offset < bytes.length; offset += 8_388_608, index++) {
      const chunk = bytes.subarray(offset, offset + 8_388_608);
      await this.json(`/api/uploads/${id}/chunks/${index}`, { method: 'PUT', data: chunk,
        headers: { ...this.uploadHeader(id), 'Content-Type': 'application/octet-stream', 'Content-Length': String(chunk.length) } });
    }
    await this.json(`/api/uploads/${id}/complete`, { method: 'POST', headers: this.uploadHeader(id) }, 202);
    const ready = await this.readyUpload(id);
    invariant(ready.received_bytes === input.bytes && Math.abs(ready.sec - input.durationSeconds) <= 0.1, 'real uploaded size/duration');
    return ready;
  }
  async uploadMany(inputs: InputFile[]) {
    const result = [];
    for (const input of inputs) result.push(await this.upload(input));
    return result;
  }
  uploadSelection(ids: string[]) {
    return { upload_ids: ids, upload_tokens: Object.fromEntries(ids.map(id => {
      invariant(this.uploads.has(id), 'unknown upload selection');
      return [id, this.uploads.get(id)!];
    })) };
  }
  async preview(sentences: Sentence[], ids: string[]) {
    return this.json<{ matches: Match[]; match_ok: number; match_low: number }>('/api/match/preview',
      { method: 'POST', data: { sentences, ...this.uploadSelection(ids) } });
  }
  async create(mode: Mode, script: string, ids: string[], sentences?: Sentence[]): Promise<string> {
    this.allowTask();
    const value = await this.json('/api/tasks', { method: 'POST', data: { mode, script,
      ...this.uploadSelection(ids), ...(sentences ? { sentences } : {}), preferences: {
        pacing: 'normal', background_music: false, motion_effects: false, transitions: false,
        news_graphics: false, color_consistency: false, generative_fill: false, enhance_speech: false,
        lower_third: true, jump_cut_cover: 'broll', quote_caption: 'asr', caption_style: 'news',
      } } }, 202);
    return this.taskReceipt(value);
  }
  status(id: string): Promise<Task> { return this.json(`/api/tasks/${id}`); }
  report(id: string): Promise<Report> { return this.json(`/api/tasks/${id}/report`); }
  async completed(id: string, revision = 0, timeout = 900_000): Promise<Task> {
    let result: Task | undefined;
    await expect.poll(async () => {
      result = await this.status(id);
      invariant(!['failed', 'cancelled'].includes(result.status) && !result.error_message, 'task failed; retain TEMP evidence, do not retry');
      return result.status === 'done' && result.revision === revision;
    }, { timeout, intervals: [100, 500, 1500, 2500], message: 'Real task must publish the expected completed revision' }).toBe(true);
    return result!;
  }
  async job(id: string, jobId: string): Promise<Job> {
    this.jobs.set(jobId, id);
    let job: Job | undefined;
    await expect.poll(async () => {
      job = await this.json<Job>(`/api/tasks/${id}/exports/${jobId}`);
      invariant(!['failed', 'cancelled', 'interrupted'].includes(job.state), 'actual export failed');
      return job.state;
    }, { timeout: 180_000, intervals: [200, 750, 1500] }).toBe('succeeded');
    invariant(job?.output_id && job.result && job.result.bytes > 0, 'real export receipt');
    return job;
  }
  revisionHashes(id: string, revision = 0) {
    invariant(this.tasks.has(id), 'hash only this test newly created task');
    const hashes = snapshotHashes(joinPath(this.manifest.taskRoot, id, `revisions/r${revision}`));
    invariant(Object.keys(hashes).length >= 15 && hashes['final.mp4'] && hashes['report.json'], 'complete immutable revision');
    return hashes;
  }
  artifact<T>(id: string, name: string): T {
    invariant(this.tasks.has(id) && ['timings.json', 'pretranscripts.json', 'match_plan.json', 'production_mode.json', 'subs.ass', 'subtitle_manifest.json'].includes(name),
      'nonsecret artifact allowlist');
    return JSON.parse(readText(joinPath(this.manifest.taskRoot, id, name))) as T;
  }
  probe(id: string) {
    invariant(this.tasks.has(id), 'probe only fresh owned task');
    return probeMedia(joinPath(this.manifest.taskRoot, id, 'final.mp4'), this.manifest.tools.ffprobe);
  }
  evidence(name: string, value: unknown) {
    invariant(/^[a-z0-9-]+$/.test(name), 'safe evidence name');
    const file = this.info.outputPath(name + '.json');
    makeDirectory(parentPath(file));
    writeJSON(file, value);
  }
  async guard(context: BrowserContext) {
    await context.route('**/*', async route => {
      // Receipts are registered before following PUT/GETs are admitted.
      await Promise.all([...this.captures]);
      const request = route.request(), url = new URL(request.url());
      if (url.origin !== BASE_URL || url.username || url.password || !this.authorize(request.method(), url.pathname)) {
        this.boundaryFailures++; await route.abort('blockedbyclient'); return;
      }
      this.observations.push({ method: request.method(), path: url.pathname });
      await route.continue(); // Never fulfill product HTTP with synthetic responses.
    });
    await context.routeWebSocket(/.*/, ws => { this.boundaryFailures++; ws.close(); });
    const watch = (page: Page) => {
      page.on('pageerror', () => { this.pageErrors++; });
      page.on('dialog', async dialog => {
        const expected = this.confirmations.get(page);
        if (dialog.type() === 'beforeunload') await dialog.accept();
        else if (dialog.type() === 'confirm' && expected?.test(dialog.message())) {
          this.confirmations.delete(page); await dialog.accept();
        } else { this.unexpectedDialogs++; await dialog.dismiss(); }
      });
      page.on('response', response => {
        if (response.request().method() !== 'POST' || ![201, 202].includes(response.status())) return;
        const url = new URL(response.url());
        if (url.origin !== BASE_URL) return;
        if (!['/api/uploads', '/api/tasks'].includes(url.pathname) && !/^\/api\/tasks\/[a-f0-9]{32}\/export$/.test(url.pathname)) return;
        const capture = (async () => {
          try {
            invariant(response.headers()['x-modes-acceptance'] === this.manifest.instanceId, 'browser receipt host identity');
            const value = await response.json();
            if (url.pathname === '/api/uploads') this.uploadReceipt(value);
            else if (url.pathname === '/api/tasks') this.taskReceipt(value);
            else this.jobs.set((value as Job).id, url.pathname.split('/')[3]);
          } catch { this.boundaryFailures++; }
        })();
        this.captures.add(capture); void capture.finally(() => this.captures.delete(capture));
      });
    };
    context.pages().forEach(watch); context.on('page', watch);
  }
  async finish() {
    await Promise.all([...this.captures]);
    const errors: string[] = [], retained: { id: string; status: string; revision: number }[] = [];
    // Keep NEW task/revision artifacts for independent inspection, even failures.
    // Upload sessions can be removed after materialization; immutable input files remain.
    for (const id of this.tasks) {
      try {
        const state = await this.status(id); retained.push({ id, status: state.status, revision: state.revision });
        if (this.info.status === 'passed' && !['done', 'failed'].includes(state.status)) errors.push('task-not-terminal');
      } catch { errors.push('task-status-unconfirmed'); }
    }
    for (const id of this.uploads.keys()) {
      try {
        const response = await this.fetch(`/api/uploads/${id}`, { method: 'DELETE', headers: this.uploadHeader(id) });
        invariant(response.status() === 204, 'owned upload cleanup'); await response.dispose();
      } catch { errors.push('upload-cleanup-unconfirmed'); }
    }
    let identity: Identity | undefined;
    try { identity = await this.identity(); } catch { errors.push('host-unreachable'); }
    const immutable = this.manifest.inputs.every(input => hashFile(input.path) === input.sha256);
    this.evidence('safety', { kind: 'synthetic-modes-case', checkpoints: this.checkpoints,
      // Capture only code coordinates, never assertion bodies/DOM/capabilities.
      failureLocations: this.info.errors.flatMap(error =>
        [...(error.stack ?? '').matchAll(/(?:acceptance\.modes\.spec|support)\.ts:\d+:\d+/g)]
          .map(match => match[0])),
      synthetic: true, speechEvidence: false, pageErrors: this.pageErrors, blockedRequests: this.boundaryFailures,
      unexpectedDialogs: this.unexpectedDialogs, cleanupErrors: errors, inputHashesUnchanged: immutable,
      deniedEgress: identity?.deniedEgress, providerCalls: identity?.providerCalls, uploadASRCalls: identity?.uploadASRCalls,
      tasksRetainedInOwnedTEMP: retained, uploadsRemoved: this.uploads.size - errors.filter(e => e.startsWith('upload')).length,
      requests: this.observations, rawBodiesHeadersTokensRecorded: false });
    invariant(!errors.length && !this.pageErrors && !this.boundaryFailures && !this.unexpectedDialogs && immutable
      && identity?.deniedEgress === 0 && !identity.providerCalls.unexpected && !identity.providerCalls.handler_error
      && !identity.uploadASRCalls.unexpected, 'safety/cleanup failure; see projected case evidence');
  }
}

export const test = base.extend<{ modes: ModesRun; modesNoRawArtifacts: boolean; _setupArtifacts: void }>({
  modesNoRawArtifacts: [true, { option: true }],
  // Installed Playwright 1.63 writes error-context even with trace/video off
  // and NO_COPY_PROMPT set. Replace ONLY its automatic artifact collector;
  // request ownership, receipts, teardown, immutable hashes and reporter stay.
  // The inherited auto/all-hooks-included scope also covers fixture failures.
  _setupArtifacts: async ({ modesNoRawArtifacts }, use) => {
    invariant(modesNoRawArtifacts, 'raw automatic artifacts must stay disabled');
    await use();
  },
  modes: async ({}, use, info) => {
    const manifest = loadManifest(runSettings().manifestPath);
    const api = await playwrightRequest.newContext({ baseURL: BASE_URL, timeout: 180_000, ignoreHTTPSErrors: false });
    const modes = new ModesRun(manifest, api, info);
    try {
      const identity = await modes.identity();
      invariant(identity.instanceId === manifest.instanceId && identity.serverState === 'ready' && identity.synthetic, 'host identity changed');
      await use(modes);
    } finally { try { await modes.finish(); } finally { await api.dispose(); } }
  },
  context: async ({ browser, modes }, use) => {
    const context = await browser.newContext({ baseURL: BASE_URL, viewport: { width: 1440, height: 960 },
      locale: 'zh-CN', serviceWorkers: 'block', acceptDownloads: true });
    context.setDefaultTimeout(20_000); context.setDefaultNavigationTimeout(30_000);
    await modes.guard(context);
    try { await use(context); } finally { await context.close(); }
  },
});

export function responseFor(page: Page, path: string, method = 'POST') {
  return page.waitForResponse(r => new URL(r.url()).origin === BASE_URL && new URL(r.url()).pathname === path
    && r.request().method() === method, { timeout: 180_000 });
}
export async function creator(page: Page, mode: Mode, script = '') {
  await page.goto('/');
  const wizard = page.getByRole('region', { name: '新建新闻作品', exact: true });
  await expect(wizard).toBeVisible();
  await expect(page.locator('.shell-ready')).toHaveText('制作服务就绪');
  const cards = wizard.getByRole('group', { name: '制作模式', exact: true });
  await expect(cards.getByRole('button')).toHaveCount(3);
  const names = { voiceover: 'AI 配音', mixed: '旁白 + 原声', original: '只用原声' };
  const card = cards.getByRole('button', { name: new RegExp(names[mode].replace('+', '\\+')) });
  await card.click(); await expect(card).toHaveAttribute('aria-pressed', 'true');
  if (script) await page.getByRole('textbox', { name: '新闻稿（第一行标题，下一行正文）', exact: true }).fill(script);
  await noLogin(page);
}
export async function chooseInputs(m: ModesRun, page: Page, inputs: InputFile[]) {
  m.allowUploads(inputs.length);
  const chooser = page.waitForEvent('filechooser');
  await page.getByLabel('选择视频或照片', { exact: true }).click();
  for (const input of inputs) invariant(hashFile(input.path) === input.sha256, 'chooser input drift');
  await (await chooser).setFiles(inputs.map(input => input.path));
  await expect(page.locator('.gm-media-item')).toHaveCount(inputs.length);
  await expect(page.locator('.gm-capacity')).toContainText(`${inputs.length} 个已预处理`, { timeout: 180_000 });
  await expect.poll(() => m.uploads.size, { timeout: 180_000 }).toBe(inputs.length);
}
export async function leanEffects(page: Page, mode: Mode) {
  await expect(page.getByRole('heading', { name: '选效果', exact: true })).toBeVisible();
  // Save CPU by explicitly choosing actual user-facing off switches, not patching QC/renderers.
  for (const name of ['背景音乐', '画面慢慢推近', '新闻台标和片尾板', '开头结尾淡入淡出', '颜色统一', '现场原声降噪']) {
    const control = page.getByRole('switch', { name, exact: true });
    if (await control.getAttribute('aria-checked') === 'true') await control.click();
    await expect(control).toHaveAttribute('aria-checked', 'false');
  }
  if (mode !== 'original') await expect(page.getByRole('switch', { name: 'AI 示意画面补充', exact: true })).toBeDisabled();
  else {
    await expect(page.getByRole('group', { name: '播音速度', exact: true })).toHaveCount(0);
    await expect(page.getByRole('switch', { name: 'AI 示意画面补充', exact: true })).toHaveCount(0);
  }
}
export async function submitWizard(m: ModesRun, page: Page) {
  m.allowTask();
  m.confirmations.set(page, /开始.*制作新闻视频/);
  const pending = responseFor(page, '/api/tasks');
  await page.getByRole('button', { name: '开始制作', exact: true }).click();
  const response = await pending;
  invariant(response.status() === 202, 'real create not accepted');
  invariant(response.request().headers()['content-type']?.includes('application/json'), 'new JSON creation contract required');
  const payload = response.request().postDataJSON() as { mode: string; upload_ids: string[]; sentences: Sentence[] };
  invariant(payload.upload_ids.length === m.uploads.size && payload.sentences.length > 0, 'creation must reuse actual uploads and confirmed sentences');
  const id = m.taskReceipt(await response.json());
  return { id, mode: payload.mode, sentences: payload.sentences, uploadCount: payload.upload_ids.length };
}
let navigation = 0;
export async function openResult(page: Page, id: string) {
  await page.goto(`/?modes-navigation=${++navigation}#work=${id}`, { waitUntil: 'domcontentloaded' });
  await expect(page.locator('main')).toHaveAttribute('data-page', 'result', { timeout: 30_000 });
  await expect(page.getByRole('region', { name: '作品结果工作台', exact: true })).toHaveAttribute('aria-busy', 'false');
  await expect(page.getByRole('group', { name: '句子选择', exact: true }).getByRole('button').first()).toBeVisible();
}
export async function nativeFrame(video: Locator) {
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState >= 2 && v.videoWidth > 0)).toBe(true);
  const measured = await video.evaluate(async (v: HTMLVideoElement) => {
    v.muted = true;
    const frame = new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Native frame decode deadline')), 15_000);
      v.requestVideoFrameCallback(() => { clearTimeout(timer); resolve(); });
    });
    await v.play(); await frame; v.pause();
    return { width: v.videoWidth, height: v.videoHeight, duration: v.duration, frames: v.getVideoPlaybackQuality().totalVideoFrames };
  });
  expect(measured.width).toBe(1920); expect(measured.height).toBe(1080); expect(measured.frames).toBeGreaterThan(0);
  return measured; // Decoded frame, NOT an ended/play-through or speech assertion.
}
export async function noLogin(page: Page) {
  const forbidden = await page.locator('body').evaluate(body => /登录|注册|账号|账户|班级|课堂|老师台|教师/.test((body as HTMLElement).innerText));
  expect(forbidden, 'No account gate or account navigation').toBe(false);
}
export async function confirmAllChecks(m: ModesRun, page: Page, id: string) {
  let gate = await m.json<Checks>(`/api/tasks/${id}/checks`);
  invariant(gate.blocking_count === 0, 'real hard QC errors cannot be waived');
  const initial = gate.pending_count;
  invariant(initial > 0, 'test requires genuine backend confirmation items, not fake QC');
  for (let i = 0; gate.pending_count > 0 && i < 80; i++) {
    const controls = page.locator('.gm-rw-qc-summary input[type=checkbox]');
    const index = await controls.evaluateAll(inputs => inputs.findIndex(input => !(input as HTMLInputElement).checked));
    invariant(index >= 0, 'pending server check requires an unchecked control');
    const before = gate.pending_count, response = responseFor(page, `/api/tasks/${id}/checks`);
    // A :not(:checked) target ceases matching after React commits the click;
    // Playwright may retry on the NEXT checkbox. Keep the target identity stable.
    await controls.nth(index).check();
    invariant((await response).status() === 200, 'UI confirmation not saved');
    gate = await m.json<Checks>(`/api/tasks/${id}/checks`);
    invariant(gate.pending_count === before - 1, 'exactly one real check confirmed');
    await expect(page.locator('.gm-rw-qc-summary input[type=checkbox]:checked')).toHaveCount(initial - gate.pending_count);
  }
  invariant(gate.passed && gate.pending_count === 0, 'server gate remains blocked');
  await expect(page.getByRole('status').filter({ hasText: '发布前检查已通过' })).toBeVisible();
  return initial;
}
export async function layout(page: Page) {
  return page.evaluate(() => {
    const root = document.documentElement;
    const items = [...document.querySelectorAll<HTMLElement>('main *, header *, aside *, dialog[open] *')];
    const outside = items.filter(element => {
      if (!element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }) || element.closest('[hidden], dialog:not([open])')) return false;
      const box = element.getBoundingClientRect();
      if (!box.width || !box.height || box.left >= -1 && box.right <= root.clientWidth + 1) return false;
      // Only actual bounded local scrolling/clipping exempts a descendant.
      for (let parent = element.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
        const style = getComputedStyle(parent), bounds = parent.getBoundingClientRect();
        if (['auto', 'scroll', 'hidden', 'clip'].includes(style.overflowX) && bounds.left >= -1 && bounds.right <= root.clientWidth + 1) return false;
      }
      return true;
    }).map(element => {
      const box = element.getBoundingClientRect();
      // Numeric geometry only: no DOM text, attributes, URLs or capabilities.
      return { index: items.indexOf(element), left: box.left, right: box.right, top: box.top,
        bottom: box.bottom, width: box.width, height: box.height };
    });
    return { clientWidth: root.clientWidth, scrollWidth: root.scrollWidth, fontSize: getComputedStyle(root).fontSize, outside };
  });
}