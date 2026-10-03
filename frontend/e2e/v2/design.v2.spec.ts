import type { BrowserContext, Page } from '@playwright/test';
import { env } from '../modes/io.mjs';
import { BASE_URL, bound, loadManifest, settings } from './environment';
import { chooseInputs, creator, DRAFT_KEY, effects, expect, invariant, nativeFrame,
  responseFor, result, selectRow, submit, test, V2Run } from './support';
import type { Task } from './support';

// AUTHORING ONLY until the parent runs this explicit suite once against a fresh
// current-source-bound host/build. Real service/FFmpeg, synthetic ASR/tone TTS;
// neither speech quality, real microphone permissions nor browser 410 is claimed.
// Serial shared disposable context deliberately spends ONE short-A generation.
// No fixture injection, route fulfillment, synthetic HTTP failures or TTL edits.
const SCRIPT = '合成色卡配音验收样片\n色块画面缓缓移动。\n测试图案清晰可见。';
// The start contract inserts one blank headline/body separator before saving
// creation_inputs. Recovery must preserve that submitted snapshot exactly,
// not the pre-submit textarea or an unapplied workbench edit.
const SUBMITTED_SCRIPT = '合成色卡配音验收样片\n\n色块画面缓缓移动。\n测试图案清晰可见。';
const RENAMED = '设计验收独立名称';
const EDIT = '色块画面继续缓缓移动。';
type Metadata = Task & { title: string; metadata_revision: number; version_count: number;
  expires_at: string; updated_at: string; created_at: string };
type Recovered = { task_id: string; access_token: string; status: string; accepted: boolean;
  mode: string; step: number; script: string; source_voice_preferred: boolean;
  recovery: { source_task_id: string; task_id: string };
  files: { file_id: string; up_id: string; token: string; access_token: string; sha256: string; status: string }[] };
let context: BrowserContext, page: Page, m: V2Run, source: string;
let r0: Record<string, string>, baseline: Metadata;
const writes = () => m.observations.filter(r => !['GET', 'HEAD', 'OPTIONS'].includes(r.method));
const scriptBox = () => page.getByRole('textbox', { name: '新闻稿（第一行标题，下一行正文）', exact: true });
const pending = () => page.getByRole('region', { name: '待保存修改', exact: true });
const metadata = (id: string) => m.json<Metadata>(`/api/tasks/${id}`);
const pendingSlot = () => page.evaluate(({ key, id }) =>
  JSON.stringify(JSON.parse(localStorage.getItem(key) ?? '{}').pend?.resultV2?.[id] ?? null), { key: DRAFT_KEY, id: source });
async function capabilityStored(id: string) {
  const token = m.tasks.get(id); invariant(token, 'captured capability required');
  // Return only a boolean, never storage/capabilities in assertion output.
  return page.evaluate(({ id, token }) => {
    const rows = JSON.parse(localStorage.getItem('golden-mic.history.v1') ?? '[]');
    return rows.some((r: { taskId: string; accessToken: string }) => r.taskId === id && r.accessToken === token);
  }, { id, token });
}
async function historyMatches(value: Metadata) {
  const row = page.locator('.shell-sidebar .shell-recent button[aria-current=true]');
  await expect(row).toHaveCount(1);
  await expect(row.locator('strong')).toHaveText(value.title);
  await expect(row).toContainText(`改了 ${value.version_count - 1} 次`);
  await expect(row.locator('.shell-work-expiry time')).toHaveAttribute('datetime', value.expires_at);
  const gate = await m.json<{ revision: number; blocking_count: number; pending_count: number; passed: boolean;
    checks: { key: string; level: number; checked: boolean; confirmable?: boolean; code: string; message: string }[] }>(`/api/tasks/${value.task_id}/checks`);
  expect(gate.revision).toBe(value.revision);
  for (const count of [gate.blocking_count, gate.pending_count]) {
    expect(Number.isSafeInteger(count) && count >= 0).toBe(true);
  }
  expect(typeof gate.passed).toBe('boolean');
  const label = gate.blocking_count > 0 ? `待修改 ${gate.blocking_count} 处`
    : gate.pending_count > 0 ? `待确认 ${gate.pending_count} 项`
      : gate.passed ? '检查通过' : '发布前检查尚未读取';
  await expect(row).toContainText(label);
  // Compare the full existing gate list too, not an optimistic report-derived count.
  await expect(page.locator('.gm-rw-check-list .gm-rw-check-item')).toHaveCount(gate.checks.length);
  for (let i = 0; i < gate.checks.length; i++) {
    await expect(page.locator('.gm-rw-check-item').nth(i).locator('span').first()).toHaveText(gate.checks[i].message);
  }
  const confirmations = gate.checks.filter(c => c.level === 1 && c.confirmable !== false
    || c.level === 0 && c.code === 'generated_media' && c.confirmable === true);
  const boxes = page.locator('.gm-rw-check-list input[type=checkbox]');
  await expect(boxes).toHaveCount(confirmations.length);
  for (let i = 0; i < confirmations.length; i++) await expect(boxes.nth(i)).toHaveJSProperty('checked', confirmations[i].checked);
}
async function unchangedSource() {
  const now = await metadata(source);
  expect(now.revision).toBe(0); expect(now.version_count).toBe(baseline.version_count);
  expect(now.expires_at).toBe(baseline.expires_at); expect(now.updated_at).toBe(baseline.updated_at);
  expect(now.created_at).toBe(baseline.created_at); expect(m.hashes(source)).toEqual(r0);
  expect(await capabilityStored(source)).toBe(true);
}

