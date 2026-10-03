import { bytesHash, joinPath, readBytes } from './io.mjs';
import type { Page, Request, Route } from '@playwright/test';
import {
  confirm, decodeMedia, downloadedBytes, expect, noAccountUI, openResult, responseFor, test,
} from './fixture';
import type { Job, ProjectEnvelope, Report, Task } from './fixture';
import { DRAFT_KEY, HISTORY_KEY, invariant } from './environment';

// 6 workflow/security tests + 8 responsive cases. No account or old canary imports.
test('WS-01 first visit opens the three-step creator without login', async ({ workspace: w, page }) => {
  const config = await w.json<{ local_history: boolean; generative_fill_available: boolean }>('/api/config/workspace');
  expect(config).toEqual({ local_history: true, generative_fill_available: false });
  await page.goto('/');
  await expect(page.getByRole('region', { name: '新建新闻作品', exact: true })).toBeVisible();
  const steps = page.getByRole('navigation', { name: '制作步骤', exact: true });
  await expect(steps.getByRole('button')).toHaveCount(3);
  await expect(steps.getByRole('button').nth(0)).toHaveAttribute('aria-current', 'step');
  await expect(page.getByRole('textbox', { name: '新闻稿（第一行标题，下一行正文）', exact: true })).toBeEditable();
  await expect(page.locator('.shell-ready')).toHaveText('制作服务就绪');
  await noAccountUI(page);
  expect(w.observations.some(item => ['POST', 'PUT', 'PATCH', 'DELETE'].includes(item.method))).toBe(false);
});

