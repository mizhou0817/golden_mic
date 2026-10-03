"""Parent-run V2 validation; no application imports before the shared guards.

--help is stdlib-only and writes nothing. --static-only hashes/parses source and
generates a design inventory, but imports no backend or tests. The default suite
discovers test_v2*.py; --legacy adds an explicit reused-contract selection, NOT
the entire historical suite. No build/server/browser/install/download is started.
Python guards are not an OS sandbox for native codecs or the static Node parser.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4


PROJECT = Path(__file__).resolve().parents[1]
LEGACY_MODULES = (
    "test_production_modes", "test_upload_sessions", "test_mode_workbench",
    "test_quality", "test_publication", "test_public_media",
    "test_anonymous_access", "test_headline_contract",
)
# Explicit platform selection, NOT successful skips or cross-platform coverage.
# New/renamed platform-specific cases must be reviewed rather than auto-filtered.
LINUX_EXCLUSIONS = frozenset({
    "tests.test_v2_windows_asyncio.WindowsTests.test_layout_fail_closed_before_loop_construction",
    "tests.test_v2_windows_asyncio.WindowsTests.test_stock_classes_and_policy_unchanged",
    "tests.test_v2_windows_asyncio.WindowsTests.test_notification_cleanup_failure_matrix",
    "tests.test_v2_windows_asyncio.WindowsTests.test_real_stock_and_corrected_30_cases_each",
    "tests.test_v2_windows_asyncio.WindowsTests.test_socketpair_bytes_and_pending_write_cancellation",
    "tests.test_v2_windows_asyncio.WindowsTests.test_tls_direct_and_start_tls_bytes",
    "tests.test_v2_sample_bundle.SampleBundleTests.test_native_windows_short_ancestor_aliases_before_content_or_creation",
})
# These are inventories, not claims that the modules have passed on current source.
SOURCE_PATTERNS = (
    "backend/**/*.py", "backend/**/*.json", "tests/**/*.py", "tests/**/*.cjs",
    "tests/**/*.json", "frontend/src/**/*", "frontend/scripts/*",
    "frontend/e2e/**/*", "frontend/playwright*.ts", "frontend/*config*",
    "frontend/package*.json", "scripts/test-v2*.mjs", "deploy/**/*.py",
    "docs/DESIGN-MAP.md", "docs/RUNBOOK.md", "docs/V2_IMPLEMENTATION_20260929.md",
    ".env.example", "deploy/golden-mic.env.production.example", "pyproject.toml",
    "requirements*.txt", "requirements*.lock", "uv.lock", "*GPT-6 Astra*.md",
    ".github/workflows/*", ".gitignore", ".gitattributes", "deploy/**/*.sh",
    "scripts/verify-publication.mjs", "scripts/test-publication.mjs",
)

# TypeScript parses the supplied handoff as JavaScript AST; it is NEVER evaluated.
# Only standard-library IO + the already installed local TypeScript parser is used.
DESIGN_PROGRAM = r"""
const fs = require('node:fs'), path = require('node:path');
const root = process.cwd();
const ts = require(path.join(root, 'frontend/node_modules/typescript'));
const names = fs.readdirSync(root).filter(n => n.endsWith('.md') && n.includes('GPT-6 Astra'));
if (names.length !== 1) throw Error('handoff_not_unique');
const text = fs.readFileSync(path.join(root, names[0]), 'utf8');
const blocks = [...text.matchAll(/^```(html|js)\r?\n([\s\S]*?)^```/gm)];
const logic = blocks.filter(b => b[1] === 'js' && b[2].includes('renderVals(){'));
if (logic.length !== 1) throw Error('logic_not_unique');
const source = ts.createSourceFile('handoff.js', logic[0][2], ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
if (source.parseDiagnostics.length) throw Error('invalid_handoff_ast');
const baseLine = text.slice(0, logic[0].index).split('\n').length;
const at = n => baseLine + source.getLineAndCharacterOfPosition(n.getStart(source)).line + 1;
const keys = [], codes = [];
function visit(n) {
  if (ts.isMethodDeclaration(n) && n.name.getText(source) === 'renderVals') {
    for (const s of n.body.statements) {
      if (!ts.isReturnStatement(s) || !s.expression || !ts.isObjectLiteralExpression(s.expression)) continue;
      for (const p of s.expression.properties) {
        if (!p.name || ts.isComputedPropertyName(p.name)) throw Error('unresolved_view_key');
        keys.push({key: p.name.text, line: at(p)});
      }
    }
  }
  if (ts.isMethodDeclaration(n) && n.name.getText(source) === 'checksFor') {
    function codeWalk(x) {
      if (ts.isPropertyAssignment(x) && x.name.getText(source) === 'code') {
        let first = x.initializer;
        while (ts.isBinaryExpression(first) && first.operatorToken.kind === ts.SyntaxKind.PlusToken) first = first.left;
        if (ts.isStringLiteral(first)) {
          const m = first.text.match(/^(?:[A-Z][A-Z0-9_]+|generated_media)/);
          if (m) codes.push({key:m[0], line:at(x)});
        }
      }
      ts.forEachChild(x, codeWalk);
    }
    codeWalk(n);
  }
  ts.forEachChild(n, visit);
}
visit(source);
const hooks = [], bindings = [];
for (const b of blocks.filter(b => b[1] === 'html')) {
  const base = text.slice(0, b.index).split('\n').length;
  for (const [rx, dest] of [[/\sdata-r="([^"]+)"/g, hooks], [/\{\{\s*([\s\S]*?)\s*\}\}/g, bindings]]) {
    for (const m of b[2].matchAll(rx)) dest.push({key:m[1].trim(), line:base+b[2].slice(0,m.index).split('\n').length});
  }
}
const copyStart = text.indexOf('## C-6');
if (copyStart < 0) throw Error('missing_copy_index');
const copies = text.slice(copyStart).split('\n').flatMap((s,i) => s.startsWith('- ') ?
  [{key:s.slice(2).trim(), line:text.slice(0,copyStart).split('\n').length+i}] : []);
function unique(items) {
  const out = new Map();
  for (const item of items) {
    if (!out.has(item.key)) out.set(item.key, {key:item.key, source_lines:[], status:'pending_integration', evidence:[]});
    out.get(item.key).source_lines.push(item.line);
  }
  return [...out.values()];
}
const groups = {hooks:unique(hooks), view_keys:unique(keys), bindings:unique(bindings), static_copy:unique(copies), check_codes:unique(codes)};
const candidates = [];
function scan(dir) {
  for (const ent of fs.readdirSync(dir, {withFileTypes:true})) {
    const full = path.join(dir,ent.name);
    if (ent.isSymbolicLink()) throw Error('source_link');
    if (ent.isDirectory()) scan(full);
    else if (/\.(?:ts|tsx|css)$/.test(ent.name)) candidates.push({file:path.relative(root,full).replaceAll('\\','/'),lines:fs.readFileSync(full,'utf8').split('\n')});
  }
}
scan(path.join(root,'frontend/src'));
const rules = JSON.parse(fs.readFileSync(path.join(root,'backend/mode_rules.json'),'utf8'));
for (const [group, items] of Object.entries(groups)) for (const item of items) {
  item.candidate_locations = [];
  if (group === 'check_codes') {
    if (Object.hasOwn(rules.checks,item.key)) item.candidate_locations.push({file:'backend/mode_rules.json',json_pointer:'/checks/'+item.key});
  } else for (const file of candidates) {
    file.lines.forEach((line,i) => {
      const found = group === 'hooks' ? line.includes('data-r="'+item.key+'"') || line.includes("data-r='"+item.key+"'") : line.includes(item.key);
      if (found) item.candidate_locations.push({file:file.file,line:i+1,kind:'literal_candidate_not_semantic_proof'});
    });
  }
}
const observed = Object.fromEntries(Object.entries(groups).map(([k,v])=>[k,v.length]));
const expected = {hooks:26,view_keys:311,bindings:609,static_copy:105,check_codes:17};
const result = {schema_version:1,handoff:names[0],status:'pending_integration',declared:expected,observed,
  binding_occurrences:bindings.length,copy_method:'appendix_C6_authored_index_not_all_dynamic_text',
  mismatches:Object.keys(expected).filter(k=>expected[k]!==observed[k]).map(k=>({dimension:k,declared:expected[k],observed:observed[k]})),groups};
// PowerShell and Windows pipe codepages cannot corrupt machine-readable paths.
process.stdout.write(JSON.stringify(result).replace(/[\u007f-\uffff]/g,c=>'\\u'+c.charCodeAt(0).toString(16).padStart(4,'0')));
"""


class SetupFailure(RuntimeError):
    """Only static error codes; never include exception messages or secrets."""


def platform_suite(suite, *, platform=sys.platform):
    def leaves(items):
        for item in items:
            if isinstance(item, unittest.TestSuite):
                yield from leaves(item)
            else:
                yield item

    tests = list(leaves(suite))
    exclusions = LINUX_EXCLUSIONS if platform == "linux" else frozenset()
    excluded = sorted(test.id() for test in tests if test.id() in exclusions)
    if set(excluded) != exclusions:
        raise SetupFailure("platform_test_inventory_changed")
    return unittest.TestSuite(test for test in tests if test.id() not in exclusions), excluded


class Sink(io.TextIOBase):
    """Discard library/test output without accumulating it in memory or on disk."""
    def write(self, text):
        return len(text)

    def flush(self):
        pass


def source_files():
    files = set()
    for pattern in SOURCE_PATTERNS:
        for path in PROJECT.glob(pattern):
            if path.is_file() and "__pycache__" not in path.parts:
                if path.is_symlink() or path.resolve() != path.absolute():
                    raise SetupFailure("source_link")
                files.add(path)
    return sorted(files)


def snapshot():
    return {p.relative_to(PROJECT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source_files()}


def write_json(path, value):
    # Exclusive, no retries/overwrite of an earlier attempt's evidence.
    with path.open("x", encoding="utf-8") as out:
        json.dump(value, out, ensure_ascii=True, indent=2)
        out.write("\n")


def source_frames(tb, inventory):
    frames = []
    for frame, line in traceback.walk_tb(tb):
        path = Path(frame.f_code.co_filename)
        try:
            name = path.resolve().relative_to(PROJECT).as_posix()
        except (OSError, ValueError):
            continue
        if name in inventory:
            frames.append({"file": name, "line": line})
    return frames


def safe_exception(error, inventory):
    """Bounded type/code/source diagnostics, never messages, args or locals."""
    chain, seen = [], set()
    while error is not None and id(error) not in seen and len(chain) < 8:
        seen.add(id(error))
        kind = type(error)
        name = kind.__module__ + "." + kind.__qualname__
        item = {"exception_type": name if re.fullmatch(r"[A-Za-z0-9_.]{1,160}", name) else "redacted_type",
                "source": source_frames(error.__traceback__, inventory)[:64]}
        for key in ("errno", "winerror", "returncode"):
            value = getattr(error, key, None)
            if type(value) is int:
                item[key] = value
        if isinstance(error, AssertionError):
            item["code"] = "assertion_failed"
        elif isinstance(error, TypeError):
            item["code"] = "type_error"
        elif isinstance(error, ImportError):
            item["code"] = "import_error"
        chain.append(item)
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    return chain


class SafeLoader(unittest.TestLoader):
    """Capture the actual import exception before unittest wraps it in text."""
    def __init__(self, inventory):
        super().__init__()
        self.inventory = inventory
        self.import_failures = []

    def _get_module_from_name(self, name):
        try:
            return super()._get_module_from_name(name)
        except Exception as error:
            self.import_failures.append({
                "module": name if re.fullmatch(r"tests\.test_[A-Za-z0-9_]+", name) else "redacted_module",
                "diagnostics": safe_exception(error, self.inventory)})
            raise


class SafeResult(unittest.TestResult):
    """Do not call base error formatting: it renders tokens/URLs/subTest params."""
    def __init__(self, inventory):
        super().__init__()
        self.inventory = inventory
        self.events = []
        self.passed = 0

    def record(self, test, status, err=None):
        parent = getattr(test, "test_case", test)
        # Never str(test), test.id(), error message, locals, or subtest parameters.
        module = parent.__class__.__module__
        method = getattr(parent, "_testMethodName", "unknown")
        identity = ".".join((module, parent.__class__.__name__, method))
        if not re.fullmatch(r"[A-Za-z0-9_.]+", identity):
            identity = "unidentified_test"
        self.events.append({"test": identity, "status": status,
                            "source": source_frames(err[2], self.inventory) if err else [],
                            "diagnostics": safe_exception(err[1], self.inventory) if err else []})

    def addSuccess(self, test):
        self.passed += 1

    def addError(self, test, err):
        self.errors.append((None, "redacted"))
        self.record(test, "error", err)

    def addFailure(self, test, err):
        self.failures.append((None, "redacted"))
        self.record(test, "failure", err)

    def addSubTest(self, test, subtest, err):
        if err is not None:
            if issubclass(err[0], test.failureException):
                self.addFailure(test, err)
            else:
                self.addError(test, err)

    def addSkip(self, test, reason):
        self.skipped.append((None, "redacted"))
        self.record(test, "skipped")

    def addExpectedFailure(self, test, err):
        self.expectedFailures.append((None, "redacted"))
        self.record(test, "expected_failure", err)

    def addUnexpectedSuccess(self, test):
        self.unexpectedSuccesses.append(None)
        self.record(test, "unexpected_success")


def install_v2_guards(stack, root, evidence, ffmpeg, pure_phase):
    # Shared helper has stdlib-only top-level imports. Never invoke its main().
    from tests.run_core_validation import Guards, normalized, under

    class V2Guards(Guards):
        def __init__(self):
            super().__init__(root, evidence, ffmpeg)
            self.databases = set()
            self.private += tuple(normalized(PROJECT / n) for n in ("eval", "frontend/dist"))

        def audit(self, event, args):
            if event == "sqlite3.connect":
                value = args[0]
                # No URI, memory database, or blanket owned-TEMP SQL exception.
                if not isinstance(value, (str, os.PathLike)):
                    self.reject("unregistered SQLite")
                path = os.fsdecode(value)
                if path.startswith("file:") or not Path(path).is_absolute() or normalized(path) not in self.databases:
                    self.reject("unregistered SQLite")
                if Path(path).resolve() != Path(path).absolute():
                    self.reject("SQLite path indirection")
                self.counts["owned_admission_connections"] += 1
                return
            if event == "subprocess.Popen":
                # CPython Windows audits the serialized command, not original argv.
                approved = getattr(self.local, "approved_media", None)
                if (approved is None or not isinstance(args[0], (str, bytes, os.PathLike))
                    or normalized(args[0]) != approved[0] or args[1] != approved[1]):
                    self.reject("unapproved native process audit")
            super().audit(event, args)

    guard = V2Guards()
    guard.install_file_guards(stack)
    # The pure import contract requires a genuinely cold module table. Audit
    # all IO before that phase, but do not import dotenv/httpx/config to patch
    # them yet. No module removal, fake module table, or weakened assertion.
    early_active = True
    def early_audit(event, arguments):
        if early_active:
            guard.audit(event, arguments)
    sys.addaudithook(early_audit)
    if sys.platform == "win32":
        stack.enter_context(patch("platform._syscmd_ver", return_value=("", "", "")))
    guard.stage = "suite"
    pure_phase()
    guard.install(stack)
    early_active = False  # the shared install registered guard.audit permanently
    guard.stage = "self_check"
    guard.self_check()
    guard.stage = "self_check"
    for value in (":memory:", root / "other.sqlite3", PROJECT / "data/classroom.sqlite3",
                  "file:" + str(root / "tasks/_v2/admission.sqlite3")):
        try:
            guard.audit("sqlite3.connect", (value,))
        except RuntimeError:
            pass
        else:
            raise SetupFailure("sqlite_self_check_failed")
    guard.stage = "suite"

    shared_init = subprocess.Popen.__init__
    ffprobe = ffmpeg.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    approved_tools = {normalized(ffmpeg), normalized(ffprobe)}

    def media_init(process, command, *args, **kwargs):
        if (not isinstance(command, (list, tuple)) or not command or args
                or kwargs.get("shell") or kwargs.get("executable") is not None):
            guard.reject("unreviewed subprocess shape")
        exe = shutil.which(os.fsdecode(command[0]))
        if exe is None or normalized(exe) not in approved_tools:
            guard.reject("non-owned media executable")
        values = [exe, *[os.fsdecode(v) if isinstance(v, os.PathLike) else str(v) for v in command[1:]]]
        previous = getattr(guard.local, "approved_media", None)
        guard.local.approved_media = (normalized(exe), subprocess.list2cmdline(values) if os.name == "nt" else values)
        try:
            # Windows otherwise audits executable=None even for absolute argv[0].
            # Pin the validated executable, rather than accepting arbitrary None
            # audit events. The shared argv/env/network checks still run.
            kwargs["executable"] = exe
            shared_init(process, values, *args, **kwargs)
        finally:
            guard.local.approved_media = previous

    stack.enter_context(patch.object(subprocess.Popen, "__init__", new=media_init))
    guard.stage = "self_check"
    from tests.run_core_validation import SafetyViolation
    for operation in (
        lambda: subprocess.Popen([str(ffmpeg), "-version"], shell=True),
        lambda: subprocess.Popen([str(ffmpeg), "-version"], executable=str(ffmpeg)),
        lambda: subprocess.Popen([sys.executable, "-V"]),
        lambda: guard.audit("subprocess.Popen", (None, "unapproved", None, None)),
        lambda: subprocess.Popen([str(ffmpeg), "-i", "https://safe.test/blocked"]),
    ):
        try:
            operation()
        except SafetyViolation:
            pass
        else:
            raise SetupFailure("native_self_check_failed")
    # Real sync AND asyncio launches exercise the actual Windows audit shape.
    for tool in (ffmpeg, ffprobe):
        completed = subprocess.run([str(tool), "-version"], capture_output=True, timeout=15, check=True)
        if not completed.stdout.startswith(tool.stem.encode("ascii") + b" version "):
            raise SetupFailure("native_version_check_failed")
    import asyncio
    async def async_version():
        process = await asyncio.create_subprocess_exec(
            str(ffmpeg), "-version", stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        await asyncio.wait_for(process.communicate(), timeout=15)
        if process.returncode != 0:
            raise SetupFailure("async_native_check_failed")
    asyncio.run(async_version())
    guard.counts["native_launch_self_checks_passed"] += 3
    guard.stage = "suite"

    # Registration is coupled to the real ledger constructor; no precreated DBs.
    # Media guards are already installed for any transitive SceneDetect probe.
    from backend.admission import AdmissionLedger
    original_ledger_init = AdmissionLedger.__init__

    def ledger_init(ledger, data_dir):
        data_dir = Path(data_dir)
        path = data_dir / "_v2/admission.sqlite3"
        if not data_dir.is_absolute() or not under(normalized(data_dir), normalized(root)):
            guard.reject("ledger outside owned TEMP")
        if data_dir.resolve() != data_dir.absolute() or path.resolve() != path.absolute():
            guard.reject("ledger path indirection")
        key = normalized(path)
        if key not in guard.databases:
            if path.exists() or any(Path(str(path) + suffix).exists() for suffix in ("-journal", "-wal", "-shm")):
                guard.reject("ledger not freshly owned")
            guard.databases.add(key)
        original_ledger_init(ledger, data_dir)

    stack.enter_context(patch.object(AdmissionLedger, "__init__", new=ledger_init))
    return guard


def static_inventory(evidence, initial, *, compile_ast):
    config = ast.parse((PROJECT / "backend/config.py").read_text(encoding="utf-8"))
    settings = next(n for n in config.body if isinstance(n, ast.ClassDef) and n.name == "Settings")
    fields = {n.target.id.upper() for n in settings.body if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)}
    templates = {}
    for name in (".env.example", "deploy/golden-mic.env.production.example"):
        keys = re.findall(r"^([A-Z][A-Z0-9_]*)=", (PROJECT / name).read_text(encoding="utf-8"), re.M)
        templates[name] = {"missing": sorted(fields - set(keys)), "unknown": sorted(set(keys) - fields),
                           "duplicates": sorted({key for key in keys if keys.count(key) > 1})}
    tests, imports = {}, {}
    for name in initial:
        if name.startswith("tests/test_v2") and name.endswith(".py"):
            tree = ast.parse((PROJECT / name).read_text(encoding="utf-8"), filename=name)
            tests[name] = sum(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_") for n in ast.walk(tree))
            imports[name] = [{"line": n.lineno, "module": n.module} for n in tree.body if isinstance(n, ast.ImportFrom)]
        elif compile_ast and name.endswith(".py"):
            ast.parse((PROJECT / name).read_text(encoding="utf-8-sig"), filename=name)
    node = shutil.which("node")
    if node is None:
        raise SetupFailure("installed_node_required")
    # No arbitrary script, eval, npm, package install, build, server, or browser.
    env = {key: value for key, value in os.environ.items() if key.upper() in {
        "PATH", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PATHEXT", "COMSPEC"}}
    completed = subprocess.run([node, "-"], input=DESIGN_PROGRAM, cwd=PROJECT, env=env,
                               capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
    if completed.returncode:
        raise SetupFailure("static_design_parser_failed")
    design = json.loads(completed.stdout)
    design["handoff_sha256"] = initial[design["handoff"]]
    write_json(evidence / "design-map.json", design)
    report = {"settings_fields": len(fields), "templates": templates,
              "v2_test_method_declarations_not_runtime_cases": tests,
              "v2_top_level_imports": imports,
              "legacy_modules_if_requested": list(LEGACY_MODULES),
              "design_counts": design["observed"], "design_count_mismatches": design["mismatches"],
              "compile_ast": compile_ast, "frontend_tests": "not_run"}
    write_json(evidence / "static-inventory.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static-only", action="store_true", help="source + AST/design inventory only; no test imports")
    parser.add_argument("--compile-ast", action="store_true", help="parse all inventoried Python without bytecode writes")
    parser.add_argument("--legacy", action="store_true", help="also run the explicit reused-contract modules")
    args = parser.parse_args(argv)
    sys.dont_write_bytecode = True
    if any(name == "backend" or name.startswith("backend.") for name in sys.modules):
        print('{"status":"refused_preimported_backend"}')
        return 2
    os.chdir(PROJECT)
    sys.path.insert(0, str(PROJECT))
    from tests.validation_environment import evidence_parent, temporary_base

    # Clean clones need an empty parent, never historical evidence. Validate
    # every ancestor before creating it; each actual run remains exclusive.
    try:
        parent = evidence_parent(PROJECT)
    except (OSError, RuntimeError):
        print('{"status":"unsafe_evidence_parent"}')
        return 2
    label = "v2-validation-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + uuid4().hex
    evidence = parent / label
    evidence.mkdir(exist_ok=False)
    summary = {"schema_version": 1, "status": "setup_failed", "run": label, "platform": sys.platform,
               "scope": "static" if args.static_only else "v2_plus_selected_legacy" if args.legacy else "v2",
               "acceptance": "pending_integration", "frontend_tests": "not_run"}
    initial, guard = {}, None
    exit_code = 2
    started = time.perf_counter()
    # Redirect native fd 1/2 as well: no codec, child, logging handler or crash dump
    # can bypass the Python sink into an evidence log. Native crashes leave an
    # incomplete claim; they are never automatically retried or labelled passed.
    with ExitStack() as stack:
        sink = Sink()
        stack.enter_context(redirect_stdout(sink))
        stack.enter_context(redirect_stderr(sink))
        null = stack.enter_context(open(os.devnull, "w"))
        for fd in (1, 2):
            saved = os.dup(fd)
            def restore_fd(fd=fd, saved=saved):
                os.dup2(saved, fd)
                os.close(saved)
            stack.callback(restore_fd)
            os.dup2(null.fileno(), fd)
        try:
            write_json(evidence / "claim.json", {"run": label, "state": "started", "pid": os.getpid()})
            initial = snapshot()
            write_json(evidence / "source-before.json", initial)
            info = static_inventory(evidence, initial, compile_ast=args.compile_ast)
            summary["static"] = info
            if args.static_only:
                summary["status"] = "static_complete_not_acceptance"
                exit_code = 0
            else:
                from tests.run_core_validation import synthetic_environment
                temp_base = temporary_base(os.environ)
                root = Path(tempfile.mkdtemp(prefix="gm-v2-validation-", dir=temp_base))
                for name in ("tmp", "tasks", "cache/asr", "cache/hf", "cache/matplotlib", "cache/numba"):
                    (root / name).mkdir(parents=True, exist_ok=True)
                environment, ffmpeg = synthetic_environment(root)
                os.environ.clear()
                os.environ.update(environment)
                tempfile.tempdir = str(root / "tmp")
                summary["temp_root"] = str(root)
                loader = SafeLoader(initial)
                result = SafeResult(initial)
                module_counts = {}
                def discover_file(name):
                    selected = loader.discover(str(PROJECT / "tests"), pattern=name, top_level_dir=str(PROJECT))
                    module_counts[name] = selected.countTestCases()
                    return selected
                def pure_phase():
                    discover_file("test_v2_text_rules.py").run(result)
                guard = install_v2_guards(stack, root, evidence, ffmpeg, pure_phase)
                suite = unittest.TestSuite()
                for name in info["v2_test_method_declarations_not_runtime_cases"]:
                    if Path(name).name != "test_v2_text_rules.py":
                        suite.addTests(discover_file(Path(name).name))
                if args.legacy:
                    suite.addTests(loader.loadTestsFromName("tests." + name) for name in LEGACY_MODULES)
                summary["discovered"] = module_counts["test_v2_text_rules.py"] + suite.countTestCases()
                suite, excluded = platform_suite(suite)
                summary["platform_exclusions_not_run"] = excluded
                summary["platform_exclusion_reason"] = "Windows-native transport/8.3 coverage requires Windows" if excluded else None
                summary["selected"] = module_counts["test_v2_text_rules.py"] + suite.countTestCases()
                summary["discovered_by_module"] = module_counts
                summary["pure_import_phase"] = "cold_before_shared_dependency_imports"
                summary["import_failure_diagnostics"] = loader.import_failures
                suite.run(result)
                summary.update(tests=result.testsRun, passed=result.passed, failures=len(result.failures),
                               errors=len(result.errors), skipped=len(result.skipped),
                               expected_failures=len(result.expectedFailures), unexpected_successes=len(result.unexpectedSuccesses),
                               discovery_errors=len(loader.errors), failure_source_only=result.events)
                clean = result.wasSuccessful() and not loader.errors and result.testsRun > 0
                # Skips/xfails need review, never quietly called a complete pass.
                clean = clean and not result.skipped and not result.expectedFailures
                summary["status"] = "backend_passed_not_acceptance" if clean else "failed_or_requires_review"
                exit_code = 0 if clean else 1
        except BaseException as error:
            summary["status"] = "runner_failed"
            summary["runner_failure_source"] = source_frames(error.__traceback__, initial)
            summary["runner_failure_diagnostics"] = safe_exception(error, initial)
            # Only allowlisted literal setup codes, never str(error).
            if type(error) is SetupFailure and error.args and re.fullmatch(r"[a-z_]+", error.args[0]):
                summary["setup_code"] = error.args[0]
            exit_code = 2
        finally:
            try:
                final = snapshot()
                write_json(evidence / "source-after.json", final)
                drift = sorted(name for name in initial.keys() | final.keys() if initial.get(name) != final.get(name))
                summary["source_drift"] = drift
                summary["source_files_checked"] = len(initial)
                if drift:
                    summary["status"], exit_code = "source_drift_requires_rerun", 1
                if guard is not None:
                    summary["guard_counters"] = dict(guard.counts)
                    summary["remaining_owned_http_listeners"] = len(guard.ports)
                    summary["registered_admission_databases"] = len(guard.databases)
                    if guard.ports or any(k.startswith("suite:denied:") for k in guard.counts):
                        summary["status"], exit_code = "guard_or_cleanup_requires_review", 1
                summary["duration_seconds"] = round(time.perf_counter() - started, 6)
                write_json(evidence / "summary.json", summary)
            except BaseException:
                summary = {"run": label, "status": "evidence_incomplete"}
                exit_code = 2
    print(json.dumps(summary, ensure_ascii=True), flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())