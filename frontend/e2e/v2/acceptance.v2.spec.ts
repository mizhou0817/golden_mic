import { chooseInputs, checks, creator, DRAFT_KEY, effects, expect, exportFile, invariant, layouts,
  nativeFrame, responseFor, result, selectRow, submit, test } from './support';
import type { Apply, Report, Source, V2Run } from './support';
import type { Page } from '@playwright/test';

const scriptBox = (page: Page) => page.getByRole('textbox', { name: '新闻稿（第一行标题，下一行正文）', exact: true });
const nextFiles = (page: Page) => page.getByRole('button', { name: '下一步：传素材', exact: true }).click();
const editsRegion = (page: Page) => page.getByRole('region', { name: '待保存修改', exact: true });
const A_SCRIPT = '合成色卡配音验收样片\n色块画面缓缓移动。\n测试图案清晰可见。';
const B_SCRIPT = '合成采访色卡验收样片\n旁白：色块画面缓缓移动。\n同期：挺热闹的，感觉年味一下就来了。';
// Explicit synthetic numeral retains a genuine FACT_CHECK item after apply;
// do not manufacture QC warnings or waive a hard blocker to exercise the gate.
const EDIT_TEXT = '一片色块画面继续移动。';

async function apply(m: V2Run, page: Page, id: string, steps: number) {
  const button = editsRegion(page).getByRole('button', { name: '应用修改 → 生成新版本', exact: true });
  m.evidence('apply-controls', {
    pendingRegions: await editsRegion(page).count(), buttons: await button.count(),
    disabled: await button.count() === 1 ? await button.isDisabled() : null,
    busy: await page.getByRole('region', { name: '作品结果工作台', exact: true }).getAttribute('aria-busy'),
  });
  await expect(button).toBeVisible();
  await expect(button).toBeEnabled();
  // Actionability failures should not leave an unhandled response-wait promise.
  await button.click({ trial: true });
  const waiting = responseFor(page, `/api/tasks/${id}/apply`);
  void waiting.catch(() => {});
  await button.click();
  const response = await waiting; expect(response.status()).toBe(202);
  const receipt = await response.json() as Apply;
  expect((receipt.plan?.steps ?? receipt.steps)?.length).toBe(steps);
  // The public receipt advances by internal plan steps, NOT by visible clicks.
  expect(receipt.revision).toBe(steps);
  await m.done(id, receipt.revision); await result(page);
  return { receipt, body: response.request().postDataJSON() as {
    trims?: Record<string, { t0: number; t1: number }>; edits?: Record<string, string>; replaces?: Record<string, unknown>;
  } };
}
async function editText(page: Page) {
  await selectRow(page, 0);
  await page.getByRole('button', { name: '改字', exact: true }).click();
  await page.getByRole('textbox', { name: '改这句话', exact: true }).fill(EDIT_TEXT);
  await page.getByRole('button', { name: '记下修改', exact: true }).click();
}
async function trim(page: Page, index: number) {
  await selectRow(page, index);
  const panel = page.getByRole('region', { name: '选中句子详情', exact: true });
  await expect(panel.getByRole('group', { name: '原声句编辑', exact: true })).toBeVisible();
  await expect(panel.getByRole('img', { name: '原声真实采样波形，已缩略', exact: true })).toBeVisible();
  await panel.locator('[data-quote-word="1"]').press('Enter');
  await panel.locator('[data-quote-word]').last().press('Enter');
  await panel.getByRole('button', { name: '记下剪短', exact: true }).click();
}
function writes(m: V2Run) { return m.observations.filter(o => ['POST', 'PUT', 'PATCH', 'DELETE'].includes(o.method)); }

