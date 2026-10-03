"""Multi-timeline snapshots, bounded identity nests and REAL codec regressions.

Authored for the main runner; no tests/commands were executed while implementing
this change. API tests use an unmounted router, ASGITransport and owned TEMP,
never main/Settings/dotenv, provider clients or a listening service. Contract
doubles deliberately fail or cancel, never assert simulated media success.
Media classes use real FFmpeg/probe/pixels/PCM and skip ONLY for missing tools.

Frontend contract: schema 1 remains additive. Main stays tracks/markers; up to
4 sequences share groups/assets/workspace; active_sequence_id=null selects main
and must be SAVED. 8 tracks/100 markers per timeline; 32 tracks/64 clips TOTAL.
Nests are video/overlay source XOR sequence references: full child visible/solo
duration, trim=0, all default controls (fit=contain) except start and mute. At
most 2 edges from any timeline, no cycles/inactive dangling refs. Render expands
at most 8 tracks/64 clips/16 decoder occurrences, without intermediate media.
This is not isolated compositing: child gaps reveal lower layers, adjustments
affect the lower composite and child text is above ALL media. Duration-changing
child edits require explicit ancestor descriptor updates in ONE project save.
"""
from __future__ import annotations

import array
import asyncio
import copy
import hashlib
import json
import math
import os
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from backend import studio_assets, studio_proxy
from backend.storage import write_json_atomic
from backend.studio import (
    Action, _BUSY, _DISK_RESERVATIONS, _RUNNING, _validate_proxy_snapshot,
    apply_action, capabilities, create_studio_router, enforce_locks,
    enforce_sequence_deletions, enforce_transition_endpoints, public_state,
    read_state, studio_task_busy, write_state,
)
from backend.studio_render import (
    MAX_BYTES, Clip, ExportOptions, Project, RenderError, Sequence, Track,
    render_project, subtitle_document, transition_summary,
)
from backend.studio_sequences import evaluate_project, sequence_summary
from tests import test_studio as media
from tests import test_studio_assets as asset_fixtures
from tests.studio_publication_fixtures import (
    QC_BLOCKER, confirm_synthetic_publication, seed_synthetic_publication,
    synthetic_publication_files,
)


def video_track(identifier: str, source: str = "final", duration: float = 1) -> dict[str, Any]:
    return {"id": identifier, "type": "video", "clips": [
        {"id": identifier + "_clip", "source_id": source, "duration": duration},
    ]}


def project_data(*, active: str | None = None) -> dict[str, Any]:
    return {
        "tracks": [video_track("main")], "markers": [{"id": "main_mark", "time": 0.2}],
        "sequences": [
            {"id": "seq_a", "name": "Timeline A", "tracks": [video_track("a", "video_only")],
             "markers": [{"id": "a_mark", "time": 0.3}]},
            {"id": "seq_b", "name": "Timeline B", "tracks": [video_track("b")],
             "markers": [{"id": "b_mark", "time": 0.4}]},
        ],
        "active_sequence_id": active,
    }


def nested_data(*, depth: int = 1, active: str | None = None) -> dict[str, Any]:
    data = project_data(active=active)
    data["tracks"] = [{"id": "main", "type": "video", "clips": [
        {"id": "nest_a", "sequence_id": "seq_a", "duration": 1},
    ]}]
    if depth == 2:
        data["sequences"][0]["tracks"] = [{"id": "a", "type": "overlay", "clips": [
            {"id": "nest_b", "sequence_id": "seq_b", "duration": 1},
        ]}]
    return data


def repeated_data(*, clips: int = 8, copies: int = 2, text: bool = False) -> dict[str, Any]:
    return {"tracks": [{"id": "main", "type": "video", "clips": [
        {"id": f"nest_{i}", "sequence_id": "child", "start": i, "duration": 1} for i in range(copies)
    ]}], "sequences": [{"id": "child", "name": "Repeated", "tracks": [
        {"id": "child_track", "type": "text" if text else "video", "clips": [
            {"id": f"leaf_{i}", "duration": 1, **({"text": "Caption"} if text else {"source_id": "final"})}
            for i in range(clips)
        ]},
    ]}]}


def transition_child_data() -> dict[str, Any]:
    return {"tracks": [{"id": "main", "type": "video", "clips": [
        {"id": "nest_first", "sequence_id": "child", "start": 0.25, "duration": 1.5},
        {"id": "nest_second", "sequence_id": "child", "start": 2, "duration": 1.5},
    ]}], "sequences": [{"id": "child", "name": "Transition", "tracks": [
        {"id": "child_video", "type": "video", "clips": [
            {"id": "left", "source_id": "final", "duration": 1, "fit": "cover"},
            {"id": "right", "source_id": "video_only", "start": 0.5, "duration": 1, "fit": "cover",
             "transition_in": {"left_clip_id": "left", "kind": "dissolve", "duration": 0.5}},
        ]},
    ]}]}


def generated_manifest() -> dict[str, Any]:
    return {"schema_version": 1, "policy": "generated_visuals_are_disclosed_and_not_evidence", "items": [
        {"sentence_id": 0, "beat_id": 0, "shot_id": 0, "mode": "video",
         "prompt_sha256": "a" * 64, "disclosure_text": "AI生成示意画面"},
    ]}


