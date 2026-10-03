import { test as base, expect } from '@playwright/test';
import type { APIRequestContext, APIResponse, BrowserContext, BrowserContextOptions, Page, TestInfo } from '@playwright/test';
import { hashFile, joinPath, makeDirectory, parentPath, snapshotHashes, writeJSON } from '../modes/io.mjs';
import { env } from '../modes/io.mjs';
import type { InputFile } from '../modes/environment';
import { BASE_URL, bound, checkedURL, DRAFT_KEY, invariant, loadManifest, settings } from './environment';
import type { Manifest } from './environment';
import { pageErrorFacts } from './diagnostics';
import { rangeDownload } from './media.mjs';
export { expect, invariant, DRAFT_KEY };
export type Mode = 'voiceover' | 'mixed' | 'original';
export interface Task { task_id: string; status: string; mode: Mode; revision: number; rules_version: number;
  accepted: boolean; error_message: string | null; stages: { number: number; status: string }[] }
export interface Source { upload_id: string; start: number; end: number; asr_text: string; precision: string; words: { w: string; s: number; e: number }[] }
export interface Row { sentence_id: number; sentence: string; spoken_text: string; kind: string; audio_kind: string; source: Source | null; duration: number; trim?: { start: number; end: number } }
export interface Report { mode: Mode; rows: Row[] }
export interface Checks { revision: number; blocking_count: number; pending_count: number; passed: boolean; checks: { key: string; checked: boolean; level: number; confirmable?: boolean }[] }
export interface Identity { instanceId: string; synthetic: boolean; serverState: string; deniedEgress: number; providerCalls: Record<string, number>; uploadASRCalls: Record<string, number> }
export interface Job { export_id: string; revision: number; state: string; output_id: string | null; result?: { bytes: number; duration: number } }
export interface Apply { revision: number; operation_id: string; plan?: { steps: { kind: string; stages: number[] }[] }; steps?: { kind: string; stages: number[] }[] }
type Options = NonNullable<Parameters<APIRequestContext['fetch']>[1]>;
const WRITE = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);
const ID = /^[a-f0-9]{32}$/;
type DesignWrite = 'metadata' | 'duplicate' | 'recover-draft' | 'start';
type DesignAdmission = { method: string; body?: Record<string, unknown> };

