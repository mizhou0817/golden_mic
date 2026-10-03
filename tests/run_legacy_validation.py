"""Source-only legacy inventory; NOT a legacy test executor.

Run with the existing interpreter and -I -B. Default/--inventory-only reads only
Python source beneath tests, never imports inventoried modules, and writes only
JSON to stdout. --module tests.test_assignment (repeatable) plans exact modules.
--execute always refuses in this first-stage tool: no execution profile has been
approved. A successful inventory exit is NOT evidence of executed test coverage.

Execution adapter proposal (not implemented): reuse install_v2_guards, SafeLoader
and SafeResult in a cold, sanitized worker with a fresh owned TEMP root, native
stdout/stderr suppression, before/after source binding and cleanup receipts.
Keep existing guard defaults unchanged. Python-child and synthetic legacy-SQLite
profiles need separately reviewed exact argv/child-bootstrap and database
registration capabilities; do not replace V2 guards with the looser core guard.
No wildcard discovery, blanket TEMP SQL allowance, or automatic retry is safe.

If shared changes are later authorized, propose an optional immutable profile
argument to install_v2_guards (default identical to today), with an empty child
allowlist and no extra SQL by default. Extra SQL must be registered by an exact
fixture constructor before file creation, under this worker's fresh TEMP, with
URI mode/sidecars separately checked. A child grant must bind interpreter, argv,
source hashes, sanitized environment and an independently guarded bootstrap;
parent audit hooks do not confine a child. Add denial regressions before enabling
one exact reviewed module. This inventory neither adds nor activates that API.
Path checks and two snapshots detect ordinary drift, not hostile concurrent
filesystem replacement; this source utility is not an OS sandbox.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any


PROJECT = Path(__file__).absolute().parents[1]
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_FILES = 512
MAX_DEPTH = 12
MODULE = re.compile(r"tests(?:\.[A-Za-z_][A-Za-z0-9_]*)*\.test[A-Za-z0-9_]*\Z")
BLOCKED_DIRECTORIES = frozenset({
    "data", "eval", "eval_sample", "artifacts", "cache", "models", "weights",
    "node_modules", ".venv", ".git",
})

# Explicit source-triage profiles, NOT reviewed execution grants. Resources below
# are review requirements, not assertions that every method uses that resource.
# Never infer an execution permission from an import, filename or historic pass.
PROFILE_GROUPS = {
    "logic_contract": (
        "test_anonymous_access", "test_assignment", "test_headline_contract",
        "test_production_modes",
    ),
    "temporary_contract": (
        "test_asr", "test_canary_rendering", "test_embedding_concurrency",
        "test_m3_matching", "test_m7_remix", "test_mode_workbench",
        "test_provenance", "test_publication", "test_round2_workbench_capacity",
        "test_studio_assets", "test_studio_audit", "test_studio_prototype",
        "test_studio_sequences", "test_studio_transitions", "test_workbench",
    ),
    "native_media": (
        "test_editing_enhancements", "test_m2_vision", "test_m4_tts",
        "test_media_input", "test_mode_exports", "test_mode_pipeline",
        "test_motion_rendering", "test_prototype_preferences", "test_public_media",
        "test_quality", "test_studio", "test_studio_audio_clock",
        "test_studio_proxy", "test_upload_sessions",
    ),
    "main_lifespan": ("test_m5_rendering", "test_m6_operations"),
    "owned_http_pipeline": ("test_m6_e2e",),
    "python_child": ("test_deployment", "test_m1_media", "test_mode_api"),
    "synthetic_legacy_sqlite": ("test_task_operations", "test_round2_task_restart"),
    "python_child_and_sqlite": ("test_workspace_access",),
}
PROFILE_RESOURCES = {
    "logic_contract": ("application_import_closure", "cold_import_order"),
    "temporary_contract": ("application_import_closure", "owned_temp", "fixture_dependency_closure"),
    "native_media": ("application_import_closure", "owned_temp", "native_codec_io", "fixture_dependency_closure"),
    "main_lifespan": ("application_import_closure", "owned_temp", "main_singleton_lifespan"),
    "owned_http_pipeline": ("application_import_closure", "owned_temp", "main_singleton_lifespan", "owned_http_listener", "native_codec_io"),
    "python_child": ("owned_temp", "exact_python_child_bootstrap", "native_codec_io"),
    "synthetic_legacy_sqlite": ("application_import_closure", "owned_temp", "exact_synthetic_sqlite_registration"),
    "python_child_and_sqlite": ("owned_temp", "exact_python_child_bootstrap", "exact_synthetic_sqlite_registration"),
}
PROFILES = {"tests." + name: group for group, names in PROFILE_GROUPS.items() for name in names}

# Conservative syntactic hints only: absence does not establish resource safety.
SIGNALS = {
    "native_process": {"subprocess", "Popen", "create_subprocess_exec", "create_subprocess_shell"},
    "sqlite": {"sqlite3", "seed_legacy_ledger"},
    "socket_or_http": {"socket", "httpx", "aiohttp", "HTTPServer", "ThreadingHTTPServer"},
    "main_lifespan": {"TestClient", "backend.main"},
    "temporary_storage": {"tempfile", "TemporaryDirectory", "mkdtemp"},
    "dynamic_loading": {"exec", "eval", "import_module", "spec_from_file_location", "runpy", "__import__"},
    "custom_discovery": {"load_tests"},
    "model_or_provider": {"torch", "transformers", "sherpa_onnx", "silero_vad_lite", "backend.providers"},
    "global_patch_cleanup": {"stopall"},
}


class InventoryError(Exception):
    """Fixed code only; never expose source text or filesystem exception details."""


class QuietParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise InventoryError("invalid_arguments")


def plain_path(path: Path, *, directory: bool) -> None:
    """Reject ancestor indirection and multiply linked regular source files."""
    for part in reversed((path, *path.parents)):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise InventoryError("source_indirection")
    info = path.lstat()
    mode = info.st_mode
    if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
        raise InventoryError("source_kind")
    # Windows DirEntry.stat may cache st_nlink=0; use actual Path.lstat above.
    # Directory link counts have different semantics and remain unrestricted.
    if not directory and info.st_nlink != 1:
        raise InventoryError("source_indirection")


def source_paths(root: Path) -> list[Path]:
    """Enumerate source only; never descend into fixture/data/cache directories."""
    found: list[Path] = []
    tests = root / "tests"
    plain_path(tests, directory=True)

    def visit(folder: Path, depth: int) -> None:
        if depth > MAX_DEPTH:
            raise InventoryError("source_depth_limit")
        with os.scandir(folder) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                path = Path(entry.path)
                # These are not source roots. No listing/stat/read below them.
                if entry.name in {"__pycache__", "cases"}:
                    continue
                if entry.name.lower() in BLOCKED_DIRECTORIES or entry.name.startswith("."):
                    raise InventoryError("unexpected_source_boundary")
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise InventoryError("source_indirection")
                if stat.S_ISDIR(info.st_mode):
                    plain_path(path, directory=True)
                    visit(path, depth + 1)
                elif entry.name.endswith(".py"):
                    plain_path(path, directory=False)
                    found.append(path)
                    if len(found) > MAX_SOURCE_FILES:
                        raise InventoryError("source_count_limit")

    visit(tests, 0)
    return sorted(found)


def snapshot(root: Path) -> dict[str, bytes]:
    sources: dict[str, bytes] = {}
    for path in source_paths(root):
        with path.open("rb") as stream:
            content = stream.read(MAX_SOURCE_BYTES + 1)
        if len(content) > MAX_SOURCE_BYTES:
            raise InventoryError("source_size_limit")
        sources[path.relative_to(root).as_posix()] = content
    return sources


def inspect_module(name: str, content: bytes) -> dict[str, Any]:
    module = name[:-3].replace("/", ".")
    group = PROFILES.get(module)
    row: dict[str, Any] = {
        "module": module, "file": name, "sha256": hashlib.sha256(content).hexdigest(),
        "family": "v2" if Path(name).name.startswith("test_v2") else "legacy",
        "profile": group or "unprofiled", "review": "pending" if group else "unprofiled",
        "execution_authorized": False,
        "resource_review_required": list(PROFILE_RESOURCES.get(group or "", ("unknown",))),
    }
    try:
        tree = ast.parse(content, filename=name)
    except (SyntaxError, ValueError, UnicodeError):
        row.update(parse_status="invalid_source", declarations_not_runtime_cases=None, resource_signals=[])
        return row
    names: set[str] = set()
    declarations = 0
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add("." * node.level + (node.module or ""))
            names.add(node.module or "")
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
            declarations += node.name.startswith("test")
    hints = sorted(label for label, tokens in SIGNALS.items()
                   if any(name == token or name.startswith(token + ".") for name in names for token in tokens))
    row.update(parse_status="parsed", declarations_not_runtime_cases=declarations,
               imports=sorted(imports), resource_signals=hints)
    return row


def inventory(root: Path, modules: tuple[str, ...] = (), *, execute: bool = False) -> tuple[dict[str, Any], int]:
    if any(MODULE.fullmatch(name) is None for name in modules) or len(set(modules)) != len(modules):
        raise InventoryError("exact_unique_modules_required")
    before = snapshot(root)
    rows = [inspect_module(name, content) for name, content in before.items()
            if Path(name).name.startswith("test")]
    after = snapshot(root)
    drift = sorted(name for name in before.keys() | after.keys() if before.get(name) != after.get(name))
    by_module = {row["module"]: row for row in rows}
    selected = list(modules) if modules else sorted(by_module)
    plan = [{"module": name, "blocker": "module_not_found" if name not in by_module else
             "invalid_source" if by_module[name]["parse_status"] != "parsed" else
             "unprofiled" if by_module[name]["profile"] == "unprofiled" else "execution_review_pending"}
            for name in selected]
    invalid = sum(row["parse_status"] != "parsed" for row in rows)
    missing = any(name not in by_module for name in modules)
    planned_declarations = sum(by_module[name]["declarations_not_runtime_cases"] or 0
                               for name in selected if name in by_module)
    status = "source_drift" if drift else "inventory_invalid" if invalid or missing else "inventory_complete_not_execution"
    code = 1 if drift or invalid or missing else 0
    if execute:
        status, code = "execution_refused", 2
    report = {
        "schema_version": 1, "status": status, "mode": "inventory_only",
        "tests_executed": 0, "application_imports_performed": False,
        "execution_adapter": "not_implemented_requires_review",
        "scope": "tests Python source; not backend, fixture contents or runtime dependency closure",
        "excluded_directories": ["__pycache__", "cases (known non-Python fixture directory)"],
        "declaration_count_is_not_discovery": True,
        "resource_signals_are_incomplete_not_permissions": True,
        "module_count": len(rows), "legacy_module_count": sum(row["family"] == "legacy" for row in rows),
        "profiled_module_count": sum(row["profile"] != "unprofiled" for row in rows),
        "selected_module_count": len(selected),
        "planned_declarations_not_runtime_cases": planned_declarations,
        "source_files_checked": len(before),
        "source_sha256": {name: hashlib.sha256(content).hexdigest() for name, content in before.items()},
        "source_drift": drift, "modules": rows, "plan": plan,
        "missing_profile_sources": sorted(set(PROFILES) - set(by_module)),
        "execution_request_requires_exact_modules": execute and not modules,
    }
    return report, code


def main(argv: list[str] | None = None) -> int:
    try:
        parser = QuietParser(description=__doc__, allow_abbrev=False)
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--inventory-only", action="store_true")
        mode.add_argument("--execute", action="store_true", help="refused until a guarded execution adapter is reviewed")
        parser.add_argument("--module", action="append", default=[], help="exact dotted test module; repeat to plan multiple")
        parser.add_argument("--summary", action="store_true", help="omit per-module rows, plan and source hashes")
        args = parser.parse_args(argv)
        report, code = inventory(PROJECT, tuple(args.module), execute=args.execute)
        if args.summary:
            report = {key: value for key, value in report.items() if key not in {"modules", "plan", "source_sha256"}}
    except InventoryError as error:
        report, code = {"status": "inventory_refused", "code": error.args[0], "tests_executed": 0}, 2
    except Exception:
        report, code = {"status": "inventory_refused", "code": "source_inspection_failed", "tests_executed": 0}, 2
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())