class StudioSequenceModelTests(unittest.TestCase):
    def test_additive_defaults_roundtrip_and_no_implicit_selection_reset(self) -> None:
        old = {"tracks": [video_track("main")]}
        state = {"revision": 3, "project": old, "past": [], "future": []}
        before = copy.deepcopy(state)
        normalized = public_state(state)["project"]
        self.assertEqual(normalized["sequences"], [])
        self.assertIsNone(normalized["active_sequence_id"])
        self.assertIsNone(normalized["tracks"][0]["clips"][0]["sequence_id"])
        self.assertEqual(state, before)
        for active in (None, "seq_a", "seq_b"):
            project = Project.model_validate(project_data(active=active))
            self.assertEqual(Project.model_validate_json(project.model_dump_json()), project)
            self.assertEqual(project.active_timeline().tracks[0].id, {None: "main", "seq_a": "a", "seq_b": "b"}[active])
            self.assertEqual(project.tracks[0].id, "main")
        with self.assertRaisesRegex(ValidationError, "active_sequence_id"):
            Project.model_validate(project_data(active="missing"))

    def test_sequence_schema_limits_and_shared_metadata_are_not_per_sequence_budgets(self) -> None:
        Sequence(id="s", name="x" * 80)
        for fields in ({"name": "x" * 81}, {"id": "../s"}, {"tracks": [video_track(f"v{i}") for i in range(9)]},
                       {"markers": [{"id": f"m{i}", "time": 0} for i in range(101)]},
                       {"workspace": {}}, {"groups": []}, {"assets": []}, {"duration": 1}, {"project": {}}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                Sequence.model_validate({"id": "s", "name": "S", **fields})
        data = {"sequences": [{"id": f"s{i}", "name": "S", "tracks": [
            {"id": f"t{i}_{j}", "type": "video"} for j in range(8)
        ]} for i in range(4)]}
        project = Project.model_validate(data)
        self.assertEqual(len(project.all_tracks()), 32)
        with self.assertRaisesRegex(ValidationError, "32 tracks"):
            Project.model_validate({**data, "tracks": [{"id": "main", "type": "video"}]})
        with self.assertRaises(ValidationError):
            Project.model_validate({"sequences": [{"id": f"s{i}", "name": "S"} for i in range(5)]})
        data = project_data()
        data["tracks"][0]["clips"] = [{"id": f"c{i}", "source_id": "final", "duration": 1} for i in range(63)]
        with self.assertRaisesRegex(ValidationError, "64 clips"):
            Project.model_validate(data)  # 63 main + one in EACH child, not 64 per timeline.
        data["tracks"][0]["clips"].pop()
        Project.model_validate(data)
        data["groups"] = [{"id": "group", "name": "Shared"}]
        data["sequences"][0]["tracks"][0]["group_id"] = "group"
        Project.model_validate(data)
        data["sequences"][1]["tracks"][0]["group_id"] = "missing"
        with self.assertRaisesRegex(ValidationError, "unknown track group"):
            Project.model_validate(data)

    def test_ids_unique_across_all_inactive_containers_and_entity_kinds(self) -> None:
        for kind in ("sequence", "track", "clip", "marker", "group"):
            data = project_data()
            sequence = data["sequences"][1]
            if kind == "sequence":
                sequence["id"] = "main_mark"
            elif kind == "track":
                sequence["tracks"][0]["id"] = "main"
            elif kind == "clip":
                sequence["tracks"][0]["clips"][0]["id"] = "a_clip"
            elif kind == "marker":
                sequence["markers"][0]["id"] = "seq_a"
            else:
                data["groups"] = [{"id": "b_mark", "name": "duplicate"}]
            with self.subTest(kind=kind), self.assertRaisesRegex(ValidationError, "unique"):
                Project.model_validate(data)

    def test_media_source_xor_sequence_and_nested_controls_are_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValidationError, "never both"):
            Clip(id="n", source_id="final", sequence_id="seq_a", duration=1)
        with self.assertRaisesRegex(ValidationError, "require source_id"):
            Track(id="v", type="video", clips=[Clip(id="c", duration=1)])
        for kind in ("audio", "text", "adjustment"):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValidationError, "only on video/overlay"):
                Track.model_validate({"id": "t", "type": kind, "clips": [{"id": "n", "sequence_id": "seq_a", "duration": 1}]})
        rejected = [{"trim": 0.01}, {"speed": 0.5}, {"reverse": True}, {"freeze": True, "mute": True},
                    {"fit": "cover"}, {"rotation": 90}, {"mirror": True}, {"flip": True}, {"scale": 0.5},
                    {"x": 0.1}, {"y": 0.1}, {"opacity": 0.9}, {"crop": {"width": 0.5}},
                    {"volume": 0.5}, {"pan": 0.5}, {"audio_effect": "denoise"}, {"bass_db": 1}, {"treble_db": 1},
                    {"brightness": 0.1}, {"color_preset": "warm"}, {"lut_id": "lut_" + "a" * 24},
                    {"fade_in": 0.1}, {"fade_out": 0.1}, {"text": "ignored"}, {"mask": {"type": "ellipse"}},
                    {"keyframes": {"x": [{"time": 0, "value": 0}, {"time": 1, "value": 0.5}]}},
                    {"transition_in": {"left_clip_id": "other", "kind": "dissolve", "duration": 0.2}}]
        for fields in rejected:
            data = nested_data()
            data["tracks"][0]["clips"][0].update(fields)
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                Project.model_validate(data)
            with self.assertRaises(ValidationError):
                Clip.model_validate({"id": "nested", "sequence_id": "seq_a", "duration": 1, **fields})
        for muted in (False, True):
            data = nested_data()
            data["tracks"][0]["clips"][0].update(start=0.5, mute=muted)
            project = Project.model_validate(data)
            self.assertEqual(evaluate_project(project).tracks[0].clips[0].mute, muted)

    def test_cycles_unknown_references_and_depth_check_even_unselected_hidden_branches(self) -> None:
        valid = Project.model_validate(nested_data(depth=2))
        self.assertEqual(len(evaluate_project(valid).instances), 2)
        for target in ("missing", "main", "a_clip"):
            data = nested_data()
            data["tracks"][0]["hidden"] = True
            data["tracks"][0]["clips"][0]["sequence_id"] = target
            with self.subTest(target=target), self.assertRaisesRegex(ValidationError, "unknown sequence_id"):
                Project.model_validate(data)
        for self_cycle in (True, False):
            data = nested_data(depth=2, active="seq_b")
            data["tracks"] = [video_track("unrelated")]
            data["sequences"][0]["tracks"][0]["hidden"] = True
            data["sequences"][1]["tracks"] = [{"id": "b", "type": "video", "clips": [
                {"id": "cycle", "sequence_id": "seq_b" if self_cycle else "seq_a", "duration": 1},
            ]}]
            with self.subTest(self_cycle=self_cycle), self.assertRaisesRegex(ValidationError, "cycle"):
                Project.model_validate(data)
        for linked_from_main in (True, False):
            sequences = [{"id": f"s{i}", "name": "S", "tracks": [{"id": f"v{i}", "type": "video", "clips": [
                {"id": f"c{i}", "duration": 1, **({"sequence_id": f"s{i + 1}"} if i < 3 else {"source_id": "final"})},
            ]}]} for i in range(4)]
            root = [{"id": "main", "type": "video", "clips": [{"id": "nest", "sequence_id": "s1", "duration": 1}]}] if linked_from_main else []
            with self.subTest(linked_from_main=linked_from_main), self.assertRaisesRegex(ValidationError, "depth is 2"):
                Project.model_validate({"tracks": root, "sequences": sequences})

    def test_full_duration_empty_solo_changed_child_and_stale_descriptors(self) -> None:
        for value in (0.9, 1.1, 1 + 1e-8):
            data = nested_data()
            data["tracks"][0]["clips"][0]["duration"] = value
            with self.subTest(duration=value), self.assertRaisesRegex(ValidationError, "duration must equal"):
                Project.model_validate(data)
        for mode in ("empty", "hidden", "changed"):
            data = nested_data()
            child = data["sequences"][0]["tracks"][0]
            if mode == "empty":
                child["clips"] = []
            elif mode == "hidden":
                child["hidden"] = True
            else:
                child["clips"][0]["duration"] = 0.5
            with self.subTest(mode=mode), self.assertRaises(ValidationError):
                Project.model_validate(data)
        data = nested_data()
        data["sequences"][0]["tracks"].append({**video_track("long", duration=5), "hidden": True})
        Project.model_validate(data)  # Hidden child extent is not its active duration.
        data["sequences"][0]["tracks"][0]["solo"] = True
        data["sequences"][0]["tracks"][1]["hidden"] = False
        Project.model_validate(data)  # Child solo is resolved in that container only.
        data["tracks"][0]["clips"][0]["start"] = 119
        self.assertEqual(Project.model_validate(data).duration, 120)
        data["tracks"][0]["clips"][0]["start"] = 119.01
        with self.assertRaises(ValidationError):
            Project.model_validate(data)

    def test_nested_overlap_rejected_without_changing_legacy_layer_overlaps(self) -> None:
        for hidden in (False, True):
            data = nested_data()
            data["tracks"][0]["hidden"] = hidden
            data["tracks"][0]["clips"].append({"id": "same_layer", "source_id": "final", "start": 0.5, "duration": 1})
            with self.subTest(hidden=hidden), self.assertRaisesRegex(ValidationError, "cannot overlap"):
                Project.model_validate(data)
        data = nested_data()
        data["tracks"][0]["clips"].append({"id": "touching", "source_id": "final", "start": 1, "duration": 1})
        Project.model_validate(data)
        ordinary = project_data()
        ordinary["tracks"][0]["clips"].append({"id": "legacy_overlap", "source_id": "final", "start": 0.5, "duration": 1})
        Project.model_validate(ordinary)

    def test_inactive_transition_uses_shared_workspace_frame_minimum(self) -> None:
        data = transition_child_data()
        data["tracks"] = []
        data["sequences"][0]["tracks"][0]["hidden"] = True
        right = data["sequences"][0]["tracks"][0]["clips"][1]
        right.update(start=0.99)
        right["transition_in"]["duration"] = 0.01
        with self.assertRaisesRegex(ValidationError, "workspace frame"):
            Project.model_validate(data)

    def test_public_limits_and_partial_tools_match_the_frontend_contract(self) -> None:
        caps = capabilities()
        tools = {tool["id"]: tool for tool in caps["tools"]}
        self.assertEqual(len(tools), 113)
        for name in ("tlPick", "nest", "camNest"):
            self.assertTrue(tools[name]["available"])
            self.assertEqual(tools[name]["classification"], "partial")
        for name, value in {"sequences": 4, "tracks": 8, "total_tracks": 32, "clips": 64, "nesting_depth": 2,
                            "expanded_tracks": 8, "expanded_clips": 64, "decoder_inputs": 16,
                            "markers_per_timeline": 100, "duration_seconds": 120, "output_bytes": MAX_BYTES}.items():
            self.assertEqual(caps["limits"][name], value)
        self.assertEqual(caps["sequence_contract"]["nest_fields"], ["id", "sequence_id", "start", "duration", "mute"])
        self.assertIn("not isolated", caps["sequence_contract"]["limitations"])
        self.assertIn("Sequence", caps["project_schema"]["$defs"])


