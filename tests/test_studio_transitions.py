"""Bounded local transitions: contracts, transactions and REAL media regressions.

Authoring this module does not run tests or commands. The main runner executes
the codec tests, which skip ONLY when FFmpeg/ffprobe are missing. No main app,
Settings construction, dotenv, provider or external HTTP transport is used.
Router contracts use ASGITransport and owned TEMP; command captures intentionally
abort before execution and are NOT evidence of successful media rendering.

Absent/null transition_in preserves legacy rendering. Explicit transitions
require authored full-frame overlaps; no geometry/default is silently repaired.
Visual easing is quadratic/smoothstep; audio is independently LINEAR. Wipe names
describe the moving edge, so wipe_left reveals the right half first. Pixel and
PCM assertions below are deliberately independent of the filter-string checks.
"""
from __future__ import annotations

import array
import asyncio
import copy
import hashlib
import json
import math
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from backend.studio import (
    Action, _BUSY, _RUNNING, apply_action, capabilities, create_studio_router,
    enforce_locks, enforce_transition_endpoints, public_state, read_state,
    studio_task_busy, write_state,
)
from backend.studio_render import (
    MAX_BYTES, MAX_TRANSITION_DURATION, Clip, ExportOptions, Project, RenderError,
    Track, TransitionIn, render_project, transition_alpha_expression,
    transition_summary,
)
from tests import test_studio as existing
from tests.studio_publication_fixtures import (
    QC_BLOCKER, confirm_synthetic_publication, seed_synthetic_publication,
    synthetic_publication_files,
)


def pair_data(*, kind: str = "dissolve", easing: str = "linear", audio: bool = True,
              track_type: str = "video", duration: float = 0.8,
              clip_duration: float = 1.6, start: float = 0,
              left_source: str = "final", right_source: str = "final") -> dict[str, Any]:
    return {"tracks": [{"id": "v", "type": track_type, "clips": [
        {"id": "a", "source_id": left_source, "start": start, "duration": clip_duration, "fit": "cover"},
        {"id": "b", "source_id": right_source, "start": float(Fraction(str(start)) + Fraction(str(clip_duration)) - Fraction(str(duration))),
         "duration": clip_duration, "fit": "cover", "transition_in": {
             "left_clip_id": "a", "kind": kind, "duration": duration, "easing": easing, "audio": audio}},
    ]}]}


def pair(**fields: Any) -> Project:
    return Project.model_validate(pair_data(**fields))


def chain_data(**fields: Any) -> dict[str, Any]:
    data = pair_data(**fields)
    left, middle = data["tracks"][0]["clips"]
    third = copy.deepcopy(middle)
    # Author exact decimal fixture geometry; do not inject a 1.6000000000000003
    # boundary from accumulated float arithmetic (production does not snap it).
    third.update(id="c", start=float(Fraction(str(middle["start"])) + Fraction(str(middle["duration"]))
                                    - Fraction(str(middle["transition_in"]["duration"]))))
    third["transition_in"]["left_clip_id"] = middle["id"]
    data["tracks"][0]["clips"] = [third, left, middle]  # Deliberately not chronological.
    return data


def edit(project: Project, op: str, clip_id: str = "a", **fields: Any) -> Project:
    return apply_action(project, Action.model_validate({
        "expected_revision": 0, "op": op, "clip_id": clip_id, **fields,
    }))


def generated_manifest() -> dict[str, Any]:
    return {"schema_version": 1, "policy": "generated_visuals_are_disclosed_and_not_evidence", "items": [
        {"sentence_id": 0, "beat_id": 0, "shot_id": 0, "mode": "video",
         "prompt_sha256": "a" * 64, "disclosure_text": "AI生成示意画面"},
    ]}


