import { execFile } from 'node:child_process';
import { createHash } from 'node:crypto';
import { promisify } from 'node:util';
import { mkdtemp, open, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

/** Probe an authenticated export via bounded Range reads, not a whole-file buffer. */
export async function probeExport(readChunk, expectedBytes, format) {
  if (!['mp4', 'mov', 'mkv', 'avi', 'gif'].includes(format)) throw Error('Unsupported probe extension');
  if (!Number.isSafeInteger(expectedBytes) || expectedBytes <= 0 || expectedBytes > 128 * 1024 * 1024) {
    throw Error('Export must fit the product 128 MiB output bound before downloading.');
  }
  const directory = await mkdtemp(join(tmpdir(), 'golden-mic-round2-probe-'));
  const file = join(directory, `output.${format}`);
  try {
    const hash = createHash('sha256');
    let chunks = 0;
    const handle = await open(file, 'wx');
    try {
      // GIF is lossless and may exceed the small-format helper's 8 MiB read
      // ceiling even at 360p. Keep that ceiling: spool at most 1 MiB per GET,
      // verify every actual Content-Range, and retain no media as evidence.
      for (let start = 0; start < expectedBytes; start += 1024 * 1024) {
        const end = Math.min(start + 1024 * 1024 - 1, expectedBytes - 1);
        const chunk = await readChunk(`bytes=${start}-${end}`);
        if (chunk.contentRange !== `bytes ${start}-${end}/${expectedBytes}`
          || chunk.bytes.length !== end - start + 1) throw Error('Export Range length/offset mismatch.');
        await handle.writeFile(chunk.bytes);
        hash.update(chunk.bytes);
        chunks++;
      }
      if ((await handle.stat()).size !== expectedBytes) throw Error('Export temporary-file length mismatch.');
    } finally { await handle.close(); }
    const { stdout } = await promisify(execFile)('ffprobe', ['-v', 'error', '-show_entries',
      'format=format_name,duration:stream=codec_type,codec_name,width,height,avg_frame_rate,sample_rate',
      '-of', 'json', file], { timeout: 30000, maxBuffer: 128 * 1024, windowsHide: true });
    return { probe: JSON.parse(stdout), bytes: expectedBytes, sha256: hash.digest('hex'),
      rangeChunks: chunks, maxChunkBytes: 1024 * 1024 };
  } finally { await rm(directory, { recursive: true, force: true }); }
}