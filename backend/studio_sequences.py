"""Bounded, in-snapshot timeline references; never projects or rendered inputs.

Main is Project.tracks/markers (active_sequence_id=None). Up to four named
sequences share the project's groups, asset metadata and workspace. All stored
references, including hidden/inactive ones, must be acyclic with at most two
reference edges from ANY timeline. A nest has trim=0 and the child's complete
visible/solo-resolved duration, not a cached duration or an automatic resize.
Changing that duration requires updating every descriptor in the same save.

Evaluation inserts child tracks at the containing track's layer position. It
preserves source trims, clip-local envelopes and transition bindings, and adds
only the ancestor start offsets and mute flags. This is deliberately NOT an
isolated group: gaps/alpha expose lower parent layers, adjustments affect the
accumulated lower composite, text is above all media, and audio is mixed once.
There is no nested transform, partial range, intermediate codec/file, private
asset path substitution, extra disclosure burn or additional disk reservation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .studio_render import Clip, Marker, Project, Track

MAX_SEQUENCES = 4
MAX_TRACKS = 8  # Per main/named timeline, not the entire project.
MAX_TOTAL_TRACKS = 32
MAX_CLIPS = 64  # Stored clips, INCLUDING nest descriptors, across all timelines.
MAX_MARKERS = 100  # Per timeline.
MAX_NESTING_DEPTH = 2  # main -> sequence A -> sequence B is allowed.
MAX_EXPANDED_TRACKS = 8
MAX_EXPANDED_CLIPS = 64
MAX_DECODER_INPUTS = 16  # Occurrences, never just distinct source IDs.
NESTING_TIME_EPSILON = 1e-12  # Serialization noise, not an editing tolerance.


def visible_tracks(tracks: list[Track]) -> list[Track]:
    """Resolve solo independently inside each timeline, before expansion."""
    solo = any(track.solo and not track.hidden for track in tracks)
    return [track for track in tracks if not track.hidden and (not solo or track.solo)]


def timeline_duration(tracks: list[Track]) -> float:
    return max((clip.start + clip.duration for track in visible_tracks(tracks) for clip in track.clips), default=0)


def project_timelines(project: Project) -> list[tuple[str | None, list[Track], list[Marker]]]:
    return [(None, project.tracks, project.markers),
            *((sequence.id, sequence.tracks, sequence.markers) for sequence in project.sequences)]


def validate_sequence_graph(project: Project) -> None:
    """Validate the WHOLE saved graph, not just the selected/rendered subgraph."""
    from .studio_render import MAX_DURATION

    timelines = {identifier: tracks for identifier, tracks, _ in project_timelines(project)}
    if project.active_sequence_id not in timelines:
        raise ValueError("unknown active_sequence_id; null explicitly selects main")
    edges = {identifier: [clip.sequence_id for track in tracks for clip in track.clips
                          if clip.sequence_id is not None] for identifier, tracks in timelines.items()}
    if any(target not in timelines for targets in edges.values() for target in targets):
        raise ValueError("unknown sequence_id; nested clips must reference an existing named sequence")
    visiting: set[str | None] = set()
    depths: dict[str | None, int] = {}

    def depth(identifier: str | None) -> int:
        if identifier in visiting:
            raise ValueError("sequence references contain a cycle, including hidden/inactive references")
        if identifier not in depths:
            visiting.add(identifier)
            result = max((1 + depth(target) for target in edges[identifier]), default=0)
            visiting.remove(identifier)
            if result > MAX_NESTING_DEPTH:
                raise ValueError("maximum nesting depth is 2 reference edges from any timeline")
            depths[identifier] = result
        return depths[identifier]

    for identifier in timelines:
        depth(identifier)
    durations = {identifier: timeline_duration(tracks) for identifier, tracks in timelines.items()}
    for tracks in timelines.values():
        for track in tracks:
            for clip in track.clips:
                if clip.sequence_id is None:
                    continue
                child_duration = durations[clip.sequence_id]
                if not 0 < child_duration <= MAX_DURATION:
                    raise ValueError("nested sequence must have a nonempty active timeline of at most 120 seconds")
                if abs(clip.duration - child_duration) > NESTING_TIME_EPSILON:
                    raise ValueError(f"nested clip {clip.id}: duration must equal sequence {clip.sequence_id} active duration "
                                     f"({child_duration}) exactly; update all references in the same save")


@dataclass(frozen=True)
class NestedInstance:
    sequence_id: str
    clip_id: str
    path: tuple[str, ...]
    start: float
    duration: float
    mute: bool


@dataclass(frozen=True)
class TimelineEvaluation:
    """Detached render plan. Never persisted as an authored Project or catalog."""
    active_sequence_id: str | None
    tracks: tuple[Track, ...]
    duration: float
    instances: tuple[NestedInstance, ...]
    clip_count: int
    decoder_inputs: int

    def active_tracks(self) -> tuple[Track, ...]:
        # Visibility/solo was resolved per container, not globally on the leaves.
        return self.tracks


def evaluate_project(project: Project) -> TimelineEvaluation:
    """Evaluate a revalidated snapshot with bounded occurrence, not unique-ID, costs.

    List mutations do not trigger Pydantic parent validation. Revalidate even
    internal callers so a changed nested transform/reference is never ignored.
    No source probing, filesystem access or mutations of the input are needed.
    """
    from .studio_render import Clip, Project, RenderError, Track

    project = Project.model_validate(project.model_dump())
    sequences = {sequence.id: sequence for sequence in project.sequences}
    selected = project.active_timeline()
    occupied = {identifier for identifier, _, _ in project_timelines(project) if identifier is not None}
    occupied.update(track.id for track in project.all_tracks())
    occupied.update(clip.id for track in project.all_tracks() for clip in track.clips)
    occupied.update(marker.id for _, _, markers in project_timelines(project) for marker in markers)
    occupied.update(group.id for group in project.groups)
    serial = 0
    tracks: list[Track] = []
    instances: list[NestedInstance] = []
    clip_count = decoder_inputs = 0

    def allocate(kind: str) -> str:
        nonlocal serial
        # Avoid even deliberately authored collisions, without truncating IDs
        # or relying on hashes. Deterministic across exports of this snapshot.
        while True:
            identifier = f"expanded_{kind}_{serial}"
            serial += 1
            if identifier not in occupied:
                occupied.add(identifier)
                return identifier

    def expand(source_tracks: list[Track], offset: float, muted: bool, path: tuple[str, ...]) -> None:
        nonlocal clip_count, decoder_inputs
        for track in visible_tracks(source_tracks):
            ordinary = [clip for clip in track.clips if clip.sequence_id is None]
            nested = [clip for clip in track.clips if clip.sequence_id is not None]
            # Empty UI lanes have no render layers/cost; stored track limits
            # still apply. Never let a nest descriptor consume a phantom layer.
            if ordinary:
                clip_count += len(ordinary)
                decoder_inputs += sum(clip.source_id is not None for clip in ordinary)
                if len(tracks) >= MAX_EXPANDED_TRACKS:
                    raise RenderError("expanded timeline layer budget exceeded (8 active tracks)")
                if clip_count > MAX_EXPANDED_CLIPS:
                    raise RenderError("expanded timeline clip budget exceeded (64 occurrences)")
                if decoder_inputs > MAX_DECODER_INPUTS:
                    raise RenderError("render decoder budget exceeded (16 expanded inputs)")
                remap = {clip.id: allocate("clip") if path else clip.id for clip in ordinary}
                leaves: list[Clip] = []
                for clip in ordinary:
                    data = clip.model_dump()
                    data.update(id=remap[clip.id], start=offset + clip.start)
                    if muted and track.type in {"video", "overlay", "audio"}:
                        data["mute"] = True
                    if clip.transition_in is not None:
                        data["transition_in"]["left_clip_id"] = remap[clip.transition_in.left_clip_id]
                    leaves.append(Clip.model_validate(data))
                tracks.append(Track.model_validate({**track.model_dump(), "id": allocate("track") if path else track.id,
                                                    "hidden": False, "solo": False, "clips": leaves}))
            # Ordinary clips and nest spans cannot overlap on this track. Thus
            # all ordinary clips can keep one layer (and their transition pairs)
            # before its child layers, without ambiguous same-track ordering.
            for clip in nested:
                assert clip.sequence_id is not None
                child_path = (*path, clip.id)
                start, child_mute = offset + clip.start, muted or clip.mute
                instances.append(NestedInstance(clip.sequence_id, clip.id, child_path, start, clip.duration, child_mute))
                expand(sequences[clip.sequence_id].tracks, start, child_mute, child_path)

    expand(selected.tracks, 0, False, ())
    return TimelineEvaluation(project.active_sequence_id, tuple(tracks), timeline_duration(selected.tracks),
                              tuple(instances), clip_count, decoder_inputs)


def sequence_summary(project: Project, timeline: TimelineEvaluation) -> dict[str, Any] | None:
    """Credential-free, occurrence-based semantics for opted-in jobs/outputs."""
    if not project.sequences:
        return None  # Keep legacy and simple-pipeline result shapes unchanged.
    return {
        "active_sequence_id": timeline.active_sequence_id,
        "mode": "full_length_identity_flatten",
        "duration": timeline.duration,
        "expanded_tracks": len(timeline.tracks), "expanded_clips": timeline.clip_count,
        "decoder_inputs": timeline.decoder_inputs,
        "nesting_depth": max((len(instance.path) for instance in timeline.instances), default=0),
        "instances": [{"sequence_id": item.sequence_id, "clip_id": item.clip_id, "path": list(item.path),
                       "start": item.start, "duration": item.duration, "mute": item.mute} for item in timeline.instances],
        "source_ids": sorted({clip.source_id for track in timeline.tracks for clip in track.clips if clip.source_id}),
        "lut_ids": sorted({clip.lut_id for track in timeline.tracks for clip in track.clips if clip.lut_id}),
        "composition": "child layers inserted at parent track position; local visibility/solo, envelopes and transitions preserved; one final audio mix and disclosure burn; no intermediate media",
        "warnings": ["Identity composition, not an isolated group: gaps/alpha expose lower parent layers and child adjustments affect the accumulated lower composite.",
                     "Text from every expanded timeline is above all media; no isolated text clipping or nested transforms."],
    }