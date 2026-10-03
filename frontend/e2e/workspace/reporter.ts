import { joinPath, makeDirectory, say, writeJSON } from './io.mjs';
import { runSettings } from './environment';

// Narrow structural hooks: the installed reporter declaration files may be
// unavailable on disk. Do not introduce a dependency or disable TS validation.
interface Result { status: string; retry: number; duration: number; errors: readonly unknown[] }
interface CompletedRun { status: string; duration: number }

/** No raw errors, stdout, console, DOM, URLs, traces or token-bearing attachments. */
export default class SafeReporter {
  private rows: { title: string; status: string; retry: number; durationMs: number; errorCount: number }[] = [];
  private globalErrors = 0;
  printsToStdio() { return true; }
  onStdOut() { /* Deliberately discard raw output. */ }
  onStdErr() { /* Deliberately discard raw output. */ }
  onError() { this.globalErrors++; say('Workspace runner error (raw details suppressed).\n'); }
  onTestEnd(test: { title: string }, result: Result) {
    // Titles in this suite are static identifiers, never request inputs.
    this.rows.push({ title: test.title, status: result.status, retry: result.retry,
      durationMs: result.duration, errorCount: result.errors.length });
    say(`${test.title}: ${result.status}\n`);
  }
  onEnd(result: CompletedRun) {
    const run = runSettings();
    makeDirectory(run.outputRoot);
    writeJSON(joinPath(run.outputRoot, 'summary.json'), {
      kind: 'synthetic-workspace-acceptance', status: result.status, globalErrors: this.globalErrors,
      durationMs: result.duration, cases: this.rows, noRealData: true, noPaidCalls: true,
      limitations: ['Deterministic fake providers; tone is not speech.', 'No real ASR, voice or model-quality validation.',
        'Page/provider network confinement is not an OS-wide firewall.', 'No historical or real-data canary executed.'],
    });
    say(`Workspace: ${this.rows.filter(row => row.status === 'passed').length}/${this.rows.length} passed; ${result.status}.\n`);
  }
}