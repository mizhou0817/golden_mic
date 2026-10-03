import { absolute, basename, env, inside, joinPath, parentPath, readText, ROOT, samePath, size, tempRoot, unlinked } from './io.mjs';

export const BASE_URL = 'http://127.0.0.1:8786';
export const DRAFT_KEY = 'gm-core-v1';
export interface Segment { id: string; start: number; end: number; text: string }
export interface InputFile {
  path: string; name: string; kind: 'interview' | 'broll'; bytes: number; sha256: string;
  durationSeconds: number; synthetic: true; audioKind: string;
  speaker: { id: string; name: string; title: string } | null; segments: Segment[];
}
export interface Sentence { idx: number; text: string; kind: 'narration' | 'quote'; speaker_hint?: string }
export interface ModeManifest {
  schemaVersion: 1; kind: 'golden-mic-mode-acceptance'; instanceId: string; synthetic: true;
  disclosure: string; baseURL: string; pid: number; root: string; taskRoot: string; serverState: string;
  frontend: { directory: string; hashes: Record<string, string>; sourceHashes: Record<string, string>; sourceBound: boolean };
  tools: { ffmpeg: string; ffprobe: string };
  sourceHashes: Record<string, string>;
  testHashes: Record<string, string>;
  sourceArtifacts: { path: string; kind: string; sha256: string }[];
  inputs: InputFile[];
  scripts: { mixed: string; original: string; voiceover: string; shortMixed: string; shortOriginal: string };
  mixedSentences: Sentence[];
  expected: { mixedNarration: 11; mixedQuotes: 5; originalQuotes: 8; originalDurationBounds: [number, number] };
  limits: { workers: 1; pending: 5; files: 20; ttlHours: 72; minFreeGB: 0; quality: 'warn'; generative: false };
  policy: Record<string, unknown>;
}
export function invariant(value: unknown, code: string): asserts value {
  if (!value) throw new Error(`Modes acceptance contract: ${code}`);
}
export function runSettings() {
  const label = env.MODES_RUN_LABEL, manifestPath = env.MODES_MANIFEST;
  invariant(label && /^[a-z0-9][a-z0-9-]{0,63}$/.test(label), 'MODES_RUN_LABEL must be fresh and safe');
  invariant(!env.MODES_BASE_URL || env.MODES_BASE_URL === BASE_URL, 'exact dedicated origin only');
  invariant(manifestPath && absolute(manifestPath), 'MODES_MANIFEST must be the absolute host-issued TEMP path');
  return { label, manifestPath, baseURL: BASE_URL };
}
export function loadManifest(file: string): ModeManifest {
  invariant(inside(tempRoot(), file) && !inside(ROOT, file), 'manifest must be outside workspace in TEMP');
  invariant(size(unlinked(file)) < 4 * 1024 * 1024, 'bounded manifest');
  const m = JSON.parse(readText(file)) as ModeManifest;
  invariant(m.schemaVersion === 1 && m.kind === 'golden-mic-mode-acceptance' && m.synthetic === true, 'wrong host kind');
  invariant(m.baseURL === BASE_URL && m.serverState === 'ready' && /^[a-f0-9]{32}$/.test(m.instanceId), 'exact host must be ready');
  invariant(absolute(m.root) && inside(tempRoot(), m.root) && !inside(ROOT, m.root)
    && /^golden-mic-modes-/.test(basename(m.root)), 'fresh TEMP root');
  unlinked(m.root);
  invariant(samePath(m.taskRoot, joinPath(m.root, 'tasks')), 'task root binding');
  invariant(samePath(parentPath(m.frontend.directory), joinPath(ROOT, 'frontend'))
    && /^dist-canary-[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(basename(m.frontend.directory)), 'custom frontend only; shared dist forbidden');
  unlinked(m.frontend.directory);
  invariant(m.disclosure.startsWith('SYNTHETIC ACCEPTANCE ONLY:'), 'synthetic disclosure');
  invariant(m.policy.dotenv === false && m.policy.inheritedSettings === false && m.policy.realData === false
    && m.policy.paidCalls === false && m.policy.accounts === false && m.policy.seededTasks === false
    && m.policy.qcPatched === false && m.policy.allTenStages === true && m.policy.sharedDistAccess === false
    && m.policy.asr === 'record-name-and-sha256-injected' && m.policy.wordTiming === 'evenly-spaced-synthetic-labels'
    && m.policy.httpEgress === 'exact-fake-listener-only' && m.policy.websocketEgress === 'deny-all', 'isolation policy');
  invariant(m.limits.workers === 1 && m.limits.pending === 5 && m.limits.files === 20 && m.limits.ttlHours === 72
    && m.limits.quality === 'warn' && m.limits.generative === false && m.limits.minFreeGB === 0, 'fixed host capacity');
  invariant(m.inputs.length === 7 && new Set(m.inputs.map(i => i.name)).size === 7, 'three interviews and four broll inputs');
  for (const input of m.inputs) {
    invariant(input.synthetic === true && /^(?:synthetic-(?:organizer|vendor|citizen)|synthetic-broll-[1-4])\.mp4$/.test(input.name)
      && samePath(input.path, joinPath(m.root, 'inputs', input.name)) && input.bytes > 0 && input.bytes <= 16 * 1024 * 1024
      && /^[a-f0-9]{64}$/.test(input.sha256) && input.durationSeconds > 0 && input.durationSeconds <= 60, 'bounded immutable input');
    unlinked(input.path);
  }
  invariant(m.inputs.filter(i => i.kind === 'interview').length === 3 && m.inputs.filter(i => i.kind === 'broll').length === 4, 'input kinds');
  invariant(m.mixedSentences.length === 16 && m.mixedSentences.filter(s => s.kind === 'quote').length === 5
    && m.mixedSentences.filter(s => s.kind === 'narration').length === 11 && m.expected.originalQuotes === 8, 'prototype parser contract');
  for (const [name, digest] of Object.entries(m.frontend.hashes)) {
    invariant(/^(?:index\.html|assets\/[a-zA-Z0-9_./-]+)$/.test(name) && !name.split('/').includes('..')
      && /^[a-f0-9]{64}$/.test(digest), 'build entry');
  }
  for (const [name, digest] of Object.entries(m.sourceHashes)) invariant(
    /^(?:backend\/[a-zA-Z0-9_./-]+\.(?:py|json)|tests\/(?:mode_acceptance_server|workspace_fixture)\.py)$/.test(name)
    && !name.split('/').includes('..') && /^[a-f0-9]{64}$/.test(digest), 'source binding entry');
  invariant(typeof m.frontend.sourceBound === 'boolean' && m.frontend.sourceHashes && typeof m.frontend.sourceHashes === 'object', 'frontend source binding shape');
  for (const [name, digest] of Object.entries(m.frontend.sourceHashes)) invariant(
    /^(?:frontend\/(?:src\/[A-Za-z0-9_./-]+|index\.html|package\.json|postcss\.config\.cjs|tailwind\.config\.ts)|backend\/mode_rules\.json)$/.test(name)
    && !name.split('/').includes('..') && /^[a-f0-9]{64}$/.test(digest), 'frontend source binding entry');
  invariant(m.testHashes && Object.keys(m.testHashes).length >= 9, 'complete test source binding');
  for (const [name, digest] of Object.entries(m.testHashes)) invariant(
    /^(?:frontend\/playwright\.modes\.config\.ts|frontend\/e2e\/modes\/[A-Za-z0-9_.-]+)$/.test(name)
    && !name.split('/').includes('..') && /^[a-f0-9]{64}$/.test(digest), 'test source binding entry');
  invariant(m.sourceArtifacts.length === 1 && samePath(m.sourceArtifacts[0].path, joinPath(m.root, 'synthetic-asr-fixtures.json')),
    'fixture artifact binding');
  return m;
}
export const evidenceRoot = (m: ModeManifest, label: string) => joinPath(m.root, 'browser-runs', label);
export function checkedURL(pathname: string) {
  invariant(pathname.startsWith('/') && !pathname.startsWith('//') && !/[\\\r\n]/.test(pathname), 'relative local request');
  const url = new URL(pathname, BASE_URL);
  invariant(url.origin === BASE_URL && !url.username && !url.password, 'exact origin only');
  return url.href;
}