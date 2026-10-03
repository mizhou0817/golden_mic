import type { APIRequestContext } from '@playwright/test';
import type { Probe } from '../modes/io.mjs';
export function rangeDownload(api: APIRequestContext, url: string, headers: Record<string, string>, target: string,
  ffprobe: string, instance: string, format: string, duration: number): Promise<{ bytes: number; sha256: string; chunks: number; format: string; media: Probe | null }>;