export class V2Run {
  readonly tasks = new Map<string, string>(); // private worker RAM; NEVER serialize
  readonly files = new Map<string, { task: string; upload: string }>();
  readonly observations: { method: string; path: string }[] = [];
  readonly tags: string[] = [];
  readonly confirmations = new WeakMap<Page, RegExp>();
  private captures = new Set<Promise<void>>();
  private draftBudget = 0; private fileBudget = 0;
  private fileReceipts = new Map<string, number>();
  private uploadWrites = new Map<string, { bytes: number; chunks: number; sent: Set<number>; probe: boolean; probed: boolean; complete: boolean }>();
  private blocked = 0; private pageErrors = 0; private dialogs = 0;
  private errorFacts: ReturnType<typeof pageErrorFacts>[] = [];
  private receipts = 0;
  private missingCapabilities = 0;
  private readonly design = env.V2_DESIGN_ONLY === '1';
  private designWrites = new Map<string, DesignAdmission>();
  private designUsed = new Set<string>();
  private designCounts = new Map<string, number>();
  private designSource: string | undefined;
  private copyReceipts = new Set<string>();
  constructor(readonly manifest: Manifest, readonly api: APIRequestContext, readonly info: TestInfo) {}
  /** One explicit actor action, exact owned path/body; never a general write waiver.
   * Metadata permits two distinct CAS bodies (one commit, one stale conflict).
   * Copy admission does NOT replenish the unrelated /api/tasks draft budget. */
  async admitDesignWrite(id: string, kind: DesignWrite, body?: Record<string, unknown>) {
    invariant(this.design && this.tasks.has(id), 'design write requires owned task');
    invariant(kind === 'start' ? this.designSource === undefined : this.designSource === id,
      'all design actions bind the single generated source');
    const path = `/api/tasks/${id}/${kind}`, method = kind === 'metadata' ? 'PATCH' : 'POST';
    invariant(!this.designWrites.has(path), 'only one pending design action per path');
    const count = this.designCounts.get(path) ?? 0;
    invariant(count < (kind === 'metadata' ? 2 : 1), 'fixed per-task design action budget');
    const key = path + ':' + JSON.stringify(body ?? null);
    invariant(!this.designUsed.has(key), 'design action cannot be replayed');
    if (kind !== 'start') {
      const current = await this.status(id);
      invariant(current.status === 'done' && (kind !== 'recover-draft' || current.accepted), 'terminal admitted source required');
      invariant(body && (kind === 'recover-draft' ? body.step === 1 && body.mode === 'original'
        : body.expected_revision === current.revision), 'exact design mutation contract');
      invariant(Object.keys(body).sort().join(',') === (kind === 'recover-draft' ? 'mode,step'
        : kind === 'metadata' ? 'expected_metadata_revision,expected_revision,title' : 'expected_revision'), 'no extra design write fields');
      if (kind === 'metadata') invariant(Number.isSafeInteger(body.expected_metadata_revision)
        && typeof body.title === 'string' && Array.from(body.title).length <= 40 && !!body.title.trim(), 'strict metadata budget');
    } else {
      invariant((await this.status(id)).status === 'draft', 'start only owned draft');
      this.designSource = id;
    }
    this.designCounts.set(path, count + 1);
    this.designUsed.add(key); this.designWrites.set(path, { method, body });
  }
  tag(value: string) { invariant(/^[a-z0-9-]{1,80}$/.test(value), 'static tag'); this.tags.push(value); }
  admitFiles(count: number) {
    invariant(!this.fileBudget && count > 0 && count <= 7, 'bounded one-shot picker');
    this.fileBudget = count;
    if (!this.tasks.size) this.draftBudget = 1;
  }
  authorize(method: string, path: string, body?: unknown): boolean {
    if (/\/(?:accounts?|classroom|login|teacher|cloud)(?:\/|$)/i.test(path)) return false;
    if (path.startsWith('/api/test/')) return method === 'GET' && path === '/api/test/v2';
    if (path.startsWith('/api/uploads') || path === '/api/match/preview') return false; // no legacy fallback
    const task = /^\/api\/tasks\/([a-f0-9]{32})(?:\/|$)/.exec(path);
    if (task && !this.tasks.has(task[1])) return false;
    if (this.design && path.startsWith('/api/tasks/') && !task)
      return method === 'GET' && path === '/api/tasks/v2-design-invalid-link'; // real 404, no capability or tombstone claim
    if (!WRITE.has(method)) return ['GET', 'HEAD', 'OPTIONS'].includes(method);
    if (method === 'DELETE') return false; // keep every new task and draft-owned upload
    if (this.design && task && /\/(?:metadata|duplicate|recover-draft|start)$/.test(path)) {
      const admission = this.designWrites.get(path);
      if (!admission || admission.method !== method) return false;
      if (path.endsWith('/start')) {
        const start = body as { mode?: string; preferences?: { voice?: string }; own_voice?: unknown } | undefined;
        if (!start || start.mode !== 'voiceover' || start.preferences?.voice !== 'mine' || start.own_voice !== undefined) return false;
      }
      if (admission.body && (!body || typeof body !== 'object' || Array.isArray(body)
        || Object.keys(body).length !== Object.keys(admission.body).length
        || Object.entries(admission.body).some(([key, value]) => (body as Record<string, unknown>)[key] !== value))) return false;
      this.designWrites.delete(path);
      if (/\/(?:duplicate|recover-draft)$/.test(path)) this.copyReceipts.add(path);
      return true;
    }
    if (method === 'POST' && path === '/api/tasks') {
      if (!this.draftBudget) return false; this.draftBudget--; return true;
    }
    if (method === 'POST' && /^\/api\/tasks\/[^/]+\/files$/.test(path)) {
      if (!task || !this.fileBudget) return false;
      this.fileBudget--;
      this.fileReceipts.set(task[1], (this.fileReceipts.get(task[1]) ?? 0) + 1);
      return true;
    }
    const file = /^\/api\/tasks\/([a-f0-9]{32})\/files\/(up_[a-f0-9]{32})\/(chunks\/(0|[1-9]\d*)|probe|complete)$/.exec(path);
    if (file) {
      const binding = this.files.get(file[2]), budget = this.uploadWrites.get(file[2]);
      if (!binding || binding.task !== file[1] || !budget) return false;
      if (file[3].startsWith('chunks/')) {
        const index = Number(file[4]);
        if (method !== 'PUT' || !Number.isSafeInteger(index) || index >= budget.chunks
          || budget.sent.has(index) || budget.probe || budget.complete) return false;
        budget.sent.add(index); return true;
      }
      // Attempt budgets, not success budgets: ambiguous failures never refund
      // or auto-retry. Only a new owned file receipt grants these two phases.
      if (method !== 'POST' || !body || typeof body !== 'object' || Array.isArray(body)
        || Object.keys(body).length || budget.sent.size !== budget.chunks) return false;
      if (file[3] === 'probe') {
        if (budget.probe || budget.complete) return false;
        budget.probe = true; return true;
      }
      if (!budget.probed || budget.complete) return false;
      budget.complete = true; return true;
    }
    if (this.design) {
      // No apply/check/export/draft PATCH, recording, delete or repeat generation.
      return !!task && method === 'POST' && path === `/api/tasks/${task[1]}/align`;
    }
    return !!task && (method === 'POST' && ['align', 'start', 'apply', 'exports'].some(action => path === `/api/tasks/${task[1]}/${action}`)
      || method === 'PUT' && path === `/api/tasks/${task[1]}/checks`
      || method === 'PATCH' && path === `/api/tasks/${task[1]}/draft`);
  }
  captureTask(value: unknown, path = '/api/tasks') {
    const r = value as { id: string; task_id: string; access_token: string; status: string };
    const copied = /^\/api\/tasks\/([a-f0-9]{32})\/(duplicate|recover-draft)$/.exec(path);
    if (copied) invariant(this.design && this.tasks.has(copied[1]) && this.copyReceipts.delete(path)
      && r && r.task_id !== copied[1] && !this.tasks.has(r.task_id)
      && r.access_token !== this.tasks.get(copied[1]), 'only admitted new copy receipt');
    invariant(r && ID.test(r.task_id) && (copied?.[2] === 'duplicate' ? r.status === 'done' : r.id === r.task_id && r.status === 'draft')
      && /^[A-Za-z0-9_-]{43}$/.test(r.access_token), 'real v2 draft receipt');
    const prior = this.tasks.get(r.task_id);
    invariant(!prior || prior === r.access_token, 'task capability changed');
    this.tasks.set(r.task_id, r.access_token); this.receipts++;
    if (copied?.[2] === 'recover-draft') {
      const recovery = value as { accepted: boolean; step: number; mode: string;
        recovery: { source_task_id: string; task_id: string }; files: { status: string; file_id: string; chunksize: number }[] };
      invariant(recovery.accepted === false && recovery.step === 1 && recovery.mode === 'original'
        && recovery.recovery.source_task_id === copied[1] && recovery.recovery.task_id === r.task_id
        && Array.isArray(recovery.files) && recovery.files.length > 0 && recovery.files.length <= 7, 'bounded independent recovery');
      for (const file of recovery.files) {
        invariant(file.status === 'ready' && !this.files.has(file.file_id), 'new ready recovery binding');
        this.captureFile(r.task_id, { ...file, chunk_size: file.chunksize }, true);
      }
    }
    return r.task_id;
  }
  captureFile(task: string, value: unknown, recovered = false) {
    const f = value as { file_id: string; up_id: string; upload_id: string; token: string; access_token: string; chunk_size: number; bytes: number; status: string };
    invariant(this.tasks.has(task) && /^up_[a-f0-9]{32}$/.test(f.file_id) && f.up_id === f.upload_id
      && f.file_id === f.up_id && !this.files.has(f.file_id)
      && f.chunk_size === 8_388_608 && f.token === this.tasks.get(task) && f.access_token === this.tasks.get(task), 'scoped file capability and 8MiB chunks');
    invariant(recovered ? this.design && f.status === 'ready' : f.status === 'uploading'
      && (this.fileReceipts.get(task) ?? 0) > 0 && Number.isSafeInteger(f.bytes) && f.bytes > 0 && f.bytes <= 500 * 1024 ** 2,
      'only admitted new file receipts grant upload budgets');
    if (!recovered) {
      this.fileReceipts.set(task, this.fileReceipts.get(task)! - 1);
      this.uploadWrites.set(f.file_id, { bytes: f.bytes, chunks: Math.ceil(f.bytes / 8_388_608), sent: new Set(), probe: false, probed: false, complete: false });
    }
    this.files.set(f.file_id, { task, upload: f.up_id });
  }
  captureProbe(path: string, value: unknown) {
    const match = /^\/api\/tasks\/([a-f0-9]{32})\/files\/(up_[a-f0-9]{32})\/probe$/.exec(path);
    const budget = match && this.uploadWrites.get(match[2]);
    const v = value as { id: string; file_id: string; bytes: number; status: string; probe_ok: boolean; metadata_only: boolean; can_materialize: boolean; sec: number; width: number; height: number; fps: number; chunks: number[] };
    invariant(match && this.files.get(match[2])?.task === match[1] && budget?.probe && !budget.probed && !budget.complete
      && v && v.id === match[2] && v.file_id === match[2] && v.bytes === budget.bytes
      && v.status === 'uploading' && v.probe_ok === true && v.metadata_only === true && v.can_materialize === false
      && Number.isFinite(v.sec) && v.sec > 0 && v.sec <= 1800
      && Number.isSafeInteger(v.width) && v.width > 0 && v.width <= 7680
      && Number.isSafeInteger(v.height) && v.height > 0 && v.height <= 4320
      && Number.isFinite(v.fps) && v.fps > 0 && v.fps <= 120
      && Array.isArray(v.chunks) && v.chunks.length === budget.chunks
      && new Set(v.chunks).size === budget.chunks && v.chunks.every(i => Number.isSafeInteger(i) && budget.sent.has(i)),
      'successful exact owned metadata-only probe required');
    budget.probed = true;
  }
  headers(id: string) { const token = this.tasks.get(id); invariant(token, 'owned capability required'); return { 'X-Task-Token': token }; }
  async fetch(path: string, options: Options = {}, auth = true): Promise<APIResponse> {
    const url = checkedURL(path), method = (options.method ?? 'GET').toUpperCase();
    invariant(this.authorize(method, url.pathname, options.data), 'request not owned/admitted');
    const task = /^\/api\/tasks\/([a-f0-9]{32})/.exec(url.pathname)?.[1];
    const response = await this.api.fetch(url.href, { ...options, method, maxRedirects: 0, maxRetries: 0,
      headers: { ...(WRITE.has(method) ? { Origin: BASE_URL } : {}), ...(auth && task ? this.headers(task) : {}), ...options.headers } });
    invariant(response.headers()['x-v2-acceptance'] === this.manifest.instanceId, 'response instance identity');
    this.observations.push({ method, path: url.pathname }); return response;
  }
  async json<T>(path: string, options: Options = {}, status = 200): Promise<T> {
    const response = await this.fetch(path, options);
    try {
      invariant(response.status() === status, `http-${response.status()}-expected-${status}`);
      const bytes = await response.body(); invariant(bytes.length <= 8 * 1024 ** 2, 'bounded JSON');
      return JSON.parse(new TextDecoder().decode(bytes)) as T;
    } finally { await response.dispose(); }
  }
  identity() { return this.json<Identity>('/api/test/v2'); }
  status(id: string) { return this.json<Task>(`/api/tasks/${id}`); }
  report(id: string) { return this.json<Report>(`/api/tasks/${id}/report`); }
  async done(id: string, revision: number) {
    let value: Task | undefined;
    await expect.poll(async () => {
      value = await this.status(id);
      invariant(!['failed', 'cancelled'].includes(value.status) && !value.error_message, 'real pipeline failed; no retry');
      return value.status === 'done' && value.revision === revision;
    }, { timeout: 900_000, intervals: [250, 750, 1500, 2500] }).toBe(true);
    return value!;
  }
  hashes(id: string) {
    invariant(this.tasks.has(id), 'only hash newly owned task');
    const value = snapshotHashes(joinPath(this.manifest.taskRoot, id, 'revisions/r0'));
    invariant(Object.keys(value).length >= 15 && value['final.mp4'] && value['report.json'], 'complete immutable r0');
    return value;
  }
  evidence(name: string, value: unknown) {
    invariant(/^[a-z0-9-]+$/.test(name), 'static evidence name');
    const path = this.info.outputPath(name + '.json'); makeDirectory(parentPath(path)); writeJSON(path, value);
  }
  async guard(context: BrowserContext) {
    await context.route('**/*', async route => {
      await Promise.all([...this.captures]);
      const r = route.request(), url = new URL(r.url());
      let body: unknown;
      if (/\/(?:probe|complete)$/.test(url.pathname) || this.design && /\/(?:metadata|duplicate|recover-draft|start)$/.test(url.pathname)) {
        try { body = r.postDataJSON(); } catch { /* Invalid bodies are not admitted. */ }
      }
      if (url.origin !== BASE_URL || url.username || url.password || !this.authorize(r.method(), url.pathname, body)) {
        this.blocked++; await route.abort('blockedbyclient'); return;
      }
      const task = /^\/api\/tasks\/([a-f0-9]{32})/.exec(url.pathname)?.[1];
      if (task) {
        const supplied = r.headers()['x-task-token'] ?? url.searchParams.get('token');
        if (supplied !== this.tasks.get(task)) { this.blocked++; this.missingCapabilities++; await route.abort('blockedbyclient'); return; }
      }
      this.observations.push({ method: r.method(), path: url.pathname });
      await route.continue(); // NO fulfill, API response substitution or origin rewrite
    });
    await context.routeWebSocket(/.*/, ws => { this.blocked++; ws.close(); });
    const watch = (page: Page) => {
      page.on('pageerror', error => {
        this.pageErrors++;
        if (this.errorFacts.length < 8) this.errorFacts.push(pageErrorFacts(error));
      });
      page.on('dialog', async dialog => {
        const pattern = this.confirmations.get(page);
        if (dialog.type() === 'beforeunload') await dialog.accept();
        else if (dialog.type() === 'confirm' && pattern?.test(dialog.message())) {
          this.confirmations.delete(page); await dialog.accept();
        } else { this.dialogs++; await dialog.dismiss(); }
      });
      page.on('response', response => {
        const path = new URL(response.url()).pathname;
        const probe = /^\/api\/tasks\/[a-f0-9]{32}\/files\/up_[a-f0-9]{32}\/probe$/.test(path) && response.status() === 200;
        if (response.request().method() !== 'POST' || !probe && (response.status() !== 201
          || path !== '/api/tasks' && !/^\/api\/tasks\/[a-f0-9]{32}\/files$/.test(path)
            && !(this.design && /^\/api\/tasks\/[a-f0-9]{32}\/(?:duplicate|recover-draft)$/.test(path)))) return;
        const capture = (async () => {
          try {
            invariant(response.headers()['x-v2-acceptance'] === this.manifest.instanceId, 'receipt host');
            const value: unknown = await response.json();
            if (probe) this.captureProbe(path, value);
            else if (path.endsWith('/files')) this.captureFile(path.split('/')[3], value); else this.captureTask(value, path);
          } catch { this.blocked++; }
        })();
        this.captures.add(capture); void capture.finally(() => this.captures.delete(capture));
      });
    };
    context.pages().forEach(watch); context.on('page', watch);
  }
  async finish() {
    await Promise.all([...this.captures]);
    let identity: Identity | undefined; const retained: { id: string; status: string; revision: number }[] = [];
    let failures = 0;
    try {
      for (const id of this.tasks.keys()) { const t = await this.status(id); retained.push({ id, status: t.status, revision: t.revision }); }
      identity = await this.identity();
    } catch { failures++; }
    let unchanged = false; try { unchanged = bound(this.manifest); } catch { failures++; }
    this.evidence('safety', { synthetic: true, speechEvidence: false, tags: this.tags, retained,
      filesRetainedInOwnedTEMP: this.files.size, noDeleteIssued: true, draftReceipts: this.receipts,
      requests: this.observations, blocked: this.blocked, pageErrors: this.pageErrors, unexpectedDialogs: this.dialogs,
      pageErrorFacts: this.errorFacts,
      missingTaskCapabilities: this.missingCapabilities,
      designOnly: this.design, unusedDesignWriteBudgets: this.designWrites.size, uncapturedCopyReceipts: this.copyReceipts.size,
      uploadPhaseAttempts: [...this.uploadWrites].map(([file, budget]) => ({ file, chunks: budget.sent.size,
        probe: Number(budget.probe), probeConfirmed: budget.probed, complete: Number(budget.complete) })),
      uncapturedFileReceipts: [...this.fileReceipts.values()].reduce((sum, count) => sum + count, 0),
      teardownFailures: failures, bindingsUnchanged: unchanged, deniedEgress: identity?.deniedEgress,
      providerCalls: identity?.providerCalls, uploadASRCalls: identity?.uploadASRCalls,
      rawBodiesHeadersCookiesURLsDOMRecorded: false });
    invariant(!failures && unchanged && !this.blocked && !this.pageErrors && !this.dialogs && identity?.deniedEgress === 0
      && !identity.providerCalls.unexpected && !identity.providerCalls.handler_error && !identity.uploadASRCalls.unexpected
      && !this.designWrites.size && !this.copyReceipts.size, 'safety invariant failed');
  }
}