test('WS-02 wizard upload runs ten real stages then survives history reload and native decode', async ({ workspace: w, page }) => {
  test.setTimeout(660_000);
  await page.goto('/');
  await page.getByRole('textbox', { name: '新闻稿（第一行标题，下一行正文）', exact: true }).fill(w.manifest.script);
  await page.getByRole('button', { name: '下一步：传素材 →', exact: true }).click();
  await expect(page.getByRole('heading', { name: '传素材', exact: true })).toBeVisible();
  // Actual file chooser + files generated in the host's fresh TEMP, not fake uploads.
  const chooser = page.waitForEvent('filechooser');
  await page.getByLabel('选择视频或照片', { exact: true }).click();
  await (await chooser).setFiles(w.manifest.inputs.map(input => input.path));
  await expect(page.getByText('正在读取时长和首帧…', { exact: false })).toHaveCount(0);
  await expect(page.locator('.gm-media-item')).toHaveCount(4);
  await page.getByRole('button', { name: '下一步：选效果 →', exact: true }).click();
  await expect(page.getByRole('heading', { name: '选效果', exact: true })).toBeVisible();
  // Short baseline fixture: explicitly opt out of optional packaging, not QC.
  for (const name of ['背景音乐', '画面慢慢推近', '新闻台标和片尾板', '开头结尾淡入淡出', '颜色统一']) {
    const toggle = page.getByRole('switch', { name, exact: true });
    if (await toggle.getAttribute('aria-checked') === 'true') await toggle.click();
    await expect(toggle).toHaveAttribute('aria-checked', 'false');
  }
  await expect(page.getByRole('switch', { name: 'AI 示意画面补充', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'AI 播音', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await noAccountUI(page);
  const before = await w.json<{ providerCalls: Record<string, number> }>('/api/test/workspace');
  const observedStages = new Set<number>(); let observedProgress = false;
  const observer = async (response: import('@playwright/test').Response) => {
    if (response.request().method() !== 'GET' || !/^\/api\/tasks\/[a-f0-9]{32}$/.test(new URL(response.url()).pathname)) return;
    try {
      const task = await response.json() as Task;
      if (task.status === 'running' && task.progress > 0 && task.progress < 100) observedProgress = true;
      for (const stage of task.stages ?? []) if (stage.status !== 'pending') observedStages.add(stage.number);
    } catch { /* The normal UI must still reach the verified final state. */ }
  };
  page.on('response', observer);
  w.allowOneGeneration();
  const submitted = responseFor(page, '/api/tasks');
  await confirm(page, () => page.getByRole('button', { name: '开始制作', exact: true }).click(), /开始上传并制作新闻视频/);
  const response = await submitted;
  expect(response.status()).toBe(202);
  const receipt = w.receipt(await response.json());
  const id = receipt.task_id;
  const tokenPresentImmediately = typeof receipt.access_token === 'string' && receipt.access_token.length >= 32;
  expect(tokenPresentImmediately).toBe(true); // Boolean assertion only: never token values.
  await expect(page.getByRole('progressbar', { name: '服务器制作进度', exact: true })).toBeVisible({ timeout: 30_000 });
  const completed = await w.completed(id);
  await expect(page.locator('main')).toHaveAttribute('data-page', 'result', { timeout: 30_000 });
  page.off('response', observer);
  expect(completed.progress).toBe(100); expect(completed.stages).toHaveLength(10);
  expect(completed.stages.every(stage => stage.status === 'done' && stage.elapsed_seconds !== null)).toBe(true);
  expect(completed.processing_started_at !== null && completed.processing_completed_at !== null
    && (completed.total_elapsed_seconds ?? 0) > 0).toBe(true);
  expect(observedProgress, 'The browser must receive real intermediate progress, not only a completed seed').toBe(true);
  const report = await w.json<Report>(`/api/tasks/${id}/report`);
  expect(report.rows).toHaveLength(2); expect(report.quality.blocking_issue_count).toBe(0);
  for (const name of Object.keys(w.manifest.seed.artifactHashes)) {
    expect(readBytes(joinPath(w.manifest.taskRoot, id, name)).byteLength > 0, 'Actual new-task artifacts exist').toBe(true);
  }
  const historyHasReceipt = await page.evaluate(({ key, id }) => {
    const value = localStorage.getItem(key);
    // Inspect existence without returning its private capability to an assertion.
    return !!value && value.includes(id);
  }, { key: HISTORY_KEY, id });
  expect(historyHasReceipt).toBe(true);
  await confirm(page, () => page.getByRole('navigation', { name: '工作区导航', exact: true })
    .getByRole('button', { name: '视频历史', exact: true }).click(), /切换页面/);
  await page.reload();
  await expect(page.getByRole('heading', { name: '视频历史', exact: true })).toBeVisible();
  const local = await w.json<{ tasks: { task_id: string }[] }>('/api/tasks');
  expect(local.tasks.some(task => task.task_id === id)).toBe(true);
  // The newest equal-title card is the submitted task, not the initial seed.
  await page.locator('.shell-history-card').filter({ hasText: w.manifest.script.split('\n')[0] }).first().click();
  await expect(page).toHaveURL(`${w.manifest.baseURL}/#work=${id}`);
  await expect(page.getByRole('region', { name: '作品结果工作台', exact: true })).toHaveAttribute('aria-busy', 'false');
  const media = await decodeMedia(page.getByLabel('新闻作品成片预览', { exact: true }));
  expect(media.width).toBe(1920); expect(media.height).toBe(1080);
  const range = await w.fetch(`/api/tasks/${id}/video`, { headers: { Range: 'bytes=0-255' } });
  expect(range.status()).toBe(206); expect((await range.body()).length).toBe(256);
  expect(range.headers()['content-range']).toMatch(/^bytes 0-255\/\d+$/);
  const after = await w.json<{ providerCalls: Record<string, number> }>('/api/test/workspace');
  for (const name of ['vision', 'embedding', 'segmentation', 'llm', 'tts']) {
    expect((after.providerCalls[name] ?? 0) > (before.providerCalls[name] ?? 0), `Real adapter called only loopback fake ${name}`).toBe(true);
  }
  await noAccountUI(page);
  await w.evidence('generation', { synthetic: true, providerMode: 'loopback-deterministic-tone-not-speech',
    tenStages: completed.stages.map(stage => ({ number: stage.number, status: stage.status, elapsedSeconds: stage.elapsed_seconds })),
    observedStageNumbers: [...observedStages].sort((a, b) => a - b),
    intermediateProgressReceived: observedProgress, tokenPresentImmediately, persistedHistory: true,
    realNativeDecode: media, realQCBlockingIssues: report.quality.blocking_issue_count });
});

test('WS-03 no-token sentence edit and remix create recoverable revisions without identity', async ({ workspace: w, page }) => {
  test.setTimeout(480_000);
  const id = await w.duplicate(); await openResult(page, id);
  const before = await w.json<Report>(`/api/tasks/${id}/report`);
  await page.getByRole('textbox', { name: '修改这句话（不换行，最多 2000 字）', exact: true }).fill(w.manifest.editedSentence);
  const edited = responseFor(page, `/api/tasks/${id}/workbench/edit`);
  await page.getByRole('button', { name: '合并提交成片修改', exact: true }).click();
  expect((await edited).status()).toBe(202);
  await w.completed(id, 1);
  await expect(page.locator('main')).toHaveAttribute('data-page', 'result', { timeout: 30_000 });
  await expect(page.getByRole('textbox', { name: '修改这句话（不换行，最多 2000 字）', exact: true })).toHaveValue(w.manifest.editedSentence);
  const report = await w.json<Report>(`/api/tasks/${id}/report`);
  expect(report.rows[0].sentence).toBe(w.manifest.editedSentence);
  expect(report.quality.blocking_issue_count).toBe(0);
  // Explicit no-token local mutation uses Origin. It reuses actual edited media.
  await w.json(`/api/tasks/${id}/remix`, { method: 'POST', data: { keep_sentence_ids: [report.rows[0].sentence_id] } }, 202);
  await w.completed(id, 2); await openResult(page, id);
  const remixed = await w.json<Report>(`/api/tasks/${id}/report`);
  expect(remixed.rows).toHaveLength(1); expect(remixed.rows[0].sentence).toBe(w.manifest.editedSentence);
  expect(remixed.quality.blocking_issue_count).toBe(0);
  await page.getByRole('combobox', { name: '查看作品版本', exact: true }).selectOption('0');
  await expect(page.getByText('历史版本 0 · 只读', { exact: true })).toBeVisible();
  await expect(page.getByRole('group', { name: '句子选择', exact: true }).getByRole('button')).toHaveCount(before.rows.length);
  const restored = responseFor(page, `/api/tasks/${id}/workbench/restore`);
  await confirm(page, () => page.getByRole('button', { name: '恢复这个版本', exact: true }).click(), /恢复为一个新版本/);
  expect((await restored).status()).toBe(202); await w.completed(id, 3);
  await expect(page.locator('main')).toHaveAttribute('data-page', 'result', { timeout: 30_000 });
  const final = await w.json<Report>(`/api/tasks/${id}/report`);
  expect(final.rows.map(row => row.sentence)).toEqual(before.rows.map(row => row.sentence));
  await expect(page.getByRole('combobox', { name: '查看作品版本', exact: true }).getByRole('option')).toHaveCount(4);
  // Deletion through the actual editor, in addition to the explicit /remix API.
  await page.getByRole('group', { name: '句子选择', exact: true }).getByRole('button').nth(1).click();
  await page.getByRole('checkbox', { name: '删除第 2 句', exact: true }).check();
  const deleted = responseFor(page, `/api/tasks/${id}/workbench/edit`);
  await page.getByRole('button', { name: '合并提交成片修改', exact: true }).click();
  expect((await deleted).status()).toBe(202); await w.completed(id, 4);
  await expect(page.locator('main')).toHaveAttribute('data-page', 'result', { timeout: 30_000 });
  const afterDeletion = await w.json<Report>(`/api/tasks/${id}/report`);
  expect(afterDeletion.rows).toHaveLength(1); expect(afterDeletion.rows[0].sentence).toBe(before.rows[0].sentence);
  await expect(page.getByRole('combobox', { name: '查看作品版本', exact: true }).getByRole('option')).toHaveCount(5);
  await noAccountUI(page);
});

test('WS-04 Studio saves and trims a real timeline then renders and downloads validated media', async ({ workspace: w, page }) => {
  test.setTimeout(420_000);
  const id = await w.duplicate(); await openResult(page, id);
  expect((await w.json<Report>(`/api/tasks/${id}/report`)).quality.blocking_issue_count).toBe(0);
  await page.getByRole('button', { name: '专业剪辑台', exact: true }).click();
  const studio = page.getByRole('region', { name: '专业剪辑台', exact: true });
  await expect(studio.getByRole('heading', { name: '专业剪辑台', exact: true })).toBeVisible();
  await expect(studio.getByRole('spinbutton', { name: '源出点（秒）', exact: true })).toBeEnabled();
  await studio.getByRole('combobox', { name: '选择预览', exact: true }).selectOption('source:final');
  await expect.poll(() => studio.locator('[data-studio-section="preview"] video').evaluate((video: HTMLVideoElement) =>
    video.readyState >= 1 && Number.isFinite(video.duration) && video.duration > 2)).toBe(true);
  await studio.getByRole('spinbutton', { name: '源入点（秒）', exact: true }).fill('0');
  await studio.getByRole('spinbutton', { name: '源出点（秒）', exact: true }).fill('2');
  await studio.getByRole('button', { name: '＋ 加入所选素材范围', exact: true }).click();
  const savedResponse = responseFor(page, `/api/tasks/${id}/studio/project`);
  await studio.getByRole('button', { name: '保存工程', exact: true }).click();
  expect((await savedResponse).status()).toBe(200);
  const saved = await w.json<ProjectEnvelope>(`/api/tasks/${id}/studio/project`);
  const clip = saved.project.tracks.flatMap(track => track.clips).find(item => item.source_id === 'final');
  invariant(clip && clip.duration === 2, 'actual saved final-source timeline');
  await studio.getByRole('combobox', { name: '选择片段', exact: true }).selectOption(clip.id);
  await studio.getByRole('combobox', { name: '修剪模式', exact: true }).selectOption('ordinary');
  await studio.getByRole('spinbutton', { name: '修剪源入点（秒）', exact: true }).fill('0.2');
  await studio.getByRole('spinbutton', { name: '修剪输出时长（秒）', exact: true }).fill('1.5');
  const trimmedResponse = responseFor(page, `/api/tasks/${id}/studio/actions`);
  await confirm(page, () => studio.locator('[data-studio-section="trim-modes"] [data-action="trim"]').click(), /普通修剪/);
  expect((await trimmedResponse).status()).toBe(200);
  const trimmed = await w.json<ProjectEnvelope>(`/api/tasks/${id}/studio/project`);
  expect(trimmed.revision).toBeGreaterThan(saved.revision);
  const persisted = trimmed.project.tracks.flatMap(track => track.clips).find(item => item.id === clip.id);
  expect(persisted?.trim).toBe(0.2); expect(persisted?.duration).toBe(1.5);
  await studio.getByRole('combobox', { name: '导出分辨率', exact: true }).selectOption('360');
  const submitted = responseFor(page, `/api/tasks/${id}/studio/render`);
  await studio.getByRole('button', { name: '渲染时间线预览 · MP4', exact: true }).click();
  const response = await submitted; expect(response.status()).toBe(202);
  const receipt = await response.json() as Job;
  const job = await w.job(id, receipt.id);
  expect(job.revision).toBe(trimmed.revision); expect(job.pipeline_revision).toBe(0);
  expect(Math.abs(job.result!.duration - 1.5)).toBeLessThan(0.15);
  await studio.getByRole('button', { name: '查看已完成输出', exact: true }).click();
  const media = await decodeMedia(studio.locator('[data-studio-section="preview"] video'), 1.5);
  expect(media.width).toBe(640); expect(media.height).toBe(360);
  const output = await w.fetch(`/api/tasks/${id}/studio/outputs/${job.output_id}`);
  expect(output.status()).toBe(200);
  const bytes = await output.body(); invariant(bytes.length <= 32 * 1024 * 1024, 'bounded short export');
  expect(bytes.length).toBe(job.result!.bytes);
  const digest = bytesHash(bytes);
  const download = await downloadedBytes(page, studio.getByRole('link', { name: '下载 MP4', exact: true }), digest);
  await noAccountUI(page);
  await w.evidence('studio-media', { savedRevision: saved.revision, trimmedRevision: trimmed.revision,
    trim: persisted?.trim, duration: persisted?.duration, nativeDecoded: media, download,
    qcRequiredAndUnpatched: true, sourcePipelineRevision: job.pipeline_revision, derivedOutputNotPipelineQC: true });
});

test('WS-05 local history and task access reject cross-site and forwarded authority', async ({ workspace: w }) => {
  const id = await w.duplicate();
  const list = await w.json<{ tasks: Record<string, unknown>[] }>('/api/tasks');
  expect(list.tasks.some(task => task.task_id === id)).toBe(true);
  expect(list.tasks.some(task => ['access_token', 'access_token_hash', 'token'].some(key => key in task))).toBe(false);
  const invalidHeaders: Record<string, string>[] = [
    { Origin: 'https://cross-site.invalid' }, { 'Sec-Fetch-Site': 'cross-site' },
    { Forwarded: 'for=203.0.113.1' }, { 'X-Forwarded-For': '127.0.0.1' },
    { 'X-Real-IP': '127.0.0.1' }, { 'X-Forwarded-Host': '127.0.0.1:8782' },
  ];
  for (const headers of invalidHeaders) {
    expect((await w.fetch('/api/tasks', { headers })).status()).toBe(404);
    expect((await w.fetch(`/api/tasks/${id}`, { headers })).status()).toBe(404);
    const config = await w.json<{ local_history: boolean }>('/api/config/workspace', { headers });
    expect(config.local_history).toBe(false);
  }
  expect((await w.fetch(`/api/tasks/${id}`, { headers: { 'X-Task-Token': 'invalid-synthetic-capability' } })).status()).toBe(404);
  expect((await w.fetch(`/api/tasks/${id}?token=`, {})).status()).toBe(404);
  // Explicit empty Origin is not equivalent to the matching origin. To omit the
  // header entirely, use this context with its exact same URL and redirect cap.
  const absent = await w.api.post(`${w.manifest.baseURL}/api/tasks/${id}/remix`, {
    data: { keep_sentence_ids: [0] }, maxRedirects: 0,
  });
  expect([403, 404]).toContain(absent.status());
  const foreign = await w.fetch(`/api/tasks/${id}/remix`, { method: 'POST',
    data: { keep_sentence_ids: [0] }, headers: { Origin: 'https://cross-site.invalid' } });
  expect(foreign.status()).toBe(403);
  const still = await w.status(id); expect(still.status).toBe('done'); expect(still.revision).toBe(0);
  // Never make a real remote connection to test remote identity.
  await w.evidence('local-authority', { deniedHeaderCases: invalidHeaders.length,
    noTokenLocalRead: true, explicitBadCapabilityRejected: true, noOriginMutationRejected: true,
    foreignOriginMutationRejected: true, realRemotePeerTest: false });
});

async function crossDebounce(page: Page) {
  // JS draft timer only; media playback clocks are never accelerated.
  await page.clock.runFor(1600);
}

test('WS-06 persisted draft survives reload and a held cold deep-link without unsolicited writes', async ({ workspace: w, page }) => {
  // Install before app timers, but leave time running during file chooser / UI
  // actions. Pause only after the final click, avoiding frozen RAF actionability.
  const epoch = Date.now(); await page.clock.install({ time: epoch - 60_000 });
  await page.goto('/');
  await page.getByRole('textbox', { name: '新闻稿（第一行标题，下一行正文）', exact: true }).fill(w.manifest.script);
  await page.getByRole('button', { name: '下一步：传素材 →', exact: true }).click();
  const chooser = page.waitForEvent('filechooser'); await page.getByLabel('选择视频或照片', { exact: true }).click();
  await (await chooser).setFiles(w.manifest.inputs.map(input => input.path));
  await expect(page.getByRole('button', { name: '下一步：选效果 →', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '下一步：选效果 →', exact: true }).click();
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await page.clock.pauseAt(epoch + 60 * 60_000);
  await crossDebounce(page);
  const canonical = await page.evaluate(key => localStorage.getItem(key), DRAFT_KEY);
  invariant(canonical && JSON.parse(canonical).step === 3, 'complete real step-three draft');
  // Warm restore must retain only metadata, not pretend to possess uploaded files.
  await page.reload();
  await expect(page.getByRole('heading', { name: '传素材', exact: true })).toBeVisible();
  await expect(page.getByText('已恢复文件清单，但没有保存文件内容。', { exact: false })).toBeVisible();
  await expect(page.getByRole('button', { name: '下一步：选效果 →', exact: true })).toBeDisabled();
  expect(await page.evaluate(key => localStorage.getItem(key), DRAFT_KEY) === canonical).toBe(true);
  await crossDebounce(page);
  // Observe real Storage methods on the next cold document; do not fake values.
  // Even an unsolicited byte-identical write must fail this regression test.
  await page.addInitScript(key => {
    const target = window as Window & { workspaceDraftWrites?: string[] };
    target.workspaceDraftWrites = [];
    const set = Storage.prototype.setItem; const remove = Storage.prototype.removeItem;
    const clear = Storage.prototype.clear;
    Storage.prototype.setItem = function (name: string, value: string) {
      if (this === localStorage && name === key) target.workspaceDraftWrites!.push('set');
      return set.call(this, name, value);
    };
    Storage.prototype.removeItem = function (name: string) {
      if (this === localStorage && name === key) target.workspaceDraftWrites!.push('remove');
      return remove.call(this, name);
    };
    Storage.prototype.clear = function () {
      if (this === localStorage) target.workspaceDraftWrites!.push('clear');
      return clear.call(this);
    };
  }, DRAFT_KEY);
  const noDraftWrite = async () => expect(await page.evaluate(() =>
    (window as Window & { workspaceDraftWrites?: string[] }).workspaceDraftWrites)).toEqual([]);
  const id = w.manifest.seed.taskId;
  let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve; });
  let hit!: (request: Request) => void; const firstHit = new Promise<Request>(resolve => { hit = resolve; });
  let held = false; let forward: Promise<void> | undefined;
  const matcher = (url: URL) => url.origin === w.manifest.baseURL && url.pathname === `/api/tasks/${id}`;
  const handler = async (route: Route) => {
    if (held || route.request().method() !== 'GET') { await route.fallback(); return; }
    held = true; forward = gate.then(() => route.fallback()); hit(route.request()); await forward;
  };
  await page.route(matcher, handler);
  try {
    const [, request] = await Promise.all([page.goto(`/?workspace-cold-link=1#work=${id}`, { waitUntil: 'domcontentloaded' }), firstHit]);
    await expect(page.getByRole('heading', { name: '正在读取作品', exact: true })).toBeVisible();
    await expect(page.getByRole('region', { name: '新建新闻作品', exact: true, includeHidden: true })).toHaveCount(0);
    await crossDebounce(page);
    expect(await page.evaluate(key => localStorage.getItem(key), DRAFT_KEY) === canonical).toBe(true);
    await noDraftWrite();
    expect(w.observations.some(item => ['POST', 'PUT', 'PATCH', 'DELETE'].includes(item.method))).toBe(false);
    // Arm only after the held-state assertions, immediately before release.
    const response = page.waitForResponse(response => response.request() === request);
    release(); expect((await response).status()).toBe(200); await forward;
    await expect(page.locator('main')).toHaveAttribute('data-page', 'result');
    await expect(page.getByRole('region', { name: '作品结果工作台', exact: true })).toHaveAttribute('aria-busy', 'false');
    await crossDebounce(page);
    expect(await page.evaluate(key => localStorage.getItem(key), DRAFT_KEY) === canonical).toBe(true);
    await noDraftWrite();
    expect(w.observations.some(item => ['POST', 'PUT', 'PATCH', 'DELETE'].includes(item.method))).toBe(false);
    await noAccountUI(page);
  } finally { release(); await forward; await page.unroute(matcher, handler); }
});

async function layout(page: Page) {
  return page.evaluate(() => {
    const root = document.documentElement;
    const visible = [...document.querySelectorAll<HTMLElement>('main *, header *, aside *')]
      .filter(element => element.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true }));
    // A local scroll container is allowed; it must not widen the document.
    const outside = visible.filter(element => {
      if (element.closest('[hidden], dialog:not([open])')) return false;
      const box = element.getBoundingClientRect(); if (!box.width || !box.height) return false;
      if (box.left >= -1 && box.right <= root.clientWidth + 1) return false;
      let ancestor: HTMLElement | null = element.parentElement;
      while (ancestor && ancestor !== document.body) {
        const style = getComputedStyle(ancestor);
        if (['auto', 'scroll', 'hidden', 'clip'].includes(style.overflowX)
          && ancestor.getBoundingClientRect().right <= root.clientWidth + 1) return false;
        ancestor = ancestor.parentElement;
      }
      return true;
    }).map(element => ({ tag: element.tagName, role: element.getAttribute('role'), className: String(element.className).slice(0, 100) }));
    return { clientWidth: root.clientWidth, scrollWidth: root.scrollWidth,
      fontSize: getComputedStyle(root).fontSize, visibleOverflow: outside };
  });
}