class StudioTransitionModelTests(unittest.TestCase):
    def test_defaults_null_and_legacy_normalization_do_not_mutate_input(self):
        legacy = {"tracks": [{"id": "v", "type": "video", "clips": [
            {"id": "a", "source_id": "final", "duration": 1},
        ]}]}
        state = {"revision": 4, "project": legacy, "past": [], "future": []}
        before = copy.deepcopy(state)
        normalized = public_state(state)["project"]
        self.assertIsNone(normalized["tracks"][0]["clips"][0]["transition_in"])
        self.assertEqual(normalized["tracks"][0]["clips"][0]["fit"], "contain")
        self.assertEqual(state, before)
        data = pair_data()
        transition = data["tracks"][0]["clips"][1]["transition_in"]
        del transition["easing"], transition["audio"]
        project = Project.model_validate(data)
        self.assertEqual(project.tracks[0].clips[1].transition_in.model_dump(), {
            "left_clip_id": "a", "kind": "dissolve", "duration": 0.8, "easing": "linear", "audio": True,
        })
        self.assertNotIn("easing", transition)
        self.assertEqual(Project.model_validate_json(project.model_dump_json()), project)

    def test_required_fields_bounds_strict_numbers_booleans_and_expression_rejection(self):
        valid = {"left_clip_id": "a", "kind": "dissolve", "duration": 0.8}
        for missing in valid:
            with self.subTest(missing=missing), self.assertRaises(ValidationError):
                TransitionIn.model_validate({k: v for k, v in valid.items() if k != missing})
        cases = [{"duration": n} for n in (0, -0.1, 1.200001, float("nan"), float("inf"), True, "0.8", None)]
        cases += [{"audio": value} for value in (0, 1, "true", "false", None)]
        cases += [{"left_clip_id": "a;movie=/private"}, {"left_clip_id": "../a"}, {"left_clip_id": ""},
                  {"kind": "crossfade"}, {"kind": "AI"}, {"easing": "sin(T)"}, {"easing": None},
                  {"filter": "movie=/private"}, {"curve": "custom"}]
        for fields in cases:
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                TransitionIn.model_validate(valid | fields)
        self.assertEqual(TransitionIn.model_validate(valid | {"duration": 1}).duration, 1)
        self.assertEqual(TransitionIn.model_validate(valid | {"duration": 1.2}).duration, MAX_TRANSITION_DURATION)

    def test_exact_overlap_one_workspace_frame_and_half_of_each_clip(self):
        for fps in (24, 25, 30, 60):
            data = pair_data(duration=1 / fps)
            data["workspace"] = {"timeline_fps": fps}
            project = Project.model_validate(data)
            self.assertEqual(project.tracks[0].clips[1].transition_in.duration, 1 / fps)
            below = pair_data(duration=1 / fps - 1e-8)
            below["workspace"] = {"timeline_fps": fps}
            with self.subTest(fps=fps), self.assertRaisesRegex(ValidationError, "workspace frame"):
                Project.model_validate(below)
        Project.model_validate(pair_data(duration=1.2, clip_duration=2.4))
        for delta in (-1e-8, 1e-8, -0.01, 0.01):
            data = pair_data()
            data["tracks"][0]["clips"][1]["start"] += delta
            with self.subTest(overlap_delta=delta), self.assertRaisesRegex(ValidationError, "equal duration exactly"):
                Project.model_validate(data)
        for endpoint in (0, 1):
            data = pair_data()
            clips = data["tracks"][0]["clips"]
            clips[endpoint]["duration"] = 1.5
            if endpoint == 0:
                clips[1]["start"] = 0.7  # Keep overlap exact, violate only left half bound.
            with self.subTest(endpoint=endpoint), self.assertRaisesRegex(ValidationError, "half"):
                Project.model_validate(data)

    def test_unknown_self_cross_track_and_cross_type_references_are_rejected(self):
        for ref in ("missing", "b", "v"):
            data = pair_data()
            data["tracks"][0]["clips"][1]["transition_in"]["left_clip_id"] = ref
            with self.subTest(ref=ref), self.assertRaises(ValidationError):
                Project.model_validate(data)
        for kind in ("video", "overlay", "audio"):
            data = pair_data()
            left = data["tracks"][0]["clips"].pop(0)
            if kind == "audio":
                left.pop("fit")
            data["tracks"].append({"id": "other", "type": kind, "clips": [left]})
            with self.subTest(foreign_track_type=kind), self.assertRaisesRegex(ValidationError, "SAME track"):
                Project.model_validate(data)

    def test_track_applicability_and_both_endpoint_orderings(self):
        for kind in ("audio", "text", "adjustment"):
            fields = {"source_id": "final"} if kind == "audio" else {"text": "caption"} if kind == "text" else {}
            clip = Clip.model_validate({"id": "c", "duration": 1, **fields, "transition_in": {
                "left_clip_id": "left", "kind": "dissolve", "duration": 0.2,
            }})
            with self.subTest(kind=kind), self.assertRaisesRegex(ValidationError, "transition_in is not applied"):
                Track.model_validate({"id": "t", "type": kind, "clips": [clip.model_dump()]})
        for start, duration in ((0, 2), (-0.1, 2), (0.8, 0.8), (0.8, 0.7)):
            data = pair_data()
            data["tracks"][0]["clips"][1].update(start=start, duration=duration)
            with self.subTest(start=start, duration=duration), self.assertRaises(ValidationError):
                Project.model_validate(data)
        pair(track_type="overlay")

    def test_every_full_frame_constraint_applies_to_both_participants(self):
        cases = [{"fit": "contain"}, {"scale": 0.99}, {"x": 0.01}, {"y": -0.01}, {"opacity": 0.99},
                 {"mask": {"type": "ellipse"}}, {"chroma_color": "00FF00"}, {"fade_in": 0.1},
                 {"fade_out": 0.1}, {"freeze": True, "mute": True}]
        cases += [{"keyframes": {name: [{"time": 0, "value": 1}, {"time": 1, "value": 1}]}}
                  for name in ("x", "y", "scale", "opacity")]
        for endpoint in (0, 1):
            for fields in cases:
                data = pair_data()
                data["tracks"][0]["clips"][endpoint].update(fields)
                before = copy.deepcopy(data)
                with self.subTest(endpoint=endpoint, fields=fields), self.assertRaisesRegex(ValidationError, "remove transition first"):
                    Project.model_validate(data)
                self.assertEqual(data, before)
        data = pair_data()
        del data["tracks"][0]["clips"][0]["fit"]
        with self.assertRaises(ValidationError):
            Project.model_validate(data)  # Never silently turn legacy contain into cover.

    def test_allowed_transforms_colors_and_audio_controls_survive_unchanged(self):
        data = pair_data()
        fields = {"trim": 0.2, "speed": 2, "reverse": True, "rotation": 90, "mirror": True,
                  "crop": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8},
                  "color_preset": "warm", "temperature": 0.3, "fade_amount": 0.3,
                  "rgb_curves": {"blue": [{"x": 0, "y": 0}, {"x": 1, "y": 0.8}]},
                  "volume": 0.5, "pan": 0.3, "audio_effect": "invert"}
        for clip in data["tracks"][0]["clips"]:
            clip.update(copy.deepcopy(fields))
        project = Project.model_validate(data)
        for clip in project.tracks[0].clips:
            self.assertEqual({key: clip.model_dump()[key] for key in fields}, fields)
        self.assertAlmostEqual(project.duration, 2.4)

    def test_third_clip_intersections_rejected_even_on_hidden_tracks(self):
        for start, duration in ((0, 2), (0.79, 0.02), (0.8, 0.1), (1.5, 0.2), (1.59999999, 0.1)):
            for hidden in (False, True):
                data = pair_data()
                data["tracks"][0]["hidden"] = hidden
                data["tracks"][0]["clips"].append({"id": "third", "source_id": "final", "start": start, "duration": duration})
                with self.subTest(start=start, hidden=hidden), self.assertRaisesRegex(ValidationError, "third same-track"):
                    Project.model_validate(data)
        data = pair_data()
        data["tracks"][0]["clips"].extend([
            {"id": "before", "source_id": "final", "duration": 0.8},
            {"id": "after", "source_id": "final", "start": 1.6, "duration": 0.8},
        ])
        Project.model_validate(data)  # Boundary touches, not intersections of the transition.
        data = pair_data()
        data["tracks"].append({"id": "above", "type": "overlay", "clips": [
            {"id": "pip", "source_id": "final", "start": 0.9, "duration": 0.3, "scale": 0.3},
        ]})
        Project.model_validate(data)  # Normal cross-track composition remains supported.

    def test_chain_half_bounds_keep_incoming_and_outgoing_envelopes_disjoint(self):
        data = chain_data()
        project = Project.model_validate(data)
        self.assertEqual([c.id for c in project.tracks[0].clips], ["c", "a", "b"])
        self.assertEqual([c.id for c in project.tracks[0].render_clips()], ["a", "b", "c"])
        self.assertAlmostEqual(project.duration, 3.2)
        self.assertEqual(len(transition_summary(project)["items"]), 2)
        middle = next(c for c in data["tracks"][0]["clips"] if c["id"] == "b")
        middle["transition_in"]["left_clip_id"] = "c"
        with self.assertRaises(ValidationError):
            Project.model_validate(data)  # A cycle cannot satisfy increasing starts/ends.
        branch = pair_data()
        branch["tracks"][0]["clips"].append({"id": "branch", "source_id": "final", "start": 1.2,
            "duration": 1.6, "fit": "cover", "transition_in": {"left_clip_id": "a", "kind": "dissolve", "duration": 0.4}})
        with self.assertRaisesRegex(ValidationError, "third same-track"):
            Project.model_validate(branch)  # Two outgoing bindings cannot silently overwrite one another.

    def test_sorting_is_start_only_stable_and_scoped_to_opted_in_track(self):
        data = pair_data()
        data["tracks"][0]["clips"].reverse()
        data["tracks"][0]["clips"].extend([
            {"id": name, "source_id": "final", "start": 3, "duration": 0.5} for name in ("z", "d")
        ])
        data["tracks"].append({"id": "legacy", "type": "video", "clips": [
            {"id": "later", "source_id": "final", "start": 1, "duration": 1},
            {"id": "earlier", "source_id": "final", "start": 0, "duration": 2},
        ]})
        project = Project.model_validate(data)
        before = project.model_dump()
        self.assertEqual([c.id for c in project.tracks[0].render_clips()], ["a", "b", "z", "d"])
        self.assertIs(project.tracks[1].render_clips(), project.tracks[1].clips)
        self.assertEqual([c.id for c in project.tracks[1].render_clips()], ["later", "earlier"])
        self.assertEqual(project.model_dump(), before)

    def test_easing_expressions_are_local_allowlisted_and_audio_independent(self):
        for easing in ("linear", "ease_in", "ease_out", "ease_in_out"):
            transition = TransitionIn.model_validate({"left_clip_id": "a", "kind": "dissolve", "duration": 0.8, "easing": easing})
            expression = transition_alpha_expression(transition)
            self.assertIn("clip((T-", expression)
            self.assertNotIn("t-", expression)
            self.assertTrue(expression.startswith("255*("))
            self.assertEqual("3-2*" in expression, easing == "ease_in_out")
            self.assertEqual("pow(" in expression, easing != "linear")
            self.assertEqual(transition_alpha_expression(transition.model_copy(update={"audio": False})), expression)
        for kind, fragment in (("wipe_left", "gte(X,W*"), ("wipe_right", "lt(X,W*"),
                               ("wipe_up", "gte(Y,H*"), ("wipe_down", "lt(Y,H*")):
            transition = TransitionIn.model_validate({"left_clip_id": "a", "kind": kind, "duration": 0.8})
            self.assertIn(fragment, transition_alpha_expression(transition))

    def test_capabilities_are_partial_honest_and_overlap_audio_is_disclosed(self):
        caps = capabilities()
        self.assertEqual(caps["tool_count"], 113)
        tools = {tool["id"]: tool for tool in caps["tools"]}
        for key in ("trLib", "trDur", "trCustom"):
            self.assertTrue(tools[key]["available"])
            self.assertEqual(tools[key]["classification"], "partial")
        for key in ("aiTransition", "interp", "beauty", "body", "expHdr"):
            self.assertFalse(tools[key]["available"])
        self.assertEqual(tools["lut"]["classification"], "partial")
        self.assertIn("tetrahedral", tools["lut"]["reason"])
        self.assertNotIn("crossfade", caps["unsupported"])
        self.assertIn("optical-flow transitions", caps["unsupported"])
        self.assertEqual(caps["limits"]["transition_seconds"], 1.2)
        summary = transition_summary(pair(audio=False))
        self.assertIn("overlapping audio", summary["warnings"][0])
        self.assertIn("LINEAR", summary["audio"])
        self.assertFalse(summary["items"][0]["audio"])
        self.assertEqual(transition_summary(pair())["warnings"], [])
        self.assertIsNone(transition_summary(existing.base_project()))
        hidden = pair()
        hidden.tracks[0].hidden = True
        self.assertIsNone(transition_summary(hidden))