export const test = base.extend<{ v2: V2Run; v2NoRawArtifacts: boolean; _setupArtifacts: void; _combinedContextOptions: BrowserContextOptions }>({
  v2NoRawArtifacts: [true, { option: true }],
  // Playwright 1.63 error-context writer runs despite screenshot/video/trace off.
  // Inherits its auto/all-hooks-included lifetime, but never installs recorder.
  _setupArtifacts: async ({ v2NoRawArtifacts }, use) => { invariant(v2NoRawArtifacts, 'no raw artifact collector'); await use(); },
  // In 1.63 that recorder fixture ALSO injects _combinedContextOptions via
  // runBeforeCreateBrowserContext. Its no-op replacement removes the injection.
  // Forward every resolved option explicitly (not only baseURL); keep recording off.
  context: async ({ browser, _combinedContextOptions, actionTimeout, navigationTimeout }, use) => {
    const context = await browser.newContext(_combinedContextOptions);
    context.setDefaultTimeout(actionTimeout);
    context.setDefaultNavigationTimeout(navigationTimeout);
    try { await use(context); } finally { await context.close(); }
  },
  v2: async ({ context }, use, info) => {
    const m = new V2Run(loadManifest(settings().file), context.request, info);
    await m.guard(context);
    try {
      const identity = await m.identity(); invariant(identity.instanceId === m.manifest.instanceId && identity.synthetic, 'host identity');
      // Same genuine service-issued owner cookie for page and API, not a second
      // APIRequestContext with another owner, nor injected unsigned test cookies.
      await m.json('/api/session');
      invariant((await context.cookies(BASE_URL)).some(c => c.httpOnly && c.sameSite === 'Strict'
        && /^[A-Za-z0-9_-]{43}$/.test(c.value)), 'real service-issued development owner cookie required');
      await use(m);
    } finally { await m.finish(); }
  },
  page: async ({ context, v2 }, use) => { void v2; const page = await context.newPage(); try { await use(page); } finally { await page.close(); } },
});

