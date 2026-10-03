"""Synthetic TEMP-only packaging; header fixtures are NOT decodable video.

Source-executed runtime functions + real ReportResponse prove the minimal
profile without importing workbench/config/providers or starting any host.
"""
from __future__ import annotations

import ast
import contextlib
import copy
import hashlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy import prepare_sample_bundle as bundle


class SampleBundleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="gm-sample-bundle-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / "reviewed"
        self.source.mkdir()
        self.output = self.root / "prepared"
        self.report = {"task_id": "a" * 32, "mode": "voiceover", "rows": [{
            "sentence_id": 0, "sentence": "Synthetic public caption", "shot_id": 1,
            "thumb_url": None, "description": "Synthetic shape", "duration": 1.0,
            "confidence": 0.8, "is_fallback": False, "audio_kind": "tts", "visual_beats": [{
                "beat_id": 0, "text": "Shape", "shot_id": 1, "thumb_url": "",
                "description": "Shape", "confidence": 0.8}]}]}
        self.video = b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42" + b"synthetic-not-video"
        self.descriptor = {"schema_version": 1, "task_id": "a" * 32,
                           "title": "Synthetic fixture", "files": {}}
        self.put("report.json", json.dumps(self.report).encode())
        self.put("final.mp4", self.video)
        self.save()

    def put(self, name, payload):
        (self.source / name).write_bytes(payload)
        self.descriptor["files"][name] = {"sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}

    def save(self):
        (self.source / bundle.DESCRIPTOR).write_text(json.dumps(self.descriptor), encoding="utf-8")

    def prepare(self):
        return bundle.prepare(self.source, self.output, rights_reviewed=True)

    def rejected(self):
        with self.assertRaises((bundle.BundleError, OSError)):
            self.prepare()
        self.assertFalse(self.output.exists())

    def test_minimal_bundle_exact_bytes_registry_and_truthful_summary(self):
        before = {p.name: p.read_bytes() for p in self.source.iterdir()}
        summary = self.prepare()
        self.assertEqual(summary["status"], "prepared_not_installed")
        self.assertEqual(summary["file_count"], 2)
        for key in ("ffprobe_performed", "privacy_verified", "quality_verified", "runtime_render_verified"):
            self.assertIs(summary[key], False)
        self.assertEqual(summary["rights_review"], "caller_attested_not_verified")
        registry = json.loads((self.output / "registry.json").read_bytes())
        self.assertEqual(registry, {"default": {"directory": "default", "task_id": "a" * 32,
                         "title": "Synthetic fixture", "files": {
                             name: item["sha256"] for name, item in self.descriptor["files"].items()}}})
        self.assertEqual({p.name for p in self.output.iterdir()}, {"default", "registry.json"})
        for name in self.descriptor["files"]:
            self.assertEqual((self.output / "default" / name).read_bytes(), before[name])
            self.assertNotEqual((self.source / name).stat().st_ino, (self.output / "default" / name).stat().st_ino)
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.source.iterdir()})

    def test_runtime_functions_and_real_model_accept_profile_without_config(self):
        from backend.models import ReportResponse
        self.report["mode"] = "mixed"
        sync = copy.deepcopy(self.report["rows"][0])
        sync.update(sentence_id=1, kind="quote", audio_kind="sync")
        self.report["rows"].append(sync)
        self.put("report.json", json.dumps(self.report).encode())
        self.put("timings.json", b'[{"sentence_id":0,"start":0,"end":1},{"sentence_id":1,"start":1,"end":2}]')
        self.save()
        self.prepare()

        def load_function(relative, name, namespace):
            tree = ast.parse((bundle.ROOT / relative).read_text(encoding="utf-8"))
            node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
            unit = ast.Module(body=[node], type_ignores=[])
            exec(compile(unit, relative, "exec", flags=__import__("__future__").annotations.compiler_flag), namespace)
            return namespace[name]

        # revisions imports storage -> config. Execute its unchanged local
        # readers with a local error type instead; never import that chain.
        readers = {"Path": Path, "PurePosixPath": __import__("pathlib").PurePosixPath,
                   "RevisionError": ValueError, "json": json}
        local_file = load_function("backend/revisions.py", "local_file", readers)
        read_json = load_function("backend/revisions.py", "read_json", readers)
        safe = load_function("backend/workbench.py", "_safe_report",
                             {"ReportResponse": ReportResponse, "read_json": read_json})
        projected = safe(self.output / "default", "a" * 32, {})
        model = ReportResponse.model_validate(projected)
        self.assertEqual([row.audio_kind for row in model.rows], ["tts", "sync"])
        self.assertEqual([row["audio_kind"] for row in projected["rows"]], ["tts", "sync"])
        self.assertEqual([row["audio_source"] for row in projected["rows"]], ["tts", "sync"])
        self.assertEqual((projected["rows"][0]["start"], projected["rows"][0]["end"]), (0, 1))
        workbench = types.ModuleType("backend.workbench")
        workbench._safe_report = safe
        revisions = types.ModuleType("backend.revisions")
        revisions.local_file = local_file
        function = load_function("backend/v2_editing.py", "packaged_sample",
                                 {"__package__": "backend", "SAMPLE_ASSETS": self.output,
                                  "re": __import__("re"), "read_json": read_json,
                      "RevisionError": ValueError})
        with patch.dict(sys.modules, {"backend.workbench": workbench, "backend.revisions": revisions}):
            response = function()
            self.assertIs(response["read_only"], True)
            self.assertEqual(response["task_id"], self.report["task_id"])
            self.assertEqual([row["audio_kind"] for row in response["report"]["rows"]], ["tts", "sync"])
            self.assertEqual([row["audio_source"] for row in response["report"]["rows"]], ["tts", "sync"])
            self.assertIsNone(response["report"]["rows"][0]["visual_beats"][0]["thumb_url"])
            self.assertEqual(response["video_url"], "/api/samples/default/video")
            self.assertEqual(Path(function(video=True).path), self.output / "default/final.mp4")
            (self.output / "default/final.mp4").write_bytes(b"tampered")
            self.assertEqual(function().status_code, 503)

    def test_rights_review_must_be_explicit_boolean_before_any_read(self):
        for value in (False, None, 1, "true"):
            with self.subTest(value=value), patch.object(Path, "open", side_effect=AssertionError("read")):
                with self.assertRaisesRegex(bundle.BundleError, "rights_review_required"):
                    bundle.prepare(self.source, self.output, rights_reviewed=value)
        self.assertFalse(self.output.exists())

    def test_relative_paths_and_overlap_rejected(self):
        for source, output in ((Path("relative"), self.output), (self.source, Path("relative")),
                               (self.source, self.source / "out"), (self.source, self.root),
                               (self.source, self.root / "x" / ".." / "out")):
            with self.subTest(source=source, output=output), self.assertRaises(bundle.BundleError):
                bundle.prepare(source, output, rights_reviewed=True)

    def test_runtime_and_user_storage_refused_without_reading_it(self):
        with patch.object(bundle, "checked_path", side_effect=AssertionError("must not access")), \
            patch.object(Path, "resolve", side_effect=AssertionError("must not resolve")):
            for relative in ("data/tasks/owned", "eval_sample/owned", "backend/assets/samples/owned"):
                with self.subTest(relative=relative), self.assertRaises(bundle.BundleError):
                    bundle.prepare(bundle.ROOT / relative, self.output, rights_reviewed=True)
                with self.assertRaises(bundle.BundleError):
                    bundle.prepare(self.source, bundle.ROOT / relative, rights_reviewed=True)

    def assert_boundary_before_io(self, source, output, code):
        with patch.object(bundle, "consume", side_effect=AssertionError("content read")), \
                patch.object(Path, "open", side_effect=AssertionError("content open")), \
                patch.object(Path, "mkdir", side_effect=AssertionError("output create")):
            with self.assertRaisesRegex(bundle.BundleError, "^" + code + "$"):
                bundle.prepare(source, output, rights_reviewed=True)
        self.assertFalse(output.exists())

    def test_synthetic_canonical_aliases_repeat_all_storage_and_overlap_checks(self):
        # Model non-link aliases with existing TEMP directories and only the
        # resolver substituted. No workspace storage is inspected or created.
        workspace = self.root / "synthetic-workspace"
        workspace.mkdir()
        alias = self.root / "ancestor-alias"
        alias.mkdir()
        checked = bundle.checked_path
        resolve = Path.resolve
        cases = []
        for relative in ("data", "eval_sample", "backend/assets/samples"):
            protected = workspace / relative
            protected.mkdir(parents=True)
            cases.extend([
                (alias, self.output, {alias: protected}, "runtime_or_user_storage_forbidden"),
                (self.source, alias / "fresh", {alias: protected}, "runtime_or_user_storage_forbidden"),
            ])
        # Output inside source, and source inside the as-yet-uncreated lexical
        # output name, both hidden by an ancestor alias before resolution.
        nested = self.source / "nested"
        nested.mkdir()
        cases.extend([
            (self.source, alias / "fresh", {alias: self.source}, "overlapping_paths"),
            (nested, alias / self.source.name, {alias: self.root}, "overlapping_paths"),
        ])
        for source, output, mapping, code in cases:
            visits = []

            def check(path, **kwargs):
                info = checked(path, **kwargs)
                visits.append(path)
                return info

            def canonical(path, *, strict=False):
                self.assertTrue(strict)
                self.assertEqual(visits, [source, output.parent])
                return resolve(mapping.get(path, path), strict=True)

            with self.subTest(code=code, source=source.name, output=output.name), \
                    patch.object(bundle, "ROOT", workspace), \
                    patch.object(bundle, "checked_path", side_effect=check), \
                    patch.object(Path, "resolve", canonical):
                self.assert_boundary_before_io(source, output, code)

    def test_native_windows_short_ancestor_aliases_before_content_or_creation(self):
        # Capability is measured, never inferred from OS alone. An explicit
        # skip means native 8.3 coverage was NOT exercised on this volume.
        if os.name != "nt":
            self.skipTest("native 8.3 unavailable: not Windows; synthetic alias tests remain separate")
        import ctypes
        from ctypes import wintypes
        workspace = self.root / "Synthetic workspace long ancestor"
        workspace.mkdir()
        get_short = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
        get_short.argtypes = (wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD)
        get_short.restype = wintypes.DWORD
        size = get_short(str(workspace), None, 0)
        if not size:
            self.fail("GetShortPathNameW failed: " + str(ctypes.get_last_error()))
        buffer = ctypes.create_unicode_buffer(size)
        written = get_short(str(workspace), buffer, size)
        self.assertGreater(written, 0)
        self.assertLess(written, size)
        # Use the native short name for OUR ancestor only, retaining the TEMP
        # parent's spelling. An alias of a user-profile ancestor is not proof.
        short_name = Path(buffer.value).name
        if short_name.casefold() == workspace.name.casefold():
            self.skipTest("native 8.3 unavailable: TEMP ancestor has no short name; not native coverage")
        alias = workspace.with_name(short_name)
        self.assertTrue(alias.samefile(workspace))
        self.assertEqual(alias.resolve(strict=True), workspace.resolve(strict=True))
        with patch.object(bundle, "ROOT", workspace):
            for relative in ("data", "eval_sample", "backend/assets/samples"):
                protected = workspace / relative
                protected.mkdir(parents=True)
                with self.subTest(relative=relative, role="source"):
                    self.assert_boundary_before_io(alias / relative, self.output,
                                                   "runtime_or_user_storage_forbidden")
                with self.subTest(relative=relative, role="output-parent"):
                    self.assert_boundary_before_io(self.source, alias / relative / "fresh",
                                                   "runtime_or_user_storage_forbidden")
            reviewed = workspace / "reviewed"
            reviewed.mkdir()
            nested = reviewed / "nested"
            nested.mkdir()
            self.assert_boundary_before_io(reviewed, alias / "reviewed/fresh", "overlapping_paths")
            # Output already exists under its canonical spelling; overlap must
            # win before freshness (the common helper expects a fresh output).
            with patch.object(bundle, "consume", side_effect=AssertionError("content read")), \
                    patch.object(Path, "mkdir", side_effect=AssertionError("output create")), \
                    self.assertRaisesRegex(bundle.BundleError, "^overlapping_paths$"):
                bundle.prepare(nested, alias / "reviewed", rights_reviewed=True)

    def test_output_existing_file_or_directory_is_never_overwritten(self):
        for directory in (False, True):
            with self.subTest(directory=directory):
                if directory:
                    self.output.mkdir()
                else:
                    self.output.write_bytes(b"retain")
                with self.assertRaisesRegex(bundle.BundleError, "output_not_fresh"):
                    self.prepare()
                if directory:
                    self.assertEqual(list(self.output.iterdir()), [])
                    self.output.rmdir()
                else:
                    self.assertEqual(self.output.read_bytes(), b"retain")
                    self.output.unlink()

    def test_unlisted_private_files_are_not_opened_or_copied(self):
        (self.source / ".env").write_bytes(b"synthetic-do-not-read")
        (self.source / "shots_annotated.json").write_bytes(b"synthetic-do-not-read")
        original = bundle.consume
        opened = []

        def observed(path, *args, **kwargs):
            opened.append(path.name)
            return original(path, *args, **kwargs)

        with patch.object(bundle, "consume", side_effect=observed):
            self.prepare()
        self.assertNotIn(".env", opened)
        self.assertNotIn("shots_annotated.json", opened)
        self.assertEqual({p.name for p in (self.output / "default").iterdir()}, {"report.json", "final.mp4"})

    def test_unsafe_unrecognized_and_case_colliding_names_rejected(self):
        original = copy.deepcopy(self.descriptor)
        for name in ("../private", "/absolute", "C:/private", "report.json:stream", "REPORT.JSON",
                     "a\\b", "a//b", "a/./b", "report.json.", "shots_annotated.json", "registry.json"):
            with self.subTest(name=name):
                self.descriptor = copy.deepcopy(original)
                self.descriptor["files"][name] = {"sha256": "a" * 64, "bytes": 1}
                self.save()
                self.rejected()

    def test_missing_or_mismatched_known_files_rejected(self):
        original = copy.deepcopy(self.descriptor)
        for name in ("report.json", "final.mp4"):
            for field, value in (("bytes", 1), ("sha256", "0" * 64), ("bytes", True),
                                 ("sha256", "A" * 64), ("bytes", bundle.MAX_VIDEO + 1)):
                with self.subTest(name=name, field=field, value=value):
                    self.descriptor = copy.deepcopy(original)
                    self.descriptor["files"][name][field] = value
                    self.save()
                    self.rejected()
        self.descriptor = original
        self.save()
        (self.source / "final.mp4").unlink()
        self.rejected()

    def test_descriptor_and_report_identity_rejected(self):
        original = copy.deepcopy(self.descriptor)
        for key, value in (("task_id", "A" * 32), ("task_id", "b" * 32),
                           ("schema_version", True), ("title", ""), ("title", "x" * 81)):
            with self.subTest(key=key, value=value):
                self.descriptor = copy.deepcopy(original)
                self.descriptor[key] = value
                self.save()
                self.rejected()

    def test_duplicate_keys_nonfinite_invalid_utf8_and_deep_json_rejected(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'\xff', b'[' * 2000 + b']' * 2000):
            with self.subTest(raw_prefix=raw[:10]):
                (self.source / bundle.DESCRIPTOR).write_bytes(raw)
                self.rejected()
        for value in (float("nan"), float("inf"), 1e309):
            self.report["rows"][0]["duration"] = value
            self.put("report.json", json.dumps(self.report).encode())
            self.save()
            self.rejected()

    def test_report_profile_rejects_unknown_private_or_invalid_fields(self):
        original = copy.deepcopy(self.report)
        for field, value in (("sentence_id", True), ("duration", 0), ("confidence", 2),
                             ("is_fallback", 1), ("thumb_url", "/private?token=secret"),
                             ("sentence", ""), ("source", {"path": "private"}),
                             ("audio_kind", "unknown"), ("audio_kind", None),
                             ("audio_kind", ""), ("audio_kind", False), ("visual_beats", None)):
            with self.subTest(field=field):
                report = copy.deepcopy(original)
                report["rows"][0][field] = value
                self.put("report.json", json.dumps(report).encode())
                self.save()
                self.rejected()
        for field in ("quality", "checks", "metrics", "access_token"):
            report = copy.deepcopy(original)
            report[field] = {}
            self.put("report.json", json.dumps(report).encode())
            self.save()
            self.rejected()

    def test_audio_kind_must_be_explicit_on_every_new_sample_row(self):
        for mode in (None, "voiceover", "mixed", "original"):
            for missing_index in (0, 1):
                with self.subTest(mode=mode, missing_index=missing_index):
                    report = copy.deepcopy(self.report)
                    if mode is None:
                        report.pop("mode")
                    else:
                        report["mode"] = mode
                    report["rows"][0]["audio_kind"] = "sync"
                    second = copy.deepcopy(report["rows"][0])
                    second["sentence_id"] = 1
                    report["rows"].append(second)
                    del report["rows"][missing_index]["audio_kind"]
                    self.put("report.json", json.dumps(report).encode())
                    self.save()
                    with self.assertRaisesRegex(bundle.BundleError, "^unsupported_fields$"):
                        self.prepare()
                    self.assertFalse(self.output.exists())

    def test_original_mode_rejects_explicit_tts_on_any_row(self):
        for tts_index in (0, 1):
            with self.subTest(tts_index=tts_index):
                report = copy.deepcopy(self.report)
                report["mode"] = "original"
                report["rows"][0]["audio_kind"] = "sync"
                second = copy.deepcopy(report["rows"][0])
                second["sentence_id"] = 1
                report["rows"].append(second)
                report["rows"][tts_index]["audio_kind"] = "tts"
                self.put("report.json", json.dumps(report).encode())
                self.save()
                with self.assertRaisesRegex(bundle.BundleError, "^original_audio_contradiction$"):
                    self.prepare()
                self.assertFalse(self.output.exists())

    def test_explicit_audio_kinds_preserved_in_accepted_profiles(self):
        for mode, kinds in ((None, ["tts"]), ("voiceover", ["tts"]),
                            ("voiceover", ["sync"]), ("mixed", ["tts", "sync"]),
                            ("original", ["sync", "sync"])):
            with self.subTest(mode=mode, kinds=kinds):
                report = copy.deepcopy(self.report)
                if mode is None:
                    report.pop("mode")
                else:
                    report["mode"] = mode
                template = report["rows"][0]
                report["rows"] = [dict(copy.deepcopy(template), sentence_id=i, audio_kind=kind)
                                  for i, kind in enumerate(kinds)]
                payload = json.dumps(report).encode()
                self.put("report.json", payload)
                self.save()
                self.output = self.root / ("prepared-" + str(mode) + "-" + "-".join(kinds))
                self.prepare()
                self.assertEqual((self.output / "default/report.json").read_bytes(), payload)

    def test_duplicate_rows_and_beats_rejected(self):
        for beats in (False, True):
            report = copy.deepcopy(self.report)
            rows = report["rows"][0]["visual_beats"] if beats else report["rows"]
            rows.append(copy.deepcopy(rows[0]))
            self.put("report.json", json.dumps(report).encode())
            self.save()
            self.rejected()

    def test_timings_require_exact_ids_finite_ordered_nonoverlapping_clocks(self):
        for timings in ([], [{"sentence_id": 1, "start": 0, "end": 1}],
                        [{"sentence_id": 0, "start": 1, "end": 1}],
                        [{"sentence_id": 0, "start": -1, "end": 1}],
                        [{"sentence_id": 0, "start": False, "end": 1}],
                        [{"sentence_id": 0, "start": 0, "end": 601}],
                        [{"sentence_id": 0, "start": 0, "end": 1, "audio_path": "private"}]):
            with self.subTest(timings=timings):
                self.put("timings.json", json.dumps(timings).encode())
                self.save()
                self.rejected()

    def test_plain_text_and_truncated_mp4_headers_rejected(self):
        for payload in (b"pretend mp4", b"\0\0\0\x18ftyp", b"\0\0\0\x08ftyp" + b"x" * 32,
                        b"\xff\xff\xff\xffftyp" + b"x" * 32):
            self.put("final.mp4", payload)
            self.save()
            self.rejected()

    def test_two_row_timing_overlap_or_reordering_rejected(self):
        second = copy.deepcopy(self.report["rows"][0])
        second["sentence_id"] = 4
        self.report["rows"].append(second)
        self.put("report.json", json.dumps(self.report).encode())
        for timings in ([{"sentence_id": 0, "start": 0, "end": 1},
                         {"sentence_id": 4, "start": 0.9, "end": 2}],
                        [{"sentence_id": 4, "start": 0, "end": 1},
                         {"sentence_id": 0, "start": 1, "end": 2}]):
            self.put("timings.json", json.dumps(timings).encode())
            self.save()
            self.rejected()

    def test_hardlinked_file_rejected(self):
        original = self.source / "final.mp4"
        os.link(original, self.root / "alias.mp4")
        self.rejected()

    def test_linked_source_directory_and_output_parent_rejected(self):
        alias = self.root / "alias"
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(self.source), str(alias))
            self.addCleanup(os.rmdir, alias)
        else:
            alias.symlink_to(self.source, target_is_directory=True)
            self.addCleanup(alias.unlink)
        with patch.object(Path, "resolve", side_effect=AssertionError("must not resolve link")), \
            self.assertRaisesRegex(bundle.BundleError, "linked_path"):
            bundle.prepare(alias, self.output, rights_reviewed=True)
        with patch.object(Path, "resolve", side_effect=AssertionError("must not resolve link")), \
            self.assertRaisesRegex(bundle.BundleError, "linked_path"):
            bundle.prepare(self.source, alias / "output", rights_reviewed=True)

    def test_media_streaming_is_bounded(self):
        self.put("final.mp4", self.video + b"x" * (bundle.CHUNK * 2))
        self.save()
        with patch.object(Path, "read_bytes", side_effect=AssertionError("whole file read")):
            summary = self.prepare()
        self.assertEqual(summary["files"]["final.mp4"]["bytes"], len(self.video) + bundle.CHUNK * 2)

    def test_size_limits_reject_before_output(self):
        with patch.object(bundle, "MAX_JSON", 10):
            self.rejected()
        with patch.object(bundle, "MAX_VIDEO", 10):
            self.rejected()

    def test_source_mutation_during_copy_never_publishes_registry(self):
        original = bundle.consume

        def mutate(path, *args, **kwargs):
            if kwargs.get("target") and path.name == "final.mp4":
                path.write_bytes(self.video + b"changed")
            return original(path, *args, **kwargs)

        with patch.object(bundle, "consume", side_effect=mutate), self.assertRaises(bundle.BundleError):
            self.prepare()
        self.assertTrue(self.output.exists())
        self.assertFalse((self.output / "registry.json").exists())
        with self.assertRaisesRegex(bundle.BundleError, "output_not_fresh"):
            self.prepare()

    def test_descriptor_mutation_after_validation_never_publishes_registry(self):
        original = bundle.consume

        def mutate(path, *args, **kwargs):
            if kwargs.get("target") and path.name == "final.mp4":
                self.descriptor["title"] = "Changed"
                self.save()
            return original(path, *args, **kwargs)

        with patch.object(bundle, "consume", side_effect=mutate), self.assertRaisesRegex(bundle.BundleError, "descriptor_changed"):
            self.prepare()
        self.assertFalse((self.output / "registry.json").exists())

    def test_cli_missing_review_requires_opt_in_and_failures_redact_paths(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            bundle.main(["--source", str(self.source), "--output", str(self.output)])
        self.assertEqual(raised.exception.code, 2)
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            result = bundle.main(["--source", str(self.root / "missing-private-name"),
                                  "--output", str(self.output), "--rights-reviewed"])
        self.assertEqual(result, 1)
        self.assertNotIn("private-name", stream.getvalue())
        self.assertEqual(json.loads(stream.getvalue())["status"], "rejected")

    def test_cli_success_creates_only_explicit_fresh_output(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            result = bundle.main(["--source", str(self.source), "--output", str(self.output), "--rights-reviewed"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(stream.getvalue())["status"], "prepared_not_installed")


if __name__ == "__main__":
    unittest.main()