class StudioSequenceEvaluationTests(unittest.TestCase):
    def test_second_level_offsets_local_envelopes_mute_and_snapshot_detachment(self) -> None:
        data = nested_data(depth=2)
        data["sequences"][1]["tracks"][0]["clips"][0].update(
            start=0.25, trim=0.2, speed=2, reverse=True, volume=0.5, audio_effect="denoise", fade_in=0.1,
            keyframes={"x": [{"time": 0, "value": -0.2}, {"time": 1, "value": 0.2}]})
        data["sequences"][0]["tracks"][0]["clips"][0].update(start=0.5, duration=1.25, mute=True)
        data["tracks"][0]["clips"][0].update(start=0.25, duration=1.75)
        project = Project.model_validate(data)
        before = project.model_dump()
        evaluated = evaluate_project(project)
        leaf = evaluated.tracks[0].clips[0]
        self.assertEqual(evaluated.duration, 2)
        self.assertEqual(leaf.start, 1)
        self.assertTrue(leaf.mute)
        original = project.sequences[1].tracks[0].clips[0].model_dump()
        self.assertEqual(leaf.model_dump(), {**original, "id": leaf.id, "start": 1, "mute": True})
        self.assertEqual(evaluated.instances[-1].path, ("nest_a", "nest_b"))
        self.assertEqual(evaluate_project(project), evaluated)
        leaf.brightness = 0.25
        self.assertEqual(project.model_dump(), before)

    def test_per_timeline_solo_does_not_hide_other_parent_layers(self) -> None:
        data = nested_data()
        data["tracks"].insert(0, video_track("below"))
        data["tracks"].append(video_track("above"))
        child = data["sequences"][0]["tracks"]
        child[0]["solo"] = True
        child.append(video_track("child_excluded"))
        evaluated = evaluate_project(Project.model_validate(data))
        self.assertEqual(len(evaluated.tracks), 3)
        self.assertEqual([track.clips[0].source_id for track in evaluated.tracks], ["final", "video_only", "final"])
        self.assertEqual([track.id for track in evaluated.tracks][::2], ["below", "above"])
        self.assertTrue(all(not track.hidden and not track.solo for track in evaluated.tracks))
        data["tracks"][1]["solo"] = True
        self.assertEqual(len(evaluate_project(Project.model_validate(data)).tracks), 1)

    def test_transition_remap_is_per_occurrence_collision_safe_and_clock_preserving(self) -> None:
        data = transition_child_data()
        data["markers"] = [{"id": "expanded_clip_0", "time": 0}]
        project = Project.model_validate(data)
        evaluated = evaluate_project(project)
        ids = [track.id for track in evaluated.tracks] + [clip.id for track in evaluated.tracks for clip in track.clips]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertNotIn("expanded_clip_0", ids)
        for offset, track in zip((0.25, 2), evaluated.tracks, strict=True):
            left, right = track.clips
            self.assertIsNotNone(right.transition_in)
            assert right.transition_in is not None
            self.assertEqual(right.transition_in.left_clip_id, left.id)
            self.assertEqual((left.start, right.start, left.trim, right.trim), (offset, offset + 0.5, 0, 0))
        summary = transition_summary(project)
        assert summary is not None
        self.assertEqual([item["start"] for item in summary["items"]], [0.75, 2.5])
        self.assertEqual(evaluated.decoder_inputs, 4)

    def test_expansion_counts_occurrences_and_enforces_separate_layer_clip_decoder_caps(self) -> None:
        good = evaluate_project(Project.model_validate(repeated_data()))
        self.assertEqual((len(good.tracks), good.clip_count, good.decoder_inputs), (2, 16, 16))
        for data, message in ((repeated_data(clips=9), "decoder budget"),
                              (repeated_data(clips=33, text=True), "clip budget"),
                              (repeated_data(clips=1, copies=9), "layer budget")):
            project = Project.model_validate(data)  # Stored limits are independent from active render cost.
            before = project.model_dump()
            with self.subTest(message=message), self.assertRaisesRegex(RenderError, message):
                evaluate_project(project)
            self.assertEqual(project.model_dump(), before)
            project.active_sequence_id = "child"
            self.assertEqual(evaluate_project(project).clip_count, len(project.sequences[0].tracks[0].clips))

    def test_empty_ui_lanes_have_no_expanded_cost_and_internal_list_edits_revalidate(self) -> None:
        data = nested_data()
        data["tracks"].extend({"id": f"empty_{i}", "type": "video"} for i in range(7))
        project = Project.model_validate(data)
        self.assertEqual(len(evaluate_project(project).tracks), 1)
        project.tracks[0].clips[0] = Clip.model_construct(id="unsafe", sequence_id="seq_a", duration=1, speed=2)
        with self.assertRaisesRegex(ValidationError, "identity composition"):
            evaluate_project(project)

    def test_nested_text_export_uses_global_offsets_not_parent_descriptors(self) -> None:
        data = nested_data()
        data["tracks"][0]["clips"][0].update(start=0.5, duration=1.25)
        data["sequences"][0]["tracks"] = [{"id": "captions", "type": "text", "clips": [
            {"id": "caption", "start": 0.25, "duration": 1, "text": "Actual nested caption", "text_animation": "fade"},
        ]}]
        project = Project.model_validate(data)
        srt = subtitle_document(project, ExportOptions(format="srt"))
        self.assertIn("00:00:00,750 --> 00:00:01,750", srt)
        self.assertIn("Actual nested caption", srt)
        self.assertNotIn("main_mark", srt)
        ass = subtitle_document(project, ExportOptions(format="ass"), disclosure=True)
        self.assertEqual(ass.count("AI生成示意画面"), 1)
        self.assertIn("Dialogue: 100,0:00:00.00,0:00:01.75", ass)
        summary = sequence_summary(project, evaluate_project(project))
        assert summary is not None
        self.assertEqual(summary["mode"], "full_length_identity_flatten")
        self.assertEqual(summary["decoder_inputs"], 0)

    def test_frontend_clone_remaps_all_structural_ids_and_transition_references(self) -> None:
        data = transition_child_data()
        data["active_sequence_id"] = "child"
        data["sequences"][0]["markers"] = [{"id": "child_marker", "time": 0.4}]
        original = Project.model_validate(data)
        cloned = copy.deepcopy(data["sequences"][0])
        cloned.update(id="clone", name="Copy")
        clip_ids = {clip["id"]: "clone_" + clip["id"] for track in cloned["tracks"] for clip in track["clips"]}
        for track in cloned["tracks"]:
            track["id"] = "clone_" + track["id"]
            for clip in track["clips"]:
                clip["id"] = clip_ids[clip["id"]]
                if clip.get("transition_in"):
                    clip["transition_in"]["left_clip_id"] = clip_ids[clip["transition_in"]["left_clip_id"]]
        for marker in cloned["markers"]:
            marker["id"] = "clone_" + marker["id"]
        data["sequences"].append(cloned)
        data["active_sequence_id"] = "clone"
        result = Project.model_validate(data)
        self.assertEqual(result.sequences[0], original.sequences[0])
        self.assertEqual(result.tracks, original.tracks)
        self.assertEqual(evaluate_project(result).decoder_inputs, 2)
        transition = result.sequences[1].tracks[0].clips[1].transition_in
        assert transition is not None
        self.assertEqual(transition.left_clip_id, "clone_left")
        cloned["tracks"][0]["clips"][1]["transition_in"]["left_clip_id"] = "left"
        with self.assertRaisesRegex(ValidationError, "SAME track"):
            Project.model_validate(data)


class StudioSequenceActionTests(unittest.TestCase):
    def test_actions_only_search_selected_tracks_clips_and_markers(self) -> None:
        project = Project.model_validate(project_data(active="seq_a"))
        moved = apply_action(project, Action(expected_revision=0, op="move", clip_id="a_clip", at=0.5))
        self.assertEqual(moved.sequences[0].tracks[0].clips[0].start, 0.5)
        self.assertEqual(moved.tracks, project.tracks)
        self.assertEqual(moved.sequences[1], project.sequences[1])
        marked = apply_action(project, Action(expected_revision=0, op="marker", at=0.75, new_id="new_marker"))
        self.assertEqual(marked.markers, project.markers)
        self.assertEqual(marked.sequences[0].markers[-1].id, "new_marker")
        cleared = apply_action(marked, Action(expected_revision=0, op="clear_markers"))
        self.assertEqual(cleared.sequences[0].markers, [])
        self.assertEqual(cleared.sequences[1].markers, project.sequences[1].markers)
        for op, fields, status in (("delete", {"clip_id": "main_clip"}, 404), ("track", {"track_id": "main", "locked": True}, 404),
                                   ("move", {"clip_id": "a_clip", "track_id": "main", "at": 0}, 422),
                                   ("duplicate", {"clip_id": "a_clip", "at": 1, "new_id": "b_clip"}, 422)):
            with self.subTest(op=op), self.assertRaises(HTTPException) as caught:
                apply_action(project, Action.model_validate({"expected_revision": 0, "op": op, **fields}))
            self.assertEqual(caught.exception.status_code, status)

    def test_nested_actions_never_split_trim_slip_or_silently_resize_children(self) -> None:
        project = Project.model_validate(nested_data())
        for op, fields in (("split", {"at": 0.5, "new_id": "right"}), ("trim", {"trim": 0, "duration": 0.5}),
                           ("ripple_trim", {"trim": 0, "duration": 0.5}), ("slip", {"trim": 0}),
                           ("roll", {"at": 0.5}), ("slide", {"at": 0.5})):
            before = project.model_dump()
            with self.subTest(op=op), self.assertRaisesRegex(HTTPException, "full child duration"):
                apply_action(project, Action.model_validate({"expected_revision": 0, "op": op, "clip_id": "nest_a", **fields}))
            self.assertEqual(project.model_dump(), before)
        duplicated = apply_action(project, Action(expected_revision=0, op="duplicate", clip_id="nest_a", at=1, new_id="nest_again"))
        self.assertEqual(evaluate_project(duplicated).decoder_inputs, 2)
        removed = apply_action(duplicated, Action(expected_revision=0, op="ripple_delete", clip_id="nest_a"))
        self.assertEqual(removed.tracks[0].clips[0].start, 0)
        self.assertEqual(removed.sequences, project.sequences)
        data = project.model_dump()
        data["tracks"][0]["clips"].insert(0, {"id": "before", "source_id": "final", "duration": 1})
        data["tracks"][0]["clips"][1]["start"] = 1
        with self.assertRaisesRegex(HTTPException, "cannot resize nested"):
            apply_action(Project.model_validate(data), Action(expected_revision=0, op="roll", clip_id="before", at=0.5))

    def test_locks_protect_all_containers_order_and_transitive_child_content(self) -> None:
        project = Project.model_validate(project_data())
        project.sequences[0].tracks[0].locked = True
        for mode in ("edit", "unlock_in_save", "reorder", "relocate", "delete"):
            data = project.model_dump()
            track = data["sequences"][0]["tracks"][0]
            if mode == "edit":
                track["clips"][0]["brightness"] = 0.1
            elif mode == "unlock_in_save":
                track["locked"] = False
            elif mode == "reorder":
                data["sequences"][0]["tracks"].insert(0, video_track("inserted"))
            else:
                data["sequences"][0]["tracks"].clear()
                if mode == "relocate":
                    data["tracks"].append(track)
            with self.subTest(mode=mode), self.assertRaises(HTTPException) as caught:
                enforce_locks(project, Project.model_validate(data))
            self.assertEqual(caught.exception.status_code, 409)
        nested = Project.model_validate(nested_data(depth=2, active="seq_b"))
        nested.tracks[0].locked = True
        changed = nested.model_dump()
        changed["sequences"][1]["tracks"][0]["clips"][0]["brightness"] = 0.2
        with self.assertRaisesRegex(HTTPException, "referencing nested track"):
            enforce_locks(nested, Project.model_validate(changed))
        with self.assertRaisesRegex(HTTPException, "referencing nested track"):
            apply_action(nested, Action(expected_revision=0, op="track", track_id="b", color="gold"))
        selected = Project.model_validate({**nested.model_dump(), "active_sequence_id": None})
        enforce_locks(nested, selected)  # Selection itself is a saved preference, not an unlock.
        unlocked = apply_action(selected, Action(expected_revision=0, op="track", track_id="main", locked=False))
        enforce_locks(unlocked, Project.model_validate(changed | {"tracks": unlocked.model_dump()["tracks"]}))

    def test_deleting_referenced_sequence_requires_separate_unlink_and_cross_container_transitions_stay_bound(self) -> None:
        old = Project.model_validate(nested_data())
        candidate = Project.model_validate({"sequences": [old.sequences[1].model_dump()]})
        with self.assertRaisesRegex(HTTPException, "separate save"):
            enforce_sequence_deletions(old, candidate)
        unlinked = Project.model_validate({**old.model_dump(), "tracks": []})
        enforce_sequence_deletions(old, unlinked)
        enforce_sequence_deletions(unlinked, candidate)
        data = transition_child_data()
        old = Project.model_validate(data)
        data["tracks"] = data["sequences"][0]["tracks"]
        data["sequences"] = []
        with self.assertRaisesRegex(HTTPException, "remove transition first"):
            enforce_transition_endpoints(old, Project.model_validate(data))


