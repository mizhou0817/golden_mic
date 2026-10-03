import { probeMedia } from './io.mjs';
import {
  chooseInputs, confirmAllChecks, creator, DRAFT_KEY, expect, invariant, layout, leanEffects,
  nativeFrame, noLogin, openResult, responseFor, submitWizard, test,
} from './support';
import type { Checks, Job, Task } from './support';

// Eight independent tests. No shared task/seed, no old browser harness imports,
// no route.fulfill, real provider calls, retries or existing user profile.
// The complete original sample is rendered ONLY ONCE; B's full 16 rows use
// the actual alignment preview, while A/B rendering use short bounded scripts.

test('MODES-01 three cards preserve the B sample and C accepts an empty manuscript', async ({ modes: m, page }) => {
  await creator(page, 'original');
  const script = page.getByRole('textbox', { name: '新闻稿（第一行标题，下一行正文）', exact: true });
  await expect(script).toHaveValue('');
  await page.getByRole('button', { name: '下一步：传素材 →', exact: true }).click();
  await expect(page.getByRole('heading', { name: '传素材', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '下一步：选效果 →', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '← 上一步', exact: true }).click();
  const cards = page.getByRole('group', { name: '制作模式', exact: true });
  await cards.getByRole('button', { name: /旁白 \+ 原声/ }).click();
  await script.fill(m.manifest.scripts.mixed);
  await expect(page.locator('.gm-sentence-row')).toHaveCount(16);
  await expect(page.locator('.gm-sentence-row[data-kind=narration]')).toHaveCount(11);
  await expect(page.locator('.gm-sentence-row[data-kind=quote]')).toHaveCount(5);
  expect(await page.locator('.gm-sentence-row > div > p:first-child').allTextContents()).toEqual(m.manifest.mixedSentences.map(s => s.text));
  await cards.getByRole('button', { name: /AI 配音/ }).click();
  await expect(script).toHaveValue(m.manifest.scripts.mixed);
  await cards.getByRole('button', { name: /旁白 \+ 原声/ }).click();
  await expect(script).toHaveValue(m.manifest.scripts.mixed);
  await expect(page.locator('.gm-sentence-row[data-kind=quote]')).toHaveCount(5);
  // Chip changes only kind, not punctuation boundaries or the other rows.
  const before = await page.locator('.gm-sentence-row > div > p:first-child').allTextContents();
  await page.getByRole('button', { name: '第 4 句：原声，改成旁白', exact: true }).click();
  await expect(page.locator('.gm-sentence-row')).toHaveCount(16);
  expect(await page.locator('.gm-sentence-row > div > p:first-child').allTextContents()).toEqual(before);
  await page.getByRole('button', { name: '第 4 句：旁白，改成原声', exact: true }).click();
  await expect(page.locator('.gm-sentence-row[data-kind=quote]')).toHaveCount(5);
  expect(m.observations.filter(o => o.method === 'POST')).toHaveLength(0);
  m.checkpoint('three-cards-empty-c-and-11-plus-5-b');
});

test('MODES-02 real upload ASR adapter and refresh never duplicate upload or complete POST', async ({ modes: m, page }) => {
  const input = m.manifest.inputs.find(i => i.name === 'synthetic-organizer.mp4')!;
  await creator(page, 'original');
  await page.getByRole('button', { name: '下一步：传素材 →', exact: true }).click();
  await chooseInputs(m, page, [input]);
  const id = [...m.uploads.keys()][0], uploaded = await m.readyUpload(id);
  expect(uploaded.transcript.segments.map(s => s.text)).toEqual(input.segments.map(s => s.text));
  expect(uploaded.transcript.precision).toBe('word');
  expect(uploaded.transcript.segments.every(s => s.words.length > 2)).toBe(true);
  const picker = page.locator('.gm-transcript-picker');
  await picker.getByRole('checkbox', { name: `选入原话：${input.segments[0].text}`, exact: true }).check();
  await page.getByRole('button', { name: '← 回写稿检查标题和原话', exact: true }).click();
  const script = page.getByRole('textbox', { name: '新闻稿（第一行标题，下一行正文）', exact: true });
  await expect(script).toHaveValue(`（写一个标题）\n${input.segments[0].text}`);
  await script.fill(`合成转写恢复验收\n${input.segments[0].text}`);
  await page.getByRole('button', { name: '下一步：传素材 →', exact: true }).click();
  await expect(page.locator('.gm-match-row[data-state=ok]')).toHaveCount(1);
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect.poll(() => page.evaluate(key => {
    const value = JSON.parse(localStorage.getItem(key) ?? 'null');
    // Only return identity/count, not stored capabilities or raw draft JSON.
    return value?.draft?.uploadIds?.length === 1 && value.draft.mode === 'original';
  }, DRAFT_KEY)).toBe(true);
  const previousWrites = m.observations.filter(o => ['POST', 'PUT'].includes(o.method) && o.path.startsWith('/api/uploads')).length;
  const before = await m.identity();
  await page.reload();
  await expect(page.getByRole('heading', { name: '传素材', exact: true })).toBeVisible();
  await expect(page.locator('.gm-capacity')).toContainText('1 个已预处理', { timeout: 180_000 });
  await expect(page.locator('.gm-match-row[data-state=ok]')).toHaveCount(1);
  const after = await m.identity();
  expect(after.uploadASRCalls).toEqual(before.uploadASRCalls);
  expect(m.observations.filter(o => ['POST', 'PUT'].includes(o.method) && o.path.startsWith('/api/uploads'))).toHaveLength(previousWrites);
  expect([...m.uploads.keys()]).toEqual([id]);
  expect((await m.uploadStatus(id)).transcript).toEqual(uploaded.transcript);
  await noLogin(page);
  m.checkpoint('real-upload-restored-without-post');
  m.evidence('refresh', { inputSHA256: input.sha256, realUploadId: id, segments: input.segments.length,
    uploadWritesBeforeReload: previousWrites, uploadWritesAfterReload: previousWrites,
    asrCallsUnchanged: true, textAndEvenWordClocksAreSynthetic: true });
});

test('MODES-03 complete eight-quote C flow trims a real revision and gates a real 1080p export', async ({ modes: m, page }) => {
  test.setTimeout(1_500_000);
  await creator(page, 'original', m.manifest.scripts.original);
  await expect(page.locator('.gm-sentence-row[data-kind=quote]')).toHaveCount(8);
  await page.getByRole('button', { name: '下一步：传素材 →', exact: true }).click();
  await chooseInputs(m, page, m.manifest.inputs);
  await expect(page.locator('.gm-match-row[data-state=ok]')).toHaveCount(8, { timeout: 180_000 });
  const preview = await m.preview(m.manifest.scripts.original.split('\n').slice(1).map((text, idx) => ({ idx, text, kind: 'quote' })), [...m.uploads.keys()]);
  expect(preview.matches).toHaveLength(8);
  expect(preview.matches.every(row => row.score >= .85 && row.source?.precision === 'word')).toBe(true);
  // Actual UI choice, not invented speaker attribution or source filenames.
  await page.getByRole('button', { name: '下一步：选效果 →', exact: true }).click();
  await leanEffects(page, 'original');
  const beforeProviders = await m.identity();
  const submitted = await submitWizard(m, page), id = submitted.id;
  expect(submitted.mode).toBe('original'); expect(submitted.sentences).toHaveLength(8);
  expect(submitted.uploadCount).toBe(7);
  await expect(page.getByRole('progressbar', { name: '服务器制作进度', exact: true })).toBeVisible({ timeout: 30_000 });
  const completed = await m.completed(id);
  expect(completed.mode).toBe('original'); expect(completed.progress).toBe(100);
  expect(completed.stages).toHaveLength(10);
  expect(completed.stages.every(s => s.status === 'done' && s.elapsed_seconds !== null && s.elapsed_seconds >= 0)).toBe(true);
  expect(completed.stages.reduce((n, s) => n + s.weight, 0)).toBeCloseTo(1, 12);
  const report = await m.report(id);
  expect(report.rows).toHaveLength(8); expect(report.mode).toBe('original');
  expect(report.rows.every(row => row.kind === 'quote' && row.audio_kind === 'sync' && row.source)).toBe(true);
  for (const row of report.rows) {
    const match = preview.matches.find(p => p.idx === row.sentence_id)!;
    expect(row.source).toEqual(match.source); // Shared real preview/stage-6 semantics, no fixture substitute.
    expect(row.spoken_text).toBe(row.source!.asr_text);
  }
  const initialMedia = m.probe(id);
  expect(initialMedia.width).toBe(1920); expect(initialMedia.height).toBe(1080);
  expect(initialMedia.videoCodec).toBe('h264'); expect(initialMedia.audioCodec).toBe('aac');
  expect(initialMedia.duration).toBeGreaterThan(m.manifest.expected.originalDurationBounds[0]);
  expect(initialMedia.duration).toBeLessThan(m.manifest.expected.originalDurationBounds[1]);
  const initialTimings = m.artifact<{ sentence_id: number; duration: number; start: number; end: number; gap_after: number;
    audio_kind: string; tempo_adjustment: number }[]>(id, 'timings.json');
  expect(initialTimings).toHaveLength(8);
  for (const timing of initialTimings) {
    const row = report.rows.find(r => r.sentence_id === timing.sentence_id)!;
    expect(Math.abs(timing.duration - (row.source!.end - row.source!.start))).toBeLessThanOrEqual(2 / 48000);
    expect(timing.audio_kind).toBe('sync'); expect(timing.tempo_adjustment).toBe(1);
  }
  const lastTiming = initialTimings[initialTimings.length - 1];
  expect(Math.abs(initialMedia.duration - lastTiming.end - lastTiming.gap_after)).toBeLessThanOrEqual(1 / 30 + .025);
  const afterGeneration = await m.identity();
  expect(afterGeneration.providerCalls.tts ?? 0).toBe(beforeProviders.providerCalls.tts ?? 0);
  expect(afterGeneration.uploadASRCalls).toEqual(beforeProviders.uploadASRCalls);
  m.checkpoint('full-c-ten-stages-real-media-no-tts');
  const originalHashes = m.revisionHashes(id);
  await openResult(page, id);
  const decoded = await nativeFrame(page.getByLabel('新闻作品成片预览', { exact: true }));
  const panel = page.getByRole('region', { name: '选中句子详情', exact: true });
  await expect(panel.getByRole('group', { name: '原声句编辑', exact: true })).toBeVisible();
  await expect(panel.locator('.gm-quote-source')).toContainText(report.rows[0].source!.asr_text);
  // Original speech edits are unavailable, not merely a disabled-looking color.
  await expect(panel.getByRole('textbox', { name: /修改这句话/ })).toHaveCount(0);
  await expect(panel.getByRole('button', { name: /录制我的配音|记下改成旁白/ })).toHaveCount(0);
  await expect(panel.getByRole('combobox', { name: '直接选镜', exact: true })).toHaveCount(0);
  await expect(page.getByRole('combobox', { name: /整片语速|旁白语速/ })).toHaveCount(0);
  const sourcePanel = await panel.locator('.gm-quote-source').evaluate(element => {
    const style = getComputedStyle(element);
    return { background: style.backgroundColor, border: style.borderLeftColor, width: parseFloat(style.borderLeftWidth) };
  });
  expect(sourcePanel.width).toBeGreaterThan(0); expect(sourcePanel.background).not.toBe('rgba(0, 0, 0, 0)');
  await expect(panel.getByRole('img', { name: '原声真实采样波形，已缩略', exact: true })).toBeVisible();
  // Server independently rejects quote rewriting; no render or revision change.
  const rejected = await m.fetch(`/api/tasks/${id}/remix`, { method: 'POST', data: {
    expected_revision: 0, keep_sentence_ids: report.rows.map(r => r.sentence_id),
    edits: [{ sentence_id: report.rows[0].sentence_id, text: '这是素材里没有说过的话' }],
  } });
  expect([400, 422]).toContain(rejected.status()); await rejected.dispose();
  expect((await m.status(id)).revision).toBe(0);
  expect(m.revisionHashes(id)).toEqual(originalHashes);
  const source = report.rows[0].source!;
  const expectedStart = source.words[1].s;
  // Select an explicit continuous word range with native keyboard clicks.
  // A single '[' could remove only the 120ms handle, not the first word.
  await panel.locator('[data-quote-word="1"]').press('Enter');
  await panel.locator('[data-quote-word]').last().press('Enter');
  await panel.getByRole('button', { name: '记下剪短', exact: true }).click();
  await expect(page.getByRole('region', { name: '待保存修改', exact: true })).toContainText('剪短 1 句');
  const applying = responseFor(page, `/api/tasks/${id}/remix`);
  await page.getByRole('region', { name: '待保存修改', exact: true }).getByRole('button', { name: /应用修改/ }).click();
  const accepted = await applying;
  expect(accepted.status()).toBe(202);
  const payload = accepted.request().postDataJSON() as { quote_trims: { id: number; start: number; end: number }[]; edits: unknown[] };
  expect(payload.edits).toEqual([]);
  expect(payload.quote_trims).toEqual([{ id: report.rows[0].sentence_id, start: expectedStart, end: source.end }]);
  const revision = await m.completed(id, 1, 180_000);
  expect(revision.revision).toBe(1);
  const edited = await m.report(id), changed = edited.rows[0];
  expect(changed.source?.start).toBe(expectedStart); expect(changed.source?.end).toBe(source.end);
  expect(changed.duration).toBeLessThan(report.rows[0].duration);
  expect(edited.rows.slice(1).map(row => row.source)).toEqual(report.rows.slice(1).map(row => row.source));
  expect(m.revisionHashes(id)).toEqual(originalHashes);
  const timings = m.artifact<{ sentence_id: number; audio_kind: string; tempo_adjustment: number; duration: number }[]>(id, 'timings.json');
  expect(timings.every(t => t.audio_kind === 'sync' && t.tempo_adjustment === 1)).toBe(true);
  m.checkpoint('real-word-boundary-remix-r1-r0-immutable');
  await openResult(page, id);
  const gate = await m.json<Checks>(`/api/tasks/${id}/checks`);
  expect(gate.revision).toBe(1); expect(gate.blocking_count).toBe(0); expect(gate.pending_count).toBeGreaterThan(0);
  const stale = await m.fetch(`/api/tasks/${id}/checks`, { method: 'POST', data: { expected_revision: 0, checked_keys: [] } });
  expect(stale.status()).toBe(409); await stale.dispose();
  await expect(page.getByRole('button', { name: '导出作品', exact: true }).first()).toBeDisabled();
  const blocked = await m.fetch(`/api/tasks/${id}/export`, { method: 'POST', data: { fmt: 'mp4', aspect: '16:9', res: '1080p', sub: 'std' } });
  expect(blocked.status()).toBe(409);
  const blockedBody = await blocked.json() as { detail: { code: string } };
  expect(blockedBody.detail.code).toBe('publication_checks_required'); await blocked.dispose();
  const confirmedCount = await confirmAllChecks(m, page, id);
  await page.getByRole('button', { name: '导出作品', exact: true }).first().click();
  const dialog = page.getByRole('dialog', { name: '导出作品', exact: true });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('combobox', { name: '分辨率', exact: true }).selectOption('1080');
  const exporting = responseFor(page, `/api/tasks/${id}/export`);
  await dialog.getByRole('button', { name: '提交导出任务', exact: true }).click();
  const exportResponse = await exporting;
  expect(exportResponse.status()).toBe(202);
  const receipt = await exportResponse.json() as Job, job = await m.job(id, receipt.export_id ?? receipt.id);
  expect(job.pipeline_revision).toBe(1);
  const link = dialog.getByRole('link', { name: '下载已完成的 MP4 文件（第 1 版）', exact: true });
  await expect(link).toBeVisible({ timeout: 180_000 });
  const event = page.waitForEvent('download'); await link.click(); const download = await event;
  invariant(await download.failure() === null, 'actual export download failed');
  const file = await download.path(); invariant(file, 'actual download absent');
  const output = probeMedia(file, m.manifest.tools.ffprobe);
  expect(output.bytes).toBe(job.result!.bytes); expect(output.width).toBe(1920); expect(output.height).toBe(1080);
  expect(output.videoCodec).toBe('h264'); expect(output.audioCodec).toBe('aac'); expect(output.audioSampleRate).toBe(48000);
  expect(output.fps).toBe('30/1'); expect(Math.abs(output.duration - job.result!.duration)).toBeLessThan(.15);
  expect(m.revisionHashes(id)).toEqual(originalHashes);
  const finalProviders = await m.identity();
  expect(finalProviders.providerCalls.tts ?? 0).toBe(beforeProviders.providerCalls.tts ?? 0);
  expect(finalProviders.uploadASRCalls).toEqual(beforeProviders.uploadASRCalls);
  m.checkpoint('backend-409-ui-confirm-real-1080p-download');
  m.evidence('complete-original', { synthetic: true, sourceSpeechVerified: false, textWordsAreInjected: true,
    initialMedia, decodedFrame: decoded, sourcePanel, fullRows: report.rows.length,
    actualSourceClocksMatchPCMTiming: true, actualTimelineMatchesProbedEOF: true,
    previewEqualsStage6: true, retainedRevision0Hashes: originalHashes, newRevision: 1,
    realSourceTrim: payload.quote_trims, confirmedCount, directExportBeforeChecks: 409, output,
    noTTSForOriginal: true, noRepeatedASR: true });
});

test('MODES-04 full B sample uses real uploaded words and numeric date matching without rendering sixteen rows', async ({ modes: m }) => {
  const inputs = m.manifest.inputs.filter(i => i.kind === 'interview');
  const uploads = await m.uploadMany(inputs), ids = uploads.map(u => u.id);
  const before = await m.identity();
  const preview = await m.preview(m.manifest.mixedSentences, ids);
  expect(preview.match_ok).toBe(.85); expect(preview.match_low).toBe(.6);
  expect(preview.matches).toHaveLength(16);
  const quotes = preview.matches.filter(match => match.kind === 'quote');
  expect(quotes.map(row => row.idx)).toEqual([3, 7, 8, 12, 15]);
  expect(preview.matches.filter(match => match.kind === 'narration')).toHaveLength(11);
  expect(quotes.every(match => match.score >= .85 && match.source?.precision === 'word')).toBe(true);
  const date = quotes.find(match => match.idx === 15)!.source!;
  expect(date.asr_text).toContain('一月三十一号');
  expect(m.manifest.mixedSentences[15].text).toContain('1月31号');
  for (const quote of quotes) {
    const source = quote.source!, upload = uploads.find(u => u.id === source.upload_id)!;
    const words = upload.transcript.segments.flatMap(segment => segment.words);
    expect(source.words.length).toBeGreaterThan(0);
    expect(source.words.every(w => words.some(observed => observed.w === w.w && observed.s === w.s && observed.e === w.e))).toBe(true);
    expect(source.start).toBeGreaterThanOrEqual(0); expect(source.end).toBeLessThanOrEqual(upload.sec);
    expect(source.start).toBeLessThan(source.end);
  }
  const after = await m.identity();
  expect(after.providerCalls).toEqual(before.providerCalls); expect(after.uploadASRCalls).toEqual(before.uploadASRCalls);
  expect(m.tasks.size).toBe(0);
  m.checkpoint('b-full-sample-real-preview-numeric-equivalence');
  m.evidence('mixed-preview', { synthetic: true, narration: 11, quotes: 5, realRendering: false,
    wordClocksPreserved: true, scriptArabicDateMatchesSyntheticChineseTranscript: true,
    matches: quotes.map(q => ({ idx: q.idx, score: q.score, start: q.source!.start, end: q.source!.end, precision: q.source!.precision })) });
});

test('MODES-05 short A browser task uses real FFmpeg with local fake tone TTS', async ({ modes: m, page }) => {
  test.setTimeout(480_000);
  await creator(page, 'voiceover', m.manifest.scripts.voiceover);
  await page.getByRole('button', { name: '下一步：传素材 →', exact: true }).click();
  await chooseInputs(m, page, m.manifest.inputs.filter(i => i.kind === 'broll').slice(0, 2));
  await page.getByRole('button', { name: '下一步：选效果 →', exact: true }).click();
  await leanEffects(page, 'voiceover');
  const before = await m.identity(), submission = await submitWizard(m, page);
  const task = await m.completed(submission.id, 0, 300_000), report = await m.report(submission.id);
  expect(task.stages.every(s => s.status === 'done')).toBe(true); expect(report.mode).toBe('voiceover');
  expect(report.rows).toHaveLength(2); expect(report.rows.every(row => row.kind === 'narration' && row.audio_kind === 'tts' && !row.source)).toBe(true);
  await openResult(page, submission.id);
  const native = await nativeFrame(page.getByLabel('新闻作品成片预览', { exact: true }));
  await expect(page.getByRole('textbox', { name: '修改这句话（不换行，最多 2000 字）', exact: true })).toBeEditable();
  const after = await m.identity();
  for (const kind of ['vision', 'embedding', 'llm', 'tts']) expect((after.providerCalls[kind] ?? 0) > (before.providerCalls[kind] ?? 0)).toBe(true);
  expect(after.uploadASRCalls).toEqual(before.uploadASRCalls);
  expect(after.providerCalls.segmentation ?? 0).toBe(before.providerCalls.segmentation ?? 0);
  m.checkpoint('short-a-rendered-without-model-resegmentation');
  m.evidence('voiceover', { mode: task.mode, rows: 2, native, media: m.probe(submission.id), speechEvidence: false,
    fakeProviderAdaptersActuallyCalled: true, clientSentencesPreserved: true });
});

test('MODES-06 short B browser task preserves separate narration and original source audio', async ({ modes: m, page }) => {
  test.setTimeout(600_000);
  await creator(page, 'mixed', m.manifest.scripts.shortMixed);
  await expect(page.locator('.gm-sentence-row[data-kind=narration]')).toHaveCount(1);
  await expect(page.locator('.gm-sentence-row[data-kind=quote]')).toHaveCount(1);
  await page.getByRole('button', { name: '下一步：传素材 →', exact: true }).click();
  const inputs = [m.manifest.inputs.find(i => i.name === 'synthetic-citizen.mp4')!, ...m.manifest.inputs.filter(i => i.kind === 'broll').slice(0, 2)];
  await chooseInputs(m, page, inputs);
  await expect(page.locator('.gm-match-row[data-state=ok]')).toHaveCount(1);
  await page.getByRole('button', { name: '下一步：选效果 →', exact: true }).click();
  await leanEffects(page, 'mixed');
  const before = await m.identity(), submission = await submitWizard(m, page);
  await m.completed(submission.id, 0, 480_000);
  const report = await m.report(submission.id);
  expect(report.mode).toBe('mixed'); expect(report.rows.map(r => r.kind)).toEqual(['narration', 'quote']);
  expect(report.rows.map(r => r.audio_kind)).toEqual(['tts', 'sync']);
  expect(report.rows[1].source?.precision).toBe('word');
  await openResult(page, submission.id);
  const cells = page.getByRole('group', { name: '句子选择', exact: true }).getByRole('button');
  await expect(page.getByRole('textbox', { name: '修改这句话（不换行，最多 2000 字）', exact: true })).toBeEditable();
  await cells.nth(1).click();
  await expect(page.getByRole('group', { name: '原声句编辑', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '记下改成旁白（AI 读）', exact: true })).toBeEnabled();
  await expect(page.getByRole('textbox', { name: /修改这句话/ })).toHaveCount(0);
  const after = await m.identity();
  expect(after.uploadASRCalls).toEqual(before.uploadASRCalls);
  expect((after.providerCalls.tts ?? 0) > (before.providerCalls.tts ?? 0)).toBe(true);
  m.checkpoint('short-b-real-audio-kind-separation');
  m.evidence('mixed', { media: m.probe(submission.id), kinds: report.rows.map(r => ({ kind: r.kind, audio: r.audio_kind })),
    syntheticASRReused: true, noOriginalTTSFallback: true });
});

test('MODES-07 forced missing quote fails real stage six and never falls back to tone TTS', async ({ modes: m, page }) => {
  test.setTimeout(480_000);
  const input = m.manifest.inputs.find(i => i.name === 'synthetic-citizen.mp4')!;
  const script = '合成缺失原话验收\n今年我们还请了舞狮队';
  await creator(page, 'original', script);
  await page.getByRole('button', { name: '下一步：传素材 →', exact: true }).click();
  await chooseInputs(m, page, [input]);
  const upload = await m.readyUpload([...m.uploads.keys()][0]);
  const sentences = [{ idx: 0, text: '今年我们还请了舞狮队', kind: 'quote' as const }];
  const preview = await m.preview(sentences, [upload.id]);
  expect(preview.matches[0].source === null || preview.matches[0].score < .6).toBe(true);
  await expect(page.locator('.gm-match-row[data-state=missing]')).toHaveCount(1);
  await expect(page.getByRole('button', { name: '下一步：选效果 →', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect.poll(() => page.evaluate(({ key, script, id }) => {
    const draft = JSON.parse(localStorage.getItem(key) ?? 'null')?.draft;
    return draft?.script === script && draft.mode === 'original' && draft.uploadIds?.length === 1
      && draft.uploadIds[0] === id && draft.uploadBindings?.length === 1 && draft.uploadBindings[0].upload_id === id;
  }, { key: DRAFT_KEY, script, id: upload.id })).toBe(true);
  const before = await m.identity();
  // Deliberately bypass ONLY the UI submit guard; use the real public JSON API.
  const id = await m.create('original', script, [upload.id], sentences);
  // Explicit recovery fixture for the API-only forced failure. This exercises
  // production snapshot READ/validation/recovery, NOT Workspace's save hook.
  // Copy the actual saved Wizard draft in-page; never export its capabilities.
  expect(await page.evaluate(({ key, id, script, uploadId, token }) => {
    const draft = JSON.parse(localStorage.getItem(key) ?? 'null')?.draft;
    if (draft?.script !== script || draft.mode !== 'original' || draft.uploadIds?.length !== 1
        || draft.uploadIds[0] !== uploadId || draft.uploadTokens?.[uploadId] !== token) return false;
    const recoveryKey = 'golden-mic.submission-recovery.v1.' + id;
    if (localStorage.getItem(recoveryKey) !== null) return false;
    const savedAt = Date.now();
    localStorage.setItem(recoveryKey, JSON.stringify({ version: 1, taskId: id, savedAt,
      expiresAt: savedAt + 30 * 24 * 60 * 60 * 1000, draft }));
    return true;
  }, { key: DRAFT_KEY, id, script, uploadId: upload.id, token: m.uploads.get(upload.id)! })).toBe(true);
  // Make the current draft DIFFERENT: returning to an arbitrary surviving
  // draft must not pass as task-bound submission recovery after reload.
  await page.getByRole('button', { name: '← 上一步', exact: true }).click();
  await page.getByRole('group', { name: '制作模式', exact: true }).getByRole('button', { name: /AI 配音/ }).click();
  const otherScript = '另一份未提交草稿\n这段旁白不是失败任务的原稿';
  await page.getByRole('textbox', { name: '新闻稿（第一行标题，下一行正文）', exact: true }).fill(otherScript);
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect.poll(() => page.evaluate(({ key, script }) => {
    const draft = JSON.parse(localStorage.getItem(key) ?? 'null')?.draft;
    return draft?.script === script && draft.mode === 'voiceover';
  }, { key: DRAFT_KEY, script: otherScript })).toBe(true);
  // This is same-document hash navigation from a nonempty saved draft, not a
  // fresh page load. Accept the actual product departure guard explicitly.
  m.confirmations.set(page, /^离开创作页？稿件、模式与上传凭据会保留。/);
  await page.goto(`/#work=${id}`);
  await expect.poll(() => m.confirmations.has(page)).toBe(false);
  await expect(page).toHaveURL(new RegExp(`#work=${id}$`));
  await expect(page.getByRole('region', { name: '新建新闻作品', exact: true })).toHaveCount(0);
  let final: Task | undefined;
  await expect.poll(async () => { final = await m.status(id); return final.status; },
    { timeout: 300_000, intervals: [250, 750, 1500] }).toBe('failed');
  expect(final!.errorKind).toBe('quote_missing'); expect(final!.badRows).toEqual([1]);
  expect(final!.stages.find(s => s.number === 6)?.status).toBe('failed');
  expect(final!.stages.find(s => s.number === 7)?.status).toBe('pending');
  await expect(page.getByRole('heading', { name: '有 1 句原话在素材里没找到', exact: true })).toBeVisible({ timeout: 30_000 });
  const writesBeforeReload = m.observations.filter(o => ['POST', 'PUT', 'PATCH', 'DELETE'].includes(o.method)
    && (o.path.startsWith('/api/tasks') || o.path.startsWith('/api/uploads')));
  await page.reload();
  await expect(page).toHaveURL(new RegExp(`#work=${id}$`));
  await expect(page.getByRole('heading', { name: '有 1 句原话在素材里没找到', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '用原素材重新制作', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: /大字模式.*20像素/ }).click();
  const failureLayouts = [];
  for (const width of [320, 900]) {
    await page.setViewportSize({ width, height: 900 });
    const geometry = await layout(page); failureLayouts.push({ width, geometry });
    m.evidence(`missing-quote-layout-${width}`, { width, geometry, assertionsPending: true });
    expect(geometry.fontSize).toBe('20px'); expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth + 1);
    expect(geometry.outside).toEqual([]);
  }
  await expect(page.getByRole('region', { name: '新建新闻作品', exact: true })).toHaveCount(0);
  expect(await page.evaluate(({ key, script }) => {
    const draft = JSON.parse(localStorage.getItem(key) ?? 'null')?.draft;
    return draft?.script === script && draft.mode === 'voiceover';
  }, { key: DRAFT_KEY, script: otherScript })).toBe(true);
  let capabilityRead = false, capabilityPreview = false;
  // Response callbacks retain booleans only. A ready UI alone could hide a
  // tokenless local fallback; require the ORIGINAL capability on real HTTP.
  page.on('response', response => {
    const request = response.request(), path = new URL(response.url()).pathname;
    if (request.method() === 'GET' && path === `/api/uploads/${upload.id}` && response.status() === 200) {
      capabilityRead ||= request.headers()['x-upload-token'] === m.uploads.get(upload.id);
    }
    if (request.method() === 'POST' && path === '/api/match/preview' && response.status() === 200) {
      const payload = request.postDataJSON() as { upload_ids?: string[]; upload_tokens?: Record<string, string> };
      capabilityPreview ||= payload.upload_ids?.length === 1 && payload.upload_ids[0] === upload.id
        && payload.upload_tokens?.[upload.id] === m.uploads.get(upload.id);
    }
  });
  m.confirmations.set(page, /^用这条失败任务提交时的原稿和素材清单替换当前创作草稿？/);
  await page.getByRole('button', { name: '返回修改原话与素材', exact: true }).click();
  await expect(page.getByRole('heading', { name: '写稿', exact: true })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '新闻稿（第一行标题，下一行正文）', exact: true })).toHaveValue(script);
  await expect(page.getByRole('group', { name: '制作模式', exact: true }).getByRole('button', { name: /只用原声/ })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.gm-sentence-row[data-kind=quote]')).toHaveCount(1);
  await page.getByRole('button', { name: '下一步：传素材 →', exact: true }).click();
  await expect(page.locator('.gm-media-item')).toHaveCount(1);
  await expect(page.locator('.gm-capacity')).toContainText('1 个已预处理', { timeout: 180_000 });
  await expect(page.locator('.gm-match-row[data-state=missing]')).toHaveCount(1);
  await expect.poll(() => capabilityRead && capabilityPreview).toBe(true);
  await expect(page.getByRole('button', { name: '下一步：选效果 →', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '保存草稿', exact: true }).click();
  await expect.poll(() => page.evaluate(({ key, script, uploadId, token }) => {
    const draft = JSON.parse(localStorage.getItem(key) ?? 'null')?.draft;
    return draft?.script === script && draft.mode === 'original' && draft.uploadIds?.length === 1
      && draft.uploadIds[0] === uploadId && draft.uploadTokens?.[uploadId] === token
      && draft.uploadBindings?.length === 1 && draft.uploadBindings[0].upload_id === uploadId;
  }, { key: DRAFT_KEY, script, uploadId: upload.id, token: m.uploads.get(upload.id)! })).toBe(true);
  expect(m.observations.filter(o => ['POST', 'PUT', 'PATCH', 'DELETE'].includes(o.method)
    && (o.path.startsWith('/api/tasks') || o.path.startsWith('/api/uploads')))).toEqual(writesBeforeReload);
  expect([...m.uploads.keys()]).toEqual([upload.id]);
  expect(await m.status(id)).toEqual(final);
  await noLogin(page);
  const after = await m.identity();
  expect(after.providerCalls.tts ?? 0).toBe(before.providerCalls.tts ?? 0);
  expect(after.uploadASRCalls).toEqual(before.uploadASRCalls);
  expect(m.observations.filter(o => o.method === 'POST' && o.path === '/api/tasks')).toHaveLength(1);
  m.checkpoint('quote-missing-stage6-no-automatic-retry');
  m.evidence('missing-quote', { errorKind: final!.errorKind, badRows: final!.badRows,
    stages: final!.stages.map(s => ({ number: s.number, status: s.status })), previewMissing: true, ttsCalled: false, failureLayouts,
    recoverySnapshotSetup: 'explicit-fixture-from-real-wizard-draft-after-api-receipt', productionSaveHookCovered: false,
    actualReloadThenExplicitEdit: true, differentUnsubmittedDraftReplacedOnlyAfterExplicitEdit: true,
    originalScriptModeAndUploadBindingsRestored: true,
    originalCapabilityOnUploadGET: capabilityRead, originalCapabilityOnPreviewPOST: capabilityPreview,
    noTaskOrUploadWritesAfterReload: true, noRepeatedASR: true });
});

test('MODES-08 320 and 900 pixel surfaces reflow at 20px including transcript, processing, quote and export', async ({ modes: m, page }) => {
  test.setTimeout(720_000);
  const samples: { screen: string; width: number; geometry: Awaited<ReturnType<typeof layout>> }[] = [];
  const check = async (screen: string) => {
    for (const width of [320, 900]) {
      await page.setViewportSize({ width, height: 900 });
      const geometry = await layout(page); samples.push({ screen, width, geometry });
      // Exclusive per-sample files survive any subsequent assertion/timeout.
      // Measured != passed: the final layout evidence is written only on success.
      m.evidence(`layout-sample-${samples.length}`, { screen, width, geometry, assertionsPending: true });
      expect(geometry.fontSize).toBe('20px'); expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth + 1);
      expect(geometry.outside).toEqual([]);
    }
    await noLogin(page);
  };
  await creator(page, 'mixed', m.manifest.scripts.mixed);
  await page.getByRole('button', { name: /大字模式.*20像素/ }).click();
  await check('mode-cards-b-manuscript-open-evidence');
  await page.locator('.gm-evidence-details > summary').click();
  await check('b-manuscript-closed-details');
  await page.getByRole('group', { name: '制作模式', exact: true }).getByRole('button', { name: /只用原声/ }).click();
  await page.getByRole('textbox', { name: '新闻稿（第一行标题，下一行正文）', exact: true }).fill(m.manifest.scripts.shortOriginal);
  await page.getByRole('button', { name: '下一步：传素材 →', exact: true }).click();
  await chooseInputs(m, page, [m.manifest.inputs.find(i => i.name === 'synthetic-citizen.mp4')!,
    ...m.manifest.inputs.filter(i => i.kind === 'broll').slice(0, 2)]);
  await expect(page.locator('.gm-match-row[data-state=ok]')).toHaveCount(1);
  await page.locator('.gm-file-transcript > summary').click();
  await page.locator('.gm-match-detail > summary').click();
  await check('upload-open-transcript-word-detail-speakers');
  await page.getByRole('button', { name: '下一步：选效果 →', exact: true }).click();
  await leanEffects(page, 'original');
  await page.getByText('效果说明与处理范围', { exact: true }).click();
  await page.getByText('本次提交清单 · 3 个素材', { exact: true }).click();
  await check('effects-expanded-submit-inventory');
  const submission = await submitWizard(m, page);
  await expect(page.getByRole('progressbar', { name: '服务器制作进度', exact: true })).toBeVisible({ timeout: 30_000 });
  await check('real-processing');
  await m.completed(submission.id, 0, 480_000);
  await openResult(page, submission.id);
  await expect(page.getByRole('group', { name: '原声句编辑', exact: true })).toBeVisible();
  await check('result-quote-waveform-checks');
  const word = page.locator('[data-quote-word="0"]'); await word.focus(); await word.press('[');
  await page.getByRole('button', { name: '记下剪短', exact: true }).click();
  await check('pending-trim-not-applied');
  await page.getByRole('button', { name: '撤销剪短', exact: true }).click();
  await confirmAllChecks(m, page, submission.id);
  await page.getByRole('button', { name: '导出作品', exact: true }).first().click();
  await check('export-dialog');
  await page.getByRole('dialog', { name: '导出作品', exact: true }).getByRole('combobox', { name: '格式', exact: true }).selectOption('mp3');
  await expect(page.getByRole('dialog', { name: '导出作品', exact: true })).toContainText('只导出现场原声');
  await check('export-mp3-description');
  await page.getByRole('dialog', { name: '导出作品', exact: true }).getByRole('button', { name: '关闭', exact: true }).click();
  await page.goto('/?modes-history=1#history');
  await expect(page.getByRole('heading', { name: '作品历史', exact: true })).toBeVisible();
  await check('history');
  await page.setViewportSize({ width: 320, height: 900 });
  await page.getByRole('button', { name: '打开导航菜单', exact: true }).click();
  const drawer = await layout(page); samples.push({ screen: 'navigation-drawer', width: 320, geometry: drawer });
  m.evidence(`layout-sample-${samples.length}`, { screen: 'navigation-drawer', width: 320, geometry: drawer, assertionsPending: true });
  expect(drawer.fontSize).toBe('20px');
  expect(drawer.scrollWidth).toBeLessThanOrEqual(drawer.clientWidth + 1); expect(drawer.outside).toEqual([]);
  await page.getByRole('button', { name: '关闭导航菜单', exact: true }).click();
  expect(m.observations.filter(o => o.method === 'POST' && o.path.endsWith('/remix'))).toHaveLength(0);
  m.checkpoint('all-mode-surfaces-narrow20px-no-overflow');
  m.evidence('layout', { synthetic: true, font: 20, samples, nativeScrollbarClaimed: false,
    hiddenDetailsRequireCheckVisibility: true, realProcessingNotMocked: true });
});