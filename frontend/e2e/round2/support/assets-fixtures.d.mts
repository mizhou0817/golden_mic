/** Runtime Buffers, exposing only the byte operations used by these fixtures.
 * Keep this module-local: no Node globals, ambient shims or Playwright Buffer aliases. */
export interface FixtureBytes extends Uint8Array {
  readUInt32BE(offset: number): number;
  toString(encoding?: 'ascii' | 'utf8', start?: number, end?: number): string;
}

export const IMAGE: {
  readonly width: 16;
  readonly height: 16;
  readonly rgba: readonly [220, 60, 20, 128];
};
export const signature: readonly number[];
export function crc32(bytes: Uint8Array): number;
export function createPNG(): FixtureBytes;
export function copyBytes(bytes: Uint8Array): FixtureBytes;
export function createAssetBytes(): {
  image: FixtureBytes;
  lut: FixtureBytes;
  missingRows: FixtureBytes;
  corrupt: FixtureBytes;
  svg: FixtureBytes;
};