class StudioTransitionActionTests(unittest.TestCase):
    def reject(self, project: Project, op: str, clip_id: str, status: int = 422, **fields: Any) -> None:
        before = project.model_dump()
        with self.assertRaises(HTTPException) as caught:
            edit(project, op, clip_id, **fields)
        self.assertEqual(caught.exception.status_code, status)
        if status == 422:
            self.assertIn("remove transition first", str(caught.exception.detail))
        self.assertEqual(project.model_dump(), before)

    def test_deleting_or_splitting_either_endpoint_requires_explicit_removal(self):
        for clip_id, at in (("a", 0.4), ("b", 2.0)):
            for op, fields in (("delete", {}), ("ripple_delete", {}), ("split", {"at": at, "new_id": "part"})):
                with self.subTest(clip_id=clip_id, op=op):
                    self.reject(pair(), op, clip_id, **fields)

    def test_move_trim_and_legacy_overlap_edits_cannot_leave_dangling_bindings(self):
        project = pair()
        project.tracks.append(Track(id="other", type="video"))
        for clip_id in ("a", "b"):
            for op, fields in (("move", {"at": 4}), ("move", {"at": 4, "track_id": "other"}),
                               ("trim", {"trim": 0, "duration": 0.9}), ("ripple_trim", {"trim": 0, "duration": 1}),
                               ("slip", {"trim": 0.1}), ("roll", {"at": 1})):
                if op == "roll" and clip_id == "b":
                    continue  # Missing next clip is a separate legacy error, not an envelope edit.
                with self.subTest(clip_id=clip_id, op=op, fields=fields):
                    self.reject(project, op, clip_id, **fields)

    def test_unrelated_actions_preserve_transition_and_geometry(self):
        project = pair()
        before = project.model_dump()
        marked = apply_action(project, Action(expected_revision=0, op="marker", new_id="mark", at=1.234))
        self.assertEqual(marked.tracks, project.tracks)
        copied = edit(project, "duplicate", "a", at=4, new_id="copy")
        self.assertEqual(copied.tracks[0].clips[:2], project.tracks[0].clips)
        deleted = edit(copied, "delete", "copy")
        self.assertEqual(deleted, project)
        trimmed = edit(project, "trim", "a", trim=0.1, duration=1.6)
        self.assertEqual(trimmed.tracks[0].clips[1].transition_in, project.tracks[0].clips[1].transition_in)
        self.reject(project, "duplicate", "b", at=4, new_id="bad_copy")
        self.assertEqual(project.model_dump(), before)

    def test_track_lock_covers_left_right_and_all_other_clips(self):
        project = edit(pair(), "duplicate", "a", at=4, new_id="unbound")
        project.tracks[0].locked = True
        for clip_id in ("a", "b", "unbound"):
            self.reject(project, "delete", clip_id, status=409)
            data = project.model_dump()
            next(c for c in data["tracks"][0]["clips"] if c["id"] == clip_id)["volume"] = 0.5
            with self.assertRaises(HTTPException) as caught:
                enforce_locks(project, Project.model_validate(data))
            self.assertEqual(caught.exception.status_code, 409)
        data = project.model_dump()
        data["tracks"][0]["clips"][1]["transition_in"] = None
        with self.assertRaises(HTTPException):
            enforce_locks(project, Project.model_validate(data))

    def test_snapshot_endpoint_deletion_guard_requires_separate_binding_removal(self):
        original = pair()
        for removed in ("a", "b", "both"):
            data = original.model_dump()
            data["tracks"][0]["clips"] = [c for c in data["tracks"][0]["clips"] if removed != "both" and c["id"] != removed]
            for clip in data["tracks"][0]["clips"]:
                clip["transition_in"] = None
            with self.subTest(removed=removed), self.assertRaisesRegex(HTTPException, "remove transition first"):
                enforce_transition_endpoints(original, Project.model_validate(data))
        data = original.model_dump()
        data["tracks"][0]["clips"][1]["transition_in"] = None
        unbound = Project.model_validate(data)
        enforce_transition_endpoints(original, unbound)
        self.assertEqual(len(edit(unbound, "delete", "b").tracks[0].clips), 1)
        self.assertEqual(len(edit(unbound, "split", "a", at=0.4, new_id="part").tracks[0].clips), 3)


class _CapturedCommand(Exception):
    def __init__(self, command: list[str]):
        self.command = command
        super().__init__("command capture only; no media execution or success")


class StudioTransitionCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "fixture.mp4"
        self.source.write_bytes(b"command-contract-only-not-decodable")
        self.info = {"duration": 4.0, "streams": [
            {"codec_type": "video", "width": 160, "height": 90, "avg_frame_rate": "30/1"},
            {"codec_type": "audio"},
        ]}

    async def asyncTearDown(self):
        self.assertEqual(self.source.read_bytes(), b"command-contract-only-not-decodable")
        self.temp.cleanup()

    async def command(self, project: Project) -> list[str]:
        async def capture(command: list[str], *_args: Any, **_kwargs: Any):
            raise _CapturedCommand(command)
        with patch("backend.studio_render.probe", new=AsyncMock(return_value=self.info)), \
                patch("backend.studio_render.shutil.which", return_value="contract-only"), \
                patch("backend.studio_render.run_logged_command", side_effect=capture):
            with self.assertRaises(_CapturedCommand) as caught:
                await render_project(project, ExportOptions(resolution=360), {"final": self.source}, self.root / "out")
        return caught.exception.command

    @staticmethod
    def graph(command: list[str]) -> str:
        return command[command.index("-filter_complex") + 1]

    async def test_none_path_legacy_effects_with_verified_sdr_tags_and_omitted_null_equivalence(self):
        project = existing.base_project(duration=1)
        command = await self.command(project)
        graph = (
            "color=c=black:s=640x360:r=30:d=1.0[base0];"
            "anullsrc=r=48000:cl=stereo,atrim=duration=1.0[silence];"
            "[0:v:0]trim=duration=1.0,setpts=PTS-STARTPTS,setpts=PTS/1.0,"
            "crop=iw*1.0:ih*1.0:iw*0.0:ih*0.0,scale=640:360:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            "setsar=1,fps=30,eq=brightness=0.0:contrast=1.0:saturation=1.0,format=rgba,"
            "colorchannelmixer=aa=1.0,setpts=PTS+0.0/TB[v0];"
            "[base0][v0]overlay=x='(W-w)/2+W*(0.0)':y='(H-h)/2+H*(0.0)':eval=frame:"
            "eof_action=pass:repeatlast=0:enable='gte(t,0.0)*lt(t,1.0)'[base1];"
            "[0:a:0]atrim=duration=1.0,asetpts=PTS-STARTPTS,aresample=48000,"
            "aformat=channel_layouts=stereo,volume=1.0,pan=stereo|c0=1*c0|c1=1*c1,"
            "atrim=duration=1.0,adelay=0S:all=1[a0];"
            "[silence][a0]amix=inputs=2:normalize=0:duration=longest,"
            "alimiter=limit=0.95:level=false:latency=true,atrim=duration=1.0[audio];"
            "[base1]scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709[video]"
        )
        expected = ["ffmpeg", "-y", "-nostdin", "-v", "error", "-filter_complex_threads", "1",
                    "-ss", "0.0", "-t", "1.0", "-threads", "2", "-protocol_whitelist", "file,pipe", "-f", "mov", "-i", str(self.source),
                    "-filter_complex", graph, "-map", "[video]", "-r", "30", "-map", "[audio]", "-ar", "48000", "-ac", "2",
                    "-c:v", "libx264", "-b:v", "4000k", "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                    "-preset", "veryfast", "-maxrate", "4000k", "-bufsize", "8000k", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                    "-threads", "2", "-t", "1.0", "-fs", str(MAX_BYTES), str(self.root / "out/output.mp4")]
        self.assertEqual(command, expected)
        data = project.model_dump()
        del data["tracks"][0]["clips"][0]["transition_in"]
        self.assertEqual(await self.command(Project.model_validate(data)), command)

    async def test_alpha_is_incoming_local_and_audio_fades_follow_tempo_and_effects(self):
        data = pair_data(easing="ease_in_out")
        for clip in data["tracks"][0]["clips"]:
            clip.update(speed=2, reverse=True, audio_effect="invert", bass_db=3, treble_db=-3)
        data["tracks"][0]["clips"].reverse()
        project = Project.model_validate(data)
        before = project.model_dump()
        graph = self.graph(await self.command(project))
        parts = graph.split(";")
        left = next(s for s in parts if s.startswith("[0:v:0]"))
        right = next(s for s in parts if s.startswith("[1:v:0]"))
        self.assertNotIn("geq=", left)
        self.assertNotIn("fade=", left)
        self.assertIn("format=rgb24,format=rgba", left)
        self.assertIn("a='255*(", right)
        self.assertIn("3-2*", right)
        self.assertLess(right.index("geq="), right.index("setpts=PTS+0.8/TB"))
        for index, expected in ((0, "afade=t=out:st=0.8:d=0.8:curve=tri"), (1, "afade=t=in:st=0:d=0.8:curve=tri")):
            audio = next(s for s in parts if s.startswith(f"[{index}:a:0]"))
            self.assertIn("atempo=2.0", audio)
            for earlier in ("atempo=", "volume=-1", "bass=", "treble="):
                self.assertLess(audio.index(earlier), audio.index(expected))
            self.assertLess(audio.index("treble="), audio.rindex("aresample=48000"))
            self.assertLess(audio.rindex("aresample=48000"), audio.index(expected))
            self.assertLess(audio.index(expected), audio.index("adelay="))
            self.assertEqual(audio.count("asetpts="), 1)
        self.assertIn("adelay=38400S:all=1[a1]", graph)
        self.assertNotIn("acrossfade", graph)
        self.assertNotIn("xfade=", graph)
        self.assertNotIn("tpad=", graph)
        self.assertEqual(project.model_dump(), before)

    async def test_audio_false_preserves_the_complete_legacy_audio_graph(self):
        data = pair_data(audio=False)
        graph = self.graph(await self.command(Project.model_validate(data)))
        data["tracks"][0]["clips"][1]["transition_in"] = None
        baseline = self.graph(await self.command(Project.model_validate(data)))
        def audio_parts(value: str) -> list[str]:
            return [part for part in value.split(";") if "anullsrc=" in part or ":a:0]" in part or "amix=" in part]
        self.assertEqual(audio_parts(graph), audio_parts(baseline))
        self.assertNotIn("afade=", graph)

    async def test_chain_middle_has_two_disjoint_audio_envelopes_not_two_visual_fades(self):
        graph = self.graph(await self.command(Project.model_validate(chain_data())))
        middle_audio = next(part for part in graph.split(";") if part.startswith("[1:a:0]"))
        self.assertIn("afade=t=in:st=0:d=0.8:curve=tri", middle_audio)
        self.assertIn("afade=t=out:st=0.8:d=0.8:curve=tri", middle_audio)
        middle_video = next(part for part in graph.split(";") if part.startswith("[1:v:0]"))
        self.assertEqual(middle_video.count("geq="), 1)
        self.assertNotIn("fade=t=out", middle_video)
        self.assertIn("d=3.2[base0]", graph)

    async def test_existing_eof_decode_reverse_output_and_graph_budgets_still_apply(self):
        with patch.dict(self.info, {"duration": 1.0}), self.assertRaisesRegex(RenderError, "source duration"):
            await self.command(pair())
        data = pair_data()
        data["tracks"].append({"id": "many", "type": "video", "clips": [
            {"id": f"extra{i}", "source_id": "final", "duration": 1} for i in range(15)
        ]})
        with self.assertRaisesRegex(RenderError, "decoder budget"):
            await self.command(Project.model_validate(data))
        reverse = pair_data()
        reverse["tracks"][0]["clips"][0]["reverse"] = True
        with patch("backend.studio_render.MAX_REVERSE_BYTES", 1), self.assertRaisesRegex(RenderError, "reverse buffer"):
            await self.command(Project.model_validate(reverse))
        with patch("backend.studio_render.MAX_BYTES", 1), self.assertRaisesRegex(RenderError, "size budget"):
            await self.command(pair())
        data["tracks"][1]["clips"].pop()  # Exactly 16 input streams, below decoder budget.
        for clip in data["tracks"][1]["clips"]:
            clip["keyframes"] = {name: [
                {"time": i / 7, "value": 0.123456789012345 + i * 0.1, "easing": "ease_in_out"} for i in range(8)
            ] for name in ("x", "y", "scale", "opacity")}
        with self.assertRaisesRegex(RenderError, "command size"):
            await self.command(Project.model_validate(data))
        self.assertFalse((self.root / "out/output.mp4").exists())


class StudioTransitionRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.root = self.data / "task"
        self.root.mkdir()
        (self.root / "final.mp4").write_bytes(b"metadata-only-fixture-never-decoded")
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
        self.assertFalse(studio_task_busy(self.root))
        self.assertEqual((self.root / "final.mp4").read_bytes(), b"metadata-only-fixture-never-decoded")
        for name, content in self.publication_originals.items():
            self.assertEqual((self.root / name).read_bytes(), content)
        self.temp.cleanup()

    async def save(self, data: dict[str, Any], revision: int = 0, route: str = "/project") -> httpx.Response:
        return await self.client.post(self.prefix + route, json={"expected_revision": revision, "project": data})

    async def action(self, revision: int, op: str, **fields: Any) -> httpx.Response:
        return await self.client.post(self.prefix + "/actions", json={"expected_revision": revision, "op": op, **fields})

    def state_bytes(self) -> bytes:
        return (self.root / "studio/state.json").read_bytes()

    async def test_save_import_restart_marker_and_undo_redo_preserve_transition_defaults(self):
        data = pair_data()
        del data["tracks"][0]["clips"][1]["transition_in"]["easing"]
        del data["tracks"][0]["clips"][1]["transition_in"]["audio"]
        saved = await self.save(data)
        self.assertEqual(saved.status_code, 200, saved.text)
        first = saved.json()["project"]
        changed = copy.deepcopy(first)
        changed["tracks"][0]["clips"][1]["transition_in"].update(kind="wipe_up", easing="ease_out", audio=False)
        imported = await self.save(changed, 1, "/project/import")
        self.assertEqual(imported.status_code, 200, imported.text)
        self.assertEqual((await self.action(2, "undo")).json()["project"], first)
        self.assertEqual((await self.action(3, "redo")).json()["project"], changed)
        marked = await self.action(4, "marker", new_id="m", at=1.234)
        self.assertEqual(marked.status_code, 200)
        self.assertEqual(marked.json()["project"]["tracks"], changed["tracks"])
        self.assertEqual((await self.action(5, "undo")).json()["project"], changed)
        app = FastAPI()
        app.include_router(create_studio_router(self.settings, self.manager, self.authorize))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                                     headers={"X-Token": "test-only"}) as client:
            exported = await client.get(self.prefix + "/project/export")
        self.assertEqual(exported.json()["project"], changed)
        self.assertEqual(exported.json()["revision"], 6)

    async def test_invalid_imports_foreign_sources_and_geometry_are_byte_atomic(self):
        self.assertEqual((await self.save(pair_data())).status_code, 200)
        before = self.state_bytes()
        cases = [("transition_in", {"left_clip_id": "missing", "kind": "dissolve", "duration": 0.8}),
                 ("source_id", "missing"), ("start", 0.9), ("fit", "contain"), ("opacity", 0.5)]
        for field, value in cases:
            data = pair_data()
            data["tracks"][0]["clips"][1][field] = value
            for route in ("/project", "/project/import"):
                response = await self.save(data, 1, route)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.state_bytes(), before)
        with patch("backend.studio.write_json_atomic", side_effect=OSError("synthetic write failure")):
            failed = await self.action(1, "marker", new_id="m", at=1)
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(self.state_bytes(), before)

    async def test_save_cannot_erase_binding_by_dropping_right_left_or_entire_track(self):
        self.assertEqual((await self.save(pair_data())).status_code, 200)
        before = self.state_bytes()
        variants = [{"tracks": []}]
        for clip_id in ("a", "b"):
            data = pair_data()
            data["tracks"][0]["clips"] = [c for c in data["tracks"][0]["clips"] if c["id"] != clip_id]
            for clip in data["tracks"][0]["clips"]:
                clip["transition_in"] = None
            variants.append(data)
        moved = pair_data()
        moved["tracks"][0]["id"] = "other"
        variants.append(moved)
        for data in variants:
            response = await self.save(data, 1, "/project/import")
            self.assertEqual(response.status_code, 422, response.text)
            self.assertIn("remove transition first", response.text)
            self.assertEqual(self.state_bytes(), before)
        data = pair_data()
        data["tracks"][0]["clips"][1]["transition_in"] = None
        self.assertEqual((await self.save(data, 1)).status_code, 200)
        self.assertEqual((await self.action(2, "delete", clip_id="b")).status_code, 200)
        self.assertEqual((await self.action(3, "undo")).json()["project"], Project.model_validate(data).model_dump())
        restored = await self.action(4, "undo")
        self.assertEqual(restored.json()["project"], pair().model_dump())

    async def test_action_failures_do_not_consume_revision_history_or_clear_binding(self):
        self.assertEqual((await self.save(pair_data())).status_code, 200)
        before = self.state_bytes()
        for clip_id in ("a", "b"):
            for op, fields in (("delete", {}), ("split", {"at": 1, "new_id": "part"}), ("move", {"at": 4}),
                               ("trim", {"trim": 0, "duration": 0.9}), ("ripple_delete", {})):
                response = await self.action(1, op, clip_id=clip_id, **fields)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertIn("remove transition first", response.text)
                self.assertEqual(self.state_bytes(), before)

    async def test_locks_protect_transition_changes_and_either_participant(self):
        self.assertEqual((await self.save(pair_data())).status_code, 200)
        self.assertEqual((await self.action(1, "track", track_id="v", locked=True)).status_code, 200)
        locked = self.state_bytes()
        for endpoint in (0, 1):
            data = read_state(self.root)["project"]
            data["tracks"][0]["clips"][endpoint]["volume"] = 0.5
            self.assertEqual((await self.save(data, 2)).status_code, 409)
            self.assertEqual((await self.action(2, "delete", clip_id=("a", "b")[endpoint])).status_code, 409)
            self.assertEqual(self.state_bytes(), locked)
        data = read_state(self.root)["project"]
        data["tracks"][0]["clips"][1]["transition_in"] = None
        self.assertEqual((await self.save(data, 2, "/project/import")).status_code, 409)
        self.assertEqual(self.state_bytes(), locked)
        self.assertEqual((await self.action(2, "track", track_id="v", locked=False)).status_code, 200)
        self.assertEqual((await self.save(pair_data(kind="wipe_down"), 3)).status_code, 200)

    async def test_revision_conflicts_concurrent_saves_and_unauthorized_writes(self):
        responses = await asyncio.gather(self.save(pair_data()), self.save(pair_data(kind="wipe_right")))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        before = self.state_bytes()
        self.assertEqual((await self.save(pair_data())).status_code, 409)
        self.assertEqual((await self.action(0, "undo")).status_code, 409)
        for header in ("X-Token", "X-CSRF"):
            denied = await self.client.post(self.prefix + "/project/import", headers={header: "denied"},
                                           json={"expected_revision": 1, "project": pair_data()})
            self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.state_bytes(), before)

    async def test_undo_revalidates_bindings_and_authorized_sources_before_history_mutation(self):
        self.assertEqual((await self.save(pair_data())).status_code, 200)
        self.assertEqual((await self.action(1, "marker", new_id="m", at=1)).status_code, 200)
        good = read_state(self.root)
        for foreign_source in (False, True):
            state = copy.deepcopy(good)
            clip = state["past"][-1]["tracks"][0]["clips"][1]
            if foreign_source:
                clip["source_id"] = "missing"
            else:
                clip["transition_in"]["left_clip_id"] = "missing"
            write_state(self.root, state)
            before = self.state_bytes()
            response = await self.action(2, "undo")
            self.assertEqual(response.status_code, 422, response.text)
            self.assertEqual(self.state_bytes(), before)

    async def test_cancelled_transition_job_cleans_snapshot_and_discloses_overlap_audio(self):
        self.assertEqual((await self.save(pair_data(audio=False))).status_code, 200)
        entered = asyncio.Event()

        async def blocked(project: Project, _options: Any, _sources: Any, work: Path, **_kwargs: Any):
            self.assertFalse(project.tracks[0].clips[1].transition_in.audio)
            (work / "partial.mp4").write_bytes(b"explicit cancellation double, not media")
            entered.set()
            await asyncio.Event().wait()

        with patch("backend.studio.render_project", side_effect=blocked):
            response = await self.client.post(self.prefix + "/render", json={"expected_revision": 1})
            self.assertEqual(response.status_code, 202, response.text)
            await asyncio.wait_for(entered.wait(), 5)
            job_id = response.json()["id"]
            self.assertIn("overlapping audio", response.json()["transition_semantics"]["warnings"][0])
            work = self.root / "studio/outputs/r1" / job_id
            snapshot = json.loads((work / "snapshot.json").read_text(encoding="utf-8"))
            self.assertFalse(snapshot["project"]["tracks"][0]["clips"][1]["transition_in"]["audio"])
            self.assertTrue(studio_task_busy(self.root))
            second = await self.client.post(self.prefix + "/render", json={"expected_revision": 1})
            self.assertEqual(second.status_code, 409)
            cancelled = await self.client.delete(self.prefix + "/jobs/" + job_id)
        self.assertEqual(cancelled.json()["state"], "cancelled")
        self.assertFalse(work.exists())
        self.assertNotIn(str(self.root.resolve()), _BUSY)
        self.assertEqual((await self.client.get(self.prefix + "/outputs/" + job_id)).status_code, 409)
        self.assertEqual(read_state(self.root)["project"], pair(audio=False).model_dump())
        self.assertEqual(read_state(self.root)["revision"], 1)

    async def test_existing_request_storage_job_and_history_budgets_still_gate_transitions(self):
        self.assertEqual((await self.save(pair_data())).status_code, 200)
        before = self.state_bytes()
        with patch("backend.studio.MAX_TASK_BYTES", 1):
            response = await self.client.post(self.prefix + "/render", json={"expected_revision": 1})
        self.assertEqual(response.status_code, 507)
        with patch("backend.studio.MAX_JOBS", 0):
            response = await self.client.post(self.prefix + "/render", json={"expected_revision": 1})
        self.assertEqual(response.status_code, 429)
        with patch("backend.studio.MAX_JSON", 1):
            self.assertEqual((await self.save(pair_data(), 1)).status_code, 413)
        with patch("backend.studio.MAX_HISTORY", 1):
            self.assertEqual((await self.action(1, "marker", new_id="m", at=1)).status_code, 409)
        self.assertEqual(self.state_bytes(), before)
        self.assertFalse(studio_task_busy(self.root))
        self.assertFalse((self.root / "studio/outputs").exists())

    async def test_mandatory_generated_disclosure_and_qc_are_not_bypassed(self):
        self.assertEqual((await self.save(pair_data())).status_code, 200)
        (self.root / "generated_media_disclosure.json").write_text(json.dumps(generated_manifest()), encoding="utf-8")
        confirm_synthetic_publication(self, self.record, generated=True)
        before = self.state_bytes()
        with patch("backend.studio.render_project", new=AsyncMock()) as render:
            for fmt in ("wav", "mp3", "srt", "ass"):
                response = await self.client.post(self.prefix + "/render", json={"expected_revision": 1, "options": {"format": fmt}})
                self.assertEqual(response.status_code, 422, response.text)
            try:
                (self.root / "quality_report.json").write_bytes(synthetic_publication_files(blocked=True)["quality_report.json"])
                response = await self.client.post(self.prefix + "/render", json={"expected_revision": 1})
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["detail"], QC_BLOCKER)
            finally:
                (self.root / "quality_report.json").write_bytes(self.publication_originals["quality_report.json"])
            render.assert_not_awaited()
        self.assertEqual(self.state_bytes(), before)


