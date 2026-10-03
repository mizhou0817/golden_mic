"""Local Studio contracts, isolated router transactions and REAL codec assertions.

No main import, Settings construction, dotenv loading or network transport. Router/media tests
reuse the existing Studio synthetic fixture helpers, under owned TEMP only.
FFmpeg tests skip for missing binaries, never for codec/effect failures. These
tests are intended for the main test runner; authoring them does not execute it.

Frame contract: half-up quantize submitted clip `at` and ripple-trim duration
using workspace FPS. Never snap old geometry, source trims/marks, ordinary trim,
markers or keyframe times. Ripple changes one track, starts >= OLD end only;
existing gaps survive. Touching endpoints are allowed, affected overlaps are
not (1e-12 seconds covers only float serialization noise). Roll/slide require
contiguous neighbours; full Project validation must succeed before persistence.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from backend.studio import (
    Action, _BUSY, _RUNNING, apply_action, capabilities, catalog,
    create_studio_router, public_state, read_state, state_default, write_state,
)
from backend.studio_render import (
    MAX_SUBTITLE_BYTES, Clip, ExportOptions, KeyPoint, Project, RenderError,
    SourceMark, Track, WorkspaceMetadata, color_filters, keyframe_expression,
    plain_ass, render_project, subtitle_document,
)
from tests import test_studio as existing
from tests.studio_publication_fixtures import seed_synthetic_publication


def sequence(**workspace: Any) -> Project:
    return Project.model_validate({
        "tracks": [{"id": "v", "type": "video", "clips": [
            {"id": "a", "source_id": "final", "start": 0, "duration": 2, "trim": 2},
            {"id": "b", "source_id": "final", "start": 2, "duration": 2, "trim": 5},
            {"id": "c", "source_id": "final", "start": 4, "duration": 2, "trim": 8},
        ]}],
        "markers": [{"id": "mark", "time": 2.12345}],
        "workspace": {"source_marks": [{"source_id": "final", "in_point": 0.12345, "out_point": 1.98765}], **workspace},
    })


def edit(project: Project, op: str, clip_id: str = "b", **fields: Any) -> Project:
    return apply_action(project, Action.model_validate({"expected_revision": 0, "op": op, "clip_id": clip_id, **fields}))


def caption_project(**fields: Any) -> Project:
    return Project(tracks=[Track(id="captions", type="text", clips=[
        Clip.model_validate({"id": "caption", "start": 0.2, "duration": 1, "text": "ABCDEFGH", **fields}),
    ])])


def dialogue_rows(document: str, layer: int = 0) -> list[list[str]]:
    return [line.split(",", 9) for line in document.splitlines() if line.startswith(f"Dialogue: {layer},")]


def centiseconds(stamp: str) -> int:
    hours, minutes, rest = stamp.split(":")
    seconds, cs = rest.split(".")
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 100 + int(cs)


class StudioPrototypeModelTests(unittest.TestCase):
    def test_schema_one_defaults_hydrate_legacy_snapshots_without_mutation(self):
        legacy = {"schema_version": 1, "tracks": [{"id": "t", "type": "text", "clips": [
            {"id": "c", "duration": 1, "text": "old"},
        ]}]}
        state = {"revision": 7, "project": legacy, "past": [], "future": []}
        before = json.dumps(state, sort_keys=True)
        actual = public_state(state)["project"]
        self.assertEqual(actual["schema_version"], 1)
        workspace = actual["workspace"]
        for key, value in {"timeline_fps": 30, "time_display": "seconds", "snap_enabled": True,
                           "ripple_enabled": False, "source_marks": []}.items():
            self.assertEqual(workspace[key], value)
        clip = actual["tracks"][0]["clips"][0]
        for name in ("temperature", "hue", "shadows", "highlights", "fade_amount"):
            self.assertEqual(clip[name], 0)
        self.assertEqual((clip["color_preset"], clip["rgb_curves"], clip["text_animation"], clip["text_template"]),
                         ("none", {}, "none", "custom"))
        self.assertEqual(json.dumps(state, sort_keys=True), before)
        first, second = WorkspaceMetadata(), WorkspaceMetadata()
        first.source_marks.append(SourceMark(source_id="final", in_point=0, out_point=1))
        self.assertEqual(second.source_marks, [])

    def test_workspace_bounds_source_marks_uniqueness_and_order(self):
        invalid: list[dict[str, Any]] = [
            {"timeline_fps": 29.97}, {"timeline_fps": 120}, {"time_display": "SMPTE"},
            {"source_marks": [{"source_id": "final", "in_point": 1, "out_point": 1}]},
            {"source_marks": [{"source_id": "final", "in_point": 2, "out_point": 1}]},
            {"source_marks": [{"source_id": "final", "in_point": -0.1, "out_point": 1}]},
            {"source_marks": [{"source_id": "final", "in_point": 0, "out_point": 3600.1}]},
            {"source_marks": [{"source_id": "../file", "in_point": 0, "out_point": 1}]},
            {"source_marks": [{"source_id": "final", "in_point": float("nan"), "out_point": 1}]},
            {"source_marks": [{"source_id": "final", "in_point": 0, "out_point": float("inf")}]},
            {"source_marks": [{"source_id": "final", "in_point": 0, "out_point": 1}] * 2},
            {"source_marks": [{"source_id": f"s{i}", "in_point": 0, "out_point": 1} for i in range(201)]},
        ]
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                WorkspaceMetadata.model_validate(fields)
        marks = [{"source_id": f"s{i}", "in_point": 0.00123, "out_point": 3600} for i in reversed(range(200))]
        workspace = WorkspaceMetadata.model_validate({"source_marks": marks})
        self.assertEqual(workspace.model_dump()["source_marks"], marks)

    def test_color_bounds_rgb_knots_and_track_applicability(self):
        invalid: list[dict[str, Any]] = [
            {"temperature": 1.001}, {"temperature": -1.001}, {"hue": 180.01},
            {"shadows": float("nan")}, {"highlights": -1.001}, {"fade_amount": -0.001},
            {"fade_amount": 1.001}, {"color_preset": "AI"}, {"color_preset": "movie=/secret"},
            {"rgb_curves": {"master": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]}},
            {"rgb_curves": {"red": []}}, {"rgb_curves": {"red": [{"x": 0, "y": 0}]}},
            {"rgb_curves": {"red": [{"x": 0.1, "y": 0}, {"x": 1, "y": 1}]}},
            {"rgb_curves": {"red": [{"x": 0, "y": 0}, {"x": 0.9, "y": 1}]}},
            {"rgb_curves": {"red": [{"x": 0, "y": 0}, {"x": 0, "y": 0.5}, {"x": 1, "y": 1}]}},
            {"rgb_curves": {"red": [{"x": 0, "y": -0.01}, {"x": 1, "y": 1}]}},
            {"rgb_curves": {"red": [{"x": 0, "y": 0}, {"x": 1, "y": float("inf")}]}},
            {"rgb_curves": {"red": [{"x": i / 8, "y": i / 8} for i in range(9)]}},
            {"rgb_curves": "curves=psfile=/secret"}, {"lut_path": "file.cube"},
            {"text_animation": "per_word_asr"}, {"text_template": "external.ass"},
        ]
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                Clip.model_validate({"id": "clip", "duration": 1, **fields})
        curve = {"red": [{"x": i / 7, "y": 1 - i / 7} for i in range(8)]}
        valid = Clip.model_validate({"id": "c", "duration": 1, "rgb_curves": curve})
        self.assertEqual(valid.model_dump()["rgb_curves"], curve)  # Only x must be monotonic.
        for kind, fields in (("audio", {"source_id": "final", "temperature": 0.5}),
                             ("text", {"text": "x", "hue": 10}),
                             ("video", {"source_id": "final", "text_animation": "pop"}),
                             ("adjustment", {"text_template": "news"})):
            with self.subTest(kind=kind), self.assertRaises(ValidationError):
                Track.model_validate({"id": "t", "type": kind, "clips": [{"id": "c", "duration": 1, **fields}]})

    def test_new_color_controls_and_curve_coordinates_are_finite_and_bounded(self):
        bounds = (("temperature", -1, 1), ("hue", -180, 180), ("shadows", -1, 1),
                  ("highlights", -1, 1), ("fade_amount", 0, 1))
        for name, low, high in bounds:
            for value in (low, high):
                with self.subTest(field=name, endpoint=value):
                    clip = Clip.model_validate({"id": "c", "duration": 1, name: value})
                    self.assertEqual(getattr(clip, name), value)
            for value in (low - 0.001, high + 0.001, float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=name, invalid=value), self.assertRaises(ValidationError):
                    Clip.model_validate({"id": "c", "duration": 1, name: value})
        for channel in ("red", "green", "blue"):
            for axis in ("x", "y"):
                for value in (-0.001, 1.001, float("nan"), float("inf"), float("-inf")):
                    curve = [{"x": 0, "y": 0}, {"x": 0.5, "y": 0.5, axis: value}, {"x": 1, "y": 1}]
                    with self.subTest(channel=channel, axis=axis, invalid=value), self.assertRaises(ValidationError):
                        Clip.model_validate({"id": "c", "duration": 1, "rgb_curves": {channel: curve}})

    def test_exact_action_shapes_and_unchanged_inventory(self):
        cases = [("ripple_delete", {}), ("ripple_trim", {"trim": 0.1, "duration": 1}),
                 ("slip", {"trim": 0.1}), ("roll", {"at": 1}), ("slide", {"at": 1})]
        for op, fields in cases:
            body = {"expected_revision": 0, "op": op, "clip_id": "c", **fields}
            Action.model_validate(body)
            for key in ("clip_id", *fields):
                with self.subTest(op=op, missing=key), self.assertRaises(ValidationError):
                    Action.model_validate({k: v for k, v in body.items() if k != key})
            for extra in ({"track_id": "other"}, {"new_id": "new"}, {"source_marks": []}):
                with self.subTest(op=op, extra=extra), self.assertRaises(ValidationError):
                    Action.model_validate({**body, **extra})
        caps = capabilities()
        self.assertEqual(caps["tool_count"], 113)
        tools = {t["id"]: t for t in caps["tools"]}
        self.assertEqual(len(tools), 113)
        self.assertFalse(tools["expHdr"]["available"])
        self.assertFalse(tools["aiColor"]["available"])
        self.assertTrue(tools["lut"]["available"])
        self.assertEqual(tools["lut"]["classification"], "partial")
        self.assertIn("LUT_3D_SIZE 2–33", tools["lut"]["reason"])
        self.assertEqual(tools["snap"]["classification"], "metadata")
        self.assertEqual(tools["inout"]["classification"], "metadata")
        self.assertIn("client", tools["snap"]["reason"])
        self.assertEqual(caps["limits"]["history_mutations"], 100)
        self.assertEqual(caps["limits"]["subtitle_bytes"], MAX_SUBTITLE_BYTES)


class StudioTimelineTests(unittest.TestCase):
    def reject_unchanged(self, project: Project, op: str, clip_id: str = "b", status: int = 422, **fields: Any) -> None:
        before = project.model_dump()
        with self.assertRaises(HTTPException) as caught:
            edit(project, op, clip_id, **fields)
        self.assertEqual(caught.exception.status_code, status)
        self.assertEqual(project.model_dump(), before)

    def test_ripple_delete_one_closed_interval_same_track_only(self):
        project = sequence(ripple_enabled=False)
        project.tracks.append(Track(id="sound", type="audio", locked=True, clips=[
            Clip(id="sound_clip", source_id="final", start=2.123, duration=2),
        ]))
        before = project.model_dump()
        actual = edit(project, "ripple_delete")
        self.assertEqual([(c.id, c.start, c.duration, c.trim) for c in actual.tracks[0].clips],
                         [("a", 0, 2, 2), ("c", 2, 2, 8)])
        self.assertEqual(actual.tracks[1], project.tracks[1])
        self.assertEqual(actual.markers, project.markers)
        self.assertEqual(actual.workspace, project.workspace)
        self.assertEqual(project.model_dump(), before)
        # The persisted preference does not turn ordinary delete into ripple.
        project.workspace.ripple_enabled = True
        ordinary = edit(project, "delete")
        self.assertEqual(ordinary.tracks[0].clips[-1].start, 4)

    def test_ripple_trim_quantizes_only_requested_duration_and_preserves_gaps(self):
        project = sequence(timeline_fps=25)
        project.tracks[0].clips[-1].start = 4.123456789
        actual = edit(project, "ripple_trim", trim=0.123456789, duration=1.019)
        middle, right = actual.tracks[0].clips[1:]
        self.assertEqual((middle.start, middle.duration, middle.trim), (2, 1, 0.123456789))
        self.assertAlmostEqual(right.start, 3.123456789, places=12)
        grown = edit(actual, "ripple_trim", trim=0.123456789, duration=2.08)
        self.assertAlmostEqual(grown.tracks[0].clips[-1].start, 4.203456789, places=12)
        removed = edit(project, "ripple_delete")
        self.assertAlmostEqual(removed.tracks[0].clips[-1].start, 2.123456789, places=12)

    def test_slip_changes_only_source_trim_even_on_reversed_or_frozen_clips(self):
        for fields in ({}, {"speed": 2, "reverse": True}, {"freeze": True, "mute": True}):
            project = sequence()
            data = project.model_dump()
            data["tracks"][0]["clips"][1].update(fields)
            data["tracks"][0]["clips"][1]["keyframes"] = {"x": [{"time": 0, "value": 0}, {"time": 2, "value": 0.5}]}
            project = Project.model_validate(data)
            actual = edit(project, "slip", trim=0.123456789)
            expected = project.model_dump()
            expected["tracks"][0]["clips"][1]["trim"] = 0.123456789
            self.assertEqual(actual.model_dump(), expected)
        self.reject_unchanged(caption_project(), "slip", "caption", trim=0)

    def test_roll_keeps_exterior_endpoints_speed_and_reverse_source_handles(self):
        for reverse in (False, True):
            project = sequence(timeline_fps=25)
            for clip in project.tracks[0].clips[:2]:
                clip.speed = 2
                clip.reverse = reverse
            actual = edit(project, "roll", "a", at=2.44)
            left, right, untouched = actual.tracks[0].clips
            self.assertEqual((left.start, left.duration, right.start, right.duration), (0, 2.44, 2.44, 1.56))
            self.assertAlmostEqual(left.trim, 1.12 if reverse else 2)
            self.assertAlmostEqual(right.trim, 5 if reverse else 5.88)
            self.assertEqual(untouched, project.tracks[0].clips[2])
            self.assertAlmostEqual(right.start + right.duration, 4)
        # Neighbour order is temporal, not JSON list order.
        shuffled = sequence()
        shuffled.tracks[0].clips.reverse()
        actual = edit(shuffled, "roll", "a", at=2.5)
        self.assertEqual(next(c for c in actual.tracks[0].clips if c.id == "b").start, 2.5)

    def test_slide_preserves_selected_source_window_and_duration(self):
        for reverse in (False, True):
            project = sequence(timeline_fps=25)
            for neighbour in (project.tracks[0].clips[0], project.tracks[0].clips[2]):
                neighbour.speed = 2
                neighbour.reverse = reverse
            actual = edit(project, "slide", at=2.4)
            left, selected, right = actual.tracks[0].clips
            expected_selected = project.tracks[0].clips[1].model_dump() | {"start": 2.4}
            self.assertEqual(selected.model_dump(), expected_selected)
            self.assertEqual((left.start, left.duration, right.start, right.duration), (0, 2.4, 4.4, 1.6))
            self.assertAlmostEqual(left.trim, 1.2 if reverse else 2)
            self.assertAlmostEqual(right.trim, 8 if reverse else 8.8)
            self.assertEqual(actual.duration, 6)

    def test_overlap_missing_neighbour_gap_and_subframe_edits_fail_closed(self):
        overlap = sequence()
        overlap.tracks[0].clips[0].duration = 2.1
        for op, fields in (("ripple_delete", {}), ("ripple_trim", {"trim": 1, "duration": 1}),
                           ("slip", {"trim": 1}), ("roll", {"at": 3}), ("slide", {"at": 2.4})):
            self.reject_unchanged(overlap, op, **fields)
        downstream = sequence()
        downstream.tracks[0].clips.append(Clip(id="overlap", source_id="final", start=5, duration=1))
        self.reject_unchanged(downstream, "ripple_delete")
        self.reject_unchanged(sequence(), "roll", "c", at=5)
        self.reject_unchanged(sequence(), "slide", "a", at=1)
        gap = sequence()
        gap.tracks[0].clips[-1].start = 4.00000001
        self.reject_unchanged(gap, "roll", at=3.5)
        self.reject_unchanged(gap, "slide", at=2.5)
        self.reject_unchanged(sequence(), "roll", "a", at=0)
        self.reject_unchanged(sequence(), "roll", "a", at=4)
        self.reject_unchanged(sequence(), "ripple_trim", trim=0, duration=0.001)
        self.reject_unchanged(sequence(), "slide", at=4)
        self.reject_unchanged(sequence(), "slip", trim=3599)

    def test_full_project_constraints_and_locks_are_not_bypassed(self):
        project = sequence()
        project.tracks[0].locked = True
        for op, fields in (("ripple_delete", {}), ("ripple_trim", {"trim": 1, "duration": 1}),
                           ("slip", {"trim": 1}), ("roll", {"at": 3}), ("slide", {"at": 2.4})):
            self.reject_unchanged(project, op, status=409, **fields)
        faded = sequence()
        faded.tracks[0].clips[1].fade_out = 1.5
        self.reject_unchanged(faded, "ripple_trim", trim=1, duration=1)
        data = sequence().model_dump()
        data["tracks"][0]["clips"][1]["keyframes"] = {"opacity": [{"time": 0, "value": 1}, {"time": 2, "value": 0}]}
        self.reject_unchanged(Project.model_validate(data), "roll", "a", at=2.5)
        handles = sequence()
        handles.tracks[0].clips[-1].trim = 0
        self.reject_unchanged(handles, "slide", at=1.5)
        end = sequence()
        end.tracks[0].clips[-1].start = 118
        self.reject_unchanged(end, "ripple_trim", trim=1, duration=3)
        text = caption_project(text_animation="typewriter")
        self.reject_unchanged(text, "split", "caption", at=0.5, new_id="right")

    def test_rational_frame_roundtrips_do_not_accumulate_or_resnap_legacy_content(self):
        for fps in (24, 25, 30, 60):
            with self.subTest(fps=fps):
                data = sequence(timeline_fps=fps, snap_enabled=False).model_dump()
                data["tracks"][0]["clips"][0].update(start=0.123, duration=1.877)
                data["tracks"][0]["clips"][1].update(duration=1.731)
                data["tracks"][0]["clips"][2].update(start=3.731, duration=1.269)
                original = Project.model_validate(data)
                project = original
                for _ in range(40):
                    project = edit(project, "roll", "a", at=2 + 1 / fps)
                    project = edit(project, "roll", "a", at=2)
                self.assertEqual(project.model_dump(), original.model_dump())
        project = sequence(timeline_fps=30)
        moved = edit(project, "move", at=1.05)
        self.assertEqual(next(c for c in moved.tracks[0].clips if c.id == "b").start, 32 / 30)
        marked = apply_action(project, Action(expected_revision=0, op="marker", at=1.05, new_id="exact"))
        self.assertEqual(marked.markers[-1].time, 1.05)
        trimmed = edit(project, "trim", trim=0.123456789, duration=1.0123456789)
        self.assertEqual(trimmed.tracks[0].clips[1].duration, 1.0123456789)

    def test_old_overlaps_outside_edit_region_and_resource_limits(self):
        project = sequence()
        # A ripple of the last clip need not rewrite earlier legacy composites.
        project.tracks[0].clips[0].duration = 2.2
        result = edit(project, "ripple_delete", "c")
        self.assertEqual(result.tracks[0].clips, project.tracks[0].clips[:2])
        full = Project(tracks=[Track(id="t", type="text", clips=[
            Clip(id=f"c{i}", start=i, duration=1, text="x") for i in range(64)
        ])])
        self.reject_unchanged(full, "duplicate", "c0", at=64, new_id="overflow")
        for data in ({"tracks": [{"id": f"t{i}", "type": "text"} for i in range(9)]},
                     {"tracks": [{"id": "t", "type": "text", "clips": [{"id": "c", "start": 119, "duration": 2, "text": "x"}]}]}):
            with self.assertRaises(ValidationError):
                Project.model_validate(data)


class StudioSubtitleContractTests(unittest.TestCase):
    def test_typewriter_is_bounded_ordered_plain_prefixes_not_word_timestamps(self):
        text = "A{\\p1}B\n世界"
        project = caption_project(text=text, text_animation="typewriter", fade_in=0.2, fade_out=0.2)
        before = project.model_dump()
        ass = subtitle_document(project, ExportOptions(format="ass"), disclosure=True)
        rows = dialogue_rows(ass)
        self.assertGreater(len(rows), 1)
        self.assertLessEqual(len(rows), 64)
        for row, following in zip(rows, rows[1:]):
            self.assertEqual(centiseconds(row[2]), centiseconds(following[1]))
        self.assertTrue(all(20 <= centiseconds(row[1]) < centiseconds(row[2]) <= 120 for row in rows))
        self.assertTrue(rows[-1][9].endswith(plain_ass(text)))
        self.assertEqual(centiseconds(rows[-1][1]), 80)  # Full reveal at 60% local effect time.
        self.assertNotIn(r"{\p1}", ass)
        self.assertNotIn(r"\fad(", "\n".join(row[9] for row in rows))  # Fades do not restart per stage.
        self.assertIn(r"\t(", ass)
        disclosure = dialogue_rows(ass, 100)
        self.assertEqual(len(disclosure), 1)
        self.assertIn("AI生成示意画面", disclosure[0][9])
        self.assertNotIn(r"\t(", disclosure[0][9])
        self.assertEqual((centiseconds(disclosure[0][1]), centiseconds(disclosure[0][2])), (0, 120))
        self.assertEqual(project.model_dump(), before)
        srt = subtitle_document(project, ExportOptions(format="srt"))
        self.assertEqual(srt.count(" --> "), 1)
        self.assertIn(text, srt)

    def test_templates_honor_render_style_without_overwriting_custom_fields(self):
        for name, color, style, bold, outline, shadow in (
            ("custom", "00FF00", "Studio", 0, 2.0, 0.0), ("news", "FFFFFF", "Studio", 1, 3, 1),
            ("outline", "FFFFFF", "Studio", 0, 5, 0), ("gold", "66D1FF", "Studio", 1, 2, 2),
            ("note", "BEF1FF", "Box", 0, 4, 0),
        ):
            project = caption_project(color="00FF00", font_size=90, x=0.2, opacity=0.5, text_template=name)
            before = project.model_dump()
            options = ExportOptions(format="ass")
            document = subtitle_document(project, options)
            self.assertEqual(subtitle_document(Project.model_validate(before), options), document)
            row = dialogue_rows(document)[0]
            self.assertEqual(row[3], style)
            self.assertIn(f"\\c&H{color}&", row[9])
            self.assertIn(f"\\b{bold}\\bord{outline}\\shad{shadow}", row[9])
            self.assertIn(r"\fs90.00", row[9])
            self.assertIn(r"\alpha&H80&", row[9])
            self.assertIn(r"\pos(", row[9])
            self.assertEqual(project.model_dump(), before)
        fade = subtitle_document(caption_project(text_animation="fade"), ExportOptions(format="ass"))
        self.assertIn(r"\fad(200,200)", fade)
        explicit = subtitle_document(caption_project(text_animation="fade", fade_in=0.1), ExportOptions(format="ass"))
        self.assertIn(r"\fad(100,0)", explicit)
        pop = subtitle_document(caption_project(text_animation="pop"), ExportOptions(format="ass"))
        self.assertIn(r"\fscx70\fscy70\t(0,200,\fscx100\fscy100)", pop)

    def test_subtitle_size_and_centisecond_limits_fail_closed(self):
        one = caption_project(text="字" * 500, text_animation="typewriter")
        options = ExportOptions(format="ass")
        document = subtitle_document(one, options)
        self.assertLessEqual(len(dialogue_rows(document)), 64)
        self.assertLessEqual(len(document.encode("utf-8")), MAX_SUBTITLE_BYTES)
        exact = len(document.encode("utf-8"))
        with patch("backend.studio_render.MAX_SUBTITLE_BYTES", exact):
            self.assertEqual(subtitle_document(one, options), document)
        with patch("backend.studio_render.MAX_SUBTITLE_BYTES", exact - 1), self.assertRaisesRegex(RenderError, "256 KiB"):
            subtitle_document(one, options)
        many = Project(tracks=[Track(id="t", type="text", clips=[
            Clip(id=f"c{i}", start=i, duration=1, text="字" * 500, text_animation="typewriter") for i in range(64)
        ])])
        with self.assertRaisesRegex(RenderError, "256 KiB"):
            subtitle_document(many, options)
        with self.assertRaises(ValidationError):
            Clip(id="c", duration=1, text="x" * 501)
        for fields in ({"start": 0, "duration": 0.001}, {"start": 0, "duration": 0.01, "text_animation": "typewriter"}):
            with self.assertRaises(RenderError):
                subtitle_document(caption_project(**fields), options)
        with self.assertRaisesRegex(RenderError, "disclosure duration"):
            subtitle_document(existing.base_project(duration=0.001), options, disclosure=True)

    def test_cubic_easing_and_fixed_filter_allowlists(self):
        points = [KeyPoint(time=0, value=0, easing="ease_in_out"), KeyPoint(time=1, value=1)]
        expression = keyframe_expression(points, 0, "t")
        self.assertIn("3-2*", expression)
        self.assertIn("pow(", expression)
        with self.assertRaises(RenderError):
            keyframe_expression(points, 0, "movie=/secret")
        neutral = Clip(id="c", duration=1)
        self.assertEqual(color_filters(neutral), [
            f"eq=brightness={neutral.brightness}:contrast={neutral.contrast}:saturation={neutral.saturation}",
        ])
        filters = color_filters(Clip(id="c", duration=1, color_preset="cinema", temperature=0.5,
                                    hue=30, shadows=0.2, highlights=-0.2, fade_amount=0.5,
                                    rgb_curves={"red": [{"x": 0, "y": 0}, {"x": 1, "y": 0.8}]}))
        self.assertTrue(all(f.split("=", 1)[0] in {"eq", "colorbalance", "hue", "curves"} for f in filters))
        self.assertTrue(filters[-1].startswith("curves=red="))


class StudioSubtitleWriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_ass_and_srt_write_canonical_utf8_at_exact_byte_limit(self):
        # Subtitle-only exports need no codec binaries. Compare raw bytes because
        # read_text() would conceal Windows CRLF expansion through universal newlines.
        with tempfile.TemporaryDirectory() as directory:
            for template in ("custom", "gold"):
                project = caption_project(text="第一行\n{\\p1}literal 字幕", text_animation="typewriter", text_template=template)
                for fmt in ("ass", "srt"):
                    with self.subTest(template=template, format=fmt):
                        options = ExportOptions(format=fmt)
                        expected = subtitle_document(project, options).encode("utf-8")
                        work = Path(directory) / template / fmt
                        with patch("backend.studio_render.MAX_SUBTITLE_BYTES", len(expected)):
                            result = await render_project(project, options, {}, work)
                        data = (work / f"output.{fmt}").read_bytes()
                        self.assertEqual(data, expected)
                        self.assertEqual(result["bytes"], len(data))
                        self.assertNotIn(b"\r\n", data)
                        rejected = work / "over_limit"
                        with patch("backend.studio_render.MAX_SUBTITLE_BYTES", len(expected) - 1), self.assertRaisesRegex(RenderError, "256 KiB"):
                            await render_project(project, options, {}, rejected)
                        self.assertFalse((rejected / f"output.{fmt}").exists())


@unittest.skipUnless(existing.TOOLS, "FFmpeg/ffprobe must be on PATH")
class StudioPrototypeRouteTests(unittest.IsolatedAsyncioTestCase):
    # Reuse real fixtures and the existing save shape, not main/startup/Settings.
    save = existing.StudioAPITests.save

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / "tasks"
        self.root = self.data / "task"
        self.root.mkdir(parents=True)
        existing.make_fixture(self.root / "final.mp4")
        self.source_hash = hashlib.sha256((self.root / "final.mp4").read_bytes()).digest()
        self.record = SimpleNamespace(task_id="task", task_dir=self.root, uploads=[], status="done", revision=0)
        self.publication_originals = seed_synthetic_publication(self, self.record)
        self.settings = SimpleNamespace(data_dir=self.data, media_command_timeout_seconds=60, minimum_free_disk_bytes=0)
        self.manager = SimpleNamespace(_draining=False)

        async def authorize(request: Any, task_id: str, write: bool = False):
            if task_id != "task" or request.headers.get("X-Token") != "test-only":
                raise HTTPException(403, "denied")
            if write and request.headers.get("X-CSRF") != "test-only":
                raise HTTPException(403, "CSRF")
            return self.record

        self.authorize = authorize
        self.app = FastAPI()
        self.app.include_router(create_studio_router(self.settings, self.manager, authorize))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
                                        base_url="http://testserver", headers={"X-Token": "test-only", "X-CSRF": "test-only"})
        self.prefix = "/api/tasks/task/studio"

    async def asyncTearDown(self):
        owned = [task for key, task in _RUNNING.items() if Path(key).is_relative_to(self.root)]
        for task in owned:
            task.cancel()
        await asyncio.gather(*owned, return_exceptions=True)
        await self.client.aclose()
        self.assertNotIn(str(self.root.resolve()), _BUSY)
        self.assertEqual(hashlib.sha256((self.root / "final.mp4").read_bytes()).digest(), self.source_hash)
        for name, content in self.publication_originals.items():
            self.assertEqual((self.root / name).read_bytes(), content)
        self.temp.cleanup()

    async def action(self, revision: int, op: str, **fields: Any) -> httpx.Response:
        return await self.client.post(self.prefix + "/actions", json={"expected_revision": revision, "op": op, **fields})

    def state_bytes(self) -> bytes:
        return (self.root / "studio/state.json").read_bytes()

    async def test_save_import_restart_undo_redo_preserve_every_new_field(self):
        project = existing.base_project(duration=1, temperature=0.4, hue=20, shadows=0.3, highlights=-0.2,
                                        fade_amount=0.25, color_preset="cinema",
                                        rgb_curves={"red": [{"x": 0, "y": 0}, {"x": 1, "y": 0.7}]})
        project.workspace = WorkspaceMetadata.model_validate({"timeline_fps": 25, "time_display": "frames",
            "snap_enabled": False, "ripple_enabled": True,
            "source_marks": [{"source_id": "final", "in_point": 0.123, "out_point": 1.567}]})
        project.tracks.append(Track(id="text", type="text", clips=[
            Clip(id="title", duration=1, text="Local", text_animation="typewriter", text_template="gold"),
        ]))
        saved = await self.save(project)
        self.assertEqual(saved.status_code, 200, saved.text)
        expected = project.model_dump()
        data = project.model_dump()
        data["workspace"]["source_marks"][0]["in_point"] = 0.234
        data["tracks"][0]["clips"][0]["temperature"] = -0.4
        imported = await self.client.post(self.prefix + "/project/import", json={"expected_revision": 1, "project": data})
        self.assertEqual(imported.status_code, 200, imported.text)
        undo = await self.action(2, "undo")
        self.assertEqual(undo.json()["project"], expected)
        redo = await self.action(3, "redo")
        self.assertEqual(redo.json()["project"], data)
        app = FastAPI()
        app.include_router(create_studio_router(self.settings, self.manager, self.authorize))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                                     headers={"X-Token": "test-only"}) as client:
            loaded = await client.get(self.prefix + "/project/export")
        self.assertEqual(loaded.json()["project"], data)
        self.assertEqual(loaded.json()["revision"], 4)

    async def test_marks_validate_real_catalog_ids_and_rejected_import_is_byte_atomic(self):
        (self.root / "norm").mkdir()
        (self.root / "norm/norm_0.mp4").write_bytes((self.root / "final.mp4").read_bytes())
        sources = catalog(self.record)
        norm_id = next(key for key in sources if key.startswith("norm_"))
        project = existing.base_project()
        project.workspace.source_marks = [SourceMark(source_id=norm_id, in_point=0.1, out_point=1.9)]
        response = await self.save(project)
        self.assertEqual(response.status_code, 200, response.text)
        before = self.state_bytes()
        for source_id in ("missing", "http://localhost", "../final.mp4"):
            data = project.model_dump()
            data["workspace"]["source_marks"][0]["source_id"] = source_id
            response = await self.client.post(self.prefix + "/project/import", json={"expected_revision": 1, "project": data})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(self.state_bytes(), before)
        # References are rechecked during history restoration, not just imports.
        self.assertEqual((await self.action(1, "undo")).status_code, 200)
        (self.root / "norm/norm_0.mp4").unlink()
        before = self.state_bytes()
        self.assertEqual((await self.action(2, "redo")).status_code, 422)
        self.assertEqual(self.state_bytes(), before)

    async def test_new_actions_locked_conflict_validation_and_write_fault_rollback(self):
        self.assertEqual((await self.save(sequence())).status_code, 200)
        self.assertEqual((await self.action(1, "track", track_id="v", locked=True)).status_code, 200)
        locked = self.state_bytes()
        cases = [("ripple_delete", {}), ("ripple_trim", {"trim": 1, "duration": 1}),
                 ("slip", {"trim": 1}), ("roll", {"at": 3}), ("slide", {"at": 2.5})]
        for op, fields in cases:
            response = await self.action(2, op, clip_id="b", **fields)
            self.assertEqual(response.status_code, 409, response.text)
            self.assertEqual(self.state_bytes(), locked)
        self.assertEqual((await self.save(sequence(), revision=2)).status_code, 409)
        self.assertEqual(self.state_bytes(), locked)
        self.assertEqual((await self.action(2, "track", track_id="v", locked=False)).status_code, 200)
        before = self.state_bytes()
        for revision, fields, code in ((2, {"op": "ripple_delete"}, 409),
                                       (3, {"op": "slide", "at": 6}, 422),
                                       (3, {"op": "slip", "trim": 3599}, 422)):
            response = await self.action(revision, clip_id="b", **fields)
            self.assertEqual(response.status_code, code, response.text)
            self.assertEqual(self.state_bytes(), before)
        with patch("backend.studio.write_json_atomic", side_effect=OSError("synthetic write failure")):
            failed = await self.action(3, "ripple_delete", clip_id="b")
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(self.state_bytes(), before)
        valid = await self.action(3, "ripple_delete", clip_id="b")
        self.assertEqual(valid.status_code, 200, valid.text)
        undo = await self.action(4, "undo")
        self.assertEqual(undo.json()["project"], sequence().model_dump())

    async def test_overlap_or_envelope_rejection_does_not_consume_history(self):
        project = sequence()
        project.tracks[0].clips[0].duration = 2.1
        self.assertEqual((await self.save(project)).status_code, 200)
        before = self.state_bytes()
        rejected = await self.action(1, "ripple_delete", clip_id="b")
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(self.state_bytes(), before)
        project = sequence()
        project.tracks[0].clips[1].fade_out = 1.5
        self.assertEqual((await self.save(project, revision=1)).status_code, 200)
        before = self.state_bytes()
        rejected = await self.action(2, "ripple_trim", clip_id="b", trim=0, duration=1)
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(self.state_bytes(), before)

    async def test_concurrent_revision_and_legacy_busy_after_body_await(self):
        self.assertEqual((await self.save(sequence())).status_code, 200)
        responses = await asyncio.gather(self.action(1, "slip", clip_id="b", trim=1),
                                         self.action(1, "ripple_delete", clip_id="b"))
        self.assertEqual(sorted(r.status_code for r in responses), [200, 409])
        self.assertEqual(read_state(self.root)["revision"], 2)
        before = self.state_bytes()
        with patch("backend.studio.legacy_task_busy", return_value=False) as busy:
            async def body():
                busy.return_value = True
                yield json.dumps({"expected_revision": 2, "op": "ripple_delete", "clip_id": "a"}).encode()
            response = await self.client.post(self.prefix + "/actions", content=body())
        self.assertEqual(response.status_code, 409, response.text)
        self.assertGreaterEqual(busy.call_count, 2)
        self.assertEqual(self.state_bytes(), before)
        self.manager._draining = True
        self.assertEqual((await self.action(2, "ripple_delete", clip_id="a")).status_code, 503)
        self.assertEqual(self.state_bytes(), before)

    async def test_pipeline_busy_rechecked_after_streaming_body(self):
        self.assertEqual((await self.save(sequence())).status_code, 200)
        before = self.state_bytes()

        async def body():
            self.record.status = "running"
            yield json.dumps({"expected_revision": 1, "op": "slip", "clip_id": "b", "trim": 1}).encode()

        response = await self.client.post(self.prefix + "/actions", content=body())
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.state_bytes(), before)

    async def test_history_size_guard_counts_actual_utf8_file_and_rolls_back(self):
        self.assertEqual((await self.save(sequence())).status_code, 200)
        before = self.state_bytes()
        compact = len(json.dumps(read_state(self.root), ensure_ascii=False).encode("utf-8"))
        self.assertGreater(len(before), compact)
        snapshot = json.loads(before)
        with patch("backend.studio.MAX_STATE", compact):
            with self.assertRaises(HTTPException) as caught:
                write_state(self.root, snapshot)
            self.assertEqual(caught.exception.status_code, 413)
        self.assertEqual(snapshot["revision"], 1)
        self.assertEqual(self.state_bytes(), before)

    async def test_hundred_mutation_cap_and_additive_legacy_disk_read(self):
        legacy = state_default()
        legacy["project"] = {"schema_version": 1, "tracks": []}
        write_state(self.root, legacy)
        response = await self.client.get(self.prefix + "/project")
        self.assertEqual(response.json()["project"]["workspace"]["timeline_fps"], 30)
        project = existing.base_project(duration=1)
        self.assertEqual((await self.save(project)).status_code, 200)
        for revision in range(1, 100):
            response = await self.action(revision, "slip", clip_id="c", trim=0.1 if revision % 2 else 0)
            self.assertEqual(response.status_code, 200, response.text)
        state = read_state(self.root)
        self.assertEqual((state["revision"], len(state["past"]), len(state["audit"])), (100, 100, 100))
        before = self.state_bytes()
        for op, fields in (("slip", {"clip_id": "c", "trim": 0}), ("undo", {})):
            self.assertEqual((await self.action(100, op, **fields)).status_code, 409)
            self.assertEqual(self.state_bytes(), before)

    async def test_real_action_save_render_and_authenticated_download(self):
        project = Project.model_validate({"tracks": [{"id": "v", "type": "video", "clips": [
            {"id": "a", "source_id": "final", "duration": 0.4, "trim": 0},
            {"id": "b", "source_id": "final", "start": 0.4, "duration": 0.4, "trim": 0.5,
             "temperature": 0.6, "color_preset": "cinema", "rgb_curves": {"blue": [{"x": 0, "y": 0}, {"x": 1, "y": 0.8}]}},
            {"id": "c", "source_id": "final", "start": 0.8, "duration": 0.4, "trim": 1},
        ]}, {"id": "text", "type": "text", "clips": [{"id": "title", "duration": 1.2, "text": "LOCAL",
                  "text_animation": "typewriter", "text_template": "news", "font_size": 100}]}],
            "workspace": {"timeline_fps": 30, "source_marks": [{"source_id": "final", "in_point": 0.1, "out_point": 1.9}]}})
        self.assertEqual((await self.save(project)).status_code, 200)
        for revision, op, clip_id, fields in ((1, "slide", "b", {"at": 0.5}),
                                               (2, "roll", "a", {"at": 0.4}),
                                               (3, "slip", "b", {"trim": 0.4}),
                                               (4, "ripple_trim", "b", {"trim": 0.4, "duration": 0.4})):
            response = await self.action(revision, op, clip_id=clip_id, **fields)
            self.assertEqual(response.status_code, 200, response.text)
        response = await self.client.post(self.prefix + "/render", json={"expected_revision": 5,
            "options": {"format": "png", "resolution": 360, "frame_time": 0.65}})
        self.assertEqual(response.status_code, 202, response.text)
        job_id = response.json()["id"]
        worker = _RUNNING.get(str((self.root / "studio/jobs" / f"{job_id}.json").resolve()))
        if worker is not None:
            await worker
        job = (await self.client.get(self.prefix + "/jobs/" + job_id)).json()
        self.assertEqual(job["state"], "succeeded", job)
        output = await self.client.get(self.prefix + "/outputs/" + job_id)
        self.assertEqual(output.status_code, 200)
        self.assertTrue(output.content.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(len(output.content), job["result"]["bytes"])
        self.assertEqual((await self.client.get(self.prefix + "/outputs/" + job_id,
                                               headers={"X-Token": "denied"})).status_code, 403)
        work = self.root / "studio/outputs/r5" / job_id
        snapshot = json.loads((work / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["project"]["tracks"][0]["clips"][1]["temperature"], 0.6)
        self.assertIn("LOCAL", (work / "studio.ass").read_text(encoding="utf-8"))
        self.assertEqual((self.root / "report.json").read_bytes(), self.publication_originals["report.json"])


@unittest.skipUnless(existing.TOOLS, "FFmpeg/ffprobe must be on PATH")
class StudioPrototypePixelTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = existing.StudioRenderTests.asyncSetUp
    asyncTearDown = existing.StudioRenderTests.asyncTearDown

    async def image(self, project: Project, at: float, name: str, *, source: Path | None = None,
                    disclosure: bool = False) -> bytes:
        work = self.root / name
        result = await render_project(project, ExportOptions(format="png", resolution=360, frame_time=at),
                                      {"final": source or self.source}, work, disclosure=disclosure)
        pixels = existing.ffmpeg("-i", str(work / "output.png"), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
        self.assertEqual(len(pixels), 640 * 360 * 3)
        self.assertEqual((result["probe"]["streams"][0]["width"], result["probe"]["streams"][0]["height"]), (640, 360))
        return pixels

    def chart(self) -> Path:
        """Neutral shadows/midtones/highlights plus a muted color patch, not saturated primaries."""
        path = self.root / "chart.mp4"
        existing.ffmpeg("-y", "-f", "lavfi", "-i",
            "color=c=0x808080:s=160x90:r=30:d=1,"
            "drawbox=x=0:y=0:w=40:h=90:color=0x202020:t=fill,"
            "drawbox=x=120:y=0:w=40:h=90:color=0xE0E0E0:t=fill,"
            "drawbox=x=60:y=20:w=40:h=50:color=0xB05030:t=fill",
            "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", str(path))
        return path

    @staticmethod
    def pixel(frame: bytes, x: int, y: int) -> tuple[int, ...]:
        offset = (y * 640 + x) * 3
        return tuple(frame[offset:offset + 3])

    async def test_each_color_property_and_preset_changes_real_pixels(self):
        source = self.chart()
        original_hash = hashlib.sha256(source.read_bytes()).digest()
        base = await self.image(existing.base_project(duration=1), 0.4, "neutral", source=source)
        cases: list[tuple[str, dict[str, Any]]] = [
            ("warm_balance", {"temperature": 1}), ("cool_balance", {"temperature": -1}),
            ("hue", {"hue": 90}), ("shadows", {"shadows": 1}),
            ("highlights", {"highlights": -1}), ("fade", {"fade_amount": 1}),
            *((name, {"color_preset": name}) for name in ("warm", "cool", "cinema", "mono")),
        ]
        outputs: dict[str, bytes] = {}
        for name, fields in cases:
            frame = await self.image(existing.base_project(duration=1, **fields), 0.4, name, source=source)
            outputs[name] = frame
            with self.subTest(effect=name):
                # Synthetic effect visibility only; not a news-footage quality score.
                self.assertGreater(sum(abs(a - b) for a, b in zip(frame, base)) / len(base), 1, name)
        neutral = self.pixel(base, 200, 40)
        for name, warmer in (("warm", True), ("cool", False)):
            with self.subTest(preset=name, check="midtone_cast"):
                pixel = self.pixel(outputs[name], 200, 40)
                if warmer:
                    self.assertGreater(pixel[0] - pixel[2], neutral[0] - neutral[2] + 10)
                else:
                    self.assertGreater(pixel[2] - pixel[0], neutral[2] - neutral[0] + 10)
        warm, cool = self.pixel(outputs["warm_balance"], 200, 40), self.pixel(outputs["cool_balance"], 200, 40)
        self.assertGreater(warm[0], warm[2] + 30)
        self.assertGreater(cool[2], cool[0] + 30)
        self.assertGreater(self.pixel(outputs["shadows"], 40, 40)[0], self.pixel(base, 40, 40)[0] + 10)
        self.assertLess(self.pixel(outputs["highlights"], 600, 40)[0], self.pixel(base, 600, 40)[0] - 10)
        self.assertGreater(self.pixel(outputs["fade"], 40, 40)[0], self.pixel(base, 40, 40)[0] + 10)
        mono = self.pixel(outputs["mono"], 320, 180)
        self.assertLessEqual(max(mono) - min(mono), 4)
        self.assertGreater(max(self.pixel(base, 320, 180)) - min(self.pixel(base, 320, 180)), 50)
        self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), original_hash)

    async def test_new_source_window_edits_still_fail_on_real_source_eof(self):
        project = edit(existing.base_project(duration=1), "slip", "c", trim=1.5)
        work = self.root / "past_source_end"
        with self.assertRaisesRegex(RenderError, "source duration"):
            await render_project(project, ExportOptions(resolution=360), {"final": self.source}, work)
        self.assertFalse((work / "output.mp4").exists())

    async def test_each_rgb_curve_changes_target_channel_and_preserves_other_channels(self):
        source = self.chart()
        base = await self.image(existing.base_project(duration=1), 0.4, "curvebase", source=source)
        neutral = self.pixel(base, 200, 40)
        for index, channel in enumerate(("red", "green", "blue")):
            project = existing.base_project(duration=1, rgb_curves={channel: [{"x": 0, "y": 0}, {"x": 1, "y": 0.2}]})
            frame = await self.image(project, 0.4, channel, source=source)
            pixel = self.pixel(frame, 200, 40)
            self.assertLess(pixel[index], neutral[index] - 60)
            for other in set(range(3)) - {index}:
                self.assertLess(abs(pixel[other] - neutral[other]), 8)

    async def test_adjustment_color_effects_apply_only_inside_time_interval(self):
        source = self.chart()
        base = await self.image(existing.base_project(duration=1), 0.5, "adjbase", source=source)
        project = existing.base_project(duration=1)
        project.tracks.append(Track(id="adjustment", type="adjustment", clips=[
            Clip(id="look", start=0.3, duration=0.4, temperature=0.8, hue=30, shadows=0.3,
                 highlights=-0.2, fade_amount=0.3, color_preset="cinema",
                 rgb_curves={"red": [{"x": 0, "y": 0}, {"x": 1, "y": 0.8}]}),
        ]))
        for name, at, active in (("before", 0.1, False), ("during", 0.5, True), ("after", 0.85, False)):
            frame = await self.image(project, at, "adjustment_" + name, source=source)
            difference = sum(abs(a - b) for a, b in zip(frame, base)) / len(base)
            if active:
                self.assertGreater(difference, 8)
            else:
                self.assertLess(difference, 3)  # Allow neutral format conversion rounding.

    async def test_ripple_and_new_effects_preserve_real_video_frame_count_and_duration(self):
        source = self.chart()
        project = Project(tracks=[Track(id="v", type="video", clips=[
            Clip(id="a", source_id="final", duration=0.4),
            Clip(id="b", source_id="final", start=0.4, duration=0.4, trim=0.3),
            Clip(id="c", source_id="final", start=0.8, duration=0.4, trim=0.6,
                 temperature=0.7, hue=20, shadows=0.2, highlights=-0.2, fade_amount=0.3,
                 color_preset="cinema", rgb_curves={"blue": [{"x": 0, "y": 0}, {"x": 1, "y": 0.8}]}),
        ])])
        project = edit(project, "ripple_delete")
        self.assertEqual(project.duration, 0.8)
        project.tracks.append(Track(id="t", type="text", clips=[
            Clip(id="caption", duration=0.8, text="Local", text_animation="pop", text_template="gold"),
        ]))
        work = self.root / "edited_video"
        result = await render_project(project, ExportOptions(resolution=360, fps=25), {"final": source}, work)
        video = next(s for s in result["probe"]["streams"] if s["codec_type"] == "video")
        audio = next(s for s in result["probe"]["streams"] if s["codec_type"] == "audio")
        self.assertEqual(video["avg_frame_rate"], "25/1")
        self.assertEqual((video["width"], video["height"]), (640, 360))
        self.assertEqual((audio["sample_rate"], audio["channels"]), ("48000", 2))
        self.assertAlmostEqual(float(result["probe"]["format"]["duration"]), 0.8, delta=1 / 25)
        frames = existing.ffmpeg("-i", str(work / "output.mp4"), "-map", "0:v:0", "-vf", "scale=16:9",
                                 "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
        self.assertEqual(len(frames), 20 * 16 * 9 * 3, "0.8s at 25fps must decode to exactly 20 frames")

    async def test_ease_in_out_is_cubic_motion_not_only_an_accepted_enum(self):
        white = self.root / "white.mp4"
        existing.ffmpeg("-y", "-f", "lavfi", "-i", "color=white:s=160x90:r=30:d=1", "-c:v", "libx264", "-threads", "1", str(white))
        samples: dict[tuple[str, float], int] = {}
        for easing in ("linear", "ease_in_out"):
            project = existing.base_project(duration=1, keyframes={"opacity": [
                {"time": 0, "value": 0.2, "easing": easing}, {"time": 1, "value": 1},
            ]})
            for at in (0.25, 0.5, 0.75):
                frame = await self.image(project, at, f"ease_{easing}_{at}", source=white)
                samples[easing, at] = self.pixel(frame, 320, 180)[0]
        self.assertLess(samples["ease_in_out", 0.25], samples["linear", 0.25] - 8)
        self.assertGreater(samples["ease_in_out", 0.75], samples["linear", 0.75] + 8)
        self.assertLessEqual(abs(samples["ease_in_out", 0.5] - samples["linear", 0.5]), 3)

    async def test_real_text_animation_visible_progress_and_time_bounds(self):
        def project(animation: str) -> Project:
            result = caption_project(text="MMMMMMMM", subtitle=False, font_size=120, outline=0,
                                     text_animation=animation)
            result.tracks.append(Track(id="length", type="text", clips=[
                Clip(id="invisible", duration=1.5, text=".", opacity=0),
            ]))
            return result

        def lit(frame: bytes) -> int:
            return sum(max(frame[i:i + 3]) > 20 for i in range(0, len(frame), 3))

        def width(frame: bytes) -> int:
            xs = [i // 3 % 640 for i in range(0, len(frame), 3) if max(frame[i:i + 3]) > 20]
            return max(xs) - min(xs) + 1 if xs else 0

        full = await self.image(project("none"), 0.9, "textfull")
        self.assertGreater(lit(full), 100)
        for animation in ("typewriter", "fade", "pop"):
            current = project(animation)
            early = await self.image(current, 0.2334, animation + "early")
            middle = await self.image(current, 0.5, animation + "middle")
            late = await self.image(current, 0.9, animation + "late")
            self.assertNotEqual(early, full)
            self.assertEqual(late, full)
            before = await self.image(current, 0.1, animation + "before")
            after = await self.image(current, 1.3, animation + "after")
            self.assertEqual((lit(before), lit(after)), (0, 0))
            if animation == "typewriter":
                self.assertEqual(lit(early), 0)
                self.assertGreater(lit(middle), 50)
                self.assertLess(lit(middle), lit(full) * 0.8)
            elif animation == "fade":
                self.assertLess(sum(early), sum(late) * 0.5)
            else:
                self.assertLess(width(early), width(late) * 0.9)

    async def test_each_template_changes_pixels_and_does_not_rewrite_authored_style(self):
        baseline = await self.image(caption_project(text="Style 字幕", subtitle=False, font_size=120,
                                                   color="00FF00", outline=0), 0.5, "template_custom")
        for template in ("news", "outline", "gold", "note"):
            project = caption_project(text="Style 字幕", subtitle=False, font_size=120,
                                      color="00FF00", outline=0, text_template=template)
            original = project.model_dump()
            frame = await self.image(project, 0.5, "template_" + template)
            self.assertGreater(sum(a != b for a, b in zip(frame, baseline)), 100)
            self.assertEqual(project.model_dump(), original)

    async def test_disclosure_survives_animation_and_oversized_subtitle_creates_no_file(self):
        project = caption_project(text=r"{\p1} cannot hide disclosure", subtitle=False, opacity=0,
                                  text_animation="typewriter", text_template="note")
        plain = await self.image(project, 0.1, "nodisclosure")
        disclosed = await self.image(project, 0.1, "disclosure", disclosure=True)
        self.assertGreater(sum(a != b for a, b in zip(plain[:640 * 100 * 3], disclosed[:640 * 100 * 3])), 100)
        document = (self.root / "disclosure/studio.ass").read_bytes()
        self.assertLessEqual(len(document), MAX_SUBTITLE_BYTES)
        self.assertNotIn(b"\r\n", document)
        self.assertEqual(document, subtitle_document(
            project, ExportOptions(format="png", resolution=360, frame_time=0.1), disclosure=True,
        ).encode("utf-8"))
        many = Project(tracks=[Track(id="t", type="text", clips=[
            Clip(id=f"c{i}", duration=1, text="字" * 500, text_animation="typewriter") for i in range(64)
        ])])
        work = self.root / "too_large"
        with self.assertRaisesRegex(RenderError, "256 KiB"):
            await render_project(many, ExportOptions(format="ass"), {}, work)
        self.assertFalse((work / "output.ass").exists())
        escaped = caption_project(text=r"{\p1}literal", text_animation="typewriter")
        work = self.root / "bounded_ass"
        result = await render_project(escaped, ExportOptions(format="ass"), {}, work)
        data = (work / "output.ass").read_bytes()
        expected = subtitle_document(escaped, ExportOptions(format="ass")).encode("utf-8")
        self.assertEqual(data, expected)
        self.assertEqual(result["bytes"], len(expected))
        self.assertNotIn(b"{\\p1}", data)


if __name__ == "__main__":
    unittest.main()