class _SequenceAPIHarness(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="studio-sequences-")
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.root = self.data / "task"
        self.root.mkdir()
        self.prepare_sources()
        self.record = SimpleNamespace(task_id="task", task_dir=self.root, uploads=[], status="done", revision=0)
        self.publication_originals = seed_synthetic_publication(self, self.record)
        self.originals = {path: path.read_bytes() for path in self.root.iterdir()
                  if path.is_file() and path.name != "publication_checks.json"}
        self.settings = SimpleNamespace(data_dir=self.data, media_command_timeout_seconds=60, minimum_free_disk_bytes=0)
        self.manager = SimpleNamespace(_draining=False, get=lambda task_id: self.record if task_id == "task" else None)

        async def authorize(request: Any, task_id: str, write: bool = False) -> Any:
            if task_id != "task" or request.headers.get("X-Token") != "synthetic-test":
                raise HTTPException(403, "denied")
            if write and request.headers.get("X-CSRF") != "synthetic-test":
                raise HTTPException(403, "CSRF")
            return self.record

        self.authorize = authorize
        self.app = FastAPI()
        self.app.include_router(create_studio_router(self.settings, self.manager, authorize))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False),
                                       base_url="http://testserver", headers={"X-Token": "synthetic-test", "X-CSRF": "synthetic-test"})
        self.prefix = "/api/tasks/task/studio"

    def prepare_sources(self) -> None:
        for name in ("final.mp4", "video_only.mp4"):
            (self.root / name).write_bytes(("metadata-only-not-decodable:" + name).encode("ascii"))

    def owned_jobs(self) -> list[asyncio.Task[Any]]:
        return [task for key, task in list(_RUNNING.items()) if Path(key).is_relative_to(self.root)]

    async def asyncTearDown(self) -> None:
        try:
            jobs = self.owned_jobs()
            for job in jobs:
                job.cancel()
            await asyncio.gather(*jobs, return_exceptions=True)
            await self.client.aclose()
            self.assertFalse(studio_task_busy(self.root))
            self.assertNotIn(str(self.root), _DISK_RESERVATIONS)
            for path, original in self.originals.items():
                self.assertEqual(path.read_bytes(), original)
        finally:
            self.temp.cleanup()

    async def save(self, data: dict[str, Any], revision: int = 0, route: str = "/project") -> httpx.Response:
        return await self.client.post(self.prefix + route, json={"expected_revision": revision, "project": data})

    async def action(self, revision: int, op: str, **fields: Any) -> httpx.Response:
        return await self.client.post(self.prefix + "/actions", json={"expected_revision": revision, "op": op, **fields})

    def state_bytes(self) -> bytes:
        return (self.root / "studio/state.json").read_bytes()

    async def finish(self, response: httpx.Response) -> dict[str, Any]:
        self.assertEqual(response.status_code, 202, response.text)
        await asyncio.wait_for(asyncio.gather(*self.owned_jobs()), 90)
        job = await self.client.get(self.prefix + "/jobs/" + response.json()["id"])
        self.assertEqual(job.status_code, 200, job.text)
        return job.json()


