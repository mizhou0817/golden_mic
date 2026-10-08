// Shared browser launcher for the opt-in native-browser tests.
// GM_BROWSER selects the engine: msedge | chrome | webkit (Safari's engine) | auto.
// "auto" (default) uses the first of Edge, Chrome, WebKit that actually launches.
// WebKit is Playwright's own build: Safari itself cannot be automated headless.
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const SAFARI_TAB = { Tab: 'Alt+Tab', 'Shift+Tab': 'Alt+Shift+Tab' };
const CHROMIUM_ONLY_ARGS = /^--(disable-|no-first-run)/;
const KNOWN = ['msedge', 'chrome', 'webkit'];

export function requestedBrowsers(env = process.env) {
  const value = (env.GM_BROWSER || 'auto').trim().toLowerCase();
  if (value === 'auto') return KNOWN;
  if (!KNOWN.includes(value)) throw new Error(`GM_BROWSER must be auto, ${KNOWN.join(', ')}`);
  return [value];
}

// options.chromiumOnly: the test needs Chromium DevTools protocol (CDP).
export async function launchBrowser(options = {}, env = process.env) {
  const { chromiumOnly = false, ...launchOptions } = options;
  const playwright = require('playwright');
  let candidates = requestedBrowsers(env);
  // CDP-only tests cannot run on WebKit: use Edge/Chrome even when WebKit was requested.
  if (chromiumOnly) candidates = candidates.filter(name => name !== 'webkit').concat(candidates.includes('webkit') && candidates.length === 1 ? ['msedge', 'chrome'] : []);
  const failures = [];
  for (const name of candidates) {
    try {
      const browser = name === 'webkit'
        ? await playwright.webkit.launch({ ...launchOptions, args: (launchOptions.args || []).filter(arg => !CHROMIUM_ONLY_ARGS.test(arg)) })
        : await playwright.chromium.launch({ ...launchOptions, channel: name });
      browser.gmBrowserName = name;
      if (name === 'webkit') adaptWebKit(browser, env);
      return browser;
    } catch (error) { failures.push(`${name}: ${String(error.message).split('\n')[0]}`); }
  }
  throw new Error(`No browser could launch (${failures.join('; ')})`);
}

// WebKit differences, applied only to the WebKit engine:
//  - offline:true makes routed navigations fail with an internal error. Every
//    test installs a '**/*' route that fulfils or aborts each request, so no
//    request can leave the context; the flag is dropped, isolation is unchanged.
//  - blob: requests are passed through before a test's own route sees them.
//  - macOS Safari does not Tab onto buttons/links by default (Option+Tab does),
//    so Tab / Shift+Tab are sent as Option+Tab / Option+Shift+Tab there.
function adaptWebKit(browser, env) {
  const mac = (env.GM_PLATFORM || process.platform) === 'darwin';
  const newContext = browser.newContext.bind(browser);
  browser.newContext = async (options = {}) => {
    const { offline, ...rest } = options;
    const context = await newContext(rest);
    // WebKit also routes page-local blob: URLs (never network traffic); Chromium does not.
    const route = context.route.bind(context);
    context.route = (url, handler, ...more) => route(url, (r, request) =>
      request.url().startsWith('blob:') ? r.continue() : handler(r, request), ...more);
    if (mac) {
      const newPage = context.newPage.bind(context);
      context.newPage = async (...args) => {
        const page = await newPage(...args), press = page.keyboard.press.bind(page.keyboard);
        page.keyboard.press = (key, ...more) => press(SAFARI_TAB[key] || key, ...more);
        return page;
      };
    }
    return context;
  };
}
