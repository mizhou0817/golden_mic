import { Buffer } from 'node:buffer';
import { deflateSync } from 'node:zlib';

// Entirely self-authored test data, not a stock image, seed imagePath, data URL,
// private filename or executable filter. Generate in memory only when requested.
export const IMAGE = { width: 16, height: 16, rgba: [220, 60, 20, 128] };
export const signature = [137, 80, 78, 71, 13, 10, 26, 10];
export function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function chunk(kind, payload) {
  const data = Buffer.concat([Buffer.from(kind, 'ascii'), payload]);
  const length = Buffer.alloc(4), checksum = Buffer.alloc(4);
  length.writeUInt32BE(payload.length); checksum.writeUInt32BE(crc32(data));
  return Buffer.concat([length, data, checksum]);
}
function pngDocument(compressed) {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(IMAGE.width, 0); header.writeUInt32BE(IMAGE.height, 4);
  header[8] = 8; header[9] = 6; // 8-bit RGBA; no interlace, profiles or metadata.
  return Buffer.concat([Buffer.from(signature), chunk('IHDR', header), chunk('IDAT', compressed), chunk('IEND', Buffer.alloc(0))]);
}
function scanlines() {
  const stride = 1 + IMAGE.width * 4, rows = Buffer.alloc(IMAGE.height * stride);
  for (let y = 0; y < IMAGE.height; y++) for (let x = 0; x < IMAGE.width; x++) {
    rows.set(IMAGE.rgba, y * stride + 1 + x * 4); // Filter byte 0 at each row start.
  }
  return rows;
}
export function createPNG() { return pngDocument(deflateSync(scanlines(), { level: 9 })); }
export function copyBytes(bytes) { return Buffer.from(bytes); }

// Input order is R fastest, then G, then B. Output RGB = (B, G, R).
// This unit-domain 2-edge LUT is already canonical ASCII/LF, exactly 8 rows.
const cubeLines = ['LUT_3D_SIZE 2', 'DOMAIN_MIN 0 0 0', 'DOMAIN_MAX 1 1 1',
  '0 0 0', '0 0 1', '0 1 0', '0 1 1', '1 0 0', '1 0 1', '1 1 0', '1 1 1'];
export function createAssetBytes() {
  const damaged = deflateSync(scanlines(), { level: 9 });
  damaged[damaged.length - 1] ^= 1; // Bad zlib Adler checksum, but valid PNG chunk CRCs/header.
  return {
    image: createPNG(),
    lut: Buffer.from(cubeLines.join('\n') + '\n', 'ascii'),
    missingRows: Buffer.from(cubeLines.slice(0, -1).join('\n') + '\n', 'ascii'),
    corrupt: pngDocument(damaged),
    svg: Buffer.from('<svg width="16" height="16"><rect width="16" height="16" fill="#dc3c14"/></svg>', 'utf8'),
  };
}