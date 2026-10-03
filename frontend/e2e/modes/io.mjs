/** Node-only boundary. No dotenv, product data, old evidence or browser profile. */
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createHash, randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

export const ROOT = path.resolve(fileURLToPath(new URL('../../../', import.meta.url)));
export const env = process.env;
export const joinPath = (...parts) => path.join(...parts);
export const parentPath = file => path.dirname(file);
export const basename = file => path.basename(file);
export const absolute = file => path.isAbsolute(file);
export const samePath = (a, b) => path.relative(path.resolve(a), path.resolve(b)) === '';
export const tempRoot = () => os.tmpdir();
export const newInstance = () => randomUUID();
export const say = text => process.stdout.write(text);
export const bytesHash = bytes => createHash('sha256').update(bytes).digest('hex');
export function inside(parent, child) {
  const relative = path.relative(path.resolve(parent), path.resolve(child));
  return relative !== '' && relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative);
}
export function exists(file) {
  try { fs.lstatSync(file); return true; }
  catch (error) { if (error.code === 'ENOENT') return false; throw error; }
}
export function unlinked(file) {
  const resolved = path.resolve(file);
  for (let current = resolved; ; current = path.dirname(current)) {
    // Windows volume roots must NOT use toNamespacedPath (Node EISDIR).
    const native = path.dirname(current) === current ? current : path.toNamespacedPath(current);
    if (fs.lstatSync(native).isSymbolicLink()) throw new Error('Linked mode acceptance path refused');
    if (path.dirname(current) === current) break;
  }
  if (!samePath(resolved, fs.realpathSync(resolved))) throw new Error('Noncanonical mode acceptance path refused');
  return resolved;
}
function readable(file) {
  const resolved = unlinked(file);
  if (path.basename(resolved).startsWith('.env') || ['data', 'eval', 'eval_sample', 'canary_test/artifacts', 'frontend/dist']
    .some(name => samePath(path.join(ROOT, name), resolved) || inside(path.join(ROOT, name), resolved))) {
    throw new Error('Real data/shared dist access refused');
  }
  return resolved;
}
export const size = file => fs.statSync(readable(file)).size;
export function readBytes(file, maximum = 16 * 1024 * 1024) {
  const resolved = readable(file);
  if (fs.statSync(resolved).size > maximum) throw new Error('Bounded mode evidence read exceeded');
  const bytes = fs.readFileSync(path.toNamespacedPath(resolved));
  if (bytes.length > maximum) throw new Error('Mode evidence grew while reading');
  return bytes;
}
export const readText = (file, maximum) => readBytes(file, maximum).toString('utf8').replace(/^\uFEFF/, '');
export function hashFile(file) {
  const descriptor = fs.openSync(path.toNamespacedPath(readable(file)), 'r');
  try {
    const hash = createHash('sha256'), buffer = Buffer.alloc(1024 * 1024);
    const before = fs.fstatSync(descriptor);
    if (before.size > 512 * 1024 * 1024) throw new Error('Synthetic file hash budget exceeded');
    let count = 0, length;
    while ((length = fs.readSync(descriptor, buffer)) > 0) { hash.update(buffer.subarray(0, length)); count += length; }
    const after = fs.fstatSync(descriptor);
    if (count !== before.size || before.size !== after.size || before.mtimeMs !== after.mtimeMs) throw new Error('Synthetic file drift');
    return hash.digest('hex');
  } finally { fs.closeSync(descriptor); }
}
function writable(file) {
  if (!absolute(file) || !inside(tempRoot(), file) || inside(ROOT, file)) throw new Error('Evidence writes require owned TEMP');
  unlinked(parentPath(file));
}
export function makeDirectory(directory) {
  if (!absolute(directory) || !inside(tempRoot(), directory) || inside(ROOT, directory)) throw new Error('Evidence directory must be TEMP');
  let ancestor = directory;
  while (!exists(ancestor)) ancestor = parentPath(ancestor);
  unlinked(ancestor);
  fs.mkdirSync(directory, { recursive: true });
  unlinked(directory);
}
export function writeJSON(file, data) {
  writable(file);
  // ASCII output also survives Windows PowerShell's native stdout codepage.
  const content = JSON.stringify(data, null, 2).replace(/[\u007f-\uffff]/g, c => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`);
  fs.writeFileSync(file, content + '\n', { flag: 'wx', encoding: 'utf8' });
}
export function snapshotHashes(directory) {
  const root = readable(directory), result = {};
  if (!inside(tempRoot(), root) || inside(ROOT, root)) throw new Error('Snapshot must belong to TEMP host');
  const visit = dir => {
    for (const item of fs.readdirSync(dir, { withFileTypes: true })) {
      const file = unlinked(path.join(dir, item.name));
      if (item.isDirectory()) visit(file);
      else if (item.isFile()) {
        if (Object.keys(result).length >= 4096) throw new Error('Snapshot file budget exceeded');
        result[path.relative(root, file).split(path.sep).join('/')] = hashFile(file);
      } else throw new Error('Unexpected snapshot entry');
    }
  };
  visit(root);
  return result;
}
export function probeMedia(file, ffprobe) {
  const input = readable(file), executable = unlinked(ffprobe);
  if (!inside(tempRoot(), input) || inside(ROOT, input) || !/^ffprobe(?:\.exe)?$/i.test(basename(executable))) {
    throw new Error('Probe requires exact host tool and TEMP media');
  }
  const keep = /^(?:SYSTEMROOT|WINDIR|PATH|PATHEXT|SYSTEMDRIVE|TEMP|TMP|LOCALAPPDATA|NUMBER_OF_PROCESSORS)$/i;
  const environment = Object.fromEntries(Object.entries(env).filter(([key]) => keep.test(key)));
  const run = spawnSync(executable, ['-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_streams', '-show_format', '-of', 'json', input],
    { env: environment, shell: false, windowsHide: true, timeout: 90_000, maxBuffer: 2 * 1024 * 1024, encoding: 'utf8' });
  if (run.status !== 0 || run.error) throw new Error('Real ffprobe failed; not replaced by fixture metadata');
  const value = JSON.parse(run.stdout), video = value.streams.find(s => s.codec_type === 'video');
  const audio = value.streams.find(s => s.codec_type === 'audio');
  return { bytes: size(input), sha256: hashFile(input), duration: Number(value.format.duration),
    width: video?.width ?? null, height: video?.height ?? null, fps: video?.r_frame_rate ?? null,
    videoCodec: video?.codec_name ?? null, audioCodec: audio?.codec_name ?? null,
    audioSampleRate: audio ? Number(audio.sample_rate) : null };
}