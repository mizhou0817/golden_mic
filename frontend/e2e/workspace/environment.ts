/** Standalone safety/binding contract. Never reads dotenv, product data or old evidence. */
import { absolute, basename, env, hashFile, inside, joinPath, parentPath, readText, ROOT, samePath, size, tempRoot, unlinked } from './io.mjs';

export { ROOT, inside, unlinked, hashFile };
export const DRAFT_KEY = 'golden-mic.draft.v2';
export const HISTORY_KEY = 'golden-mic.history.v1';
export interface WorkspaceManifest {
  schemaVersion: 1; kind: 'golden-mic-workspace-acceptance'; instanceId: string;
  synthetic: true; disclosure: string; baseURL: string; root: string; taskRoot: string;
  serverState: 'ready';
  frontend: { directory: string; hashes: Record<string, string> };
  script: string; editedSentence: string;
  inputs: { path: string; name: string; bytes: number; sha256: string; durationSeconds: number; synthetic: true }[];
  seed: { taskId: string; revision: number; durationSeconds: number; blockingIssues: number;
    stageCount: number; snapshotDirectory: string; snapshotHashes: Record<string, string>; artifactHashes: Record<string, string> };
  policy: Record<string, unknown>;
}
export function invariant(value: unknown, code: string): asserts value {
  if (!value) throw new Error(`Workspace safety/contract check: ${code}`);
}
export function runSettings() {
  const label = env.WORKSPACE_RUN_LABEL;
  invariant(label && /^[a-z0-9][a-z0-9-]{0,63}$/.test(label), 'WORKSPACE_RUN_LABEL must be a new safe label');
  const baseURL = env.WORKSPACE_BASE_URL ?? 'http://127.0.0.1:8782';
  invariant(/^http:\/\/127\.0\.0\.1:(?:878[2-9]|879[0-9])$/.test(baseURL), 'only a dedicated 127.0.0.1 acceptance origin is allowed');
  const manifestPath = env.WORKSPACE_MANIFEST;
  invariant(manifestPath && absolute(manifestPath), 'WORKSPACE_MANIFEST must be an absolute host-issued TEMP path');
  return { label, baseURL, manifestPath, outputRoot: joinPath(ROOT, 'canary_test', 'artifacts', `workspace-${label}`) };
}
export function loadManifest(file: string, baseURL: string): WorkspaceManifest {
  invariant(inside(tempRoot(), file) && !inside(ROOT, file), 'manifest must be outside workspace, below system TEMP');
  invariant(size(unlinked(file)) <= 4 * 1024 * 1024, 'manifest size');
  const m = JSON.parse(readText(file)) as WorkspaceManifest;
  invariant(m.schemaVersion === 1 && m.kind === 'golden-mic-workspace-acceptance' && m.synthetic === true,
    'wrong host manifest kind');
  invariant(m.baseURL === baseURL && m.serverState === 'ready' && /^[a-f0-9]{32}$/.test(m.instanceId), 'host must be ready and match exact origin');
  invariant(absolute(m.root) && inside(tempRoot(), m.root) && !inside(ROOT, m.root)
    && /^golden-mic-workspace-/.test(basename(m.root)), 'fresh synthetic root');
  unlinked(m.root);
  invariant(samePath(m.taskRoot, joinPath(m.root, 'tasks')), 'task root binding');
  invariant(samePath(parentPath(m.frontend.directory), joinPath(ROOT, 'frontend'))
    && /^dist(?:-[a-zA-Z0-9_-]+)?$/.test(basename(m.frontend.directory)), 'frontend dist binding');
  unlinked(m.frontend.directory);
  invariant(m.policy.dotenv === false && m.policy.inheritedSettings === false && m.policy.realData === false
    && m.policy.paidCalls === false && m.policy.accounts === false && m.policy.qcPatched === false
    && m.policy.allTenStages === true && m.policy.httpEgress === 'exact-fake-listener-only'
    && m.policy.websocketEgress === 'deny-all' && m.policy.asr === false && m.policy.ownVoice === false,
  'unapproved fixture policy');
  invariant(m.disclosure.startsWith('SYNTHETIC ACCEPTANCE ONLY:'), 'missing synthetic disclosure');
  invariant(m.seed && /^[a-f0-9]{32}$/.test(m.seed.taskId) && m.seed.revision === 0 && m.seed.stageCount === 10
    && m.seed.blockingIssues === 0 && m.seed.durationSeconds > 2 && m.seed.durationSeconds < 20,
  'complete short QC-checked seed required');
  invariant(samePath(m.seed.snapshotDirectory, joinPath(m.root, 'original-seed-snapshot')), 'snapshot binding');
  const artifacts = Object.entries(m.seed.artifactHashes);
  invariant(artifacts.length >= 20 && ['final.mp4', 'report.json', 'quality_report.json', 'timings.json', 'task_state.json']
    .every(name => artifacts.some(([key]) => key === name)), 'complete seed artifact hashes');
  for (const [name, digest] of artifacts) invariant(/^[a-z_]+\.(?:json|txt|ass|m4a|mp4)$/.test(name)
    && /^[a-f0-9]{64}$/.test(digest), 'unsafe seed artifact entry');
  const snapshot = Object.entries(m.seed.snapshotHashes);
  invariant(snapshot.length >= artifacts.length && snapshot.length <= 10_000, 'complete original snapshot hashes');
  for (const [name, digest] of snapshot) invariant(/^[A-Za-z0-9_.-]+(?:\/[A-Za-z0-9_.-]+)*$/.test(name)
    && !name.split('/').some(part => part === '.' || part === '..') && /^[a-f0-9]{64}$/.test(digest), 'unsafe snapshot entry');
  invariant(Array.isArray(m.inputs) && m.inputs.length === 4, 'four synthetic input files required');
  invariant(new Set(m.inputs.map(input => input.name)).size === 4, 'distinct synthetic inputs');
  for (const input of m.inputs) {
    invariant(input.synthetic === true && /^synthetic-pattern-[1-4]\.mp4$/.test(input.name)
      && samePath(input.path, joinPath(m.root, 'inputs', input.name)) && input.bytes > 0 && input.bytes <= 8 * 1024 * 1024
      && /^[a-f0-9]{64}$/.test(input.sha256), 'unsafe input file');
    unlinked(input.path);
  }
  return m;
}
export function checkedURL(base: string, pathname: string): string {
  invariant(pathname.startsWith('/') && !pathname.startsWith('//') && !/[\\\r\n]/.test(pathname), 'unsafe request path');
  const url = new URL(pathname, base);
  invariant(url.origin === base && !url.username && !url.password, 'request must stay on the exact host');
  return url.href;
}