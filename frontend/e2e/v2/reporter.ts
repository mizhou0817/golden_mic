import { env, joinPath, makeDirectory, say, writeJSON } from '../modes/io.mjs';
import { bound, evidenceRoot, loadManifest, settings } from './environment';
/** Regex ONLY extracts static spec IDs/source coordinates, never raw errors. */
export const locations = (errors: readonly { stack?: string }[]) => errors.flatMap(e =>
  [...(e.stack ?? '').matchAll(/(?:acceptance\.v2\.spec|design\.v2\.spec|navigation\.v2\.spec|support|environment|global-setup)\.ts:\d+:\d+/g)].map(m => m[0]));
export default class V2Reporter {
  private suite = env.V2_DESIGN_ONLY === '1' ? 'design' : env.V2_NAV_ONLY === '1' ? 'navigation' : 'base';
  private expected = this.suite === 'design' ? ['VD-01', 'VD-02', 'VD-03', 'VD-04']
    : this.suite === 'navigation' ? ['V2-00'] : ['V2-01', 'V2-02', 'V2-03', 'V2-04', 'V2-05', 'V2-06'];
  private run = settings(); private manifest = loadManifest(this.run.file);
  private rows: { id: string; status: string; retry: number; durationMs: number; errorCount: number; sourceLocations: string[] }[] = [];
  private globalErrors = 0;
  printsToStdio() { return true; }
  onStdOut() { /* No raw output. */ }
  onStdErr() { /* No raw output. */ }
  onError() { this.globalErrors++; say('V2 runner error; details suppressed; no automatic retry.\n'); }
  onTestEnd(test: { title: string }, result: { status: string; retry: number; duration: number; errors: readonly { stack?: string }[] }) {
    const id = /^(?:V2|VD)-\d{2}\b/.exec(test.title)?.[0] ?? 'unknown-case';
    const status = /^(?:passed|failed|timedOut|skipped|interrupted)$/.test(result.status) ? result.status : 'unknown';
    this.rows.push({ id, status, retry: result.retry, durationMs: result.duration, errorCount: result.errors.length, sourceLocations: locations(result.errors) });
    say(`${id}: ${status}\n`);
  }
  onEnd(result: { status: 'passed' | 'failed' | 'timedout' | 'interrupted'; duration: number }) {
    let unchanged = false; try { unchanged = bound(this.manifest); } catch { /* Fail closed. */ }
    const complete = this.rows.length === this.expected.length && this.expected.every(id =>
      this.rows.filter(r => r.id === id && r.status === 'passed' && r.retry === 0).length === 1);
    const status = result.status === 'passed' && (!complete || !unchanged || this.globalErrors) ? 'failed' : result.status;
    const output = evidenceRoot(this.manifest, this.run.label); makeDirectory(output);
    writeJSON(joinPath(output, 'summary.json'), { kind: 'isolated-v2-acceptance', status, completeSuitePassed: complete && status === 'passed',
      suite: this.suite, expectedCases: this.expected.length, expectedCaseIds: this.expected,
      baseSixPassed: this.suite === 'base' && complete && status === 'passed',
      cases: this.rows, sourceBuildInputsUnchanged: unchanged, globalErrors: this.globalErrors,
      durationMs: result.duration, synthetic: true, speechEvidence: false, quotaAcceptanceClaimed: false,
      fullOriginalSampleRunsPlanned: this.suite === 'base' ? 1 : 0,
      endToEndModesRequired: this.suite === 'base' ? ['voiceover', 'mixed', 'original'] : this.suite === 'design' ? ['voiceover'] : [],
      real410BrowserCoverage: false, microphonePermissionCoverage: false,
      screenshotEvidence: false, retrySameBrowserCoverage: false, shutdownClaimed: false });
    say(`V2: ${status}; ${this.rows.filter(r => r.status === 'passed').length}/${this.rows.length}.\n`);
    return { status };
  }
}