class StudioSequenceRouteTests(_SequenceAPIHarness):
    async def test_saved_selection_reopen_import_undo_redo_and_active_only_subtitles(self) -> None:
        response = await self.save(project_data())
        self.assertEqual(response.status_code, 200, response.text)
        main = response.json()["project"]
        changed = copy.deepcopy(main)
        changed["active_sequence_id"] = "seq_a"
        selected = await self.save(changed, 1)
        self.assertEqual(selected.status_code, 200, selected.text)
        self.assertEqual(selected.json()["revision"], 2)
        self.assertEqual((await self.action(2, "undo")).json()["project"], main)
        self.assertEqual((await self.action(3, "redo")).json()["project"], changed)
        app = FastAPI()
        app.include_router(create_studio_router(self.settings, self.manager, self.authorize))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=self.client.headers) as reopened:
            saved = await reopened.get(self.prefix + "/project/export")
        self.assertEqual(saved.json()["project"], changed)
        self.assertEqual(saved.json()["revision"], 4)
        imported = await self.save(saved.json()["project"], 4, "/project/import")
        self.assertEqual(imported.status_code, 200, imported.text)
        subtitles = await self.client.post(self.prefix + "/subtitles/import", json={
            "expected_revision": 5, "track_id": "imported_captions", "srt": "1\n00:00:00,100 --> 00:00:00,900\nOnly A",
        })
        self.assertEqual(subtitles.status_code, 200, subtitles.text)
        result = subtitles.json()["project"]
        self.assertEqual(result["tracks"], main["tracks"])
        self.assertEqual(result["sequences"][1], main["sequences"][1])
        self.assertEqual(result["sequences"][0]["tracks"][-1]["id"], "imported_captions")
        self.assertEqual((await self.action(6, "undo")).json()["project"], changed)
        self.assertEqual([row["op"] for row in read_state(self.root)["audit"]],
                         ["project.save", "project.save", "undo", "redo", "project.save", "subtitles.import", "undo"])

    async def test_inactive_missing_sources_images_luts_and_shared_references_are_atomic(self) -> None:
        self.assertEqual((await self.save(project_data())).status_code, 200)
        before = self.state_bytes()
        for sequence_index in (0, 1):
            for fields in ({"source_id": "foreign"}, {"source_id": "image_" + "f" * 24, "mute": True},
                           {"lut_id": "lut_" + "f" * 24}):
                data = project_data()
                data["sequences"][sequence_index]["tracks"][0]["hidden"] = True
                data["sequences"][sequence_index]["tracks"][0]["clips"][0].update(fields)
                for route in ("/project", "/project/import"):
                    response = await self.save(data, 1, route)
                    self.assertEqual(response.status_code, 422, response.text)
                    self.assertEqual(self.state_bytes(), before)
        for fields in ({"assets": [{"source_id": "foreign"}]},
                       {"workspace": {"source_marks": [{"source_id": "foreign", "in_point": 0, "out_point": 1}]}}):
            response = await self.save({**project_data(), **fields}, 1)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(self.state_bytes(), before)
        for header in ("X-Token", "X-CSRF"):
            denied = await self.client.post(self.prefix + "/project/import", headers={header: "denied"},
                                           json={"expected_revision": 1, "project": project_data(active="seq_b")})
            self.assertEqual(denied.status_code, 403)
        self.assertEqual(self.state_bytes(), before)

    async def test_invalid_child_history_and_selection_fail_before_mutating_undo_redo(self) -> None:
        self.assertEqual((await self.save(nested_data())).status_code, 200)
        self.assertEqual((await self.action(1, "marker", new_id="marker", at=0.5)).status_code, 200)
        baseline = read_state(self.root)
        corruptions = []
        for mode in ("source", "lut", "selection", "duration", "cycle"):
            data = Project.model_validate(nested_data()).model_dump()
            if mode == "source":
                data["sequences"][1]["tracks"][0]["clips"][0]["source_id"] = "foreign"
            elif mode == "lut":
                data["sequences"][1]["tracks"][0]["clips"][0]["lut_id"] = "lut_" + "f" * 24
            elif mode == "selection":
                data["active_sequence_id"] = "missing"
            elif mode == "duration":
                data["sequences"][0]["tracks"][0]["clips"][0]["duration"] = 0.5
            else:
                data["sequences"][0]["tracks"][0]["clips"] = [{"id": "cycle", "sequence_id": "seq_a", "duration": 1}]
            corruptions.append(data)
        for operation, stack in (("undo", "past"), ("redo", "future")):
            for data in corruptions:
                state = copy.deepcopy(baseline)
                state[stack] = [data]
                write_state(self.root, state)
                before = self.state_bytes()
                response = await self.action(2, operation)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.state_bytes(), before)

    async def test_stale_child_duration_rejected_until_every_ancestor_explicitly_updated(self) -> None:
        data = nested_data(depth=2, active="seq_b")
        self.assertEqual((await self.save(data)).status_code, 200)
        before = self.state_bytes()
        resized = await self.action(1, "trim", clip_id="b_clip", trim=0, duration=0.5)
        self.assertEqual(resized.status_code, 422, resized.text)
        self.assertIn("update all references", resized.text)
        self.assertEqual(self.state_bytes(), before)
        data["sequences"][1]["tracks"][0]["clips"][0]["duration"] = 0.5
        data["sequences"][0]["tracks"][0]["clips"][0]["duration"] = 0.5
        self.assertEqual((await self.save(data, 1)).status_code, 422)  # Main still has a stale descriptor.
        self.assertEqual(self.state_bytes(), before)
        data["tracks"][0]["clips"][0]["duration"] = 0.5
        saved = await self.save(data, 1)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(evaluate_project(Project.model_validate(saved.json()["project"])).duration, 0.5)
        undone = await self.action(2, "undo")
        self.assertEqual(undone.json()["project"], Project.model_validate(nested_data(depth=2, active="seq_b")).model_dump())

    async def test_locks_reordering_and_reference_deletion_cannot_be_bypassed_by_import(self) -> None:
        data = nested_data(depth=2, active="seq_b")
        data["tracks"][0]["locked"] = True
        self.assertEqual((await self.save(data)).status_code, 200)
        before = self.state_bytes()
        changed = copy.deepcopy(data)
        changed["sequences"][1]["tracks"][0]["clips"][0]["brightness"] = 0.1
        self.assertEqual((await self.save(changed, 1, "/project/import")).status_code, 409)
        self.assertEqual((await self.action(1, "track", track_id="b", color="gold")).status_code, 409)
        imported = await self.client.post(self.prefix + "/subtitles/import", json={
            "expected_revision": 1, "track_id": "captions", "srt": "1\n00:00:00,000 --> 00:00:00,500\nCaption",
        })
        self.assertEqual(imported.status_code, 409, imported.text)
        self.assertEqual(self.state_bytes(), before)
        data["active_sequence_id"] = None
        self.assertEqual((await self.save(data, 1)).status_code, 200)
        unlocked = await self.action(2, "track", track_id="main", locked=False)
        self.assertEqual(unlocked.status_code, 200)
        changed = unlocked.json()["project"]
        changed["tracks"] = []
        changed["sequences"] = []
        self.assertEqual((await self.save(changed, 3, "/project/import")).status_code, 422)
        # Explicitly unlink EVERY old descriptor, then separately remove sequences.
        unlinked = unlocked.json()["project"]
        unlinked["tracks"][0]["clips"] = []
        unlinked["sequences"][0]["tracks"][0]["clips"] = []
        self.assertEqual((await self.save(unlinked, 3)).status_code, 200)
        self.assertEqual((await self.save(changed, 4)).status_code, 200)

    async def test_conflicts_and_write_faults_preserve_all_timeline_history_bytes(self) -> None:
        responses = await asyncio.gather(self.save(project_data(active="seq_a")), self.save(project_data(active="seq_b")))
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        before = self.state_bytes()
        self.assertEqual((await self.save(project_data(), 0)).status_code, 409)
        with patch("backend.studio.write_json_atomic", side_effect=OSError("synthetic write failure")):
            failed = await self.action(1, "marker", new_id="new", at=0.1)
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(self.state_bytes(), before)

    async def test_render_admission_uses_expanded_budgets_before_job_or_reservation(self) -> None:
        data = repeated_data(clips=9)
        self.assertEqual((await self.save(data)).status_code, 200)
        before = self.state_bytes()
        with patch("backend.studio.render_project", new=AsyncMock()) as render:
            response = await self.client.post(self.prefix + "/render", json={"expected_revision": 1})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertIn("decoder budget", response.text)
            render.assert_not_awaited()
        self.assertFalse((self.root / "studio/outputs").exists())
        self.assertFalse((self.root / "studio/jobs").exists())
        self.assertFalse(studio_task_busy(self.root))
        self.assertNotIn(str(self.root), _DISK_RESERVATIONS)
        self.assertEqual(self.state_bytes(), before)

    async def test_shared_disk_job_history_and_json_limits_still_apply(self) -> None:
        self.assertEqual((await self.save(nested_data())).status_code, 200)
        before = self.state_bytes()
        for constant, value, status in (("MAX_TASK_BYTES", 1, 507), ("MAX_JOBS", 0, 429)):
            with patch("backend.studio." + constant, value):
                response = await self.client.post(self.prefix + "/render", json={"expected_revision": 1})
            self.assertEqual(response.status_code, status, response.text)
        with patch("backend.studio.MAX_HISTORY", 1):
            self.assertEqual((await self.save(nested_data(active="seq_a"), 1)).status_code, 409)
        with patch("backend.studio.MAX_JSON", 1):
            self.assertEqual((await self.save(nested_data(), 1)).status_code, 413)
        self.assertEqual(self.state_bytes(), before)
        self.assertNotIn(str(self.root), _DISK_RESERVATIONS)

    async def test_nested_final_picture_burned_subtitle_gate_uses_actual_leaves(self) -> None:
        data = nested_data(depth=2)
        self.assertEqual((await self.save(data)).status_code, 200)
        before = self.state_bytes()
        with patch("backend.studio.render_project", new=AsyncMock()) as render:
            rejected = await self.client.post(self.prefix + "/render", json={"expected_revision": 1,
                "options": {"subtitles": "none", "resolution": 360}})
            self.assertEqual(rejected.status_code, 422, rejected.text)
            self.assertIn("burned subtitles", rejected.text)
            render.assert_not_awaited()
        self.assertEqual(self.state_bytes(), before)

    async def test_qc_and_task_wide_disclosure_gate_even_if_generated_sequence_is_inactive(self) -> None:
        self.assertEqual((await self.save(nested_data())).status_code, 200)
        (self.root / "generated_media_disclosure.json").write_text(json.dumps(generated_manifest()), encoding="utf-8")
        confirm_synthetic_publication(self, self.record, generated=True)
        before = self.state_bytes()
        with patch("backend.studio.render_project", new=AsyncMock()) as render:
            for format_ in ("wav", "mp3", "srt", "ass"):
                response = await self.client.post(self.prefix + "/render", json={"expected_revision": 1, "options": {"format": format_}})
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

    async def test_immutable_job_contains_all_sequence_assets_and_cancellation_cleans_only_owned_work(self) -> None:
        image = asset_fixtures.install_canonical(self.root, asset_fixtures.png(), "image")
        lut = asset_fixtures.install_canonical(self.root, asset_fixtures.cube(), "lut")
        data = nested_data()
        data["sequences"][1]["tracks"][0].update(hidden=True, clips=[
            {"id": "hidden_image", "source_id": image.id, "lut_id": lut.id, "duration": 1, "mute": True},
        ])
        data["assets"] = [{"source_id": "final", "rating": 4}]
        self.assertEqual((await self.save(data)).status_code, 200)
        initial = Project.model_validate(data).model_dump()
        entered = asyncio.Event()
        captured: dict[str, Any] = {}

        async def hold(project: Project, _options: Any, sources: dict[str, Path], work: Path, *, luts: dict[str, Path], **_kwargs: Any) -> dict[str, Any]:
            captured.update(project=project.model_dump(), sources=set(sources), luts=set(luts))
            (work / "partial.mp4").write_bytes(b"explicit cancellation sentinel, not rendered media")
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("cancellation hold unexpectedly released")

        with patch("backend.studio.render_project", side_effect=hold):
            response = await self.client.post(self.prefix + "/render", json={"expected_revision": 1})
            self.assertEqual(response.status_code, 202, response.text)
            await asyncio.wait_for(entered.wait(), 5)
            job_id = response.json()["id"]
            work = self.root / "studio/outputs/r1" / job_id
            snapshot = json.loads((work / "snapshot.json").read_text(encoding="utf-8"))
            self.assertEqual(captured["project"], initial)
            self.assertEqual(captured["sources"], {"final", "video_only", image.id})
            self.assertEqual(captured["luts"], {lut.id})
            self.assertTrue({"final", "video_only", image.id, lut.id} <= set(snapshot["sources"]))
            self.assertEqual(snapshot["asset_sha256"], {image.id: image.sha256, lut.id: lut.sha256})
            self.assertEqual(response.json()["sequence_semantics"]["source_ids"], ["video_only"])
            self.assertEqual(_DISK_RESERVATIONS[str(self.root)], MAX_BYTES * 2)  # No nested intermediate reserve.
            changed = copy.deepcopy(initial)
            changed["active_sequence_id"] = "seq_a"
            changed["sequences"][0]["tracks"][0]["clips"][0]["brightness"] = 0.1
            self.assertEqual((await self.save(changed, 1)).status_code, 200)  # In-flight snapshot remains detached.
            self.assertEqual(captured["project"], initial)
            self.assertEqual((await self.client.post(self.prefix + "/render", json={"expected_revision": 2})).status_code, 409)
            cancelled = await self.client.delete(self.prefix + "/jobs/" + job_id)
        self.assertEqual(cancelled.json()["state"], "cancelled")
        self.assertFalse(work.exists())
        self.assertNotIn(str(self.root), _BUSY)
        self.assertNotIn(str(self.root), _DISK_RESERVATIONS)
        self.assertEqual((await self.client.get(self.prefix + "/outputs/" + job_id)).status_code, 409)
        self.assertEqual(read_state(self.root)["project"], changed)
        self.assertEqual(read_state(self.root)["revision"], 2)

    async def test_inactive_original_source_change_invalidates_job_not_just_root_sources(self) -> None:
        self.assertEqual((await self.save(nested_data())).status_code, 200)
        entered, release = asyncio.Event(), asyncio.Event()

        async def hold(_project: Any, _options: Any, sources: dict[str, Path], _work: Any, **_kwargs: Any) -> dict[str, Any]:
            self.assertIn("final", sources)  # Only in inactive seq_b, not active seq_a.
            entered.set()
            await release.wait()
            return {"file": "not-media.mp4", "duration": 1}  # Failure sentinel; NOT a successful render double.

        with patch("backend.studio.render_project", side_effect=hold):
            response = await self.client.post(self.prefix + "/render", json={"expected_revision": 1})
            self.assertEqual(response.status_code, 202, response.text)
            await asyncio.wait_for(entered.wait(), 5)
            path = self.root / "final.mp4"
            try:
                path.write_bytes(path.read_bytes() + b"changed-in-owned-temp")
                release.set()
                job = await self.finish(response)
            finally:
                path.write_bytes(self.originals[path])
        self.assertEqual(job["state"], "failed", job)
        self.assertIn("changed", job["error"])
        self.assertIsNone(job["output_id"])
        self.assertFalse((self.root / "studio/outputs/r1" / job["id"]).exists())

    async def test_inactive_lut_same_stat_tamper_rejected_after_render(self) -> None:
        lut = asset_fixtures.install_canonical(self.root, asset_fixtures.cube(), "lut")
        data = nested_data()
        data["sequences"][1]["tracks"][0]["clips"][0]["lut_id"] = lut.id
        self.assertEqual((await self.save(data)).status_code, 200)

        async def tamper(_project: Any, _options: Any, _sources: Any, _work: Any, *, luts: dict[str, Path], **_kwargs: Any) -> dict[str, Any]:
            path = luts[lut.id]
            stat = path.stat()
            path.write_bytes(path.read_bytes().replace(b"0 0 0\n", b"1 0 0\n", 1))
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            return {"file": "not-media.mp4", "duration": 1}  # Integrity-failure sentinel only.

        with patch("backend.studio.render_project", side_effect=tamper):
            job = await self.finish(await self.client.post(self.prefix + "/render", json={"expected_revision": 1}))
        self.assertEqual(job["state"], "failed", job)
        self.assertIsNone(job["output_id"])
        self.assertNotIn(str(self.root), json.dumps(job))
        self.assertFalse((self.root / "studio/outputs/r1" / job["id"]).exists())


class StudioSequenceRendererContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_declared_ids_rejected_before_srt_early_return_or_commands(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sequence-reference-") as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"reference-only-never-decoded")
            base = project_data()
            base["tracks"] = [{"id": "text", "type": "text", "clips": [{"id": "caption", "duration": 1, "text": "Caption"}]}]
            for fields, maps, lut_maps in (
                ({"source_id": "foreign"}, {"final": source, "video_only": source}, {}),
                ({"lut_id": "lut_" + "f" * 24}, {"final": source, "video_only": source}, {}),
                ({"source_id": "image_" + "f" * 24, "mute": True}, {"final": source, "video_only": source, "image_" + "f" * 24: source}, {}),
            ):
                data = copy.deepcopy(base)
                data["sequences"][1]["tracks"][0]["hidden"] = True
                data["sequences"][1]["tracks"][0]["clips"][0].update(fields)
                project = Project.model_validate(data)
                with patch("backend.studio_render.run_logged_command", new=AsyncMock()) as command:
                    with self.assertRaises(RenderError):
                        await render_project(project, ExportOptions(format="srt"), maps, root / "work", luts=lut_maps)
                    command.assert_not_awaited()
                self.assertFalse((root / "work/output.srt").exists())

    async def test_render_revalidates_mutated_graph_and_leaves_before_any_files_or_commands(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sequence-revalidate-") as directory:
            root = Path(directory)
            project = Project.model_validate(nested_data())
            project.sequences[0].tracks[0].clips[0].duration = 0.5  # List-contained edits cannot validate the parent.
            with patch("backend.studio_render.run_logged_command", new=AsyncMock()) as command:
                with self.assertRaisesRegex(ValidationError, "duration must equal"):
                    await render_project(project, ExportOptions(), {}, root / "work")
                command.assert_not_awaited()
            self.assertFalse((root / "work").exists())

    async def test_nested_source_bounds_and_output_size_limits_are_still_enforced(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sequence-bounds-") as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"probe-contract-only")
            info = {"duration": 1, "streams": [{"codec_type": "video", "width": 160, "height": 90, "avg_frame_rate": "30/1"}]}
            data = nested_data()
            data["sequences"][0]["tracks"][0]["clips"][0]["trim"] = 0.5
            with patch("backend.studio_render.probe", new=AsyncMock(return_value=info)), \
                    patch("backend.studio_render.shutil.which", return_value="contract-only"), \
                    patch("backend.studio_render.run_logged_command", new=AsyncMock()) as command:
                with self.assertRaisesRegex(RenderError, "source duration"):
                    await render_project(Project.model_validate(data), ExportOptions(resolution=360), {"final": source, "video_only": source}, root / "eof")
                with patch("backend.studio_render.MAX_BYTES", 1), self.assertRaisesRegex(RenderError, "size budget"):
                    await render_project(Project.model_validate(nested_data()), ExportOptions(resolution=360), {"final": source, "video_only": source}, root / "size")
                command.assert_not_awaited()

    async def test_cancel_reaches_single_expanded_render_command_without_child_jobs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sequence-command-cancel-") as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"command-cancellation-fixture-never-decoded")
            info = {"duration": 2, "streams": [{"codec_type": "video", "width": 160, "height": 90, "avg_frame_rate": "30/1"}]}
            entered, cancelled = asyncio.Event(), asyncio.Event()
            commands: list[list[str]] = []

            async def hold(command: list[str], *_args: Any, **_kwargs: Any) -> bytes:
                commands.append(command)
                entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
                raise AssertionError("command hold unexpectedly released")

            with patch("backend.studio_render.probe", new=AsyncMock(return_value=info)), \
                    patch("backend.studio_render.shutil.which", return_value="contract-only"), \
                    patch("backend.studio_render.run_logged_command", side_effect=hold):
                task = asyncio.create_task(render_project(Project.model_validate(nested_data(depth=2)), ExportOptions(resolution=360),
                                                           {"final": source, "video_only": source}, root / "work"))
                try:
                    await asyncio.wait_for(entered.wait(), 5)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                finally:
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            self.assertTrue(cancelled.is_set())
            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0].count("-i"), 1)
            self.assertFalse((root / "work/nested").exists())
            self.assertFalse((root / "work/output.mp4").exists())


class StudioSequenceProxyCompatibilityTests(unittest.TestCase):
    def documents(self, *, legacy: bool) -> tuple[dict[str, Any], dict[str, Any]]:
        profile = copy.deepcopy(studio_proxy.profile())
        project = studio_proxy.project("final", 1, True).model_dump()
        if legacy:
            for document in (profile["project_template"], project):
                document.pop("active_sequence_id")
                document.pop("sequences")
                for track in document["tracks"]:
                    for clip in track["clips"]:
                        clip.pop("sequence_id")
        binding = {"source_id": "final", "source_path": "final.mp4", "source_sha256": "a" * 64,
                   "source_fingerprint": [1, 2, 10, 3, 4], "pipeline_revision": 0, "duration": 1,
                   "has_audio": True, "disclosure": False, "profile": profile,
                   "metadata": {name: None for name in studio_proxy.METADATA}}
        key_document = {"source_sha256": binding["source_sha256"], "pipeline_revision": 0, "profile": profile, "disclosure": False}
        key = hashlib.sha256(json.dumps(key_document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        job = {"kind": "proxy", "source_id": "final", "pipeline_revision": 0, "disclosure": False,
               "cache_key": key, "options": studio_proxy.options().model_dump()}
        snapshot = {"proxy": binding, "options": studio_proxy.options().model_dump(), "project": project}
        return job, snapshot

    def test_exact_old_empty_fields_compatibility_is_read_only_and_retains_old_key(self) -> None:
        for legacy in (False, True):
            job, snapshot = self.documents(legacy=legacy)
            original = copy.deepcopy((job, snapshot))
            binding, key = _validate_proxy_snapshot(job, snapshot)
            self.assertEqual(key, job["cache_key"])
            self.assertEqual(binding.model_dump(), snapshot["proxy"])
            self.assertEqual((job, snapshot), original)

    def test_proxy_adapter_does_not_accept_edited_nested_mixed_profiles_or_forged_keys(self) -> None:
        for kind in ("kind", "key", "edit", "sequences", "mixed", "profile"):
            job, snapshot = self.documents(legacy=True)
            if kind == "kind":
                job["kind"] = "render"
            elif kind == "key":
                job["cache_key"] = "f" * 64
            elif kind == "edit":
                snapshot["project"]["tracks"][0]["clips"][0]["brightness"] = 0.1
            elif kind == "sequences":
                snapshot["project"]["sequences"] = [{"id": "hidden", "name": "hidden", "tracks": []}]
            elif kind == "mixed":
                snapshot["project"]["active_sequence_id"] = None
            else:
                snapshot["proxy"]["profile"]["options"]["video_bitrate_kbps"] = 999
            with self.subTest(kind=kind), self.assertRaises((RenderError, ValidationError)):
                _validate_proxy_snapshot(job, snapshot)


@unittest.skipUnless(media.TOOLS, "FFmpeg/ffprobe must be on PATH; no simulated codec success")
class StudioSequenceMediaTests(_SequenceAPIHarness):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        self.render_number = 0

    def prepare_sources(self) -> None:
        for filename, color, frequency in (("final.mp4", "red", 440), ("video_only.mp4", "blue", 880)):
            media.ffmpeg("-y", "-f", "lavfi", "-i", f"color=c={color}:s=160x90:r=30:d=3",
                         "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration=3",
                         "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "3", str(self.root / filename))
        media.ffmpeg("-y", "-f", "lavfi", "-i", "color=c=lime:s=160x90:r=30:d=3",
                     "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", "-an", str(self.root / "silent.mp4"))

    async def produce(self, data: dict[str, Any], **options: Any) -> tuple[dict[str, Any], Path]:
        self.render_number += 1
        work = self.root / f"direct_{self.render_number}"
        index = studio_assets.load_index(self.root)
        sources = {"final": self.root / "final.mp4", "video_only": self.root / "video_only.mp4", "silent": self.root / "silent.mp4"}
        if (self.root / "tone.wav").exists():
            sources["tone"] = self.root / "tone.wav"
        sources.update(studio_assets.image_paths(self.root, index))
        result = await render_project(Project.model_validate(data), ExportOptions.model_validate(options), sources, work,
                                      luts=studio_assets.lut_paths(self.root, index))
        self.assertFalse((work / "nested").exists())
        return result, work / result["file"]

    def frame(self, path: Path, at: float = 0) -> bytes:
        pixels = media.ffmpeg("-ss", str(at), "-i", str(path), "-frames:v", "1", "-vf", "scale=320:180",
                              "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
        self.assertEqual(len(pixels), 320 * 180 * 3)
        return pixels

    @staticmethod
    def pixel(frame: bytes, x: int = 160, y: int = 90) -> tuple[int, ...]:
        return tuple(frame[(y * 320 + x) * 3:(y * 320 + x) * 3 + 3])

    def assert_dominant(self, path: Path, channel: int, at: float = 0.25) -> None:
        pixel = self.pixel(self.frame(path, at))
        self.assertGreater(pixel[channel], 200, pixel)
        self.assertTrue(all(value < 25 for index, value in enumerate(pixel) if index != channel), pixel)

    def pcm(self, path: Path) -> array.array[int]:
        with wave.open(str(path), "rb") as reader:
            self.assertEqual((reader.getframerate(), reader.getnchannels(), reader.getsampwidth()), (48000, 2, 2))
            frames = reader.getnframes()
            payload = reader.readframes(frames)
        self.assertEqual(len(payload), frames * 4)  # Check actual PCM, not just the header.
        return array.array("h", payload)

    @staticmethod
    def amplitude(samples: array.array[int], frequency: float, start: float, end: float) -> float:
        mono = samples[round(start * 48000) * 2:round(end * 48000) * 2:2]
        if not mono:
            raise AssertionError("empty PCM analysis window")
        omega = 2 * math.pi * frequency / 48000
        real = sum(value * math.cos(omega * index) for index, value in enumerate(mono))
        imaginary = sum(value * math.sin(omega * index) for index, value in enumerate(mono))
        return 2 * math.hypot(real, imaginary) / len(mono) / 32768

    async def rendered_job(self, revision: int, *, route: str = "/render", **options: Any) -> tuple[dict[str, Any], Path]:
        job = await self.finish(await self.client.post(self.prefix + route, json={"expected_revision": revision, "options": options}))
        self.assertEqual(job["state"], "succeeded", job)
        work = self.root / "studio/outputs" / f"r{job['revision']}" / job["id"]
        self.assertFalse((work / "nested").exists())
        return job, work / job["result"]["file"]

    async def test_actual_selected_source_color_save_reopen_undo_and_simple_export_unchanged(self) -> None:
        data = project_data()
        saved = await self.save(data)
        self.assertEqual(saved.status_code, 200, saved.text)
        main, main_path = await self.rendered_job(1, resolution=360)
        self.assert_dominant(main_path, 0)
        self.assertIsNone(main["result"]["sequence_semantics"]["active_sequence_id"])
        data["active_sequence_id"] = "seq_a"
        self.assertEqual((await self.save(data, 1)).status_code, 200)
        selected, selected_path = await self.rendered_job(2, resolution=360)
        self.assert_dominant(selected_path, 2)
        self.assertEqual(selected["result"]["sequence_semantics"]["source_ids"], ["video_only"])
        self.assertEqual(selected["result"]["sequence_semantics"]["active_sequence_id"], "seq_a")
        app = FastAPI()
        app.include_router(create_studio_router(self.settings, self.manager, self.authorize))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=self.client.headers) as reopened:
            project = (await reopened.get(self.prefix + "/project/export")).json()
        self.assertEqual(project["project"], Project.model_validate(data).model_dump())
        # The simple pipeline export is still finished_final, not this active blue timeline.
        simple, simple_path = await self.rendered_job(2, route="/export", format="png", resolution=360, frame_time=0.25)
        self.assert_dominant(simple_path, 0, at=0)
        self.assertNotIn("sequence_semantics", simple["result"])
        snapshot = json.loads((simple_path.parent / "snapshot.json").read_text(encoding="utf-8"))["project"]
        self.assertEqual(snapshot["sequences"], [])
        self.assertIsNone(snapshot["active_sequence_id"])
        self.assertEqual(read_state(self.root)["project"]["active_sequence_id"], "seq_a")
        self.assertEqual((await self.action(2, "undo")).status_code, 200)
        _, restored_path = await self.rendered_job(3, resolution=360)
        self.assert_dominant(restored_path, 0)
        response = await self.client.get(self.prefix + "/outputs/" + selected["id"], headers={"Range": "bytes=0-31"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, selected_path.read_bytes()[:32])
        denied = await self.client.get(self.prefix + "/outputs/" + selected["id"], headers={"X-Token": "denied", "Range": "bytes=0-31"})
        self.assertEqual(denied.status_code, 403)

    async def test_actual_second_level_child_edit_changes_parent_and_undo_restores_pixels(self) -> None:
        data = nested_data(depth=2)
        self.assertEqual((await self.save(data)).status_code, 200)
        first, first_path = await self.rendered_job(1, resolution=360)
        self.assert_dominant(first_path, 0)
        changed = copy.deepcopy(data)
        changed["sequences"][1]["tracks"][0]["clips"][0]["source_id"] = "video_only"
        self.assertEqual((await self.save(changed, 1, "/project/import")).status_code, 200)
        second, second_path = await self.rendered_job(2, resolution=360)
        self.assert_dominant(second_path, 2)
        self.assertEqual(second["sequence_semantics"]["nesting_depth"], 2)
        self.assertEqual(second["sequence_semantics"]["decoder_inputs"], 1)
        self.assertEqual(second["sequence_semantics"], second["result"]["sequence_semantics"])
        manifest = json.loads((second_path.parent / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["sequence_semantics"], second["result"]["sequence_semantics"])
        first_snapshot = json.loads((first_path.parent / "snapshot.json").read_text(encoding="utf-8"))
        self.assertEqual(first_snapshot["project"], Project.model_validate(data).model_dump())
        self.assertEqual((await self.client.get(self.prefix + "/project/export")).json()["project"], Project.model_validate(changed).model_dump())
        self.assertEqual((await self.action(2, "undo")).status_code, 200)
        _, restored_path = await self.rendered_job(3, resolution=360)
        self.assert_dominant(restored_path, 0)
        self.assertEqual(first["result"]["duration"], second["result"]["duration"])
        self.assertEqual((self.root / "report.json").read_bytes(), self.publication_originals["report.json"])

    async def test_real_child_source_eof_failure_discards_partial_output(self) -> None:
        data = nested_data()
        data["sequences"][0]["tracks"][0]["clips"][0]["trim"] = 2.5
        self.assertEqual((await self.save(data)).status_code, 200)
        job = await self.finish(await self.client.post(self.prefix + "/render", json={"expected_revision": 1, "options": {"resolution": 360}}))
        self.assertEqual(job["state"], "failed", job)
        self.assertIn("source duration", job["error"])
        self.assertIsNone(job["output_id"])
        self.assertFalse((self.root / "studio/outputs/r1" / job["id"]).exists())
        self.assertEqual((await self.client.get(self.prefix + "/outputs/" + job["id"])).status_code, 409)

    async def test_transition_repeated_nest_pixels_pcm_and_gaps_match_manual_ordinary_tracks(self) -> None:
        nested = transition_child_data()
        manual: dict[str, Any] = {"tracks": []}
        for index, offset in enumerate((0.25, 2.0)):
            manual["tracks"].append({"id": f"manual_{index}", "type": "video", "clips": [
                {"id": f"left_{index}", "source_id": "final", "start": offset, "duration": 1, "fit": "cover"},
                {"id": f"right_{index}", "source_id": "video_only", "start": offset + 0.5, "duration": 1, "fit": "cover",
                 "transition_in": {"left_clip_id": f"left_{index}", "kind": "dissolve", "duration": 0.5}},
            ]})
        result, nested_path = await self.produce(nested, resolution=360)
        baseline, manual_path = await self.produce(manual, resolution=360)
        self.assertEqual(result["duration"], 3.5)
        self.assertEqual(result["duration"], baseline["duration"])
        for at in (0.1, 0.4, 1.0, 1.5, 1.85, 2.1, 2.75, 3.3):
            actual, reference = self.pixel(self.frame(nested_path, at)), self.pixel(self.frame(manual_path, at))
            self.assertLessEqual(max(abs(a - b) for a, b in zip(actual, reference, strict=True)), 3, (at, actual, reference))
        for at in (0.1, 1.85):
            self.assertLess(max(self.pixel(self.frame(nested_path, at))), 12)
        blend = self.pixel(self.frame(nested_path, 1.0))
        self.assertGreater(blend[0], 65)
        self.assertGreater(blend[2], 65)
        self.assertLess(blend[1], 20)
        _, nested_audio = await self.produce(nested, format="wav")
        _, manual_audio = await self.produce(manual, format="wav")
        actual_pcm, reference_pcm = self.pcm(nested_audio), self.pcm(manual_audio)
        self.assertEqual(actual_pcm, reference_pcm)
        self.assertEqual(len(actual_pcm) // 2, 168000)
        self.assertGreater(self.amplitude(actual_pcm, 440, 0.9, 1.1), 0.02)
        self.assertGreater(self.amplitude(actual_pcm, 880, 0.9, 1.1), 0.02)

    async def test_child_layer_position_transparent_gaps_and_text_above_parent_media(self) -> None:
        data = nested_data()
        data["tracks"].insert(0, {**video_track("below", duration=1.5), "clips": [
            {"id": "background", "source_id": "final", "duration": 1.5, "mute": True},
        ]})
        data["tracks"][1]["clips"][0].update(start=0.25, duration=1.0)
        data["sequences"][0]["tracks"] = [{"id": "child_picture", "type": "video", "clips": [
            {"id": "child_blue", "source_id": "video_only", "start": 0.25, "duration": 0.75,
             "scale": 0.5, "x": -0.25, "mute": True, "fit": "cover"},
        ]}]
        _, output = await self.produce(data, resolution=360)
        early, inside, after = self.frame(output, 0.35), self.frame(output, 0.75), self.frame(output, 1.35)
        self.assertGreater(self.pixel(early, 80, 90)[0], 220)  # Child's own initial gap exposes lower parent.
        self.assertGreater(self.pixel(inside, 80, 90)[2], 220)
        self.assertGreater(self.pixel(inside, 250, 90)[0], 220)  # Child's PIP alpha is not an opaque group canvas.
        self.assertGreater(self.pixel(after, 80, 90)[0], 220)
        data["tracks"].append({"id": "above", "type": "overlay", "clips": [
            {"id": "top_green", "source_id": "silent", "duration": 1.5, "fit": "cover"},
        ]})
        data["sequences"][0]["tracks"].append({"id": "child_text", "type": "text", "clips": [
            {"id": "words", "duration": 1, "text": "Nested text", "subtitle": False, "font_size": 100},
        ]})
        _, text_path = await self.produce(data, resolution=360)
        pixels = self.frame(text_path, 0.75)
        self.assertGreater(self.pixel(pixels, 10, 10)[1], 220)  # Parent above covers lower child picture.
        # Count glyph interiors at the encoded resolution. The helper's 2x
        # downscale blends thin strokes with green and removes all >210 RGB
        # pixels even for an ordinary, correctly burned standalone text clip.
        native = media.ffmpeg("-ss", "0.75", "-i", str(text_path), "-frames:v", "1",
                      "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1")
        self.assertEqual(len(native), 640 * 360 * 3)
        whites = sum(all(channel > 210 for channel in native[index:index + 3]) for index in range(0, len(native), 3))
        self.assertGreater(whites, 40)  # But nested text really is above ALL media, as disclosed.

    async def test_imported_image_lut_actual_channels_and_alpha_survive_nested_asset_paths(self) -> None:
        image = asset_fixtures.install_canonical(self.root, asset_fixtures.png(rgba=(255, 0, 0, 128)), "image")
        lut = asset_fixtures.install_canonical(self.root, asset_fixtures.cube(swap=True), "lut")
        paths = [studio_assets.asset_path(self.root, image.id), studio_assets.asset_path(self.root, lut.id)]
        hashes = [hashlib.sha256(path.read_bytes()).digest() for path in paths]
        data = nested_data()
        data["tracks"].insert(0, video_track("under"))
        data["sequences"][0]["tracks"] = [{"id": "image_track", "type": "overlay", "clips": [
            {"id": "image_clip", "source_id": image.id, "lut_id": lut.id, "duration": 1, "mute": True, "fit": "cover"},
        ]}]
        result, output = await self.produce(data, format="png", resolution=360, frame_time=0.5)
        rgb = self.pixel(self.frame(output))
        self.assertTrue(100 < rgb[0] < 160 and rgb[1] < 20 and 100 < rgb[2] < 160, rgb)
        self.assertEqual(result["sequence_semantics"]["lut_ids"], [lut.id])
        self.assertIn(image.id, result["sequence_semantics"]["source_ids"])
        self.assertEqual([hashlib.sha256(path.read_bytes()).digest() for path in paths], hashes)

    async def test_child_transform_color_keyframes_and_audio_effect_match_one_stage_ordinary_render(self) -> None:
        data = nested_data()
        data["tracks"][0]["clips"][0]["start"] = 0.25
        leaf = {"id": "edited_child", "source_id": "final", "start": 0.2, "duration": 0.8, "trim": 0.25,
                "speed": 2, "reverse": True, "mirror": True, "rotation": 90,
                "crop": {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8},
                "fit": "cover", "scale": 0.6, "color_preset": "cool", "saturation": 0.7, "brightness": 0.1,
                "fade_in": 0.1, "fade_out": 0.1, "volume": 0.5, "pan": 0.25, "audio_effect": "denoise",
                "keyframes": {"x": [{"time": 0, "value": -0.2}, {"time": 0.8, "value": 0.2}]}}
        data["sequences"][0]["tracks"][0]["clips"] = [leaf]
        ordinary = {"tracks": [{"id": "manual", "type": "video", "clips": [{**leaf, "start": 0.45}]}]}
        actual, path = await self.produce(data, resolution=360)
        reference, baseline = await self.produce(ordinary, resolution=360)
        self.assertAlmostEqual(actual["duration"], reference["duration"], places=12)
        for at in (0.6, 0.9, 1.1):
            pixels, expected = self.frame(path, at), self.frame(baseline, at)
            self.assertLess(sum(abs(a - b) for a, b in zip(pixels, expected, strict=True)) / len(pixels), 1)
            self.assertGreater(sum(value > 40 for value in pixels), 1000)
        _, sound = await self.produce(data, format="wav")
        _, reference_sound = await self.produce(ordinary, format="wav")
        samples = self.pcm(sound)
        self.assertEqual(samples, self.pcm(reference_sound))
        self.assertGreater(sum(value * value for value in samples), 100000)

    async def test_odd_pcm_full_child_duration_preserves_nonzero_onset_tail_and_clock(self) -> None:
        rate, frames, delay = 48000, 48037, 12000
        samples = array.array("h")
        for index in range(frames):
            value = round(6000 * math.cos(2 * math.pi * 440 * index / rate))
            samples.extend((value, value))
        source = self.root / "tone.wav"
        with wave.open(str(source), "wb") as writer:
            writer.setparams((2, 2, rate, 0, "NONE", "not compressed"))
            writer.writeframes(samples.tobytes())
        duration = frames / rate
        data = {"tracks": [{"id": "main", "type": "video", "clips": [
            {"id": "nest", "sequence_id": "child", "start": delay / rate, "duration": duration},
        ]}], "sequences": [{"id": "child", "name": "Odd PCM", "tracks": [
            {"id": "audio", "type": "audio", "clips": [{"id": "pcm", "source_id": "tone", "duration": duration}]},
        ]}]}
        result, output = await self.produce(data, format="wav")
        actual = self.pcm(output)
        self.assertLessEqual(abs(len(actual) // 2 - (frames + delay)), 1)
        self.assertAlmostEqual(result["duration"], (frames + delay) / rate, places=12)
        self.assertTrue(all(abs(value) <= 1 for value in actual[:(delay - 1) * 2]))
        for segment, reference in ((actual[delay * 2:(delay + 37) * 2], samples[:74]),
                                   (actual[-74:], samples[-74:])):
            self.assertEqual(len(segment), 74)
            original_energy = sum(value * value for value in reference)
            self.assertGreater(original_energy, 100000)
            self.assertGreater(sum(value * value for value in segment), original_energy * 0.8)
        self.assertGreater(self.amplitude(actual, 440, 0.4, 0.9), 0.17)
        self.assertEqual(source.read_bytes()[-len(samples) * 2:], samples.tobytes())

    async def test_parent_mute_propagates_only_to_child_and_silent_child_keeps_outer_audio(self) -> None:
        for silent in (False, True):
            data = nested_data()
            data["tracks"][0]["clips"][0]["mute"] = True
            data["sequences"][0]["tracks"][0]["clips"][0]["source_id"] = "silent" if silent else "final"
            data["tracks"].append({"id": "outer_audio", "type": "audio", "clips": [
                {"id": "outer_sound", "source_id": "video_only", "duration": 1},
            ]})
            _, output = await self.produce(data, format="wav")
            samples = self.pcm(output)
            self.assertGreater(self.amplitude(samples, 880, 0.2, 0.8), 0.07)
            self.assertLess(self.amplitude(samples, 440, 0.2, 0.8), 0.003)
            result, video = await self.produce(data, resolution=360)
            self.assertTrue(any(stream["codec_type"] == "audio" for stream in result["probe"]["streams"]))
            self.assert_dominant(video, 1 if silent else 0)
        muted = nested_data()
        muted["tracks"][0]["clips"][0]["mute"] = True
        with self.assertRaisesRegex(RenderError, "no audible"):
            await self.produce(muted, format="wav")

    async def test_nested_disclosure_burned_once_on_final_canvas_and_full_timeline(self) -> None:
        data = nested_data(depth=2)
        data["tracks"][0]["clips"][0]["start"] = 0.5
        self.assertEqual((await self.save(data)).status_code, 200)
        plain, plain_path = await self.rendered_job(1, resolution=360)
        (self.root / "generated_media_disclosure.json").write_text(json.dumps(generated_manifest()), encoding="utf-8")
        confirm_synthetic_publication(self, self.record, generated=True)
        marked, marked_path = await self.rendered_job(1, resolution=360)
        document = (marked_path.parent / "studio.ass").read_text(encoding="utf-8")
        self.assertEqual(document.count("AI生成示意画面"), 1)
        self.assertIn("Dialogue: 100,0:00:00.00,0:00:01.50", document)
        manifest = json.loads((marked_path.parent / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["disclosure_intervals"], [[0, 1.5]])
        self.assertTrue(marked["result"]["disclosure"])
        self.assertFalse(plain["result"]["disclosure"])
        for at in (0.1, 0.8, 1.4):
            a, b = self.frame(plain_path, at), self.frame(marked_path, at)
            # The initial black gap is covered too, after ALL nesting/layers.
            changed_top = sum(abs(a[(y * 320 + x) * 3 + channel] - b[(y * 320 + x) * 3 + channel]) > 25
                              for y in range(5, 35) for x in range(5, 300) for channel in range(3))
            self.assertGreater(changed_top, 80, at)
        for format_ in ("wav", "mp3", "srt", "ass"):
            response = await self.client.post(self.prefix + "/render", json={"expected_revision": 1, "options": {"format": format_}})
            self.assertEqual(response.status_code, 422)

    async def test_real_pre_sequence_proxy_survives_upgrade_and_is_reused_without_encoding(self) -> None:
        self.assertEqual((await self.save(nested_data(active="seq_a"))).status_code, 200)
        response = await self.client.post(self.prefix + "/proxies", json={"expected_revision": 1, "source_id": "final"})
        job = await self.finish(response)
        self.assertEqual(job["state"], "succeeded", job)
        work = self.root / "studio/outputs/r1" / job["id"]
        snapshot = json.loads((work / "snapshot.json").read_text(encoding="utf-8"))
        manifest = json.loads((work / "manifest.json").read_text(encoding="utf-8"))
        output = work / "output.mp4"
        output_hash = hashlib.sha256(output.read_bytes()).hexdigest()
        # Turn only this newly created, owned, REAL proxy fixture into the exact
        # old profile. This is not a production migration or content replacement.
        for document in (snapshot["project"], snapshot["proxy"]["profile"]["project_template"]):
            document.pop("active_sequence_id")
            document.pop("sequences")
            for track in document["tracks"]:
                for clip in track["clips"]:
                    clip.pop("sequence_id")
        binding = snapshot["proxy"]
        key_document = {"source_sha256": binding["source_sha256"], "pipeline_revision": binding["pipeline_revision"],
                        "profile": binding["profile"], "disclosure": binding["disclosure"]}
        old_key = hashlib.sha256(json.dumps(key_document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        job["cache_key"] = old_key
        manifest.update(cache_key=old_key, proxy=binding)
        write_json_atomic(work / "snapshot.json", snapshot)
        write_json_atomic(work / "manifest.json", manifest)
        write_json_atomic(self.root / "studio/jobs" / (job["id"] + ".json"), job)
        before = read_state(self.root)
        with patch("backend.studio.render_project", new=AsyncMock()) as render:
            downloaded = await self.client.get(self.prefix + "/proxies/" + job["id"] + "/media")
            self.assertEqual(downloaded.status_code, 200, downloaded.text[:100] if downloaded.status_code != 200 else "")
            self.assertEqual(hashlib.sha256(downloaded.content).hexdigest(), output_hash)
            cached = await self.client.post(self.prefix + "/proxies", json={"expected_revision": 1, "source_id": "final"})
            self.assertEqual(cached.status_code, 200, cached.text)
            self.assertTrue(cached.json()["cached"])
            self.assertEqual(cached.json()["id"], job["id"])
            self.assertEqual(cached.json()["cache_key"], old_key)
            render.assert_not_awaited()
        self.assertEqual(read_state(self.root), before)
        self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), output_hash)