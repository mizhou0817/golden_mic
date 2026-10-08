/** Minimal Node boundary; no dependency or package-manifest changes required.
 * io.d.mts supplies the exact interface used by the strictly checked TS suite.
 */
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { createHash, randomUUID } from 'node:crypto';

export const env = process.env;
export const ROOT = path.resolve(fileURLToPath(new URL('../../../', import.meta.url)));
export const tempRoot = () => fs.realpathSync(os.tmpdir());
export const joinPath = (...parts) => path.join(...parts);
export const resolvePath = (...parts) => path.resolve(...parts);
export const parentPath = file => path.dirname(file);
export const basename = file => path.basename(file);
export const absolute = file => path.isAbsolute(file);
export const samePath = (left, right) => path.relative(path.resolve(left), path.resolve(right)) === '';
export function exists(file) {
  try { fs.lstatSync(file); return true; }
  catch (error) { if (error.code === 'ENOENT') return false; throw error; }
}
export const size = file => fs.statSync(file).size;
export const readText = file => fs.readFileSync(file, 'utf8');
export const readBytes = file => fs.readFileSync(file);
export const makeDirectory = directory => { fs.mkdirSync(directory, { recursive: true }); };
export const newInstance = () => randomUUID();
export const say = text => { process.stdout.write(text); };
export const bytesHash = bytes => createHash('sha256').update(bytes).digest('hex');
export const hashFile = file => bytesHash(readBytes(unlinked(file)));
export const writeJSON = (file, value) => fs.writeFileSync(file, JSON.stringify(value, null, 2), { encoding: 'utf8', flag: 'wx' });
export function inside(parent, child) {
  const relative = path.relative(path.resolve(parent), path.resolve(child));
  return relative !== '' && !relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative);
}
export function unlinked(file) {
  const absolutePath = path.resolve(file);
  let current = absolutePath;
  for (;;) {
    // Ordinary roots avoid Windows lstat(namespaced-volume-root) EISDIR.
    if (fs.lstatSync(current).isSymbolicLink()) throw new Error('Linked acceptance path refused');
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  if (!samePath(absolutePath, fs.realpathSync(absolutePath))) throw new Error('Noncanonical acceptance path refused');
  return absolutePath;
}