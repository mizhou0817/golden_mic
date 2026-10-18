import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

// "AI 自动选效果" client: only whitelisted values can reach the effects step, and the summary names the choice.
const read = p => readFileSync(new URL(p, import.meta.url), 'utf8');
const compile = text => ts.transpileModule(text, { fileName: 'actual.ts', compilerOptions: {
  module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const plain = value => JSON.parse(JSON.stringify(value));
const requests = [];
const lib = (() => {
  const module = { exports: {} };
  vm.runInNewContext(compile(read('../src/lib/effectsSuggest.ts')), { module, exports: module.exports, Error, JSON, Object, Array,
    require: name => {
      if (name === './appApi') return { createAppRequest: () => async (path, init) => { requests.push([path, JSON.parse(init.body)]);
        return { preferences: { tone: 'energetic', pacing: 'fast', voice: 'mine' }, reason: '节庆活动', source: 'ai' }; } };
      throw Error('unexpected ' + name);
    } });
  return module.exports;
})();

test('only whitelisted effect values are kept; anything else is dropped', () => {
  const parsed = lib.parseEffectsSuggestion({ preferences: { tone: 'solemn', pacing: 'warp', background_music: false, music_mood: 'tense',
    voice: 'mine', custom_instructions: 'x', generative_fill: true, caption_style: 'big', lower_third: 'yes' }, reason: '突发事件', source: 'ai' });
  assert.deepEqual(plain(parsed), { preferences: { tone: 'solemn', music_mood: 'tense', caption_style: 'big', background_music: false }, reason: '突发事件', source: 'ai' });
  assert.equal(lib.parseEffectsSuggestion({ preferences: { tone: 'neutral' }, reason: 'x'.repeat(31), source: 'other' }).reason, '');
  assert.equal(lib.parseEffectsSuggestion({ preferences: { tone: 'neutral' } }).source, 'rules');
  for (const bad of [null, [], {}, { preferences: [] }, { preferences: { voice: 'mine' } }]) {
    assert.throws(() => lib.parseEffectsSuggestion(bad), /AI 没有给出可用的效果/, JSON.stringify(bad));
  }
});

test('the request sends the mode and manuscript once, and an empty manuscript never calls the server', async () => {
  requests.length = 0;
  const result = await lib.requestEffectsSuggestion('mixed', '迎春市集\n热闹开市。', new AbortController().signal);
  assert.deepEqual(requests, [['/api/script/effects', { mode: 'mixed', script: '迎春市集\n热闹开市。' }]]);
  assert.deepEqual(plain(result.preferences), { tone: 'energetic', pacing: 'fast' });
  await assert.rejects(lib.requestEffectsSuggestion('voiceover', '   ', new AbortController().signal), /先写好稿子/);
  assert.equal(requests.length, 1);
});

test('the one-line summary names tone, pace, music and captions', () => {
  const base = { tone: 'energetic', pacing: 'normal', background_music: true, music_mood: 'uplifting', caption_style: 'news', motion_effects: true };
  assert.equal(lib.describeEffects(base, 'voiceover'), '明快 · 正常语速 · 背景音乐（昂扬） · 新闻标准字幕 · 画面推近');
  assert.equal(lib.describeEffects({ ...base, tone: 'solemn', background_music: false, motion_effects: false, caption_style: 'big' }, 'original'), '庄重 · 无背景音乐 · 大字字幕');
});