for (const width of [320, 390, 768, 1440]) for (const font of [16, 20]) {
  test(`WS-LAYOUT-${width}-${font} visible layout, draft steps, history, result and Studio reflow`, async ({ workspace: w, page }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width, height: 1000 });
    await page.addInitScript(size => localStorage.setItem('golden-mic.font-size', String(size)), font);
    await page.goto('/');
    await page.getByRole('textbox', { name: '新闻稿（第一行标题，下一行正文）', exact: true }).fill(w.manifest.script);
    const samples: { screen: string; geometry: Awaited<ReturnType<typeof layout>> }[] = [];
    const check = async (screen: string) => {
      const geometry = await layout(page);
      samples.push({ screen, geometry });
      expect(geometry.fontSize).toBe(`${font}px`);
      expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth + 1);
      expect(geometry.visibleOverflow).toEqual([]);
      await noAccountUI(page);
    };
    await check('creator-step-1-open-evidence');
    await page.locator('.gm-evidence-details > summary').click();
    await check('creator-step-1-closed-evidence');
    await page.getByRole('button', { name: '下一步：传素材 →', exact: true }).click();
    const chooser = page.waitForEvent('filechooser'); await page.getByLabel('选择视频或照片', { exact: true }).click();
    await (await chooser).setFiles(w.manifest.inputs.map(input => input.path));
    await expect(page.getByRole('button', { name: '下一步：选效果 →', exact: true })).toBeEnabled();
    await check('creator-step-2-real-file-metadata');
    await page.getByRole('button', { name: '下一步：选效果 →', exact: true }).click();
    await check('creator-step-3-effects');
    await page.getByRole('button', { name: '保存草稿', exact: true }).click();
    await page.goto('/?workspace-history=1#history');
    await expect(page.getByRole('heading', { name: '视频历史', exact: true })).toBeVisible();
    await expect(page.locator('.shell-history-card').first()).toBeVisible();
    await check('history');
    await openResult(page, w.manifest.seed.taskId);
    await check('result');
    await page.getByRole('button', { name: '专业剪辑台', exact: true }).click();
    const studio = page.getByRole('region', { name: '专业剪辑台', exact: true });
    await expect(studio.getByRole('combobox', { name: '目标轨道', exact: true })).toBeVisible();
    await check('studio');
    // Read-only result/Studio opening; no creation, saves or render side effects.
    expect(w.observations.some(item => ['POST', 'PUT', 'PATCH', 'DELETE'].includes(item.method))).toBe(false);
    await w.evidence('responsive', { viewport: { width, height: 1000 }, font,
      ignoresOnlyActuallyHiddenElements: true, nativeScrollbarCoverageClaimed: false, samples });
  });
}