@unittest.skipUnless(existing.TOOLS, "FFmpeg/ffprobe must be on PATH")
class StudioTransitionMediaTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.sources: dict[str, Path] = {}
        # Native stereo PCM in Matroska avoids AAC encoder delay/rematrix gain
        # in the reference. Each source has one independent channel/clock.
        for name, color, tone in (("red", "red", "0.16*sin(2*PI*440*t)|0"),
                                  ("blue", "blue", "0|0.12*sin(2*PI*880*t)")):
            path = self.root / f"{name}.mkv"
            existing.ffmpeg("-y", "-f", "lavfi", "-i", f"color={color}:s=160x90:r=30:d=4",
                            "-f", "lavfi", "-i", f"aevalsrc={tone}:s=48000:d=4:c=stereo",
                            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "0", "-threads", "1",
                            "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-ac", "2", "-t", "4", str(path))
            self.sources[name] = path
        self.hashes = {name: hashlib.sha256(path.read_bytes()).digest() for name, path in self.sources.items()}

    async def asyncTearDown(self):
        self.assertEqual({name: hashlib.sha256(path.read_bytes()).digest() for name, path in self.sources.items()}, self.hashes)
        self.temp.cleanup()

    def project(self, **fields: Any) -> Project:
        return pair(left_source="red", right_source="blue", **fields)

    async def render(self, project: Project, name: str, *, disclosure: bool = False, **options: Any):
        if options.get("format") not in {"wav", "mp3", "srt", "ass"}:
            options.setdefault("resolution", 360)
        before = project.model_dump()
        work = self.root / name
        result = await render_project(project, ExportOptions.model_validate(options), self.sources, work, disclosure=disclosure)
        self.assertEqual(project.model_dump(), before)
        return result, work / result["file"]

    async def image(self, project: Project, at: float, name: str, *, disclosure: bool = False) -> bytes:
        _result, path = await self.render(project, name, format="png", frame_time=at, disclosure=disclosure)
        raw = existing.ffmpeg("-i", str(path), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
        self.assertEqual(len(raw), 640 * 360 * 3)
        return raw

    @staticmethod
    def pixel(frame: bytes, x: int = 320, y: int = 180) -> tuple[int, ...]:
        offset = (y * 640 + x) * 3
        return tuple(frame[offset:offset + 3])

    @staticmethod
    def samples(path: Path) -> array.array:
        return array.array("h", existing.ffmpeg("-i", str(path), "-map", "0:a:0", "-ar", "48000", "-ac", "2", "-f", "s16le", "pipe:1"))

    @staticmethod
    def amplitude(samples: array.array, channel: int, frequency: int, start: float, duration: float = 0.1) -> float:
        first, count = round(start * 48000), round(duration * 48000)
        window = samples[first * 2 + channel:(first + count) * 2:2]
        if len(window) != count:
            raise AssertionError("PCM window was truncated")
        real = sum(v * math.cos(2 * math.pi * frequency * i / 48000) for i, v in enumerate(window))
        imag = sum(v * math.sin(2 * math.pi * frequency * i / 48000) for i, v in enumerate(window))
        return 2 * math.hypot(real, imag) / count / 32768

    async def test_real_dissolve_midpoint_has_both_colors_without_shortening_frames(self):
        project = self.project()
        result, path = await self.render(project, "dissolve")
        self.assertAlmostEqual(result["duration"], 2.4, places=10)
        self.assertAlmostEqual(float(result["probe"]["format"]["duration"]), 2.4, delta=1 / 30)
        tiny = existing.ffmpeg("-i", str(path), "-map", "0:v:0", "-vf", "scale=16:9", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
        self.assertEqual(len(tiny), 72 * 16 * 9 * 3)
        frame = tiny[36 * 16 * 9 * 3:37 * 16 * 9 * 3]
        pixel = tuple(frame[(4 * 16 + 8) * 3:(4 * 16 + 8) * 3 + 3])
        self.assertTrue(90 < pixel[0] < 165, pixel)
        self.assertTrue(90 < pixel[2] < 165, pixel)
        self.assertLess(pixel[1], 20)
        for at, channel in ((0.4, 0), (1.8, 2)):
            pixel = self.pixel(await self.image(project, at, f"edge_{at}"))
            self.assertGreater(pixel[channel], 225)
            self.assertLess(pixel[2 - channel], 15)

    async def test_all_four_wipes_reveal_opposite_spatial_halves(self):
        for kind, outgoing, incoming in (
            ("wipe_left", (160, 180), (480, 180)), ("wipe_right", (480, 180), (160, 180)),
            ("wipe_up", (320, 90), (320, 270)), ("wipe_down", (320, 270), (320, 90)),
        ):
            with self.subTest(kind=kind):
                frame = await self.image(self.project(kind=kind), 1.2, kind)
                red, blue = self.pixel(frame, *outgoing), self.pixel(frame, *incoming)
                self.assertGreater(red[0], 225, red)
                self.assertLess(red[2], 15, red)
                self.assertGreater(blue[2], 225, blue)
                self.assertLess(blue[0], 15, blue)

    async def test_every_visual_easing_is_numeric_clip_local_not_global_time(self):
        for easing, quarter in (("linear", 0.25), ("ease_in", 0.0625), ("ease_out", 0.4375), ("ease_in_out", 0.15625)):
            # Start the entire pair later: the fade must still use B-local 0.2s,
            # not the global selected frame at 1.4s or A's local time.
            frame = await self.image(self.project(easing=easing, start=0.4), 1.4, easing)
            pixel = self.pixel(frame)
            self.assertAlmostEqual(pixel[2], 255 * quarter, delta=14, msg=f"{easing}: {pixel}")
            self.assertAlmostEqual(pixel[0], 255 * (1 - quarter), delta=14, msg=f"{easing}: {pixel}")
            self.assertLess(pixel[1], 20)

    async def test_chain_json_order_and_boundary_keep_lower_layers_opaque(self):
        data = chain_data(left_source="red", right_source="blue", track_type="overlay")
        third = next(c for c in data["tracks"][0]["clips"] if c["id"] == "c")
        third["source_id"] = "red"
        data["tracks"].insert(0, {"id": "lower", "type": "video", "clips": [
            {"id": "under", "source_id": "red", "duration": 3.2, "fit": "cover", "brightness": 1, "mute": True},
        ]})
        project = Project.model_validate(data)
        for at in (1.2, 2.0):
            pixel = self.pixel(await self.image(project, at, f"chain_{at}"))
            self.assertTrue(90 < pixel[0] < 165, pixel)
            self.assertTrue(90 < pixel[2] < 165, pixel)
            self.assertLess(pixel[1], 20, "opaque left must prevent white lower-track bleed")
        boundary = self.pixel(await self.image(project, 1.6, "chain_boundary"))
        self.assertGreater(boundary[2], 225, boundary)
        self.assertLess(boundary[0], 15, boundary)
        self.assertAlmostEqual(project.duration, 3.2)

    async def test_audio_linear_complementary_clock_and_channel_amplitudes(self):
        # Nonlinear visual easing must NOT turn the audio fades nonlinear.
        result, path = await self.render(self.project(easing="ease_in"), "audio", format="wav")
        samples = self.samples(path)
        self.assertEqual(len(samples), round(2.4 * 48000) * 2)
        self.assertEqual(result["probe"]["streams"][0]["channels"], 2)
        self.assertAlmostEqual(self.amplitude(samples, 0, 440, 0.2), 0.16, delta=0.008)
        self.assertLess(self.amplitude(samples, 1, 880, 0.2), 0.001)
        for progress in (0.25, 0.5, 0.75):
            start = 0.8 + 0.8 * progress - 0.05  # Mean of each window is exactly progress.
            self.assertAlmostEqual(self.amplitude(samples, 0, 440, start), 0.16 * (1 - progress), delta=0.008)
            self.assertAlmostEqual(self.amplitude(samples, 1, 880, start), 0.12 * progress, delta=0.008)
        self.assertLess(self.amplitude(samples, 0, 440, 1.8), 0.001)
        self.assertAlmostEqual(self.amplitude(samples, 1, 880, 2.25), 0.12, delta=0.008)
        # Clock at the overlap edges, not just header duration or average loudness.
        self.assertLess(self.amplitude(samples, 1, 880, 0.75, 0.025), 0.001)
        self.assertLess(self.amplitude(samples, 0, 440, 1.65, 0.025), 0.001)

    async def test_audio_false_is_pcm_identical_and_true_remains_post_tempo_effect(self):
        data = pair_data(left_source="red", right_source="blue", audio=False)
        for clip in data["tracks"][0]["clips"]:
            clip.update(speed=2, audio_effect="invert")
        result, untouched_path = await self.render(Project.model_validate(data), "audio_false", format="wav")
        untouched = self.samples(untouched_path)
        self.assertIn("overlapping audio", result["transition_semantics"]["warnings"][0])
        data["tracks"][0]["clips"][1]["transition_in"] = None
        _, baseline_path = await self.render(Project.model_validate(data), "audio_none", format="wav")
        baseline = self.samples(baseline_path)
        self.assertEqual(untouched, baseline)
        self.assertEqual(len(untouched), round(2.4 * 48000) * 2)
        self.assertAlmostEqual(self.amplitude(untouched, 0, 440, 1.15), 0.16, delta=0.012)
        self.assertAlmostEqual(self.amplitude(untouched, 1, 880, 1.15), 0.12, delta=0.012)
        data["tracks"][0]["clips"][1]["transition_in"] = {
            "left_clip_id": "a", "kind": "dissolve", "duration": 0.8, "audio": True,
        }
        _, faded_path = await self.render(Project.model_validate(data), "audio_true_speed", format="wav")
        faded = self.samples(faded_path)
        self.assertEqual(len(faded), len(baseline))
        self.assertAlmostEqual(self.amplitude(faded, 0, 440, 1.15), 0.08, delta=0.012)
        self.assertAlmostEqual(self.amplitude(faded, 1, 880, 1.15), 0.06, delta=0.012)
        # No transition-induced head delay on A, or tail loss on B, outside envelopes.
        self.assertEqual(faded[:round(0.7 * 48000) * 2], baseline[:round(0.7 * 48000) * 2])
        self.assertEqual(faded[round(1.7 * 48000) * 2:], baseline[round(1.7 * 48000) * 2:])

    async def test_post_loudnorm_rate_does_not_change_transition_or_timeline_clock(self):
        data = pair_data(left_source="red", right_source="blue")
        for clip in data["tracks"][0]["clips"]:
            clip["audio_effect"] = "normalize"
        _, path = await self.render(Project.model_validate(data), "normalized", format="wav")
        samples = self.samples(path)
        self.assertEqual(len(samples), round(2.4 * 48000) * 2)
        full_left = self.amplitude(samples, 0, 440, 0.2)
        full_right = self.amplitude(samples, 1, 880, 1.9)
        self.assertGreater(full_left, 0.05)
        self.assertGreater(full_right, 0.05)
        # A 192kHz post-effect stream with an uncorrected 38400-sample delay
        # starts B at 0.2s, NOT 0.8s. Header length alone would miss that error.
        self.assertLess(self.amplitude(samples, 1, 880, 0.5), 0.001)
        self.assertLess(self.amplitude(samples, 0, 440, 1.8), 0.001)
        self.assertAlmostEqual(self.amplitude(samples, 0, 440, 1.15) / full_left, 0.5, delta=0.08)
        self.assertAlmostEqual(self.amplitude(samples, 1, 880, 1.15) / full_right, 0.5, delta=0.08)

    async def test_generated_disclosure_is_burned_after_transitions_not_faded_away(self):
        project = self.project(kind="wipe_up")
        plain = await self.image(project, 1.2, "plain")
        disclosed = await self.image(project, 1.2, "disclosed", disclosure=True)
        self.assertGreater(sum(a != b for a, b in zip(plain[:640 * 90 * 3], disclosed[:640 * 90 * 3])), 100)
        self.assertEqual(self.pixel(plain), self.pixel(disclosed))
        ass = (self.root / "disclosed/studio.ass").read_text(encoding="utf-8")
        disclosure = [line for line in ass.splitlines() if line.startswith("Dialogue: 100,")]
        self.assertEqual(len(disclosure), 1)
        self.assertIn("AI生成示意画面", disclosure[0])
        self.assertIn("0:00:00.00,0:00:02.40", disclosure[0])
        self.assertNotIn("\\fad", disclosure[0])
        for fmt in ("wav", "mp3", "srt", "ass"):
            with self.assertRaisesRegex(RenderError, "strips"):
                await self.render(project, "stripped_" + fmt, format=fmt, disclosure=True)

    async def test_omitted_and_null_default_outputs_are_byte_identical(self):
        data = pair_data(left_source="red", right_source="blue")
        data["tracks"][0]["clips"].reverse()  # Keep legacy overlap/list-order semantics.
        for clip in data["tracks"][0]["clips"]:
            clip.pop("transition_in", None)
        _, absent = await self.render(Project.model_validate(data), "absent", format="png", frame_time=1.2)
        for clip in data["tracks"][0]["clips"]:
            clip["transition_in"] = None
        _, explicit = await self.render(Project.model_validate(data), "null", format="png", frame_time=1.2)
        self.assertEqual(absent.read_bytes(), explicit.read_bytes())
        image = existing.ffmpeg("-i", str(explicit), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
        self.assertGreater(self.pixel(image)[0], 225, "legacy reversed list order must still put A above B")

    async def test_actual_source_eof_is_not_extended_to_manufacture_a_transition(self):
        data = pair_data(left_source="red", right_source="blue")
        data["tracks"][0]["clips"][1]["trim"] = 3.5
        project = Project.model_validate(data)  # Geometry is valid; real EOF is not.
        with self.assertRaisesRegex(RenderError, "source duration"):
            await self.render(project, "past_eof")
        self.assertFalse((self.root / "past_eof/output.mp4").exists())


@unittest.skipUnless(existing.TOOLS, "FFmpeg/ffprobe must be on PATH")
class StudioTransitionMediaRouteTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = existing.StudioAPITests.asyncSetUp
    asyncTearDown = existing.StudioAPITests.asyncTearDown
    save = existing.StudioAPITests.save
    finish = existing.StudioAPITests.finish

    async def test_real_authenticated_output_range_snapshot_and_mandatory_manifest(self):
        project = pair(kind="wipe_left", audio=False)
        self.assertEqual((await self.save(project)).status_code, 200)
        (self.root / "generated_media_disclosure.json").write_text(json.dumps(generated_manifest()), encoding="utf-8")
        confirm_synthetic_publication(self, self.record, generated=True)
        job = await self.finish(await self.client.post(self.prefix + "/render", json={"expected_revision": 1,
            "options": {"format": "png", "resolution": 360, "frame_time": 1.2}}))
        self.assertEqual(job["state"], "succeeded", job)
        self.assertTrue(job["disclosure"])
        self.assertIn("overlapping audio", job["transition_semantics"]["warnings"][0])
        response = await self.client.get(self.prefix + "/outputs/" + job["id"])
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(len(response.content), job["result"]["bytes"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        part = await self.client.get(self.prefix + "/outputs/" + job["id"], headers={"Range": "bytes=0-31"})
        self.assertEqual(part.status_code, 206)
        self.assertEqual(part.content, response.content[:32])
        self.assertEqual((await self.client.get(self.prefix + "/outputs/" + job["id"],
            headers={"X-Token": "bad", "Range": "bytes=0-31"})).status_code, 403)
        work = self.root / "studio/outputs/r1" / job["id"]
        snapshot = json.loads((work / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["project"], project.model_dump())
        manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["transition_semantics"], job["result"]["transition_semantics"])
        self.assertAlmostEqual(manifest["disclosure_intervals"][0][1], project.duration)
        self.assertIn("AI生成示意画面", (work / "studio.ass").read_text(encoding="utf-8"))
        self.assertEqual((self.root / "report.json").read_bytes(), self.publication_originals["report.json"])

    async def test_real_eof_failure_has_no_download_or_partial_output(self):
        data = pair_data()
        data["tracks"][0]["clips"][1]["trim"] = 1
        self.assertEqual((await self.save(Project.model_validate(data))).status_code, 200)
        job = await self.finish(await self.client.post(self.prefix + "/render", json={"expected_revision": 1,
            "options": {"resolution": 360}}))
        self.assertEqual(job["state"], "failed", job)
        self.assertIn("source duration", job["error"])
        self.assertEqual((await self.client.get(self.prefix + "/outputs/" + job["id"])).status_code, 409)
        self.assertFalse((self.root / "studio/outputs/r1" / job["id"]).exists())


if __name__ == "__main__":
    unittest.main()