test('V2-01 current shell, all three modes visible, real sample availability and responsive create', { tag: ['@v2', '@shell'] }, async ({ v2: m, page }) => {
  await creator(page, 'voiceover');
  const cards = page.getByRole('group', { name: '制作模式', exact: true });
  await expect(cards.getByRole('button')).toHaveCount(3);
  await expect(page.locator('.gm-mode-more')).toHaveCount(0);
  await expect(cards.getByRole('button', { name: /AI 配音/ })).toHaveAttribute('aria-pressed', 'true');
  await scriptBox(page).fill('标题\n太短');
  await expect(page.getByRole('button', { name: '下一步：传素材', exact: true })).toBeDisabled();
  await scriptBox(page).fill('题'.repeat(41) + '\n色块画面缓缓移动，测试图案清晰可见。');
  await expect(page.getByRole('button', { name: '下一步：传素材', exact: true })).toBeDisabled();
  await scriptBox(page).fill(A_SCRIPT);
  await expect(page.getByRole('button', { name: '下一步：传素材', exact: true })).toBeEnabled();
  await cards.getByRole('button', { name: /旁白 \+ 原声/ }).click();
  await scriptBox(page).fill('标题\n太短');
  await expect(page.getByRole('button', { name: '下一步：传素材', exact: true })).toBeDisabled();
  await scriptBox(page).fill(B_SCRIPT);
  await expect(page.getByRole('button', { name: '下一步：传素材', exact: true })).toBeEnabled();
  const sample = await m.fetch('/api/samples/default');
  const sampleStatus = sample.status(); expect([200, 503]).toContain(sampleStatus);
  const sampleBody = await sample.json() as { read_only: boolean; code?: string; video_url?: string; report?: unknown };
  expect(sampleBody.read_only).toBe(true);
  if (sampleStatus === 503) expect(sampleBody.code).toBe('sample_unavailable');
  else { expect(sampleBody.video_url).toBe('/api/samples/default/video'); invariant(sampleBody.report, 'real packaged report'); }
  await sample.dispose();
  await layouts(m, page, 'create');
  expect(m.tasks.size).toBe(0); expect(writes(m)).toHaveLength(0);
  const forbidden = await page.locator('body').evaluate(e => /登录|注册|班级|老师台|教师/.test((e as HTMLElement).innerText));
  expect(forbidden).toBe(false);
  m.evidence('sample', { actualStatus: sampleStatus, noFakeSample: true, readOnly: true });
  m.tag('current-shell-no-old-title-real-sample-status');
});

test('V2-02 first file creates one draft; GET-only refresh retains IDs and ASR', { tag: ['@v2', '@draft'] }, async ({ v2: m, page }) => {
  await creator(page, 'original', '合成草稿恢复验收\n挺热闹的，感觉年味一下就来了');
  await nextFiles(page);
  const id = await chooseInputs(m, page, [m.manifest.inputs.find(i => i.name === 'synthetic-citizen.mp4')!]);
  const files = [...m.files.entries()];
  await expect(page.locator('.gm-match-row[data-state=ok]')).toHaveCount(1, { timeout: 180_000 });
  await page.locator('summary').filter({ hasText: '草稿操作' }).click();
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect.poll(() => page.evaluate(({ key, id }) => {
    const draft = JSON.parse(localStorage.getItem(key) ?? 'null')?.draft;
    return draft?.draftTaskId === id && draft?.uploadIds?.length === 1;
  }, { key: DRAFT_KEY, id })).toBe(true);
  const before = await m.identity(), previous = writes(m).length;
  await page.reload();
  await expect(page.getByRole('heading', { name: '传素材', exact: true })).toBeVisible();
  await expect(page.locator('.gm-capacity')).toContainText('1 个已预处理', { timeout: 180_000 });
  const after = await m.identity();
  expect(writes(m)).toHaveLength(previous); expect([...m.files.entries()]).toEqual(files);
  expect(after.uploadASRCalls).toEqual(before.uploadASRCalls); expect(after.providerCalls).toEqual(before.providerCalls);
  const task = await m.status(id); expect(task.status).toBe('draft'); expect(task.accepted).toBe(false);
  expect(m.observations.filter(o => o.method === 'POST' && o.path === '/api/tasks')).toHaveLength(1);
  expect(m.observations.filter(o => o.method === 'POST' && o.path.endsWith('/complete'))).toHaveLength(1);
  const tokenless = await m.fetch(`/api/tasks/${id}/files`, {}, false);
  expect(tokenless.status()).toBe(404); await tokenless.dispose();
  await layouts(m, page, 'draft-refresh');
  m.tag('one-draft-one-upload-read-only-refresh-no-repeat-asr');
});

