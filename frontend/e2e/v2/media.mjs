import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { inside, ROOT, tempRoot, unlinked, probeMedia } from '../modes/io.mjs';

/** Exclusive retained TEMP download, max 1 MiB/response and 128 MiB total.
 * No capability, response body, error body or URL is written to evidence.
 * No unbounded download.path()/response.body() of an entire movie/GIF.
 */
export async function rangeDownload(api, url, headers, target, ffprobe, instance, format, duration, onFailure) {
  // Closed projection only. Never pass the error/response/request to a callback.
  const facts = { stage: 'duration', offset: 0, status: null, identityMatches: null,
    rangeStart: null, rangeEnd: null, rangeTotal: null, length: null, expectedLength: null, bodyLength: null };
  try {
    return await download(api, url, headers, target, ffprobe, instance, format, duration, facts);
  } catch (error) {
    try {
      const number = value => typeof value === 'number' && Number.isSafeInteger(value) ? value : null;
      const errorClass = ['Error', 'TypeError', 'RangeError', 'TimeoutError', 'AbortError'].find(name => name === error?.name) ?? 'other';
      // Synchronous evidence failures AND rejected async callbacks cannot replace
      // the original failure. Do not await a diagnostic sink or retry a download.
      Promise.resolve(onFailure?.({ stage: facts.stage, offset: number(facts.offset), status: number(facts.status),
        identityMatches: facts.identityMatches, rangeStart: number(facts.rangeStart), rangeEnd: number(facts.rangeEnd),
        rangeTotal: number(facts.rangeTotal), length: number(facts.length), expectedLength: number(facts.expectedLength),
        bodyLength: number(facts.bodyLength), errorClass })).catch(() => {});
    } catch { /* Diagnostic-only: preserve the exact original rejection. */ }
    throw error;
  }
}

async function download(api, url, headers, target, ffprobe, instance, format, duration, facts) {
  // Independent product v2 format budgets, not the old Studio blanket 128MiB.
  if (!Number.isFinite(duration) || duration < 0 || duration > 3600) throw new Error('Export duration budget');
  const productCap = format === 'mp4' ? Math.ceil(duration * 4192 * 125 * 1.25) + 4 * 1024 ** 2
    : format === 'mp3' ? Math.ceil(duration * 192 * 125 * 1.1) + 1024 ** 2
    : format === 'gif' ? 64 * 1024 ** 2 : format === 'png' ? 16 * 1024 ** 2 : 256 * 1024;
  facts.stage = 'target';
  if (!inside(tempRoot(), target) || inside(ROOT, target) || !/^download-[a-f0-9]{32}\.(mp4|gif|mp3|srt|png)$/.test(path.basename(target))) {
    throw new Error('Owned TEMP export target required');
  }
  facts.stage = 'target-unlinked';
  unlinked(path.dirname(target));
  facts.stage = 'file-open';
  const fd = fs.openSync(target, 'wx'), digest = createHash('sha256');
  let offset = 0, total = null, chunks = 0;
  const signature = [];
  try {
    while (total === null || offset < total) {
      const end = Math.min(offset + 1024 * 1024, total ?? Infinity) - 1;
      Object.assign(facts, { stage: 'request-transport', offset, status: null, identityMatches: null,
        rangeStart: null, rangeEnd: null, rangeTotal: null, length: null, expectedLength: null, bodyLength: null });
      const response = await api.get(url, { headers: { ...headers, Range: `bytes=${offset}-${end}` }, maxRedirects: 0, maxRetries: 0 });
      try {
        facts.stage = 'response-metadata';
        const match = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(response.headers()['content-range'] ?? '');
        facts.status = response.status();
        facts.identityMatches = response.headers()['x-v2-acceptance'] === instance;
        if (match) {
          facts.rangeStart = Number(match[1]); facts.rangeEnd = Number(match[2]); facts.rangeTotal = Number(match[3]);
        }
        facts.stage = 'response-206';
        if (facts.status !== 206) throw new Error('Exact bounded media Range contract failed');
        facts.stage = 'response-marker';
        if (!facts.identityMatches) throw new Error('Exact bounded media Range contract failed');
        facts.stage = 'content-range-parse';
        if (!match) throw new Error('Exact bounded media Range contract failed');
        facts.stage = 'content-range-bounds';
        if (Number(match[1]) !== offset || Number(match[2]) !== Math.min(end, Number(match[3]) - 1)) {
          throw new Error('Exact bounded media Range contract failed');
        }
        facts.stage = 'size-budget';
        if (Number(match[3]) <= 0 || Number(match[3]) > Math.min(productCap, 128 * 1024 ** 2)) {
          throw new Error('Exact bounded media Range contract failed');
        }
        facts.stage = 'content-range-total';
        if (total !== null && total !== Number(match[3])) {
          throw new Error('Exact bounded media Range contract failed');
        }
        total = Number(match[3]);
        const length = Number(match[2]) - offset + 1;
        facts.stage = 'content-length'; facts.expectedLength = length;
        facts.length = Number(response.headers()['content-length']);
        if (facts.length !== length || length > 1024 * 1024) throw new Error('Range length rejected');
        facts.stage = 'body-read';
        const bytes = await response.body();
        facts.stage = 'body-length'; facts.bodyLength = bytes.length;
        if (bytes.length !== length) throw new Error('Range payload mismatch');
        facts.stage = 'signature-read';
        if (!offset) signature.push(...bytes.subarray(0, 64));
        facts.stage = 'file-write';
        let written = 0;
        while (written < bytes.length) {
          const count = fs.writeSync(fd, bytes, written, bytes.length - written);
          if (count <= 0) throw new Error('Export write made no progress');
          written += count;
        }
        facts.stage = 'digest';
        digest.update(bytes); offset += bytes.length; chunks++;
      } finally {
        const stage = facts.stage; facts.stage = 'response-dispose';
        await response.dispose(); facts.stage = stage;
      }
    }
  } finally {
    const stage = facts.stage; facts.stage = 'file-close';
    fs.closeSync(fd); facts.stage = stage;
  }
  facts.offset = offset; facts.stage = 'signature';
  const first = Buffer.from(signature);
  if (format === 'png' && first.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a'
    || format === 'gif' && !/^GIF8[79]a/.test(first.toString('ascii'))
    || format === 'mp4' && first.subarray(4, 8).toString('ascii') !== 'ftyp') throw new Error('Export signature mismatch');
  let media = null;
  if (format === 'srt') {
    facts.stage = 'srt-size';
    if (total > 2 * 1024 ** 2) throw new Error('SRT size budget');
    facts.stage = 'file-read';
    const text = fs.readFileSync(target, 'utf8');
    facts.stage = 'srt-cues';
    if (!/^\ufeff?1\r?\n\d{2}:\d{2}:\d{2},\d{3} --> /u.test(text)) throw new Error('Real SRT cues required');
  } else { facts.stage = 'probe'; media = probeMedia(target, ffprobe); }
  facts.stage = 'digest';
  return { bytes: offset, sha256: digest.digest('hex'), chunks, format, media };
}