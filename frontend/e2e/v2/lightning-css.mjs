/** Reviewed, opt-in Vite 8 fallback; not a dependency repair or production default.
 * Real PostCSS/Tailwind/Autoprefixer run BEFORE vite:css. Vite's real Lightning
 * bundleAsync still owns @import resolution, URL rewriting and CSS Module exports.
 * Its imported CSS bypasses Vite transform hooks: fail closed if preprocessing an
 * imported file would change it. This deliberately supports the current graph,
 * not arbitrary future imported Tailwind directives. Nothing is stripped/stubbed.
 * Real PostCSS and Vite/Lightning transform maps are observed, not fabricated.
 * Vite 8 emits hidden JS maps but does not emit a final extracted-CSS map.
 * Verified with a write:false constructed Vite build: getCombinedSourcemap()
 * after vite:css has sources/content but EMPTY mappings. Record that limitation;
 * do not claim composed CSS mappings. The original lightning-a attempt failed
 * on an incorrect nonempty-mappings assertion; its diagnostic is immutable.
 */
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { createHash } from 'node:crypto';
import { hashFile, ROOT, unlinked } from '../modes/io.mjs';

const digest = value => createHash('sha256').update(value).digest('hex');
const relative = file => path.relative(ROOT, file).split(path.sep).join('/');
const check = (condition, message) => { if (!condition) throw new Error(message); };
const clean = id => path.resolve(id.split('?')[0]);

