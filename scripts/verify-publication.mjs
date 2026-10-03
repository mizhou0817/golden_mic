/** Check publication inputs, never private files or ignored evidence payloads. */
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, posix, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const CURRENT_DOCS = [
  'README.md', 'deploy/README.md', 'docs/README.md', 'docs/QUICKSTART.md',
  'docs/USER_GUIDE.md', 'docs/CONFIGURATION.md', 'docs/TROUBLESHOOTING.md',
  'docs/DEVELOPMENT.md', 'docs/RUNBOOK.md', 'docs/V2_PRODUCT_DELIVERY_20261003.md',
];
const generated = /^(?:canary_test\/artifacts(?:\/|$)|frontend\/dist[^/]*(?:\/|$)|data(?:\/|$)|eval_sample(?:\/|$)|private-models(?:\/|$))/;
const SOURCE = 'https://github.com/mizhou0817/golden_mic/';

export function checkLinks(documents, published) {
  const files = new Set(published), errors = [];
  const exists = target => files.has(target) || [...files].some(name => name.startsWith(target.replace(/\/$/, '') + '/'));
  let checked = 0;
  for (const [file, text] of Object.entries(documents)) {
    const prose = text.replace(/^```[^\n]*\r?\n[\s\S]*?^```[^\n]*$/gm, value => value.replace(/[^\r\n]/g, ' '));
    for (const match of prose.matchAll(/\[[^\]\n]+\]\(([^)\n]+)\)/g)) {
      let destination = match[1].trim().replace(/^<|>$/g, ''), external = false;
      if (destination.startsWith(SOURCE + 'blob/main/') || destination.startsWith(SOURCE + 'tree/main/')) {
        destination = destination.slice(SOURCE.length).replace(/^(?:blob|tree)\/main\//, '');
        external = true;
      } else if (/^(?:[a-z]+:|#|\/\/)/i.test(destination)) continue;
      let target;
      try {
        target = decodeURIComponent(destination.split('#')[0].split('?')[0]);
      } catch { errors.push({ file, reason: 'invalid_link_encoding' }); continue; }
      if (!target) continue;
      target = posix.normalize(external ? target : posix.join(posix.dirname(file), target));
      checked++;
      if (target.startsWith('../') || generated.test(target) || !exists(target)) {
        errors.push({ file, line: text.slice(0, match.index).split('\n').length, target,
          reason: generated.test(target) ? 'unpublished_generated_target' : 'missing_published_target' });
      }
    }
  }
  return { checked, errors };
}

export function verify(root) {
  // Git's publication set, not local filesystem existence. Do not read any
  // ignored .env/data/evidence/model files while auditing candidate inputs.
  const files = execFileSync('git', ['ls-files', '--cached', '--others', '--exclude-standard', '-z'],
    { cwd: root, encoding: 'utf8' }).split('\0').filter(Boolean);
  const documents = Object.fromEntries(CURRENT_DOCS.map(file => {
    assert.ok(files.includes(file), `Missing published guide: ${file}`);
    return [file, readFileSync(resolve(root, file), 'utf8')];
  }));
  const result = checkLinks(documents, files);
  const privateFiles = files.filter(file => generated.test(file) || /(?:^|\/)\.env$|\.(?:pem|key|pfx|p12|onnx|safetensors|pt|pth)$/.test(file));
  assert.deepEqual(privateFiles, [], 'Private inputs must not be published');
  assert.deepEqual(result.errors, [], 'Published navigation has missing/private/generated targets');
  console.log(JSON.stringify({ publication: 'pass', documents: CURRENT_DOCS.length, links: result.checked }));
  return result;
}

if (process.argv[1] && pathToFileURL(resolve(process.argv[1])).href === import.meta.url) {
  verify(resolve(dirname(fileURLToPath(import.meta.url)), '..'));
}