test('V2-03 full sample C once: ten stages, word trim, immutable r0, checks and MP4', { tag: ['@v2', '@pipeline', '@full-c'] }, async ({ v2: m, page }) => {
  await creator(page, 'original', m.manifest.scripts.original);
  await nextFiles(page);
  const id = await chooseInputs(m, page, m.manifest.inputs);
  await expect(page.locator('.gm-match-row[data-state=ok]')).toHaveCount(8, { timeout: 180_000 });
  await effects(page);
  const before = await m.identity();
  await submit(m, page, id);
  const completed = await m.done(id, 0); expect(completed.rules_version).toBe(2); expect(completed.accepted).toBe(true);
  expect(completed.stages).toHaveLength(10); expect(completed.stages.every(s => s.status === 'done')).toBe(true);
  await result(page);
  const report = await m.report(id); expect(report.mode).toBe('original'); expect(report.rows).toHaveLength(8);
  expect(report.rows.every(r => r.kind === 'quote' && r.audio_kind === 'sync' && r.source?.precision === 'word')).toBe(true);
  const decoded = await nativeFrame(page); expect(decoded.duration).toBeGreaterThan(50); expect(decoded.duration).toBeLessThan(65);
  const original = m.hashes(id), source = report.rows[0].source as Source;
  await trim(page, 0);
  await expect(page.getByRole('button', { name: '改字', exact: true })).toHaveCount(0);
  await layouts(m, page, 'c-pending-trim');
  const applied = await apply(m, page, id, 1);
  expect(applied.body.trims).toEqual({ [report.rows[0].sentence_id]: { t0: source.words[1].s, t1: source.end } });
  const edited = await m.report(id);
  // Report may preserve original source plus explicit current trim. Both are
  // public supported shapes; never reconstruct spoken text from UI tokens.
  expect(edited.rows[0].trim?.start ?? edited.rows[0].source?.start).toBe(source.words[1].s);
  expect(edited.rows[0].duration).toBeLessThan(report.rows[0].duration);
  expect(edited.rows.slice(1).map(r => r.source)).toEqual(report.rows.slice(1).map(r => r.source));
  const count = await checks(m, page, id, 1);
  await exportFile(m, page, id, 1, 'mp4');
  expect(m.hashes(id)).toEqual(original);
  const after = await m.identity();
  expect(after.providerCalls.tts ?? 0).toBe(before.providerCalls.tts ?? 0); expect(after.uploadASRCalls).toEqual(before.uploadASRCalls);
  expect(m.observations.filter(o => o.method === 'POST' && o.path.endsWith('/start'))).toHaveLength(1);
  m.evidence('c-complete', { synthetic: true, speechEvidence: false, fullRows: 8, filesActuallyUploaded: m.files.size,
    decoded, revision: 1, retainedR0: original, confirmedChecks: count, noTTS: true, noRepeatedASR: true });
  m.tag('full-c-once-pipeline-trim-checks-mp4-r0-immutable');
});

test('V2-04 short A actual pipeline and multi-step apply publishes revision two then MP4', { tag: ['@v2', '@pipeline', '@apply-multi'] }, async ({ v2: m, page }) => {
  await creator(page, 'voiceover', A_SCRIPT); await nextFiles(page);
  // All four b-roll inputs leave genuine unused candidates for replacement.
  const id = await chooseInputs(m, page, m.manifest.inputs.filter(i => i.kind === 'broll'));
  await effects(page); const before = await m.identity(); await submit(m, page, id);
  const completed = await m.done(id, 0); expect(completed.stages.every(s => s.status === 'done')).toBe(true);
  expect(completed.stages).toHaveLength(10);
  await result(page); const report = await m.report(id); expect(report.rows).toHaveLength(2);
  expect(report.rows.every(r => r.kind === 'narration' && r.audio_kind === 'tts')).toBe(true);
  const original = m.hashes(id); await nativeFrame(page); await editText(page);
  await page.getByRole('textbox', { name: '想要什么画面', exact: true }).fill('色块画面继续移动');
  await page.getByRole('button', { name: '记下换画面', exact: true }).click();
  const applied = await apply(m, page, id, 2);
  expect(applied.body.edits).toEqual({ [report.rows[0].sentence_id]: EDIT_TEXT });
  expect(Object.keys(applied.body.replaces ?? {})).toEqual([String(report.rows[0].sentence_id)]);
  expect((await m.report(id)).rows[0].sentence).toBe(EDIT_TEXT);
  const context = await m.json<{ revision: number; versions: { revision: number }[] }>(`/api/tasks/${id}/workbench/context`);
  expect(context.revision).toBe(2); expect(context.versions.map(v => v.revision)).toContain(2);
  // Internal revision one must not masquerade as the final visible publication.
  expect(context.versions.map(v => v.revision)).not.toContain(1);
  await checks(m, page, id, 2); await exportFile(m, page, id, 2, 'mp4');
  expect(m.hashes(id)).toEqual(original); const after = await m.identity();
  expect(after.providerCalls.tts ?? 0).toBeGreaterThan(before.providerCalls.tts ?? 0);
  expect(after.uploadASRCalls).toEqual(before.uploadASRCalls);
  m.evidence('a-complete', { actualRows: 2, uploadedFiles: 4, visibleApplyActions: 1, internalSteps: 2,
    publishedRevision: 2, intermediateRevisionVisible: false, retainedR0: original, syntheticToneNotSpeech: true });
  m.tag('a-real-pipeline-multi-apply-two-steps-one-publication-mp4');
});

