import { hashFile, joinPath, makeDirectory, ROOT, say, writeJSON } from './io.mjs';
import { evidenceRoot, loadManifest, runSettings } from './environment';

interface Result { status: string; retry: number; duration: number; errors: readonly unknown[] }
interface CompletedRun { status: string; duration: number }

/** Never persist raw DOM, stdout, errors, request bodies, query tokens or traces. */
export default class ModesReporter {
  private readonly run = runSettings();
  private readonly manifest = loadManifest(this.run.manifestPath);
  private rows: { title: string; status: string; retry: number; durationMs: number; errorCount: number }[] = [];
  private globalErrors = 0;
  printsToStdio() { return true; }
  onStdOut() { /* Raw worker output deliberately suppressed. */ }
  onStdErr() { /* No request capabilities in reports. */ }
  onError() { this.globalErrors++; say('Modes runner error; raw details suppressed. No automatic rerun.\n'); }
  onTestEnd(test: { title: string }, result: Result) {
    this.rows.push({ title: test.title, status: result.status, retry: result.retry,
      durationMs: result.duration, errorCount: result.errors.length });
    say(`${test.title}: ${result.status}\n`);
  }
  onEnd(result: CompletedRun) {
    // Retain the original binding even if the host later crashes or stops.
    const { run, manifest } = this, output = evidenceRoot(manifest, run.label);
    let bindingUnchanged = false;
    try {
      bindingUnchanged = Object.entries(manifest.sourceHashes).every(([name, hash]) => hashFile(joinPath(ROOT, name)) === hash)
        && Object.entries(manifest.testHashes).every(([name, hash]) => hashFile(joinPath(ROOT, name)) === hash)
        && Object.entries(manifest.frontend.sourceHashes).every(([name, hash]) => hashFile(joinPath(ROOT, name)) === hash)
        && Object.entries(manifest.frontend.hashes).every(([name, hash]) => hashFile(joinPath(manifest.frontend.directory, name)) === hash)
        && manifest.inputs.every(input => hashFile(input.path) === input.sha256);
    } catch { bindingUnchanged = false; }
    const status = result.status === 'passed' && !bindingUnchanged ? 'failed' : result.status;
    makeDirectory(output);
    writeJSON(joinPath(output, 'summary.json'), { kind: 'synthetic-three-mode-browser-acceptance', status,
      globalErrors: this.globalErrors, durationMs: result.duration, cases: this.rows, bindingUnchanged,
      frontendSourcesBoundToBuild: manifest.frontend.sourceBound,
      allCasesPassed: this.rows.length === 8 && this.rows.every(row => row.status === 'passed' && row.retry === 0),
      noPaidCalls: true, speechEvidence: false, expectedCompleteSuiteCases: 8,
      limits: ['Injected synthetic ASR text/speakers/even word clocks; tone is not speech.',
        'Real local uploads, alignment, FFmpeg, revisions and exports; model quality not validated.',
        'Python/HTTP guards are not an OS-wide firewall; no production or full WCAG acceptance.',
        'Host shutdown belongs to parent; summary does not assert serverState=stopped.'] });
    say(`Modes: ${this.rows.filter(row => row.status === 'passed').length}/${this.rows.length} passed; ${status}.\n`);
    return { status };
  }
}