export async function prepareLightning({ frontend, sourceHashes, evidence }) {
  const require = createRequire(import.meta.url);
  const [{ default: postcss }, { default: tailwindcss }, { default: autoprefixer }, lightning, rolldown] =
    await Promise.all([import('postcss'), import('tailwindcss'), import('autoprefixer'), import('lightningcss'), import('rolldown')]);
  const native = lightning.transform({ filename: 'constructed.module.css',
    code: Buffer.from('.probe{display:flex;user-select:none;color:#123456}'),
    cssModules: true, sourceMap: true, targets: { safari: 12 << 16 } });
  check(native.exports?.probe?.name && native.map && native.code.includes(Buffer.from('#123456')),
    'Lightning CSS native constructed probe failed.');
  const bundle = await rolldown.rolldown({ input: 'constructed-native-probe', plugins: [{
    name: 'constructed-native-probe', resolveId: id => id === 'constructed-native-probe' ? id : null,
    load: id => id === 'constructed-native-probe' ? 'export const probe = 42;' : null,
  }] });
  try { check((await bundle.generate({ format: 'es' })).output[0].code.includes('42'), 'Rolldown native probe failed.'); }
  finally { await bundle.close(); }
  evidence.nativeHealth = { lightningcss: true, rolldown: true, constructedOnly: true,
    css: native.code.toString(), exports: native.exports, sourceMapBytes: native.map.length };
  const toolFiles = {};
  evidence.tools = { node: process.version, packages: {}, fileHashes: toolFiles };
  for (const name of ['vite', '@vitejs/plugin-react', 'rolldown', 'lightningcss', 'postcss', 'tailwindcss', 'autoprefixer']) {
    const entry = require.resolve(name);
    let directory = path.dirname(entry), metadata;
    for (;;) {
      const candidate = path.join(directory, 'package.json');
      if (fs.existsSync(candidate)) {
        const pkg = JSON.parse(fs.readFileSync(candidate, 'utf8'));
        if (pkg.name === name) { metadata = { candidate, pkg }; break; }
      }
      check(path.dirname(directory) !== directory, `Package metadata missing: ${name}`);
      directory = path.dirname(directory);
    }
    evidence.tools.packages[name] = { version: metadata.pkg.version, entry: relative(entry) };
    for (const file of [entry, metadata.candidate]) toolFiles[relative(file)] = hashFile(file);
  }
  for (const file of Object.keys(require.cache).filter(file => file.endsWith('.node'))) toolFiles[relative(file)] = hashFile(file);
  for (const name of ['frontend/package-lock.json', 'frontend/node_modules/vite/dist/node/chunks/node.js']) {
    toolFiles[name] = hashFile(path.join(ROOT, name));
  }
  const prepared = new Map();
  evidence.stylesheets = [];
  const config = { config: path.join(frontend, 'tailwind.config.ts') };
  for (const name of Object.keys(sourceHashes).filter(name => name.endsWith('.css'))) {
    const file = unlinked(path.join(ROOT, name)), input = fs.readFileSync(file, 'utf8');
    const result = await postcss([tailwindcss(config), autoprefixer()]).process(input,
      { from: file, to: file, map: { inline: false, annotation: false, sourcesContent: true } });
    result.root.walkAtRules(/^(tailwind|apply|screen|config)$/, rule => { throw new Error(`Unprocessed ${rule.name}: ${name}`); });
    check(result.warnings().length === 0, `PostCSS warnings require review: ${name}`);
    const imports = [];
    result.root.walkAtRules('import', rule => imports.push(rule.params));
    const row = { file: name, inputSHA256: digest(input), postcssSHA256: digest(result.css),
      inputBytes: Buffer.byteLength(input), postcssBytes: Buffer.byteLength(result.css),
      sourceMapSHA256: digest(result.map.toString()), imports, viteTransformSeen: false };
    evidence.stylesheets.push(row);
    prepared.set(file, { input, result, row });
  }
  // Keep @imports in the actual input to Lightning, but prove that bypassing the
  // pre-transform for each import loses no Tailwind/Autoprefixer work.
  for (const [file, value] of prepared) for (const params of value.row.imports) {
    const match = /^(?:"([^"]+)"|'([^']+)')\s*$/.exec(params);
    check(match, `Import syntax requires explicit review: ${relative(file)}`);
    const target = path.resolve(path.dirname(file), match[1] ?? match[2]);
    const imported = prepared.get(target);
    check(imported && imported.input === imported.result.css,
      `Imported CSS needs preprocessing outside Vite hooks: ${relative(target)}`);
    (evidence.imports ??= []).push({ from: relative(file), to: relative(target),
      preservedForViteLightning: true, postcssIdentityVerified: true });
  }
  let resolved;
  const moduleJS = new Map();
  const pre = { name: 'v2-reviewed-tailwind-before-lightning', enforce: 'pre',
    configResolved(config) {
      resolved = config;
      check(config.css.transformer === 'lightningcss', 'Lightning CSS option not resolved.');
      evidence.resolvedConfig = { root: config.root, mode: config.mode, configFile: config.configFile,
        envDir: config.envDir, publicDir: config.publicDir, css: config.css,
        build: { outDir: config.build.outDir, emptyOutDir: config.build.emptyOutDir,
          target: config.build.target, cssTarget: config.build.cssTarget, cssMinify: config.build.cssMinify,
          minify: config.build.minify, sourcemap: config.build.sourcemap, cssCodeSplit: config.build.cssCodeSplit },
        plugins: config.plugins.map(plugin => plugin.name) };
      check(config.plugins.indexOf(pre) < config.plugins.findIndex(plugin => plugin.name === 'vite:css'),
        'Tailwind pre-plugin must precede vite:css.');
    },
    transform(code, id) {
      if (!/\.css(?:\?|$)/.test(id)) return;
      const value = prepared.get(clean(id));
      check(value, `Unreviewed stylesheet: ${id}`);
      check(code === value.input, `CSS changed before approved preprocessing: ${id}`);
      value.row.viteTransformSeen = true;
      return { code: value.result.css, map: value.result.map.toJSON() };
    },
  };
  const post = { name: 'v2-observe-real-css-module-exports', enforce: 'post',
    transform(code, id) { if (/\.module\.css$/.test(id)) moduleJS.set(clean(id), code); },
  };
  const observe = { name: 'v2-observe-vite-lightning-css-maps',
    transform(code, id) {
      if (!/\.css$/.test(id)) return;
      const value = prepared.get(clean(id)), map = this.getCombinedSourcemap();
      check(value && map.version === 3 && map.sources.length && typeof map.mappings === 'string',
        `Missing actual Vite CSS transform map: ${id}`);
      value.row.viteCSSMap = { sha256: digest(JSON.stringify(map)), sources: map.sources,
        mappingsLength: map.mappings.length, composedMappingsAvailable: map.mappings.length > 0,
        transformedCSSSHA256: digest(code) };
    },
  };
  return { plugins: [pre, observe, post], css: { transformer: 'lightningcss' },
    build: { sourcemap: 'hidden', cssMinify: 'lightningcss' },
    async verify(output) {
      const cssAssets = output.filter(asset => asset.type === 'asset' && asset.fileName.endsWith('.css'));
      const css = cssAssets.map(asset => Buffer.from(asset.source).toString()).join('\n');
      const js = output.filter(asset => asset.type === 'chunk').map(asset => asset.code).join('\n');
      check(css.length && js.length, 'Missing returned CSS/JS bundle.');
      const root = postcss.parse(css);
      root.walkAtRules(/^(tailwind|apply|screen|config|import)$/, rule => { throw new Error(`Unresolved bundled @${rule.name}`); });
      const tokensFile = path.join(frontend, 'src/components/ui/tokens.css');
      const tokenCSS = lightning.transform({ filename: tokensFile,
        code: Buffer.from(prepared.get(tokensFile).result.css), minify: true,
        targets: resolved.css.lightningcss.targets }).code.toString();
      const expectedTokens = [];
      postcss.parse(tokenCSS).walkDecls(/^--/, decl => expectedTokens.push([decl.prop, decl.value]));
      for (const [prop, value] of expectedTokens) {
        let found = false;
        root.walkDecls(prop, decl => { if (decl.value === value && decl.parent.type === 'rule'
          && decl.parent.selector.includes(':root')) found = true; });
        check(found, `Bundled palette token missing: ${prop}`);
      }
      const hasRule = (selector, prop, value) => {
        let found = false;
        root.walkRules(rule => { if (rule.selectors.includes(selector)) rule.walkDecls(prop,
          decl => { if (decl.value === value) found = true; }); });
        return found;
      };
      check(hasRule('*', 'box-sizing', 'border-box'), 'Tailwind preflight box sizing missing.');
      for (const prop of ['--tw-translate-x', '--tw-ring-offset-width']) {
        let found = false; root.walkDecls(prop, () => { found = true; });
        check(found, `Tailwind baseline variable missing: ${prop}`);
      }
      const utilities = [];
      const processedMain = prepared.get(path.join(frontend, 'src/styles.css')).result.root;
      for (const candidate of [['.flex', 'display', 'flex'], ['.hidden', 'display', 'none'],
        ['.block', 'display', 'block'], ['.grid', 'display', 'grid'], ['.relative', 'position', 'relative']]) {
        processedMain.walkRules(rule => {
          if (rule.selector === candidate[0]) rule.walkDecls(candidate[1], decl => {
            if (decl.value === candidate[2] && !utilities.includes(candidate)) utilities.push(candidate);
          });
        });
      }
      check(utilities.length > 0, 'No baseline utilities generated by actual Tailwind configuration.');
      for (const [selector, prop, value] of utilities) check(hasRule(selector, prop, value), `Tailwind utility missing: ${selector}`);
      evidence.cssModules = [];
      for (const [file, value] of prepared) if (file.endsWith('.module.css')) {
        // Independent native transformation predicts scoped names; Vite's own
        // generated module and emitted CSS must contain those names, not stubs.
        const expected = lightning.transform({ ...resolved.css.lightningcss, filename: file,
          projectRoot: frontend, code: Buffer.from(value.result.css), cssModules: true });
        const generated = moduleJS.get(file);
        check(generated, `Vite CSS Module not observed: ${relative(file)}`);
        const names = Object.values(expected.exports).map(entry => entry.name);
        for (const name of names) {
          check(generated.includes(name) && css.includes(name), `CSS Module export/rule missing: ${name}`);
        }
        const referenced = names.filter(name => js.includes(name));
        check(referenced.length > 0, `No real bundled JS CSS Module references: ${relative(file)}`);
        const rules = [];
        root.walkRules(rule => {
          if (names.some(name => rule.selector.includes(`.${name}`)) && rule.nodes.some(node => node.type === 'decl')) {
            rules.push(rule.selector);
          }
        });
        check(rules.length >= 5, `CSS Module real rules missing: ${relative(file)}`);
        evidence.cssModules.push({ file: relative(file), exports: expected.exports,
          viteGeneratedModuleSHA256: digest(generated), bundledJSReferences: referenced, ruleCount: rules.length });
      }
      check(evidence.cssModules.length === 2, 'Expected both current CSS Modules.');
      check(evidence.stylesheets.every(row => row.viteTransformSeen || evidence.imports?.some(item => item.to === row.file)),
        'Source stylesheet never processed by Vite.');
      const maps = output.filter(asset => asset.fileName.endsWith('.map'));
      check(maps.some(asset => asset.fileName.endsWith('.js.map')), 'Real bundled JS source maps missing.');
      for (const asset of maps) {
        const map = JSON.parse(asset.type === 'asset' ? Buffer.from(asset.source).toString() : asset.code);
        check(map.version === 3 && map.sources.length && map.mappings.length, `Invalid source map: ${asset.fileName}`);
      }
      for (const [name, sha] of Object.entries(toolFiles)) check(hashFile(path.join(ROOT, name)) === sha, `Build tool drift: ${name}`);
      evidence.validation = { passed: true, tokenDeclarations: expectedTokens.length,
        tailwindPreflight: true, tailwindUtilities: utilities.map(row => row[0]),
        cssAssets: cssAssets.map(asset => ({ file: asset.fileName, sha256: digest(asset.source), bytes: Buffer.byteLength(asset.source) })),
        sourceMaps: maps.map(asset => asset.fileName), extractedCSSMapEmittedByVite: false, toolsUnchanged: true,
        boundary: 'Static bundle validation only; no browser visual-equivalence or production acceptance claim.' };
    },
  };
}