export function responseFor(page: Page, path: string, method = 'POST') {
  return page.waitForResponse(r => new URL(r.url()).origin === BASE_URL && new URL(r.url()).pathname === path
    && r.request().method() === method, { timeout: 180_000 });
}
export async function creator(page: Page, mode: Mode, script = '') {
  await page.goto('/');
  const wizard = page.getByRole('region', { name: '新建新闻作品', exact: true });
  await expect(wizard).toBeVisible();
  await expect(page.getByRole('heading', { name: '写稿', exact: true })).toBeVisible();
  if (mode !== 'voiceover') await page.getByRole('button', { name: /^更多制作方式/ }).click();
  const names = { voiceover: /AI 配音/, mixed: /旁白 \+ 原声/, original: /只用原声/ };
  await wizard.getByRole('group', { name: '制作模式', exact: true }).getByRole('button', { name: names[mode] }).click();
  if (script) await page.getByRole('textbox', { name: '新闻稿（第一行标题，下一行正文）', exact: true }).fill(script);
}
export async function chooseInputs(m: V2Run, page: Page, inputs: InputFile[]) {
  const before = m.files.size; m.admitFiles(inputs.length);
  invariant(inputs.every(i => hashFile(i.path) === i.sha256), 'input drift');
  await page.getByLabel('选择视频或照片', { exact: true }).setInputFiles(inputs.map(i => i.path));
  await expect(page.locator('.gm-media-item')).toHaveCount(before + inputs.length);
  await expect(page.locator('.gm-capacity')).toContainText(`${before + inputs.length} 个已预处理`, { timeout: 180_000 });
  await expect.poll(() => m.files.size).toBe(before + inputs.length);
  invariant(m.tasks.size === 1, 'one draft created at first file, never one per file');
  return [...m.tasks.keys()][0];
}
export async function effects(page: Page) {
  const next = page.getByRole('button', { name: '下一步：选效果', exact: true });
  try { await next.click(); }
  catch (error) {
    // Static classifications/numeric metadata only, never raw DOM or messages.
    const gate = await page.locator('.gm-action-hint').evaluate(e => {
      const text = e.textContent ?? '';
      return { trimInvalid: text.includes('请修正素材裁剪时间'),
        outsideTrim: text.includes('超出所选素材裁剪范围'),
        quoteUnverified: text.includes('未核实的增改'),
        stillProcessing: text.includes('正在') || text.includes('仍在'),
        files: [...document.querySelectorAll<HTMLInputElement>('.gm-trim input')].map(i => ({ value: Number(i.value), max: Number(i.max) })) };
    });
    const target = test.info().outputPath('effects-gate.json');
    makeDirectory(parentPath(target)); writeJSON(target, { ...gate, disabled: await next.isDisabled() });
    throw error;
  }
  for (const name of ['背景音乐', '画面慢慢推近', '新闻台标和片尾板', '开头结尾淡入淡出', '颜色统一', '现场原声降噪']) {
    const control = page.getByRole('switch', { name, exact: true });
    if (await control.getAttribute('aria-checked') === 'true') await control.click();
    await expect(control).toHaveAttribute('aria-checked', 'false');
  }
}
export async function submit(m: V2Run, page: Page, id: string) {
  m.confirmations.set(page, /制作新闻视频/);
  const waiting = responseFor(page, `/api/tasks/${id}/start`);
  await page.getByRole('button', { name: '开始制作', exact: true }).click();
  const response = await waiting;
  if (response.status() !== 202) {
    const rejected = await response.json().catch(() => null) as { detail?: unknown } | null;
    const detail = rejected?.detail;
    const knownMessages = ['Unsupported creation parameters', 'source_voice_preferred must be a boolean',
      'Invalid preferences', 'target_cpm is derived by the server', 'Quality gate cannot weaken server policy',
      'Sentences do not match the full script', 'Sentence punctuation does not match the script boundary',
      'Source range exceeds verified file duration', 'Source marks exceed verified duration',
      'asset_options must match the current file order and count', 'Service is draining',
      'Invalid draft parameters', 'Invalid mode', 'Invalid script', 'Invalid speaker names', 'Invalid speakers',
      'Duplicate speaker identity', 'Invalid speaker identity', 'Invalid speaker name/role',
      'Speaker identity/name max 8 and role max 12 characters', 'Invalid type marks',
      'A headline and nonempty body are required', 'Replace the placeholder headline',
      'A/B script must contain at least 20 characters', 'Unable to parse the supplied script',
      'Invalid sentences', 'Type marks must refer to existing sentence indices',
      'At least one body sentence is required', 'Sentence indices must be contiguous',
      'Voiceover requires narration', 'Original mode requires quotes', 'Source hint belongs to another task',
      'At least one ready file is required', 'Unable to safely align the supplied sentences',
      '裁剪范围必须位于原始素材时长内。', '素材预处理时长校验失败。', '素材备注最多 20 字且不能含控制字符。'];
    const knownCodes = ['local_speech_unavailable', 'local_speech_inference_unavailable', 'quote_missing',
      'files_not_ready', 'accepted_start_uncertain'];
    const code = detail && typeof detail === 'object' && 'code' in detail ? detail.code : undefined;
    m.evidence('start-rejection', { status: response.status(),
      message: knownMessages.find(message => message === detail) ?? null,
      code: knownCodes.find(known => known === code) ?? null,
      detailShape: detail === null ? 'null' : Array.isArray(detail) ? 'array' : typeof detail });
  }
  invariant(response.status() === 202, 'real start accepted');
  const body = response.request().postDataJSON() as Record<string, unknown>;
  invariant(!('upload_ids' in body) && !('upload_tokens' in body) && !('files' in body) && typeof body.script === 'string', 'start only editorial fields');
  invariant(response.request().headers()['x-task-token'] === m.tasks.get(id), 'start uses draft capability');
  const receipt = await response.json() as { task_id: string; access_token?: string };
  invariant(receipt.task_id === id && (!receipt.access_token || receipt.access_token === m.tasks.get(id)), 'same draft identity/capability');
  return body;
}
export async function result(page: Page) {
  // Remain on product navigation: do not inject a receipt or replace canonical URL.
  await expect(page.locator('main')).toHaveAttribute('data-page', 'result', { timeout: 30_000 });
  await expect(page.getByRole('region', { name: '作品结果工作台', exact: true })).toHaveAttribute('aria-busy', 'false');
  await expect(page.getByRole('group', { name: '句子选择', exact: true }).getByRole('button').first()).toBeVisible();
}
export async function selectRow(page: Page, index: number) {
  await page.getByRole('group', { name: '句子选择', exact: true }).getByRole('button').nth(index).click();
  await expect(page.getByRole('region', { name: '选中句子详情', exact: true })).toBeVisible();
}
export async function nativeFrame(page: Page) {
  const video = page.getByLabel('新闻作品成片预览', { exact: true });
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState >= 2 && v.videoWidth > 0)).toBe(true);
  const measured = await video.evaluate(async (v: HTMLVideoElement) => {
    v.muted = true;
    const frame = new Promise<void>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Native decode deadline')), 15_000);
      v.requestVideoFrameCallback(() => { clearTimeout(timer); resolve(); });
    });
    await v.play(); await frame; v.pause();
    return { width: v.videoWidth, height: v.videoHeight, duration: v.duration, frames: v.getVideoPlaybackQuality().totalVideoFrames };
  });
  expect(measured.width).toBe(1920); expect(measured.height).toBe(1080); expect(measured.frames).toBeGreaterThan(0);
  return measured;
}
export async function checks(m: V2Run, page: Page, id: string, revision: number) {
  let gate = await m.json<Checks>(`/api/tasks/${id}/checks`);
  expect(gate.revision).toBe(revision); expect(gate.blocking_count).toBe(0);
  const initial = gate.pending_count; expect(initial).toBeGreaterThan(0);
  const blocked = await m.fetch(`/api/tasks/${id}/exports`, { method: 'POST', data: { expected_revision: revision, fmt: 'mp4', aspect: '16:9', res: '1080p', sub: 'standard' } });
  expect(blocked.status()).toBe(409); await blocked.dispose();
  for (let i = 0; gate.pending_count && i < 80; i++) {
    const controls = page.locator('.gm-rw-qc-summary input[type=checkbox]');
    const index = await controls.evaluateAll(items => items.findIndex(item => !(item as HTMLInputElement).checked));
    invariant(index >= 0, 'real unchecked confirmation');
    const before = gate.pending_count, waiting = responseFor(page, `/api/tasks/${id}/checks`, 'PUT');
    await controls.nth(index).check(); invariant((await waiting).status() === 200, 'PUT check saved');
    gate = await m.json<Checks>(`/api/tasks/${id}/checks`); expect(gate.pending_count).toBe(before - 1);
  }
  expect(gate.passed).toBe(true); expect(gate.pending_count).toBe(0); return initial;
}
export async function exportFile(m: V2Run, page: Page, id: string, revision: number, format: 'mp4' | 'gif' | 'mp3' | 'srt' | 'png') {
  await page.getByRole('button', { name: '导出 / 分享', exact: true }).click();
  const dialog = page.getByRole('dialog', { name: '导出 / 分享', exact: true });
  await expect(dialog).toBeVisible();
  const labels = { mp4: '视频 MP4', gif: '动图 GIF', mp3: '音频 MP3', srt: '字幕 SRT', png: '封面图' };
  await dialog.getByRole('button', { name: labels[format], exact: true }).click();
  if (format === 'mp4') await dialog.getByRole('combobox', { name: '清晰度', exact: true }).selectOption('1080');
  const waiting = responseFor(page, `/api/tasks/${id}/exports`);
  await dialog.getByRole('button', { name: '开始导出', exact: true }).click();
  const response = await waiting; invariant(response.status() === 202, 'actual export submission');
  const receipt = await response.json() as Job; invariant(ID.test(receipt.export_id), 'export id');
  let job: Job | undefined;
  await expect.poll(async () => {
    job = await m.json<Job>(`/api/tasks/${id}/exports/${receipt.export_id}`);
    invariant(!['failed', 'cancelled', 'interrupted'].includes(job.state), 'real export failed'); return job.state;
  }, { timeout: 240_000, intervals: [250, 750, 1500] }).toBe('succeeded');
  invariant(job?.result && job.output_id && job.revision === revision, 'export captured correct revision');
  const link = dialog.getByRole('link', { name: `下载已完成的 ${format.toUpperCase()} 文件（第 ${revision} 版）`, exact: true });
  await expect(link).toBeVisible({ timeout: 30_000 });
  const href = await link.getAttribute('href'); invariant(href, 'real UI download link');
  const url = checkedURL(href), expected = `/api/tasks/${id}/exports/${receipt.export_id}/file`;
  invariant(url.pathname === expected && url.searchParams.get('token') === m.tasks.get(id), 'task capability bound public download');
  const target = m.info.outputPath(`download-${receipt.export_id}.${format}`); makeDirectory(parentPath(target));
  // The actual UI-provided URL is fetched, but only pathname is observed. No raw download artifacts from browser.
  m.observations.push({ method: 'GET', path: expected });
  // Local signature extension keeps the frozen declaration file outside this diagnostic-only slice.
  const downloaded = await (rangeDownload as (...args: [...Parameters<typeof rangeDownload>, ((facts: unknown) => void)?]) => ReturnType<typeof rangeDownload>)(
    m.api, url.href, m.headers(id), target, m.manifest.tools.ffprobe, m.manifest.instanceId, format, job.result.duration,
    facts => m.evidence(`download-${format}-failure`, facts));
  expect(downloaded.bytes).toBe(job.result.bytes);
  if (format === 'mp4') {
    expect(downloaded.media?.width).toBe(1920); expect(downloaded.media?.height).toBe(1080);
    expect(downloaded.media?.videoCodec).toBe('h264'); expect(downloaded.media?.audioCodec).toBe('aac');
    expect(downloaded.media?.audioSampleRate).toBe(48000); expect(downloaded.media?.fps).toBe('30/1');
  } else if (format === 'mp3') expect(downloaded.media?.audioCodec).toBe('mp3');
  else if (format === 'gif') { expect(downloaded.media?.videoCodec).toBe('gif'); expect(downloaded.media!.duration).toBeLessThanOrEqual(6.1); }
  else if (format === 'png') expect(downloaded.media?.videoCodec).toBe('png');
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  m.evidence(`export-${format}`, { revision, ...downloaded, retrievedVia: 'ui-link-with-authenticated-bounded-range', browserDownloadClickCovered: false });
}
export async function layouts(m: V2Run, page: Page, screen: string) {
  // Five primary breakpoints + both edges of the narrower 480/400/380 CSS rules.
  const sizes = [{ width: 1280, height: 900 }, { width: 1000, height: 900 }, { width: 900, height: 900 },
    { width: 600, height: 900 }, { width: 390, height: 844 }, { width: 480, height: 844 },
    { width: 400, height: 844 }, { width: 380, height: 844 }, { width: 320, height: 844 }];
  const toggle = page.getByRole('button', { name: /^(?:大字|常规字号)$/ });
  if (await toggle.getAttribute('aria-pressed') !== 'true') await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  const samples = [];
  for (const size of sizes) {
    await page.setViewportSize(size);
    const geometry = await page.evaluate(() => {
      const root = document.documentElement;
      const outside = [...document.querySelectorAll<HTMLElement>('main *, header *, aside *, dialog[open] *')].filter(e => {
        if (!e.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false;
        const b = e.getBoundingClientRect(); if (!b.width || !b.height || b.left >= -1 && b.right <= root.clientWidth + 1) return false;
        for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) {
          const bounds = p.getBoundingClientRect();
          if (['auto', 'scroll', 'hidden', 'clip'].includes(getComputedStyle(p).overflowX) && bounds.left >= -1 && bounds.right <= root.clientWidth + 1) return false;
        }
        return true;
      }).map(e => { const b = e.getBoundingClientRect(); return { left: b.left, right: b.right, width: b.width }; });
      return { clientWidth: root.clientWidth, scrollWidth: root.scrollWidth, fontSize: getComputedStyle(root).fontSize, outside };
    });
    samples.push({ ...size, geometry });
    expect(geometry.fontSize).toBe('20px'); expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth + 1); expect(geometry.outside).toEqual([]);
  }
  m.evidence(`layout-${screen}`, { screen, samples, screenshots: false, nativeScrollbarCoverage: false });
  await page.setViewportSize({ width: 1280, height: 900 });
}