test.describe('current design: one owned short-A lifecycle', () => {
  test.describe.configure({ mode: 'serial' });
  test.beforeAll(async ({ browser }, info) => {
    invariant(env.V2_DESIGN_ONLY === '1' && env.V2_NAV_ONLY !== '1', 'explicit design suite required');
    const manifest = loadManifest(settings().file);
    invariant(bound(manifest) && manifest.testHashes['frontend/e2e/v2/design.v2.spec.ts'], 'new design source must be host-bound');
    // _setupArtifacts remains disabled by support.ts. This shared context must
    // explicitly carry options normally supplied by the test-scoped context.
    context = await browser.newContext({ baseURL: BASE_URL, locale: 'zh-CN', viewport: { width: 1280, height: 900 },
      serviceWorkers: 'block', acceptDownloads: false, permissions: [] });
    context.setDefaultTimeout(20_000); context.setDefaultNavigationTimeout(30_000);
    m = new V2Run(manifest, context.request, info); await m.guard(context);
    const identity = await m.identity(); invariant(identity.synthetic && identity.instanceId === manifest.instanceId, 'real isolated host');
    await m.json('/api/session');
    invariant((await context.cookies(BASE_URL)).some(c => c.httpOnly && c.sameSite === 'Strict'
      && /^[A-Za-z0-9_-]{43}$/.test(c.value)), 'service-issued owner cookie');
    await context.addInitScript(() => {
      // Deny before any device/permission request, count attempts. Zero is the
      // assertion; no fake recording stream and no human permission prompt.
      Object.defineProperty(globalThis, '__vdMicAttempts', { value: 0, writable: true });
      if (navigator.mediaDevices) Object.defineProperty(navigator.mediaDevices, 'getUserMedia', { value: () => {
        const scope = globalThis as unknown as { __vdMicAttempts: number }; scope.__vdMicAttempts++;
        return Promise.reject(new Error('Design acceptance never admits microphone access'));
      } });
    });
    page = await context.newPage();
  });
  test.afterAll(async () => {
    // Keep every owned task and file for the parent's final integrity receipt.
    try { if (m) await m.finish(); } finally { if (context) await context.close(); }
  });

  test('VD-01 native keyboard mode fold and C selection preserve manuscript without writes', async () => {
    await creator(page, 'voiceover', SCRIPT);
    const cards = page.getByRole('group', { name: '制作模式', exact: true });
    const fold = page.locator('.gm-mode-more');
    await expect(cards.getByRole('button')).toHaveCount(1);
    const before = writes().length;
    const taskGETs = () => m.observations.filter(r => r.method === 'GET' && r.path.startsWith('/api/tasks/')).length;
    const getsBefore = taskGETs();
    for (const expanded of [true, false, true]) {
      await fold.focus(); await fold.press('Space');
      await expect(fold).toHaveAttribute('aria-expanded', String(expanded));
      await expect(cards.getByRole('button')).toHaveCount(expanded ? 3 : 1);
      await expect(scriptBox()).toHaveValue(SCRIPT);
    }
    const c = cards.getByRole('button', { name: /只用原声/ });
    await c.focus(); await c.press('Space'); await expect(c).toHaveAttribute('aria-pressed', 'true');
    await expect(fold).toHaveCount(0); // Current contract: C keeps all choices visible, no C fold button.
    await expect(cards.getByRole('button')).toHaveCount(3); await expect(scriptBox()).toHaveValue(SCRIPT);
    await cards.getByRole('button', { name: /AI 配音/ }).press('Space');
    await fold.press('Space'); await expect(cards.getByRole('button')).toHaveCount(1);
    await fold.press('Space'); await expect(cards.getByRole('button')).toHaveCount(3);
    expect(writes()).toHaveLength(before); expect(m.tasks.size).toBe(0);
    expect(taskGETs()).toBe(getsBefore);
    m.evidence('design-fold', { taskGETsBefore: getsBefore, taskGETsAfter: taskGETs(), writes: 0, nativeSpace: true });
    m.tag('design-native-space-fold-no-api-write');
  });

  test('VD-02 single mine-intent A generation, dirty rename CAS, native dialog reflow and read-only refresh', async () => {
    await page.getByRole('button', { name: '下一步：传素材', exact: true }).click();
    source = await chooseInputs(m, page, m.manifest.inputs.filter(i => i.kind === 'broll'));
    await effects(page);
    await page.getByRole('button', { name: '我自己读', exact: true }).click();
    const providers = await m.identity(); await m.admitDesignWrite(source, 'start');
    const sent = await submit(m, page, source);
    invariant((sent.preferences as { voice?: string }).voice === 'mine', 'actual start preserves mine intent');
    const done = await m.done(source, 0); expect(done.stages).toHaveLength(10);
    expect(done.stages.every(s => s.status === 'done')).toBe(true); await result(page);
    const report = await m.report(source); expect(report.rows).toHaveLength(2);
    expect(report.rows.every(r => r.kind === 'narration' && r.audio_kind === 'tts')).toBe(true);
    await expect(page.locator('.gm-rw-own-voice')).toContainText('初版先用 AI 配音');
    await expect(page.locator('.gm-rw-own-voice')).toContainText('不会自动开启麦克风');
    expect(await page.evaluate(() => (globalThis as unknown as { __vdMicAttempts: number }).__vdMicAttempts)).toBe(0);
    const decoded = await nativeFrame(page); expect(decoded.duration).toBeLessThan(30);
    const generated = await m.identity(); expect(generated.providerCalls.tts ?? 0).toBeGreaterThan(providers.providerCalls.tts ?? 0);
    expect(generated.uploadASRCalls).toEqual(providers.uploadASRCalls);
    baseline = await metadata(source); invariant(baseline.version_count >= 1 && !!baseline.expires_at, 'actual metadata required');
    r0 = m.hashes(source); await historyMatches(baseline);
    await selectRow(page, 0); await page.getByRole('button', { name: '改字', exact: true }).click();
    await page.getByRole('textbox', { name: '改这句话', exact: true }).fill(EDIT);
    await page.getByRole('button', { name: '记下修改', exact: true }).click(); await expect(pending()).toBeVisible();
    await expect.poll(async () => (await pendingSlot()).includes(EDIT)).toBe(true);
    const savedPending = await pendingSlot(), beforeRename = writes().length;
    await page.getByRole('button', { name: '大字', exact: true }).click();
    const rename = page.getByRole('button', { name: '重命名', exact: true });
    const dialog = page.getByRole('dialog', { name: '重命名作品', exact: true });
    const layout = [];
    for (const width of [320, 900]) {
      await page.setViewportSize({ width, height: 900 }); await rename.click();
      await expect(dialog).toBeVisible();
      await expect(dialog.getByRole('textbox')).toBeFocused();
      for (let tab = 0; tab < 4; tab++) {
        await page.keyboard.press('Tab');
        expect(await dialog.evaluate(e => e.contains(document.activeElement))).toBe(true);
      }
      await dialog.getByRole('textbox').fill('尚未保存的名称');
      const geometry = await dialog.evaluate(e => {
        const root = document.documentElement, box = e.getBoundingClientRect();
        return { left: box.left, right: box.right, clientWidth: root.clientWidth, scrollWidth: root.scrollWidth,
          fontSize: getComputedStyle(root).fontSize, modal: e.matches(':modal'),
          overflow: [...e.querySelectorAll<HTMLElement>('input,button,p,label')].filter(n => n.checkVisibility())
            .some(n => { const b = n.getBoundingClientRect(); return b.left < -1 || b.right > root.clientWidth + 1; }) };
      });
      layout.push({ width, ...geometry }); expect(geometry.modal).toBe(true); expect(geometry.fontSize).toBe('20px');
      expect(geometry.left).toBeGreaterThanOrEqual(0); expect(geometry.right).toBeLessThanOrEqual(geometry.clientWidth + 1);
      expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth + 1); expect(geometry.overflow).toBe(false);
      await dialog.press('Escape'); await expect(dialog).toHaveCount(0); await expect(rename).toBeFocused();
      expect(await pendingSlot()).toBe(savedPending); expect(writes()).toHaveLength(beforeRename);
    }
    await page.setViewportSize({ width: 1280, height: 900 }); await rename.click();
    const body = { expected_revision: baseline.revision, expected_metadata_revision: baseline.metadata_revision, title: RENAMED };
    await dialog.getByRole('textbox').fill(' '); await expect(dialog.getByRole('button', { name: '保存名称', exact: true })).toBeDisabled();
    await dialog.getByRole('textbox').fill('题'.repeat(41)); await expect(dialog.getByRole('button', { name: '保存名称', exact: true })).toBeDisabled();
    await dialog.getByRole('textbox').fill(RENAMED); await m.admitDesignWrite(source, 'metadata', body);
    const waiting = responseFor(page, `/api/tasks/${source}/metadata`, 'PATCH'); void waiting.catch(() => {});
    await dialog.getByRole('button', { name: '保存名称', exact: true }).click();
    const response = await waiting; expect(response.status()).toBe(200); expect(response.request().postDataJSON()).toEqual(body);
    expect(await response.json()).toEqual({ task_id: source, title: RENAMED, revision: 0, metadata_revision: baseline.metadata_revision + 1 });
    await expect(dialog).toHaveCount(0); await expect(page.getByRole('heading', { name: RENAMED, exact: true })).toBeVisible();
    await expect(pending()).toBeVisible(); expect(await pendingSlot()).toBe(savedPending);
    await historyMatches({ ...baseline, title: RENAMED }); await unchangedSource();
    // Real stale CAS conflict, not a fulfilled browser response or second rename.
    const stale = { ...body, title: '不能覆盖的新名称' }; await m.admitDesignWrite(source, 'metadata', stale);
    const conflict = await m.json<{ detail: { code: string } }>(`/api/tasks/${source}/metadata`, { method: 'PATCH', data: stale }, 409);
    expect(conflict.detail.code).toBe('stale_metadata'); expect((await metadata(source)).title).toBe(RENAMED);
    const beforeRefresh = writes().length, gets = m.observations.filter(r => r.method === 'GET' && r.path === `/api/tasks/${source}`).length;
    await page.reload(); await result(page); await expect(pending()).toBeVisible();
    expect(await pendingSlot()).toBe(savedPending); expect(writes()).toHaveLength(beforeRefresh);
    expect(m.observations.filter(r => r.method === 'GET' && r.path === `/api/tasks/${source}`).length).toBeGreaterThan(gets);
    await historyMatches({ ...baseline, title: RENAMED }); await unchangedSource();
    const final = await m.identity(); expect(final.providerCalls).toEqual(generated.providerCalls); expect(final.uploadASRCalls).toEqual(generated.uploadASRCalls);
    m.evidence('design-rename', { layout, decoded, immutableR0: r0, singleGeneration: true, casConflict: 409,
      metadataWrites: 2, committedRenames: 1, refreshWrites: 0, pendingPreserved: true, microphoneAccessAttempted: false });
  });

  test('VD-03 exact keep-copy and C recover-draft preserve capabilities, bindings and source without ASR/start', async () => {
    const before = await m.identity(), savedPending = await pendingSlot();
    // ResultWorkbench's dirty-editor departure confirm precedes native copy UI.
    m.confirmations.set(page, /^还有待应用修改。修改会保留/);
    await page.getByRole('button', { name: '复制一份', exact: true }).click();
    const choice = page.getByRole('dialog', { name: '复制一份', exact: true });
    await expect(choice.getByRole('radio', { name: '保持当前方式', exact: true })).toBeChecked();
    await choice.getByRole('button', { name: '继续', exact: true }).click();
    const confirm = page.getByRole('dialog', { name: '创建独立副本？', exact: true });
    await expect(confirm).toContainText('未保存的结果修改');
    // Cancel is read-only and focus-contained; reopen for the one admitted copy.
    const beforeCancel = writes().length; await confirm.press('Escape'); await expect(confirm).toHaveCount(0);
    expect(writes()).toHaveLength(beforeCancel);
    m.confirmations.set(page, /^还有待应用修改。修改会保留/);
    await page.getByRole('button', { name: '复制一份', exact: true }).click();
    await choice.getByRole('button', { name: '继续', exact: true }).click();
    await m.admitDesignWrite(source, 'duplicate', { expected_revision: 0 });
    const copied = responseFor(page, `/api/tasks/${source}/duplicate`); void copied.catch(() => {});
    await confirm.getByRole('button', { name: '确认复制', exact: true }).click();
    const copyResponse = await copied; expect(copyResponse.status()).toBe(201);
    const copy = await copyResponse.json() as { task_id: string };
    await expect.poll(() => m.tasks.has(copy.task_id)).toBe(true); await result(page);
    invariant(copy.task_id !== source, 'independent completed copy'); expect(await capabilityStored(copy.task_id)).toBe(true);
    const copyStatus = await m.status(copy.task_id);
    expect(copyStatus.status).toBe('done'); expect(copyStatus.mode).toBe('voiceover'); expect(copyStatus.revision).toBe(0);
    // Public media URLs correctly rebind to the copy. Compare saved editorial
    // content, not task-scoped URL strings; independently compare media hashes.
    const projectRows = (rows: Awaited<ReturnType<V2Run['report']>>['rows']) => rows.map(r => ({
      sentence_id: r.sentence_id, sentence: r.sentence, spoken_text: r.spoken_text,
      kind: r.kind, audio_kind: r.audio_kind, duration: r.duration,
    }));
    expect(projectRows((await m.report(copy.task_id)).rows)).toEqual(projectRows((await m.report(source)).rows));
    const copyHashes = m.hashes(copy.task_id); expect(copyHashes['final.mp4']).toBe(r0['final.mp4']);
    expect(await pendingSlot()).toBe(savedPending); await unchangedSource();
    await page.goto(`/tasks/${source}`); await result(page); await expect(pending()).toBeVisible();
    m.confirmations.set(page, /^还有待应用修改。修改会保留/);
    await page.getByRole('button', { name: '复制一份', exact: true }).click();
    await choice.getByRole('radio', { name: /^改为 C/ }).check();
    const originalDraft = await m.json<{ script: string; source_voice_preferred: boolean; files: { file_id: string; name: string; bytes: number }[] }>(`/api/tasks/${source}/draft`);
    expect(originalDraft.script).toBe(SUBMITTED_SCRIPT);
    const sourceFiles = [...m.files.entries()].filter(([, f]) => f.task === source);
    await m.admitDesignWrite(source, 'recover-draft', { step: 1, mode: 'original' });
    m.confirmations.set(page, /^用这条任务提交时的原稿和素材清单替换当前创作草稿/);
    const recovering = responseFor(page, `/api/tasks/${source}/recover-draft`); void recovering.catch(() => {});
    await choice.getByRole('button', { name: '继续', exact: true }).click();
    const recoveredResponse = await recovering; expect(recoveredResponse.status()).toBe(201);
    const recovered = await recoveredResponse.json() as Recovered;
    expect(recoveredResponse.request().postDataJSON()).toEqual({ step: 1, mode: 'original' });
    await expect.poll(() => m.tasks.has(recovered.task_id)).toBe(true);
    invariant(recovered.task_id !== source && recovered.task_id !== copy.task_id && recovered.access_token === m.tasks.get(recovered.task_id), 'captured recovered capability');
    expect(recovered.status).toBe('draft'); expect(recovered.accepted).toBe(false); expect(recovered.mode).toBe('original');
    expect(recovered.script).toBe(SUBMITTED_SCRIPT);
    expect(recovered.source_voice_preferred).toBe(originalDraft.source_voice_preferred);
    expect(recovered.files).toHaveLength(sourceFiles.length);
    for (const f of recovered.files) {
      invariant(f.status === 'ready' && f.token === recovered.access_token && f.access_token === recovered.access_token
        && /^[a-f0-9]{64}$/.test(f.sha256) && !sourceFiles.some(([id]) => id === f.file_id)
        && m.files.get(f.file_id)?.task === recovered.task_id && m.files.get(f.file_id)?.upload === f.up_id, 'new file binding with draft capability');
    }
    // The fixture manifest independently binds media bytes; fresh file IDs do
    // not by themselves prove copied media identity.
    expect(recovered.files.map(f => f.sha256)).toEqual(originalDraft.files.map(f => {
      const input = m.manifest.inputs.find(i => i.name === f.name && i.bytes === f.bytes);
      invariant(input, 'source file is an exact owned fixture'); return input.sha256;
    }));
    await expect(page.getByRole('heading', { name: '写稿', exact: true })).toBeVisible();
    await expect(page.getByRole('group', { name: '制作模式', exact: true }).getByRole('button', { name: /只用原声/ })).toHaveAttribute('aria-pressed', 'true');
    await expect(scriptBox()).toHaveValue(SUBMITTED_SCRIPT); expect(await capabilityStored(recovered.task_id)).toBe(true);
    const token = m.tasks.get(recovered.task_id)!;
    expect(await page.evaluate(({ key, id, token, ids }) => {
      const d = JSON.parse(localStorage.getItem(key) ?? '{}').draft;
      return d?.draftTaskId === id && d.draftTaskToken === token && d.step === 1 && d.mode === 'original'
        && d.uploadBindings.length === ids.length && d.uploadBindings.every((b: { server_file_id: string; draftTaskId: string; upload_id: string }) =>
          b.draftTaskId === id && ids.includes(b.server_file_id) && d.uploadTokens[b.upload_id] === token);
    }, { key: DRAFT_KEY, id: recovered.task_id, token, ids: recovered.files.map(f => f.file_id) })).toBe(true);
    const beforeReload = writes().length;
    const filesRead = responseFor(page, `/api/tasks/${recovered.task_id}/files`, 'GET'); void filesRead.catch(() => {});
    await page.reload(); expect((await filesRead).status()).toBe(200);
    await expect(page.getByRole('heading', { name: '写稿', exact: true })).toBeVisible();
    await expect(scriptBox()).toHaveValue(SUBMITTED_SCRIPT);
    await page.getByRole('button', { name: '下一步：传素材', exact: true }).click();
    await expect(page.locator('.gm-capacity')).toContainText(`${recovered.files.length} 个已预处理`);
    expect(writes()).toHaveLength(beforeReload); expect((await m.status(recovered.task_id)).accepted).toBe(false);
    expect(await capabilityStored(recovered.task_id)).toBe(true); expect(await pendingSlot()).toBe(savedPending);
    await unchangedSource(); expect(m.hashes(copy.task_id)).toEqual(copyHashes);
    const after = await m.identity(); expect(after.providerCalls).toEqual(before.providerCalls); expect(after.uploadASRCalls).toEqual(before.uploadASRCalls);
    expect(writes().filter(r => r.path.endsWith('/start'))).toHaveLength(1);
    m.evidence('design-copy', { completedCopy: copy.task_id, recoveredDraft: recovered.task_id, newBindings: recovered.files.length,
      capabilitiesStored: true, noRepeatedASR: true, noCopyGeneration: true, copyR0: copyHashes, retainedSourceR0: r0 });
  });

  test('VD-04 real invalid-link 404 remains inaccessible, never claimed expired; read-only retry', async () => {
    const path = '/api/tasks/v2-design-invalid-link', before = writes().length;
    const waiting = responseFor(page, path, 'GET'); void waiting.catch(() => {});
    await page.goto('/tasks/v2-design-invalid-link'); expect((await waiting).status()).toBe(404);
    await expect(page.getByRole('heading', { name: '作品不存在或暂不可访问', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: '作品已到期或已清理', exact: true })).toHaveCount(0);
    const beforeRetry = m.observations.filter(r => r.method === 'GET' && r.path === path).length;
    const retry = responseFor(page, path, 'GET'); void retry.catch(() => {});
    await page.getByRole('button', { name: '重新读取作品', exact: true }).click(); expect((await retry).status()).toBe(404);
    await expect(page.getByRole('heading', { name: '作品不存在或暂不可访问', exact: true })).toBeVisible();
    expect(m.observations.filter(r => r.method === 'GET' && r.path === path).length).toBe(beforeRetry + 1);
    expect(writes()).toHaveLength(before); await unchangedSource();
    for (const id of m.tasks.keys()) expect(await capabilityStored(id)).toBe(true);
    expect(writes().filter(r => r.path.endsWith('/start'))).toHaveLength(1);
    expect(writes().some(r => r.method === 'DELETE')).toBe(false);
    m.evidence('design-gone-boundary', { actualHTTP: 404, retryGETs: 1, noWrites: true,
      tombstone410Tested: false, expiryTested: false, deletionTested: false, syntheticAPIResponses: false });
  });
});