test('V2-05 short B actual mixed pipeline, apply, checks and all five export formats', { tag: ['@v2', '@pipeline', '@exports'] }, async ({ v2: m, page }) => {
  await creator(page, 'mixed', B_SCRIPT); await nextFiles(page);
  const inputs = [m.manifest.inputs.find(i => i.name === 'synthetic-citizen.mp4')!, ...m.manifest.inputs.filter(i => i.kind === 'broll').slice(0, 2)];
  const id = await chooseInputs(m, page, inputs);
  await expect(page.locator('.gm-match-row[data-state=ok]')).toHaveCount(1, { timeout: 180_000 });
  await effects(page); const before = await m.identity(); await submit(m, page, id);
  const completed = await m.done(id, 0); expect(completed.stages.every(s => s.status === 'done')).toBe(true);
  expect(completed.stages).toHaveLength(10);
  await result(page); const report: Report = await m.report(id);
  expect(report.mode).toBe('mixed'); expect(report.rows.map(r => r.audio_kind)).toEqual(['tts', 'sync']);
  const original = m.hashes(id); await nativeFrame(page); await editText(page); await trim(page, 1);
  const applied = await apply(m, page, id, 1);
  const source = report.rows[1].source as Source;
  expect(applied.body.edits).toEqual({ [report.rows[0].sentence_id]: EDIT_TEXT });
  expect(applied.body.trims).toEqual({ [report.rows[1].sentence_id]: { t0: source.words[1].s, t1: source.end } });
  const edited = await m.report(id);
  expect(edited.rows[0].sentence).toBe(EDIT_TEXT);
  expect(edited.rows[1].trim?.start ?? edited.rows[1].source?.start).toBe(source.words[1].s);
  expect(edited.rows[1].duration).toBeLessThan(report.rows[1].duration);
  await nativeFrame(page); await layouts(m, page, 'b-result');
  await checks(m, page, id, 1);
  for (const format of ['mp4', 'gif', 'mp3', 'srt', 'png'] as const) await exportFile(m, page, id, 1, format);
  expect(m.hashes(id)).toEqual(original); const after = await m.identity();
  expect(after.providerCalls.tts ?? 0).toBeGreaterThan(before.providerCalls.tts ?? 0);
  expect(after.uploadASRCalls).toEqual(before.uploadASRCalls);
  m.evidence('b-complete', { actualRows: 2, uploadedFiles: 3, revision: 1, retainedR0: original,
    formatsDownloadedAndValidated: ['mp4', 'gif', 'mp3', 'srt', 'png'], previewOnly: false, syntheticToneNotSpeech: true });
  m.tag('b-real-pipeline-apply-checks-five-real-exports');
});

test('V2-06 missing quote is rejected at real start 422 without forcing pipeline failure', { tag: ['@v2', '@admission'] }, async ({ v2: m, page }) => {
  const script = '合成缺失原话验收\n今年我们还请了舞狮队';
  await creator(page, 'original', script); await nextFiles(page);
  const id = await chooseInputs(m, page, [m.manifest.inputs.find(i => i.name === 'synthetic-citizen.mp4')!]);
  // The unchanged normative containment formula scores the observed single
  // character “了” as 1. `ok` is a lexical score, NOT verified quote coverage.
  // Preserve this manuscript: a disjoint replacement would hide the admission
  // defect. The strict /start 422 assertion below intentionally remains red
  // until the backend enforces the same original-mode evidence gate as the UI.
  await expect(page.locator('.gm-match-row[data-state=ok]')).toHaveCount(1, { timeout: 180_000 });
  await expect(page.locator('.gm-action-hint')).toHaveText('原话有未核实的增改或中间删词；请重新选取连续原话，不能拼出素材里没有说过的话。');
  await expect(page.getByRole('button', { name: '下一步：选效果', exact: true })).toBeDisabled();
  const before = await m.identity();
  const response = await m.fetch(`/api/tasks/${id}/start`, { method: 'POST', data: { mode: 'original', script } });
  expect(response.status()).toBe(422); await response.dispose();
  const task = await m.status(id); expect(task.status).toBe('draft'); expect(task.accepted).toBe(false);
  expect(task.stages.every(s => s.status === 'pending')).toBe(true);
  const after = await m.identity(); expect(after.providerCalls).toEqual(before.providerCalls); expect(after.uploadASRCalls).toEqual(before.uploadASRCalls);
  m.tag('missing-quote-start-422-no-charge-no-stage-bypass-no-retry');
});