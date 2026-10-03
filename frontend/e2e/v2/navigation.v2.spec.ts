import type { BrowserContextOptions } from '@playwright/test';
import { test, expect } from './support';
import { BASE_URL } from './environment';

// Navigation only: no file picker, draft admission, task/private API or generation.
test('V2-00 configured context resolves relative navigation without raw artifacts', async ({ context, page, v2 }) => {
  test.setTimeout(60_000);
  // Read only allowlisted option comparisons, never cookies/headers/storage state.
  // Private introspection deliberately pins this regression to installed Playwright.
  // _options is protocol-normalized: acceptDownloads:false becomes 'deny'.
  const options = (context as unknown as {
    _options: Omit<BrowserContextOptions, 'acceptDownloads'> & { acceptDownloads?: string };
  })._options;
  const configured = {
    baseURL: options.baseURL === BASE_URL,
    locale: options.locale === 'zh-CN',
    viewport: options.viewport?.width === 1280 && options.viewport.height === 900,
    serviceWorkers: options.serviceWorkers === 'block',
    acceptDownloads: options.acceptDownloads === 'deny',
  };
  let errorCode = 'none';
  try {
    await page.goto('/', { timeout: 30_000 });
  } catch (error) {
    // Exact known static suffix only; do NOT serialize raw message/stack/call log.
    const message = error instanceof Error ? error.message : '';
    errorCode = message.includes('Cannot navigate to invalid URL') ? 'invalid-url'
      : message.includes('net::ERR_CONNECTION_REFUSED') ? 'connection-refused'
      : error instanceof Error && error.name === 'TimeoutError' ? 'timeout' : 'unclassified';
  }
  v2.evidence('navigation', { configured, errorCode, relativeURL: true,
    browserRootGET: v2.observations.some(r => r.method === 'GET' && r.path === '/'),
    noOwnedTasks: v2.tasks.size === 0, noOwnedFiles: v2.files.size === 0 });
  expect(errorCode).toBe('none');
  expect(Object.values(configured).every(Boolean)).toBe(true);
  await expect(page.getByRole('heading', { name: '写稿', exact: true })).toBeVisible();
  expect(v2.tasks.size).toBe(0);
  expect(v2.files.size).toBe(0);
  expect(v2.observations.some(r => !['GET', 'HEAD', 'OPTIONS'].includes(r.method))).toBe(false);
});