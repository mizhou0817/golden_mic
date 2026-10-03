/** Exact runtime API, not a permissive ambient Node shim. */
export const ROOT: string;
export const env: Record<string, string | undefined>;
export function joinPath(...parts: string[]): string;
export function parentPath(file: string): string;
export function basename(file: string): string;
export function absolute(file: string): boolean;
export function samePath(a: string, b: string): boolean;
export function tempRoot(): string;
export function newInstance(): string;
export function say(text: string): void;
export function bytesHash(value: Uint8Array): string;
export function inside(parent: string, child: string): boolean;
export function exists(file: string): boolean;
export function unlinked(file: string): string;
export function size(file: string): number;
export function readBytes(file: string, maximum?: number): Uint8Array;
export function readText(file: string, maximum?: number): string;
export function hashFile(file: string): string;
export function makeDirectory(directory: string): void;
export function writeJSON(file: string, data: unknown): void;
export function snapshotHashes(directory: string): Record<string, string>;
export interface Probe {
  bytes: number; sha256: string; duration: number; width: number | null; height: number | null;
  fps: string | null; videoCodec: string | null; audioCodec: string | null; audioSampleRate: number | null;
}
export function probeMedia(file: string, ffprobe: string): Probe;