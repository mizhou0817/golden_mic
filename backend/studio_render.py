"""Bounded local studio renderer. No provider calls and no pipeline artifact writes."""
from __future__ import annotations

import asyncio
import json
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .audio_filters import AFFTDN_CLOCK_FILTER
from .media import run_logged_command
from .studio_assets import AssetError, AssetIndex, ImageAsset, LUT_ID_PATTERN, is_image_id, verified_asset
from .studio_sequences import (
    MAX_CLIPS, MAX_DECODER_INPUTS, MAX_MARKERS, MAX_SEQUENCES, MAX_TOTAL_TRACKS, MAX_TRACKS,
    NESTING_TIME_EPSILON, TimelineEvaluation, evaluate_project, project_timelines,
    sequence_summary, timeline_duration, validate_sequence_graph, visible_tracks,
)

MAX_DURATION = 120.0
# Independent fixed-composition export, not an editable Studio timeline.
MAX_MODE_DURATION = 600.0
MAX_BYTES = 128 * 1024 * 1024
MAX_REVERSE_BYTES = 128 * 1024 * 1024
MAX_SUBTITLE_BYTES = 256 * 1024
MAX_TYPEWRITER_STEPS = 64
MAX_TRANSITION_DURATION = 1.2
_TRANSITION_TIME_EPSILON = 1e-12  # Float serialization noise, never a frame/gap tolerance.
THREADS = 2
ID = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")]
Seconds = Annotated[float, Field(ge=0, le=MAX_DURATION)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)


class Crop(StrictModel):
    """Normalized source rectangle; applied before rotation and scaling."""
    x: float = Field(default=0, ge=0, lt=1)
    y: float = Field(default=0, ge=0, lt=1)
    width: float = Field(default=1, gt=0, le=1)
    height: float = Field(default=1, gt=0, le=1)

    @model_validator(mode="after")
    def contained(self) -> Crop:
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("crop must fit inside the source")
        return self


class KeyPoint(StrictModel):
    time: Seconds
    value: float
    easing: Literal["linear", "ease_in", "ease_out", "ease_in_out"] = "linear"


class Keyframes(StrictModel):
    x: list[KeyPoint] = Field(default_factory=list, max_length=8)
    y: list[KeyPoint] = Field(default_factory=list, max_length=8)
    scale: list[KeyPoint] = Field(default_factory=list, max_length=8)
    opacity: list[KeyPoint] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def points(self) -> Keyframes:
        for name, low, high in (("x", -1, 1), ("y", -1, 1), ("scale", 0.05, 1), ("opacity", 0, 1)):
            points = getattr(self, name)
            if points and (len(points) < 2 or points[0].time != 0):
                raise ValueError("keyframes require 2–8 points starting at local time zero")
            if any(not low <= p.value <= high for p in points):
                raise ValueError(f"{name} keyframe value out of bounds")
            if any(a.time >= b.time for a, b in zip(points, points[1:])):
                raise ValueError("keyframe times must strictly increase")
        return self


class ShapeMask(StrictModel):
    type: Literal["rectangle", "ellipse"]
    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.5, ge=0, le=1)
    width: float = Field(default=1, gt=0, le=1)
    height: float = Field(default=1, gt=0, le=1)
    feather: float = Field(default=0, ge=0, le=0.5)
    invert: bool = False


class CurvePoint(StrictModel):
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


RGBKnots = Annotated[list[CurvePoint], Field(min_length=2, max_length=8)]


class TransitionIn(StrictModel):
    """Explicit incoming overlap, not an instruction to move/shorten either clip.

    Kind, left_clip_id and numeric duration are required. Omitted easing/audio
    mean linear/True; only transition_in=None disables the transition. Wipe
    names describe the moving edge: wipe_left reveals from right to left, and
    wipe_up from bottom to top. Participants render opaque source RGB (not
    intrinsic source alpha). Audio easing is always linear, independently of
    the visual easing; audio=False preserves the existing overlapping mix.
    """
    left_clip_id: ID
    kind: Literal["dissolve", "wipe_left", "wipe_right", "wipe_up", "wipe_down"]
    duration: float = Field(gt=0, le=MAX_TRANSITION_DURATION, strict=True)
    easing: Literal["linear", "ease_in", "ease_out", "ease_in_out"] = "linear"
    audio: bool = Field(default=True, strict=True)


class Clip(StrictModel):
    _timeline_limit: ClassVar[float] = MAX_DURATION
    id: ID
    source_id: ID | None = None
    sequence_id: ID | None = Field(default=None, description="Named timeline instead of source_id; video/overlay only, full child duration, identity controls and optional mute")
    lut_id: Annotated[str, Field(pattern=LUT_ID_PATTERN, strict=True)] | None = None
    start: Seconds = 0
    trim: float = Field(default=0, ge=0, le=3600)
    duration: float = Field(gt=0, le=MAX_DURATION)
    speed: float = Field(default=1, ge=0.5, le=2)
    reverse: bool = False
    freeze: bool = False
    rotation: Literal[0, 90, 180, 270] = 0
    mirror: bool = False
    flip: bool = False
    crop: Crop = Field(default_factory=Crop)
    scale: float = Field(default=1, ge=0.05, le=1)
    x: float = Field(default=0, ge=-1, le=1)
    y: float = Field(default=0, ge=-1, le=1)
    opacity: float = Field(default=1, ge=0, le=1)
    fit: Literal["contain", "cover"] = "contain"
    keyframes: Keyframes = Field(default_factory=Keyframes)
    mask: ShapeMask | None = None
    transition_in: TransitionIn | None = None
    volume: float = Field(default=1, ge=0, le=2)
    pan: float = Field(default=0, ge=-1, le=1)
    mute: bool = False
    brightness: float = Field(default=0, ge=-1, le=1)
    contrast: float = Field(default=1, ge=0, le=2)
    saturation: float = Field(default=1, ge=0, le=3)
    temperature: float = Field(default=0, ge=-1, le=1)
    hue: float = Field(default=0, ge=-180, le=180)
    shadows: float = Field(default=0, ge=-1, le=1)
    highlights: float = Field(default=0, ge=-1, le=1)
    fade_amount: float = Field(default=0, ge=0, le=1)
    color_preset: Literal["none", "warm", "cool", "cinema", "mono"] = "none"
    rgb_curves: dict[Literal["red", "green", "blue"], RGBKnots] = Field(default_factory=dict)
    sharpen: float = Field(default=0, ge=0, le=1)
    noise: int = Field(default=0, ge=0, le=20)
    fade_in: Seconds = 0
    fade_out: Seconds = 0
    audio_effect: Literal["none", "compressor", "limiter", "denoise", "invert", "reverb", "normalize"] = "none"
    bass_db: float = Field(default=0, ge=-12, le=12)
    treble_db: float = Field(default=0, ge=-12, le=12)
    text: str = Field(default="", max_length=500)
    subtitle: bool = True
    font_size: int = Field(default=42, ge=12, le=160)
    color: str = Field(default="FFFFFF", pattern=r"^[0-9A-Fa-f]{6}$")
    bold: bool = False
    # Keep ASS numeric tags identical before and after snapshot revalidation.
    outline: float = Field(default=2.0, ge=0, le=8)
    shadow: float = Field(default=0.0, ge=0, le=8)
    background: bool = False
    text_animation: Literal["none", "typewriter", "fade", "pop"] = "none"
    text_template: Literal["custom", "news", "outline", "gold", "note"] = "custom"
    chroma_color: str | None = Field(default=None, pattern=r"^[0-9A-Fa-f]{6}$")
    chroma_similarity: float = Field(default=0.1, ge=0.01, le=1)
    chroma_blend: float = Field(default=0, ge=0, le=1)

    @model_validator(mode="after")
    def times(self) -> Clip:
        if self.source_id is not None and self.sequence_id is not None:
            raise ValueError("media clips require either source_id or sequence_id, never both")
        if self.sequence_id is not None:
            defaults = Clip(id=self.id, duration=self.duration).model_dump()
            for key, value in self.model_dump().items():
                if key not in {"id", "sequence_id", "start", "duration", "mute"} and value != defaults[key]:
                    raise ValueError(f"nested clips require full-length identity composition; {key} must remain default (only id/sequence_id/start/duration/mute may change)")
        if is_image_id(self.source_id) and (
                self.trim != 0 or self.speed != 1 or self.reverse or self.freeze or not self.mute
                or self.volume != 1 or self.pan != 0 or self.audio_effect != "none" or self.bass_db or self.treble_db):
            raise ValueError("still images require trim=0, speed=1, reverse=false, freeze=false, mute=true and default audio controls; duration is the authored still hold, not source EOF")
        for points in self.rgb_curves.values():
            if points[0].x != 0 or points[-1].x != 1 or any(a.x >= b.x for a, b in zip(points, points[1:])):
                raise ValueError("RGB curves require 2–8 knots with strictly increasing x and endpoints x=0, x=1")
        if self.freeze and (self.duration > 10 or self.speed != 1 or self.reverse or not self.mute):
            raise ValueError("freeze requires mute, speed 1, no reverse and duration <=10 seconds")
        if any(p.time > self.duration for name in ("x", "y", "scale", "opacity") for p in getattr(self.keyframes, name)):
            raise ValueError("keyframe time exceeds clip output duration")
        if self.start + self.duration > self._timeline_limit + 1e-6:
            raise ValueError("clip exceeds 120-second timeline")
        if self.fade_in + self.fade_out > self.duration:
            raise ValueError("fades exceed clip duration")
        if self.chroma_color is None and (self.chroma_similarity != 0.1 or self.chroma_blend != 0):
            raise ValueError("chroma parameters require chroma_color")
        if any(ord(c) < 32 and c not in "\n\t" for c in self.text):
            raise ValueError("text contains control characters")
        return self


class Track(StrictModel):
    id: ID
    type: Literal["video", "audio", "text", "overlay", "adjustment"]
    name: str = Field(default="", max_length=80)
    locked: bool = False
    hidden: bool = False
    solo: bool = False
    color: Literal["gold", "cyan", "red", "purple", "gray"] = "gray"
    group_id: ID | None = None
    clips: list[Clip] = Field(default_factory=lambda: list[Clip](), max_length=MAX_CLIPS)

    @model_validator(mode="after")
    def applicable_fields(self) -> Track:
        shared = {"id", "start", "duration"}
        color = {"brightness", "contrast", "saturation", "temperature", "hue", "shadows", "highlights", "fade_amount", "color_preset", "rgb_curves", "lut_id"}
        visual = color | {"source_id", "trim", "speed", "reverse", "freeze", "rotation", "mirror", "flip", "crop", "scale", "x", "y", "opacity", "fit", "keyframes", "mask", "transition_in", "sharpen", "noise", "fade_in", "fade_out", "chroma_color", "chroma_similarity", "chroma_blend"}
        audio = {"source_id", "trim", "speed", "reverse", "volume", "pan", "mute", "fade_in", "fade_out", "audio_effect", "bass_db", "treble_db"}
        allowed = {
            "video": shared | visual | audio,
            "overlay": shared | visual | audio,
            "audio": shared | audio,
            "text": shared | {"text", "subtitle", "font_size", "color", "x", "y", "opacity", "fade_in", "fade_out", "bold", "outline", "shadow", "background", "text_animation", "text_template"},
            "adjustment": shared | color,
        }[self.type]
        for clip in self.clips:
            if clip.sequence_id is not None and self.type not in {"video", "overlay"}:
                raise ValueError("nested clips are allowed only on video/overlay tracks")
            if is_image_id(clip.source_id) and self.type not in {"video", "overlay"}:
                raise ValueError("still images are video/overlay sources, never audio")
            if self.type in {"video", "audio", "overlay"} and clip.source_id is None and clip.sequence_id is None:
                raise ValueError("media clips require source_id or a video/overlay sequence_id")
            if self.type == "text" and not clip.text.strip():
                raise ValueError("text clips require plain text")
            defaults = type(clip)(id=clip.id, duration=clip.duration).model_dump()
            applied = shared | {"sequence_id", "mute"} if clip.sequence_id is not None else allowed
            for key, value in clip.model_dump().items():
                if key not in applied and value != defaults[key]:
                    raise ValueError(f"{key} is not applied to {self.type} clips")
        for index, left in enumerate(self.clips):
            for right in self.clips[index + 1:]:
                if left.sequence_id is not None or right.sequence_id is not None:
                    if min(left.start + left.duration, right.start + right.duration) - max(left.start, right.start) > NESTING_TIME_EPSILON:
                        raise ValueError("nested clip spans cannot overlap any other clip on the same track; use separate layers")
        return self

    @model_validator(mode="after")
    def valid_transitions(self) -> Track:
        """Validate even hidden/solo-excluded clips; never repair authored geometry.

        A third clip may touch an envelope boundary, not intersect its open
        interval. This permits A->B->C, including envelopes meeting exactly at
        B's midpoint. Unrelated legacy overlaps outside the envelopes survive.
        The Project validator adds the workspace-frame minimum.
        """
        if not any(c.transition_in is not None for c in self.clips):
            return self
        by_id = {c.id: c for c in self.clips}
        if len(by_id) != len(self.clips):
            raise ValueError("transition endpoints require unique clip IDs")
        for right in self.clips:
            transition = right.transition_in
            if transition is None:
                continue
            left = by_id.get(transition.left_clip_id)
            if self.type not in {"video", "overlay"} or left is None or left.id == right.id:
                raise ValueError("transition requires two different video/overlay clips on the SAME track; remove transition first before deleting or moving endpoints")
            left_end, right_end = left.start + left.duration, right.start + right.duration
            if not left.start < right.start or not left_end < right_end:
                raise ValueError("transition requires left.start < right.start and left.end < right.end; remove transition first before changing endpoint geometry")
            if abs(left_end - right.start - transition.duration) > _TRANSITION_TIME_EPSILON:
                raise ValueError("transition overlap must equal duration exactly; remove transition first before changing endpoint geometry")
            if transition.duration > min(left.duration, right.duration) / 2 + _TRANSITION_TIME_EPSILON:
                raise ValueError("transition duration cannot exceed half of EITHER clip; remove transition first before shortening endpoints")
            for participant in (left, right):
                if (participant.sequence_id is not None or participant.scale != 1 or participant.x != 0 or participant.y != 0 or participant.opacity != 1
                        or participant.fit != "cover" or participant.mask is not None or participant.chroma_color is not None
                        or participant.fade_in or participant.fade_out or participant.freeze
                        or any(getattr(participant.keyframes, name) for name in ("x", "y", "scale", "opacity"))):
                    raise ValueError("transition endpoints require scale=1, x=y=0, opacity=1, fit=cover and no mask/chroma/keyframes/fades/freeze; remove transition first before applying these controls")
            if any(c.id not in {left.id, right.id}
                   and min(c.start + c.duration, left_end) - max(c.start, right.start) > _TRANSITION_TIME_EPSILON
                   for c in self.clips):
                raise ValueError("a third same-track clip intersects the transition overlap; remove transition first before overlapping another clip")
        return self

    def render_clips(self) -> list[Clip]:
        # Stable start-only order is necessary for incoming alpha composition.
        # No transition means EXACT legacy list order, even on other tracks.
        return sorted(self.clips, key=lambda c: c.start) if any(c.transition_in is not None for c in self.clips) else self.clips


class Marker(StrictModel):
    id: ID
    time: Seconds
    label: str = Field(default="", max_length=120)


class Sequence(StrictModel):
    """Ordinary tracks in the same saved Project; not a source or subproject."""
    id: ID
    name: str = Field(max_length=80)
    tracks: list[Track] = Field(default_factory=list, max_length=MAX_TRACKS)
    markers: list[Marker] = Field(default_factory=list, max_length=MAX_MARKERS)

    @property
    def duration(self) -> float:
        return timeline_duration(self.tracks)


class TrackGroup(StrictModel):
    id: ID
    name: str = Field(max_length=80)


class AssetMetadata(StrictModel):
    source_id: ID
    rating: int = Field(default=0, ge=0, le=5)
    tags: list[Annotated[str, Field(min_length=1, max_length=40)]] = Field(default_factory=list, max_length=16)


class SourceMark(StrictModel):
    """Source seconds, not timeline frames; authorized catalog IDs are checked by the router."""
    source_id: ID
    in_point: float = Field(ge=0, le=3600)
    out_point: float = Field(gt=0, le=3600)

    @model_validator(mode="after")
    def ordered(self) -> SourceMark:
        if self.out_point <= self.in_point:
            raise ValueError("source out_point must be after in_point")
        return self


class WorkspaceMetadata(StrictModel):
    layout: Literal["default", "editing", "audio", "captions"] = "default"
    timeline_zoom: float = Field(default=1, ge=0.25, le=8)
    timeline_fps: Literal[24, 25, 30, 60] = 30
    time_display: Literal["seconds", "frames"] = "seconds"
    snap_enabled: bool = True
    ripple_enabled: bool = False
    source_marks: list[SourceMark] = Field(default_factory=list, max_length=200)
    shortcuts: dict[Literal["play_pause", "split", "delete", "undo", "redo", "marker"],
                    Annotated[str, Field(pattern=r"^(?:(?:Ctrl|Alt|Shift)\+){0,3}(?:[A-Z0-9]|Space|Delete|Left|Right)$")]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def unique_shortcuts(self) -> WorkspaceMetadata:
        if len({mark.source_id for mark in self.source_marks}) != len(self.source_marks):
            raise ValueError("source marks require unique source_id values")
        keys = [tuple(sorted(key.split("+"))) for key in self.shortcuts.values()]
        if len(keys) != len(set(keys)) or any(len(k) != len(set(k)) for k in keys):
            raise ValueError("shortcut chords must be unique and have no repeated modifiers")
        return self


class Project(StrictModel):
    schema_version: Literal[1] = 1
    name: str = Field(default="Studio", max_length=120)
    tracks: list[Track] = Field(default_factory=lambda: list[Track](), max_length=MAX_TRACKS)
    markers: list[Marker] = Field(default_factory=lambda: list[Marker](), max_length=MAX_MARKERS)
    sequences: list[Sequence] = Field(default_factory=list, max_length=MAX_SEQUENCES)
    active_sequence_id: ID | None = Field(default=None, description="null selects main (root tracks/markers); selecting a sequence is a normal saved, undoable mutation")
    groups: list[TrackGroup] = Field(default_factory=list, max_length=8)
    assets: list[AssetMetadata] = Field(default_factory=list, max_length=200)
    workspace: WorkspaceMetadata = Field(default_factory=WorkspaceMetadata)

    @model_validator(mode="after")
    def unique_ids(self) -> Project:
        tracks = self.all_tracks()
        clips = [c for t in tracks for c in t.clips]
        if len(tracks) > MAX_TOTAL_TRACKS:
            raise ValueError("maximum 32 tracks across main and all sequences")
        if len(clips) > MAX_CLIPS:
            raise ValueError("maximum 64 clips across main and all sequences, including nested descriptors")
        ids = ([s.id for s in self.sequences] + [t.id for t in tracks] + [c.id for c in clips]
               + [m.id for _, _, markers in project_timelines(self) for m in markers] + [g.id for g in self.groups])
        if len(set(ids)) != len(ids):
            raise ValueError("sequence, track, clip, marker and group IDs must be unique across the entire project")
        if any(t.group_id is not None and t.group_id not in {g.id for g in self.groups} for t in tracks):
            raise ValueError("unknown track group")
        if len({a.source_id for a in self.assets}) != len(self.assets):
            raise ValueError("duplicate asset metadata")
        return self

    @model_validator(mode="after")
    def transition_frames(self) -> Project:
        if any(c.transition_in is not None
               and c.transition_in.duration + _TRANSITION_TIME_EPSILON < 1 / self.workspace.timeline_fps
               for t in self.all_tracks() for c in t.clips):
            raise ValueError("transition duration must be at least one workspace frame; remove transition first or explicitly update the overlap")
        return self

    @model_validator(mode="after")
    def sequence_graph(self) -> Project:
        validate_sequence_graph(self)
        return self

    def all_tracks(self) -> list[Track]:
        return [track for _, tracks, _ in project_timelines(self) for track in tracks]

    def source_ids(self) -> set[str]:
        return ({c.source_id for t in self.all_tracks() for c in t.clips if c.source_id is not None}
                | {asset.source_id for asset in self.assets} | {mark.source_id for mark in self.workspace.source_marks})

    def lut_ids(self) -> set[str]:
        return {c.lut_id for t in self.all_tracks() for c in t.clips if c.lut_id is not None}

    def active_timeline(self) -> Project | Sequence:
        if self.active_sequence_id is None:
            return self
        sequence = next((s for s in self.sequences if s.id == self.active_sequence_id), None)
        if sequence is None:
            raise ValueError("unknown active_sequence_id; never implicitly reset to main")
        return sequence

    def active_tracks(self) -> list[Track]:
        """Visible authored tracks of the selection; renderers use evaluate_project."""
        return visible_tracks(self.active_timeline().tracks)

    @property
    def duration(self) -> float:
        return timeline_duration(self.active_timeline().tracks)


class ExportOptions(StrictModel):
    format: Literal["mp4", "mov", "mkv", "avi", "gif", "mp3", "wav", "png", "srt", "ass"] = "mp4"
    resolution: Literal[360, 720, 1080] = 1080
    fps: Literal[24, 25, 30, 60] = 30
    aspect: Literal["16:9", "9:16", "1:1"] = "16:9"
    subtitles: Literal["standard", "large", "none"] = "standard"
    video_bitrate_kbps: int = Field(default=4000, ge=250, le=12000)
    audio_bitrate_kbps: Literal[96, 128, 192, 256, 320] = 192
    frame_time: Seconds | None = None

    @property
    def dimensions(self) -> tuple[int, int]:
        short = self.resolution
        long = {360: 640, 720: 1280, 1080: 1920}[short]
        return {"16:9": (long, short), "9:16": (short, long), "1:1": (short, short)}[self.aspect]

    @model_validator(mode="after")
    def applicable_options(self) -> ExportOptions:
        if self.frame_time is not None and self.format != "png":
            raise ValueError("frame_time only applies to PNG")
        if self.format == "png" and self.fps != 30:
            raise ValueError("fps does not apply to a single PNG frame")
        nonvideo = {"mp3", "wav", "srt", "ass"}
        defaults: dict[str, int | str] = {"resolution": 1080, "fps": 30, "aspect": "16:9", "video_bitrate_kbps": 4000}
        if self.format in nonvideo:
            for name, default in defaults.items():
                if getattr(self, name) != default:
                    raise ValueError(f"{name} does not apply to {self.format}")
        if self.format in {"gif", "png", "wav", "srt", "ass"} and self.audio_bitrate_kbps != 192:
            raise ValueError("audio bitrate only applies to compressed audio")
        if self.format in {"gif", "png", "srt", "ass"} and self.video_bitrate_kbps != 4000:
            raise ValueError("video bitrate does not apply to this format")
        if self.format in {"srt", "ass"} and self.subtitles == "none":
            raise ValueError("subtitle export requires subtitles enabled")
        if self.format == "srt" and self.subtitles == "large":
            raise ValueError("SRT has timing/plain text only; large style requires ASS or video")
        if self.format in {"mp3", "wav"} and self.subtitles != "standard":
            raise ValueError("subtitle style does not apply to audio-only export")
        return self


class RenderError(ValueError):
    pass


class V2ExportOptions(StrictModel):
    """Separate fixed-export contract; professional Studio limits are unchanged."""
    fmt: Literal["mp4", "gif", "mp3", "srt", "png"] = "mp4"
    aspect: Literal["16:9", "9:16", "1:1"] = "16:9"
    res: Literal["360p", "720p", "1080p"] = "1080p"
    sub: Literal["standard", "large", "none"] = "standard"
    frame_seconds: float | None = Field(default=None, ge=0, le=3600)
    expected_revision: int = Field(ge=0, strict=True)
    # The user was shown every unresolved check and chose to export anyway. Strict boolean only.
    acknowledge_unresolved: bool = Field(default=False, strict=True)

    @model_validator(mode="before")
    @classmethod
    def subtitle_aliases(cls, value: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("sub"), str):
            value = {**value, "sub": {"std": "standard", "big": "large"}.get(value["sub"], value["sub"])}
        return value

    @model_validator(mode="after")
    def valid(self) -> V2ExportOptions:
        if self.frame_seconds is not None and self.fmt != "png":
            raise ValueError("frame_seconds only applies to PNG")
        if self.fmt == "png" and self.frame_seconds is None:
            raise ValueError("PNG requires the current frame_seconds")
        if self.fmt in {"mp3", "srt"} and (self.aspect != "16:9" or self.res != "1080p"):
            raise ValueError("Visual sizing does not apply to audio/text exports")
        if self.fmt == "mp3" and self.sub != "standard":
            raise ValueError("Subtitles do not apply to MP3")
        if self.fmt == "srt" and self.sub != "standard":
            raise ValueError("SRT uses confirmed canonical events, not styling")
        return self


@dataclass(frozen=True)
class V2ExportBudget:
    duration: float
    output_bytes: int
    pcm_bytes: int
    reservation_bytes: int
    timeout_seconds: float


def v2_export_budget(duration: float, options: V2ExportOptions, settings: Any) -> V2ExportBudget:
    """Public preflight calculator, based on confirmed OUTPUT clock, not text size.

    8000 characters are not a 600-second film. Hard source/output safety ceiling
    is one hour; any tighter future output cap must be advertised at creation.
    Bounds include encoder/mux headroom, PCM and faststart scratch space.
    """
    if not math.isfinite(duration) or not 0 < duration <= 3600:
        raise RenderError("v2 output clock must be in (0, 3600] seconds")
    if options.frame_seconds is not None and options.frame_seconds > duration:
        raise RenderError("frame_seconds exceeds EOF")
    pcm = math.ceil(duration * 48000) * 4 + 44
    if options.fmt == "mp4":
        output = math.ceil(duration * 4192 * 125 * 1.25) + 4 * 1024**2
    elif options.fmt == "mp3":
        output = math.ceil(duration * 192 * 125 * 1.1) + 1024**2
    elif options.fmt == "gif":
        output = 64 * 1024**2
    elif options.fmt == "png":
        output = 16 * 1024**2
    else:
        output = MAX_SUBTITLE_BYTES
    timeout = min(float(getattr(settings, "media_command_timeout_seconds", 7200)), max(120, duration * 4))
    if not math.isfinite(timeout) or timeout <= 0:
        raise RenderError("Invalid export timeout")
    return V2ExportBudget(duration, output, pcm, output * 2 + (pcm if options.fmt == "mp3" else 0) + 32 * 1024**2, timeout)


async def render_v2_export(root: Path, work: Path, options: V2ExportOptions,
                           budget: V2ExportBudget) -> dict[str, Any]:
    """Real fixed media export; called only inside the host's reserved transaction."""
    from .studio import assemble_mode_voice, prepare_mode_export, mode_export_hashes
    from .graphics import validate_mode_subtitle_artifacts
    from .revisions import local_file
    from .pipeline import _run_blocking_until_complete
    from functools import partial

    work.mkdir(parents=True, exist_ok=True)
    info = await probe(local_file(root, "final.mp4"), work)
    duration = info["duration"]
    if abs(duration - budget.duration) > .15:
        raise RenderError("Actual final clock disagrees with preflight")
    canonical = await _run_blocking_until_complete(partial(prepare_mode_export, root, work,
        ExportOptions(format="mp4", subtitles=options.sub)))
    if abs(canonical.source_duration - duration) > .15:
        raise RenderError("Canonical timeline disagrees with final")
    output = work / ("output." + options.fmt)
    frame = None
    if options.fmt == "srt":
        # The regenerated manifest is validated against the committed document
        # above. Never use coarse sentence boundaries as word-caption events.
        events = validate_mode_subtitle_artifacts(local_file(root, "subs.ass"), local_file(root, "subtitle_manifest.json"))["events"]
        text = "\n\n".join(f"{i}\n{srt_time(e['start'])} --> {srt_time(e['end'])}\n{e['text']}"
            for i, e in enumerate(events, 1)) + "\n"
        if not events or len(text.encode("utf-8")) > budget.output_bytes:
            raise RenderError("Canonical SRT is empty or exceeds metadata budget")
        output.write_text(text, encoding="utf-8", newline="\n")
        measured = None
    else:
        command = ["ffmpeg", "-y", "-nostdin", "-v", "error", "-filter_complex_threads", "1"]
        if options.fmt == "mp3":
            voice, _ = await _run_blocking_until_complete(partial(assemble_mode_voice, root, work,
                duration_limit=3600, v2_budget=budget))
            command += ["-protocol_whitelist", "file", "-f", "wav", "-i", str(voice), "-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-b:a", "192k"]
        else:
            from .rendering import _prepare_ass_font_directory
            fonts = _prepare_ass_font_directory(work, Path(__file__).parent / "assets/fonts")
            width, height = ExportOptions(resolution=int(options.res[:-1]), aspect=options.aspect).dimensions
            if options.fmt == "gif":
                height = {"16:9": 270, "9:16": 854, "1:1": 480}[options.aspect]
                width = 480
            command += ["-protocol_whitelist", "file", "-f", "mov", "-i", str(local_file(root, "video_only.mp4"))]
            if options.fmt == "mp4":
                command += ["-protocol_whitelist", "file", "-f", "mov", "-i", str(local_file(root, "final.mp4"))]
            filters = [f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1", "fps=30"]
            for i, doc in enumerate((canonical.captions, canonical.graphics)):
                if doc:
                    path = work / f"canonical-{i}.ass"
                    path.write_text(doc, encoding="utf-8", newline="\n")
                    filters.append(f"subtitles=filename='{filter_path(path)}':fontsdir='{filter_path(fonts)}'")
            if canonical.fade_in:
                filters.append(f"fade=t=in:d={canonical.fade_in}")
            if canonical.fade_out:
                filters.append(f"fade=t=out:st={max(0, duration-canonical.fade_out)}:d={canonical.fade_out}")
            graph = "[0:v]" + ",".join(filters)
            if options.fmt == "gif":
                graph += f",trim=duration={min(duration, 6)},fps=12,split[a][b];[a]palettegen[p];[b][p]paletteuse[v]"
                command += ["-filter_complex", graph, "-map", "[v]", "-an", "-loop", "0", "-t", str(min(duration, 6))]
            elif options.fmt == "png":
                frame = min(math.floor(float(options.frame_seconds) * 30 + 1e-9), max(0, math.ceil(duration * 30 - 1e-9) - 1))
                graph += f",trim=start_frame={frame}:end_frame={frame+1},setpts=PTS-STARTPTS,format=rgb24[v]"
                command += ["-filter_complex", graph, "-map", "[v]", "-an", "-frames:v", "1", "-update", "1", "-c:v", "png"]
            else:
                graph += ",scale=out_color_matrix=bt709:out_range=tv,format=yuv420p[v]"
                command += ["-filter_complex", graph, "-map", "[v]", "-map", "1:a:0", "-c:v", "libx264", "-preset", "veryfast",
                    "-b:v", "4000k", "-maxrate", "4000k", "-bufsize", "8000k", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", "-t", str(duration)]
        command += ["-threads", str(THREADS), "-fs", str(budget.output_bytes), str(output)]
        await asyncio.wait_for(run_logged_command(command, work, "v2 fixed export"), budget.timeout_seconds)
        raw = await run_logged_command(["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-show_streams", "-show_format", "-of", "json", str(output)],
                                       work, "v2 output verification", capture_stdout=True)
        measured = json.loads(raw)
        if options.fmt != "png":
            try:
                measured_duration = float(measured["format"]["duration"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RenderError("Export duration is unavailable") from exc
            if (not math.isfinite(measured_duration) or measured_duration <= 0
                    or abs(measured_duration - (min(duration, 6) if options.fmt == "gif" else duration)) > .15):
                raise RenderError("Export duration mismatch; possible truncation")
        streams = measured["streams"]
        if options.fmt in {"png", "gif"} and any(s["codec_type"] == "audio" for s in streams):
            raise RenderError("Visual-only output contains audio")
        if options.fmt != "mp3":
            video = next(s for s in streams if s["codec_type"] == "video")
            if (video["width"], video["height"]) != (width, height):
                raise RenderError("Export dimensions disagree")
            if options.fmt == "mp4":
                try:
                    numerator, denominator = video["avg_frame_rate"].split("/")
                    fps = float(numerator) / float(denominator)
                except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
                    raise RenderError("Export frame rate is unavailable") from exc
                if not math.isfinite(fps) or abs(fps - 30) > 1e-6:
                    raise RenderError("Export frame rate disagrees with fixed 30fps")
    size = output.stat().st_size
    if not 0 < size < budget.output_bytes:
        raise RenderError("Export empty or size-limited")
    digest = await _run_blocking_until_complete(partial(mode_export_hashes, {"output": output}))
    return {"file": output.name, "bytes": size, "sha256": digest["output"],
            "duration": min(duration, 6) if options.fmt == "gif" else duration,
            "frame_seconds": frame / 30 if frame is not None else None,
            "audio": "voice_only_no_bgm" if options.fmt == "mp3" else "finished_mix" if options.fmt == "mp4" else "none"}


DEMUXERS = {".mp4": "mov", ".mov": "mov", ".m4a": "mov", ".mkv": "matroska", ".avi": "avi", ".wav": "wav", ".mp3": "mp3"}


def input_args(path: Path) -> list[str]:
    if path.suffix.lower() == ".png":
        # Renderer/probe authorize the content-addressed catalog entry first.
        # Never let image2 expand a pattern, infer another codec or follow URLs.
        return ["-threads", str(THREADS), "-protocol_whitelist", "file", "-f", "image2",
                "-pattern_type", "none", "-c:v", "png", "-i", str(path)]
    demuxer = DEMUXERS.get(path.suffix.lower())
    if demuxer is None:
        raise RenderError("unsupported source container")
    # Force the demuxer: a playlist renamed .mp4 must not trigger network/file reads.
    return ["-threads", str(THREADS), "-protocol_whitelist", "file,pipe", "-f", demuxer, "-i", str(path)]


async def probe(path: Path, work: Path, *, asset_cache: dict[Path, AssetIndex] | None = None) -> dict[str, Any]:
    if path.suffix.lower() == ".png":
        try:
            row = verified_asset(path, path.stem, "image", asset_cache)
        except (AssetError, ValueError, OSError) as exc:
            raise RenderError("unknown or changed still image asset") from exc
        if not isinstance(row, ImageAsset):
            raise RenderError("still image requires an authorized image asset")
        # 120 is an editing budget, NOT a 120-second image file/EOF or 25fps
        # video. The real import already probed a single canonical PNG frame.
        return {"duration": MAX_DURATION, "is_image": True, "streams": [{
            "codec_type": "video", "codec_name": "png", "width": row.width,
            "height": row.height, "pix_fmt": "rgba", "avg_frame_rate": "0/1", "nb_frames": "1",
        }]}
    args = input_args(path)
    raw = await asyncio.wait_for(run_logged_command(
        ["ffprobe", "-v", "error", *args, "-show_streams", "-show_format", "-of", "json"],
        work, "studio probe", capture_stdout=True), 30)
    result = json.loads(raw)
    streams = result.get("streams", [])
    duration = float(result.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or not 0 < duration <= 3600:
        raise RenderError("source duration is unknown or exceeds 3600 seconds")
    for stream in streams:
        if stream.get("codec_type") == "video":
            if stream.get("color_transfer") in {"smpte2084", "arib-std-b67"} or stream.get("color_primaries") == "bt2020":
                raise RenderError("HDR/BT.2020 sources require an unsupported color-managed conversion")
            width, height = int(stream.get("width", 0)), int(stream.get("height", 0))
            if not (0 < width <= 4096 and 0 < height <= 4096 and width * height <= 4096 * 2160):
                raise RenderError("source dimensions exceed decoder budget")
            n, d = stream.get("avg_frame_rate", "0/1").split("/")
            fps = float(n) / max(1, float(d))
            if not 0 < fps <= 120:
                raise RenderError("source frame rate exceeds decoder budget")
    result["duration"] = duration
    return result


def ass_time(seconds: float) -> str:
    cs = round(seconds * 100)
    return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"


def srt_time(seconds: float) -> str:
    ms = round(seconds * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def plain_ass(text: str) -> str:
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\r", "").replace("\n", r"\N")


def keyframe_expression(points: list[KeyPoint], default: float, clock: str) -> str:
    """Only validated numbers and server-owned clock names enter FFmpeg syntax."""
    if clock not in {"t", "T"}:
        raise RenderError("invalid animation clock")
    if not points:
        return str(default)
    result = str(points[-1].value)
    for left, right in reversed(list(zip(points, points[1:]))):
        u = f"clip(({clock}-{left.time})/{right.time-left.time},0,1)"
        if left.easing == "ease_in":
            u = f"pow({u},2)"
        elif left.easing == "ease_out":
            u = f"(1-pow(1-{u},2))"
        elif left.easing == "ease_in_out":
            # Cubic smoothstep: 3u² - 2u³, symmetric with zero endpoint slope.
            u = f"(pow({u},2)*(3-2*{u}))"
        value = f"({left.value}+({right.value-left.value})*{u})"
        result = f"if(lt({clock},{right.time}),{value},{result})"
    return result


def alpha_expression(clip: Clip) -> str:
    opacity = keyframe_expression(clip.keyframes.opacity, clip.opacity, "T")
    mask = clip.mask
    if mask is None:
        return f"alpha(X,Y)*({opacity})"
    x = f"((X/W-{mask.x})/{mask.width/2})"
    y = f"((Y/H-{mask.y})/{mask.height/2})"
    distance = f"sqrt({x}*{x}+{y}*{y})" if mask.type == "ellipse" else f"max(abs({x}),abs({y}))"
    coverage = f"clip((1-({distance}))/{mask.feather},0,1)" if mask.feather else f"lte({distance},1)"
    if mask.invert:
        coverage = f"(1-({coverage}))"
    return f"alpha(X,Y)*({opacity})*({coverage})"


def transition_alpha_expression(transition: TransitionIn) -> str:
    """Full-frame coverage in incoming clip-local seconds, before timeline PTS.

    Reuse the bounded keyframe easing math, including cubic smoothstep. No
    client expressions or clocks enter the graph. At zero no incoming pixel
    is visible; at duration every incoming pixel is opaque, including edges.
    """
    progress = keyframe_expression([
        KeyPoint(time=0, value=0, easing=transition.easing),
        KeyPoint(time=transition.duration, value=1),
    ], 0, "T")
    coverage = {
        "dissolve": progress,
        "wipe_left": f"gte(X,W*(1-({progress})))",
        "wipe_right": f"lt(X,W*({progress}))",
        "wipe_up": f"gte(Y,H*(1-({progress})))",
        "wipe_down": f"lt(Y,H*({progress}))",
    }[transition.kind]
    return f"255*({coverage})"


def transition_summary(project: Project | TimelineEvaluation) -> dict[str, Any] | None:
    """Public, credential-free semantics for opted-in jobs/manifests only."""
    if isinstance(project, Project):
        project = evaluate_project(project)
    items = [{"track_id": track.id, "right_clip_id": clip.id, "start": clip.start,
              **clip.transition_in.model_dump()}
             for track in project.active_tracks() for clip in track.render_clips() if clip.transition_in is not None]
    if not items:
        return None
    return {
        "items": items,
        "geometry": "authored same-track overlaps only; no shifts, source trims or project duration changes",
        "visual": "opaque full-frame source RGB; incoming alpha only; wipe direction names describe the moving edge",
        "audio": "audio=true uses complementary LINEAR fades after atempo/effects on existing unmuted streams, independent of visual easing; audio=false leaves audio unchanged (overlap mix)",
        "export": "audio-only exports use only audio envelopes; subtitle-only exports do not render transitions",
        "warnings": (["audio=false: overlapping audio is mixed unchanged, not crossfaded; simultaneous streams can be louder."]
                     if any(not item["audio"] for item in items) else []),
    }


_COLOR_PRESETS: dict[str, tuple[str, ...]] = {
    "none": (),
    # Bounded RGB knots also affect neutral grays. Direct pixel verification on
    # the installed FFmpeg found colorbalance midtones unchanged on gray input.
    # "none" retains the legacy EQ-only path, with no additional conversion.
    "warm": ("curves=r='0/0 0.5/0.58 1/1':b='0/0 0.5/0.42 1/1'",),
    "cool": ("curves=r='0/0 0.5/0.42 1/1':b='0/0 0.5/0.58 1/1'",),
    "cinema": ("eq=contrast=1.08:saturation=0.85", "curves=r='0/0 0.25/0.24 0.75/0.78 1/1':b='0/0 0.25/0.30 0.75/0.73 1/1'"),
    "mono": ("hue=s=0",),
}


def color_filters(clip: Clip, lut_path: Path | None = None) -> list[str]:
    """Fixed SDR order: EQ, preset, balance, hue, fade, RGB curves, real 3D LUT.

    lut_path is a server-resolved, validated asset, never a project path. Fade
    is a lifted-black/reduced-white photographic look, NOT a temporal fade.
    Missing RGB channels are identity; y need not increase (creative inversion
    is valid). FFmpeg's bounded channel output clips any cubic overshoot.
    """
    if (clip.lut_id is None) != (lut_path is None):
        raise RenderError("unknown or missing lut_id")
    filters = [f"eq=brightness={clip.brightness}:contrast={clip.contrast}:saturation={clip.saturation}"]
    filters.extend(_COLOR_PRESETS[clip.color_preset])
    if clip.temperature:
        cast = clip.temperature * 0.15
        filters.append(f"curves=r='0/0 0.5/{0.5+cast} 1/1':b='0/0 0.5/{0.5-cast} 1/1'")
    if clip.shadows or clip.highlights:
        low, high = 0.25 + clip.shadows * 0.18, 0.75 + clip.highlights * 0.18
        filters.append(f"curves=all='0/0 0.25/{low} 0.5/0.5 0.75/{high} 1/1'")
    if clip.hue:
        filters.append(f"hue=h={clip.hue}")
    if clip.fade_amount:
        filters.append(f"curves=all='0/{0.16*clip.fade_amount} 0.5/0.5 1/{1-0.10*clip.fade_amount}'")
    if clip.rgb_curves:
        channels = []
        for name in ("red", "green", "blue"):
            if name in clip.rgb_curves:
                knots = " ".join(f"{p.x}/{p.y}" for p in clip.rgb_curves[name])
                channels.append(f"{name}='{knots}'")
        filters.append("curves=" + ":".join(channels))
    if lut_path is not None:
        filters.append(f"lut3d=file='{filter_path(lut_path)}':interp=tetrahedral")
    return filters


_TEXT_TEMPLATES: dict[str, tuple[str, bool, float, float, bool]] = {
    "news": ("FFFFFF", True, 3, 1, False),
    "outline": ("FFFFFF", False, 5, 0, False),
    "gold": ("FFD166", True, 2, 2, False),
    "note": ("FFF1BE", False, 4, 0, True),
}


def _text_fades(clip: Clip) -> tuple[int, int]:
    """Explicit fades win; 'fade' with neither set uses 20% edges capped at 300ms."""
    if clip.text_animation == "fade" and not (clip.fade_in or clip.fade_out):
        edge = round(min(0.3, clip.duration * 0.2) * 1000)
        return edge, edge
    return round(clip.fade_in * 1000), round(clip.fade_out * 1000)


def _staged_alpha(clip: Clip, start_ms: int, end_ms: int, total_ms: int) -> str:
    """Evaluate the ORIGINAL clip fade envelope, never restart it at each prefix."""
    fade_in, fade_out = _text_fades(clip)

    def alpha(at: int) -> int:
        coverage = min(1.0, at / fade_in if fade_in else 1.0,
                       (total_ms - at) / fade_out if fade_out else 1.0)
        return round(255 * (1 - clip.opacity * max(0, coverage)))

    tags = f"\\alpha&H{alpha(start_ms):02X}&"
    previous, previous_alpha = start_ms, alpha(start_ms)
    boundaries = sorted({p for p in (fade_in, total_ms - fade_out, end_ms) if start_ms < p <= end_ms})
    for boundary in boundaries:
        target = alpha(boundary)
        if target != previous_alpha:
            tags += f"\\t({previous-start_ms},{boundary-start_ms},\\alpha&H{target:02X}&)"
        previous, previous_alpha = boundary, target
    return tags


def _bounded_subtitle(text: str) -> str:
    if len(text.encode("utf-8")) > MAX_SUBTITLE_BYTES:
        raise RenderError("subtitle document exceeds 256 KiB; shorten text or reduce typewriter clips")
    return text


def subtitle_document(project: Project | TimelineEvaluation, options: ExportOptions, *, disclosure: bool = False) -> str:
    """ASS animations use normalized effect time, never inferred speech timestamps.

    Named templates replace only rendered color/bold/outline/shadow/background;
    stored custom fields, font size, position, opacity and text are not mutated.
    Choosing 'custom' restores authored styling. SRT stays one plain cue per clip.
    """
    if isinstance(project, Project):
        project = evaluate_project(project)
    width, height = options.dimensions
    events = sorted((c for t in project.active_tracks() if t.type == "text" for c in t.clips
                     if (not c.subtitle or options.subtitles != "none")), key=lambda c: c.start)
    if options.format == "srt":
        events = [c for c in events if c.subtitle]
        return _bounded_subtitle("\n".join(f"{i}\n{srt_time(c.start)} --> {srt_time(c.start + c.duration)}\n{c.text}\n" for i, c in enumerate(events, 1)))
    header = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\n"
              "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
              "Style: Studio,Noto Sans SC,42,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,20,20,30,1\n"
              "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    header = header.replace("[Events]", "Style: Box,Noto Sans SC,42,&H00FFFFFF,&H00FFFFFF,&H80000000,&H80000000,0,0,0,0,100,100,0,0,3,2,0,2,20,20,30,1\n[Events]")
    lines: list[str] = [header]
    byte_count = len(header.encode("utf-8"))

    def append(line: str) -> None:
        nonlocal byte_count
        byte_count += len((line + "\n").encode("utf-8"))
        if byte_count > MAX_SUBTITLE_BYTES:
            raise RenderError("subtitle document exceeds 256 KiB; shorten text or reduce typewriter clips")
        lines.append(line + "\n")

    for c in events:
        size = c.font_size * height / 1080 * (1.5 if c.subtitle and options.subtitles == "large" else 1)
        rgb, bold, outline, shadow, background = _TEXT_TEMPLATES.get(
            c.text_template, (c.color, c.bold, c.outline, c.shadow, c.background))
        tags = (f"\\fs{size:.2f}\\c&H{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}&"
                f"\\b{int(bold)}\\bord{outline}\\shad{shadow}")
        if c.x or c.y or not c.subtitle:
            tags += f"\\an5\\pos({width*(0.5+c.x/2):.2f},{height*(0.5+c.y/2):.2f})"
        style = "Box" if background else "Studio"
        start_cs, end_cs = round(c.start * 100), round((c.start + c.duration) * 100)
        span_cs = end_cs - start_cs
        if span_cs <= 0:
            raise RenderError("text duration collapses at ASS centisecond precision")
        if c.text_animation == "typewriter":
            if span_cs < 2:
                raise RenderError("typewriter requires at least two ASS centiseconds")
            # At most 64 prefix events. Long strings reveal groups, not fake word
            # timestamps. Absolute integer ticks avoid cumulative rounding drift.
            reveal_cs = min(span_cs - 1, max(1, round(span_cs * 0.6)))
            steps = min(len(c.text), MAX_TYPEWRITER_STEPS, reveal_cs)
            for index in range(1, steps + 1):
                begin = index * reveal_cs // steps
                end = (index + 1) * reveal_cs // steps if index < steps else span_cs
                count = (index * len(c.text) + steps - 1) // steps
                envelope = _staged_alpha(c, begin * 10, end * 10, span_cs * 10)
                append(f"Dialogue: 0,{ass_time((start_cs+begin)/100)},{ass_time((start_cs+end)/100)},{style},,0,0,0,,"
                       f"{{{tags}{envelope}}}{plain_ass(c.text[:count])}")
        else:
            fade_in, fade_out = _text_fades(c)
            tags += f"\\alpha&H{round((1-c.opacity)*255):02X}&\\fad({fade_in},{fade_out})"
            if c.text_animation == "pop":
                attack = max(1, round(min(0.3, c.duration * 0.2) * 1000))
                tags += f"\\fscx70\\fscy70\\t(0,{attack},\\fscx100\\fscy100)"
            append(f"Dialogue: 0,{ass_time(c.start)},{ass_time(c.start+c.duration)},{style},,0,0,0,,{{{tags}}}{plain_ass(c.text)}")
    if disclosure:
        if round(project.duration * 100) <= 0:
            raise RenderError("disclosure duration collapses at ASS centisecond precision")
        append(f"Dialogue: 100,{ass_time(0)},{ass_time(project.duration)},Studio,,0,0,0,,"
               f"{{\\an7\\pos(20,20)\\fs{max(16, height*0.035):.2f}\\bord3}}AI生成示意画面（本片含AI生成内容）")
    return "".join(lines)


def filter_path(path: Path) -> str:
    # Paths are server generated, never user-supplied filter expressions.
    # Two parsing levels: preserve a backslash+quote through the graph's outer
    # quotes so the option parser receives an escaped literal apostrophe.
    return str(path.resolve()).replace("\\", "/").replace(":", r"\:").replace("'", r"\'\''")


@dataclass(frozen=True)
class ModeExport:
    """Server-generated canonical documents; never accepted in Project JSON."""
    captions: str
    graphics: str = ""
    fade_in: float = 0.0
    fade_out: float = 0.0
    source_duration: float = 0.0


class _ModeClip(Clip):
    _timeline_limit: ClassVar[float] = MAX_MODE_DURATION
    duration: float = Field(gt=0, le=MAX_MODE_DURATION)


class _ModeTrack(Track):
    clips: list[_ModeClip] = Field(max_length=1)


@dataclass(frozen=True)
class SimpleModeProject:
    """Internal fixed composition, NEVER a Project JSON validation context.

    Rebuild the complete ordinary Project template on every evaluation. Only
    its single full-length source per lane has an independent duration budget;
    no caller-provided clips, effects, nesting, tracks or metadata are accepted.
    """
    format: Literal["mp4", "mp3", "gif"]
    duration: float
    limit: float

    def template(self) -> Project:
        if (self.format not in {"mp4", "mp3", "gif"}
                or not math.isfinite(self.limit) or not 0 < self.limit <= MAX_MODE_DURATION
                or not math.isfinite(self.duration) or not 0 < self.duration <= self.limit
                or (self.format == "gif" and self.duration > 6)):
            raise RenderError("mode export exceeds independent duration budget")
        duration = min(self.duration, MAX_DURATION)
        tracks = []
        if self.format != "mp3":
            tracks.append(Track(id="video", type="video", clips=[Clip(
                id="picture", source_id="video_only", duration=duration, mute=True, fit="cover")]))
        if self.format != "gif":
            tracks.append(Track(id="audio", type="audio", clips=[Clip(
                id="sound", source_id="mode_voice" if self.format == "mp3" else "final", duration=duration)]))
        return Project(tracks=tracks)

    def source_ids(self) -> set[str]:
        return self.template().source_ids()

    def lut_ids(self) -> set[str]:
        return set()

    @property
    def sequences(self) -> tuple[()]:
        return ()

    def evaluate(self) -> TimelineEvaluation:
        ordinary = evaluate_project(self.template())
        tracks = tuple(_ModeTrack.model_validate({**t.model_dump(), "clips": [
            {**c.model_dump(), "duration": self.duration} for c in t.clips]}) for t in ordinary.tracks)
        return TimelineEvaluation(None, tracks, self.duration, (), ordinary.clip_count, ordinary.decoder_inputs)

    def model_dump(self) -> dict[str, Any]:
        # Audit receipt, deliberately NOT importable/editable Project JSON.
        return {"kind": "server_simple_mode_export", "format": self.format,
                "duration": self.duration, "limit": self.limit,
                "tracks": [t.model_dump() for t in self.evaluate().tracks]}


async def render_project(project: Project | SimpleModeProject, options: ExportOptions, sources: dict[str, Path], work: Path,
                         *, disclosure: bool = False, timeout: float = 300,
                         luts: dict[str, Path] | None = None,
                         mode_export: ModeExport | None = None) -> dict[str, Any]:
    """Render one immutable snapshot; caller owns job directory and cancellation cleanup."""
    mode_plan = project if type(project) is SimpleModeProject else None
    simple_mode = mode_plan is not None
    project = mode_plan.template() if mode_plan is not None else Project.model_validate(project.model_dump())
    options = ExportOptions.model_validate(options.model_dump())
    if mode_plan is not None and (options.format != mode_plan.format or (options.format != "mp3" and mode_export is None)):
        raise RenderError("mode export format/canonical overlay binding is missing")
    luts = dict(luts or {})
    asset_cache: dict[Path, AssetIndex] = {}
    # All declared references, including inactive sequences, metadata and early
    # text/audio exports. Only the evaluated leaves will actually be decoded.
    for source_id in project.source_ids():
        path = sources.get(source_id)
        if path is None or not path.is_file():
            raise RenderError("unknown or missing source_id in saved project")
        if path.suffix.lower() == ".png" and not is_image_id(source_id):
            raise RenderError("PNG sources must be imported, sanitized catalog images")
    for lut_id in project.lut_ids():
        if lut_id not in luts:
            raise RenderError("unknown or missing lut_id")
        try:
            verified_asset(luts[lut_id], lut_id, "lut", asset_cache)
        except (AssetError, ValueError, OSError) as exc:
            raise RenderError("unknown or changed LUT asset") from exc
    for source_id in project.source_ids():
        if not is_image_id(source_id):
            continue
        if source_id not in sources:
            raise RenderError("unknown or missing still image source_id")
        try:
            verified_asset(sources[source_id], source_id, "image", asset_cache)
        except (AssetError, ValueError, OSError) as exc:
            raise RenderError("unknown or changed still image asset") from exc
    timeline = mode_plan.evaluate() if mode_plan is not None else evaluate_project(project)
    sequences = sequence_summary(project, timeline)
    duration = timeline.duration
    if not 0 < duration <= (mode_plan.limit if mode_plan is not None else MAX_DURATION):
        raise RenderError("active timeline must be between 0 and 120 seconds")
    if options.frame_time is not None and options.frame_time >= duration:
        raise RenderError("frame_time must be inside the active timeline")
    if options.format == "gif":
        duration = min(duration, 6.0)
    if disclosure and options.format in {"mp3", "wav", "srt", "ass"}:
        raise RenderError("this mode strips burned AI disclosure; export a disclosed video instead")
    work.mkdir(parents=True, exist_ok=True)
    output = work / f"output.{options.format}"
    if options.format in {"srt", "ass"}:
        if not any(t.type == "text" and any(c.subtitle for c in t.clips) for t in timeline.active_tracks()):
            raise RenderError("no actual timed subtitles in the project")
        # Match the generator's UTF-8 byte budget on Windows as well as POSIX.
        output.write_text(subtitle_document(timeline, options), encoding="utf-8", newline="\n")
        text_result: dict[str, Any] = {"file": output.name, "bytes": output.stat().st_size, "duration": duration, "applied": options.model_dump()}
        text_transitions = transition_summary(timeline)
        if text_transitions is not None:
            text_result["transition_semantics"] = text_transitions
        if sequences is not None:
            text_result["sequence_semantics"] = sequences
        return text_result
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RenderError("FFmpeg and ffprobe must be available on PATH")
    bitrate = options.audio_bitrate_kbps if simple_mode and options.format == "mp3" else options.video_bitrate_kbps + options.audio_bitrate_kbps
    if duration * bitrate * 125 > MAX_BYTES * 0.85:
        raise RenderError("requested duration/bitrate exceeds output size budget")
    width, height = options.dimensions
    audio_only = options.format in {"mp3", "wav"}
    visual_only = options.format in {"gif", "png"}
    command = ["ffmpeg", "-y", "-nostdin", "-v", "error", "-filter_complex_threads", "1"]
    graph: list[str] = []
    if not audio_only:
        graph.append(f"color=c=black:s={width}x{height}:r={options.fps}:d={duration}[base0]")
    voice_only = simple_mode and options.format == "mp3"
    if not voice_only:
        graph.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={duration}[silence]")
    audio_labels = [] if voice_only else ["[silence]"]
    video_number = 0
    source_number = 0
    probes: dict[str, dict[str, Any]] = {}
    actual_audio = False
    actual_video = False
    decoder_pixels = 0
    for track in timeline.active_tracks():
        outgoing_transitions = {c.transition_in.left_clip_id: c.transition_in for c in track.clips if c.transition_in is not None}
        for clip in track.render_clips():
            incoming = clip.transition_in
            outgoing = outgoing_transitions.get(clip.id)
            lut_path = luts.get(clip.lut_id) if clip.lut_id else None
            end = clip.start + clip.duration
            if track.type == "text":
                continue
            if track.type == "adjustment":
                if not audio_only:
                    enabled = [f"{effect}:enable='gte(t,{clip.start})*lt(t,{end})'" for effect in color_filters(clip, lut_path)]
                    graph.append(f"[base{video_number}]" + ",".join(enabled) + f"[base{video_number+1}]")
                    video_number += 1
                continue
            path = sources.get(clip.source_id or "")
            if path is None or not path.is_file():
                raise RenderError("unknown or missing source_id")
            if path.suffix.lower() == ".png" and (not is_image_id(clip.source_id) or path.stem != clip.source_id):
                raise RenderError("PNG sources must be imported, sanitized catalog images")
            if is_image_id(clip.source_id) and (path.suffix != ".png" or path.stem != clip.source_id):
                raise RenderError("image source mapping does not match its catalog ID")
            if clip.source_id not in probes:
                if is_image_id(clip.source_id):
                    probes[clip.source_id or ""] = await probe(path, work, asset_cache=asset_cache)
                else:
                    probes[clip.source_id or ""] = await probe(path, work)
                    asset_cache.clear()  # Do not reuse a catalog cache across media awaits.
            info = probes[clip.source_id or ""]
            is_image = info.get("is_image") is True
            streams = info["streams"]
            video = next((s for s in streams if s.get("codec_type") == "video"), None)
            has_audio = any(s.get("codec_type") == "audio" for s in streams)
            decoder_pixels += (video["width"] * video["height"]) if video else 0
            if source_number >= MAX_DECODER_INPUTS or decoder_pixels > 32 * 1920 * 1080:
                raise RenderError("render decoder budget exceeded (16 inputs / 32 full-HD frames)")
            if track.type == "audio" and not has_audio:
                raise RenderError("audio track source has no audio stream")
            if track.type != "audio" and video is None:
                raise RenderError("visual track source has no video stream")
            if not has_audio and (clip.volume != 1 or clip.pan != 0 or clip.audio_effect != "none" or clip.bass_db or clip.treble_db):
                raise RenderError("audio controls require a source with an audio stream")
            read_duration = clip.duration * clip.speed
            if clip.freeze and video:
                n, d = video["avg_frame_rate"].split("/")
                read_duration = float(d) / float(n)
                if clip.trim >= info["duration"]:
                    raise RenderError("freeze frame must be inside source duration")
            if not is_image and clip.trim + read_duration > info["duration"] + 0.025:
                raise RenderError("clip trim + duration * speed exceeds source duration")
            if clip.reverse and video:
                n, d = video["avg_frame_rate"].split("/")
                memory = video["width"] * video["height"] * 4 * read_duration * float(n) / max(float(d), 1)
                if memory > MAX_REVERSE_BYTES:
                    raise RenderError("reverse buffer exceeds 128 MiB; trim to a shorter clip")
            if voice_only:
                command += input_args(path)
            elif is_image:
                command += ["-loop", "1", "-framerate", str(options.fps), "-t", str(clip.duration), *input_args(path)]
            else:
                command += ["-ss", str(clip.trim), "-t", str(read_duration), *input_args(path)]
            idx = source_number
            source_number += 1
            if video is not None and track.type != "audio" and not audio_only:
                actual_video = True
                vf = [f"trim=duration={read_duration}", "setpts=PTS-STARTPTS"]
                if clip.freeze:
                    vf += ["trim=end_frame=1", f"tpad=stop_mode=clone:stop_duration={clip.duration}", f"trim=duration={clip.duration}"]
                if clip.reverse:
                    vf.append("reverse")
                vf.append(f"setpts=PTS/{clip.speed}")
                crop = clip.crop
                vf.append(f"crop=iw*{crop.width}:ih*{crop.height}:iw*{crop.x}:ih*{crop.y}")
                if clip.rotation == 90:
                    vf.append("transpose=1")
                elif clip.rotation == 180:
                    vf.extend(["hflip", "vflip"])
                elif clip.rotation == 270:
                    vf.append("transpose=2")
                if clip.mirror:
                    vf.append("hflip")
                if clip.flip:
                    vf.append("vflip")
                w, h = max(2, int(width*clip.scale)//2*2), max(2, int(height*clip.scale)//2*2)
                vf += [f"scale={w}:{h}:force_original_aspect_ratio={'increase' if clip.fit == 'cover' else 'decrease'}:force_divisible_by=2"]
                if clip.fit == "cover":
                    vf.append(f"crop={w}:{h}")
                vf += ["setsar=1", f"fps={options.fps}"]
                video_input = f"[{idx}:v:0]"
                if is_image or lut_path is not None:
                    # RGB/YUV color filters may negotiate away intrinsic alpha.
                    # Protect it explicitly only for the new asset paths; the
                    # existing no-LUT video/transition command stays unchanged.
                    graph.append(video_input + ",".join(vf) + f",format=rgba,split[cr{idx}][ca{idx}]")
                    graph.append(f"[ca{idx}]alphaextract[keepa{idx}]")
                    graph.append(f"[cr{idx}]format=rgb24," + ",".join(color_filters(clip, lut_path)) + f",format=rgb24[col{idx}]")
                    video_input = f"[col{idx}][keepa{idx}]"
                    vf = ["alphamerge"]
                else:
                    vf += color_filters(clip)
                if clip.keyframes.scale:
                    scale = keyframe_expression(clip.keyframes.scale, clip.scale, "t")
                    vf.append(f"scale=w='max(2,trunc(iw*({scale})/{clip.scale}/2)*2)':h='max(2,trunc(ih*({scale})/{clip.scale}/2)*2)':eval=frame")
                if clip.sharpen:
                    vf.append(f"unsharp=5:5:{clip.sharpen}:5:5:0")
                if clip.noise:
                    vf.append(f"noise=alls={clip.noise}:allf=t:all_seed=1")
                if clip.chroma_color:
                    vf.append(f"chromakey=0x{clip.chroma_color}:{clip.chroma_similarity}:{clip.chroma_blend}")
                if incoming is not None or outgoing is not None:
                    # Explicit full-frame transition contract: discard intrinsic
                    # source alpha so the outgoing picture cannot expose lower
                    # tracks through a second fade. Legacy sources are untouched.
                    vf.append("format=rgb24")
                vf.append("format=rgba")
                if clip.keyframes.scale:
                    # Keep downstream alpha/compositor frame geometry stable. Without
                    # this canvas, FFmpeg can reinitialize filters and reset clocks
                    # whenever the animated dimensions change.
                    vf += ["setsar=1", f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black@0:eval=frame"]
                if incoming is not None:
                    vf.append(f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{transition_alpha_expression(incoming)}'")
                elif clip.mask or clip.keyframes.opacity:
                    vf.append(f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{alpha_expression(clip)}'")
                else:
                    vf.append(f"colorchannelmixer=aa={clip.opacity}")
                if clip.fade_in:
                    vf.append(f"fade=t=in:st=0:d={clip.fade_in}:alpha=1")
                if clip.fade_out:
                    vf.append(f"fade=t=out:st={clip.duration-clip.fade_out}:d={clip.fade_out}:alpha=1")
                vf.append(f"setpts=PTS+{clip.start}/TB")
                graph.append(video_input + ",".join(vf) + f"[v{idx}]")
                x = keyframe_expression(clip.keyframes.x, clip.x, "t")
                y = keyframe_expression(clip.keyframes.y, clip.y, "t")
                # Replace only the clock token, never letters in clip()/lt().
                x = re.sub(r"\bt\b", f"(t-{clip.start})", x)
                y = re.sub(r"\bt\b", f"(t-{clip.start})", y)
                graph.append(f"[base{video_number}][v{idx}]overlay=x='(W-w)/2+W*({x})':y='(H-h)/2+H*({y})':eval=frame:eof_action=pass:repeatlast=0:enable='gte(t,{clip.start})*lt(t,{end})'[base{video_number+1}]")
                video_number += 1
            if has_audio and not clip.mute and not visual_only:
                actual_audio = True
                if voice_only:
                    graph.append(f"[{idx}:a:0]anull[a{idx}]")
                    audio_labels.append(f"[a{idx}]")
                    continue
                af = [f"atrim=duration={read_duration}", "asetpts=PTS-STARTPTS"]
                if clip.reverse:
                    af.append("areverse")
                # Even atempo=1 runs WSOLA: real odd-length mono PCM lost 55
                # samples, then the silent mix bed concealed the missing tail.
                # A true 1x clip must bypass the tempo processor, not pad loss.
                if clip.speed != 1:
                    af.append(f"atempo={clip.speed}")
                af += ["aresample=48000", "aformat=channel_layouts=stereo", f"volume={clip.volume}", f"pan=stereo|c0={min(1, 1-clip.pan)}*c0|c1={min(1, 1+clip.pan)}*c1"]
                # Compensate AFFTDN on the post-tempo 48 kHz clock, before
                # authored fades/timeline delay. Only synthetic warm-up/flush
                # samples are removed; Studio denoise adds no speech high-pass.
                effects = {"compressor": "acompressor=threshold=0.125:ratio=4", "limiter": "alimiter=limit=0.95:level=false", "denoise": AFFTDN_CLOCK_FILTER, "invert": "volume=-1", "reverb": "aecho=0.8:0.88:60|120:0.3|0.2", "normalize": "loudnorm=I=-16:TP=-1.5:LRA=11"}
                if clip.audio_effect in effects:
                    af.append(effects[clip.audio_effect])
                if clip.bass_db:
                    af.append(f"bass=g={clip.bass_db}:f=100")
                if clip.treble_db:
                    af.append(f"treble=g={clip.treble_db}:f=3000")
                if clip.fade_in:
                    af.append(f"afade=t=in:d={clip.fade_in}")
                if clip.fade_out:
                    af.append(f"afade=t=out:st={clip.duration-clip.fade_out}:d={clip.fade_out}")
                # Local output seconds AFTER tempo/effects, before timeline
                # delay. Never use acrossfade (it would shorten the timeline).
                if (incoming is not None and incoming.audio) or (outgoing is not None and outgoing.audio):
                    # loudnorm can emit 192 kHz. Only this opted-in envelope path
                    # restores 48 kHz before the existing sample-count adelay;
                    # otherwise its timeline offset would run four times fast.
                    # No sample padding/trimming or effect settings are changed.
                    af.append("aresample=48000")
                if incoming is not None and incoming.audio:
                    af.append(f"afade=t=in:st=0:d={incoming.duration}:curve=tri")
                if outgoing is not None and outgoing.audio:
                    af.append(f"afade=t=out:st={clip.duration-outgoing.duration}:d={outgoing.duration}:curve=tri")
                af += [f"atrim=duration={clip.duration}", f"adelay={round(clip.start*48000)}S:all=1"]
                graph.append(f"[{idx}:a:0]" + ",".join(af) + f"[a{idx}]")
                audio_labels.append(f"[a{idx}]")
    if audio_only and not actual_audio:
        raise RenderError("no audible audio stream to export")
    if not audio_only and not actual_video and not any(t.type == "text" and t.clips for t in timeline.active_tracks()):
        raise RenderError("no visual content to export")
    if voice_only:
        # Exact assembled PCM: no silent bed or limiter can conceal lost EOF.
        graph.append("".join(audio_labels) + "anull[audio]")
    else:
        graph.append("".join(audio_labels) + f"amix=inputs={len(audio_labels)}:normalize=0:duration=longest,alimiter=limit=0.95:level=false:latency=true,atrim=duration={duration}[audio]")
    if not audio_only:
        last = f"[base{video_number}]"
        if mode_export is not None:
            from .rendering import _prepare_ass_font_directory
            local_fonts = _prepare_ass_font_directory(work, Path(__file__).parent / "assets/fonts")
            for index, document in enumerate((mode_export.captions, mode_export.graphics)):
                if not document:
                    continue
                if len(document.encode("utf-8")) > MAX_SUBTITLE_BYTES:
                    raise RenderError("mode subtitle document exceeds safe size")
                subtitles = work / f"mode-export-{index}.ass"
                subtitles.write_text(document, encoding="utf-8", newline="\n")
                graph.append(f"{last}subtitles=filename='{filter_path(subtitles)}':fontsdir='{filter_path(local_fonts)}'[mode{index}]")
                last = f"[mode{index}]"
            # Same order as pipeline: captions, graphics, then finishing fades.
            # Keep the full-film clock: GIF EOF is NOT the film's fade-out.
            if mode_export.fade_in:
                graph.append(f"{last}fade=t=in:d={mode_export.fade_in}[modefadein]")
                last = "[modefadein]"
            if mode_export.fade_out:
                start = max(0, mode_export.source_duration - mode_export.fade_out)
                graph.append(f"{last}fade=t=out:st={start}:d={mode_export.fade_out}[modefadeout]")
                last = "[modefadeout]"
        if disclosure or any(t.type == "text" and t.clips for t in timeline.active_tracks()):
            subtitles = work / "studio.ass"
            # Burned captions/disclosure use the same bounded, canonical LF bytes.
            subtitles.write_text(subtitle_document(timeline, options, disclosure=disclosure), encoding="utf-8", newline="\n")
            fonts = Path(__file__).parent / "assets" / "fonts"
            font_files = list(fonts.glob("*.otf")) + list(fonts.glob("*.ttf"))
            if not font_files:
                raise RenderError("bundled subtitle font is missing")
            local_fonts = work / "fonts"
            local_fonts.mkdir(exist_ok=True)
            for font in font_files:
                shutil.copyfile(font, local_fonts / font.name)
            graph.append(f"{last}subtitles=filename='{filter_path(subtitles)}':fontsdir='{filter_path(local_fonts)}'[texted]")
            last = "[texted]"
        if options.format == "gif":
            graph.extend([f"{last}trim=duration={duration},split[g0][g1]", "[g0]palettegen[p]", "[g1][p]paletteuse[video]"])
        elif options.format == "png":
            frame = math.floor((options.frame_time or 0) * options.fps + 1e-9)
            graph.append(f"{last}trim=start_frame={frame}:end_frame={frame+1},setpts=PTS-STARTPTS,format=rgb24[video]")
        else:
            # Frame properties from the synthetic canvas/overlay can override
            # encoder-only -color_* options. Tag the FINAL SDR frames too;
            # verified ffprobe must retain all three BT.709 attributes. This
            # is SDR tagging, not a claim of color-managed HDR conversion.
            # Convert the actual YUV matrix too. Merely tagging RGB->YUV output
            # BT.709 left a BT.601 matrix on 360p frames (lime decoded at 212
            # rather than 250); never relabel those samples as a conversion.
            graph.append(f"{last}scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709[video]")
    # Every filter output must be connected, including unused silent beds.
    if visual_only:
        graph.append("[audio]anullsink")
    filter_graph = ";".join(graph)
    if len(filter_graph) > 20000:
        raise RenderError("filter graph exceeds bounded command size")
    command += ["-filter_complex", filter_graph]
    if not audio_only:
        command += ["-map", "[video]", "-r", str(options.fps)]
    if not visual_only:
        command += ["-map", "[audio]", "-ar", "48000", "-ac", "2"]
    if options.format in {"mp4", "mov", "mkv", "avi"}:
        command += ["-c:v", "libx264" if options.format != "avi" else "mpeg4", "-b:v", f"{options.video_bitrate_kbps}k", "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
        if options.format != "avi":
            command += ["-preset", "veryfast", "-maxrate", f"{options.video_bitrate_kbps}k", "-bufsize", f"{options.video_bitrate_kbps*2}k"]
        command += ["-c:a", "aac" if options.format != "avi" else "libmp3lame", "-b:a", f"{options.audio_bitrate_kbps}k"]
        if options.format in {"mp4", "mov"}:
            command += ["-movflags", "+faststart"]
    elif options.format == "mp3":
        command += ["-c:a", "libmp3lame", "-b:a", f"{options.audio_bitrate_kbps}k"]
    elif options.format == "wav":
        command += ["-c:a", "pcm_s16le"]
    elif options.format == "png":
        command += ["-frames:v", "1", "-update", "1", "-c:v", "png"]
    elif options.format == "gif":
        command += ["-loop", "0"]
    command += ["-threads", str(THREADS)]
    if not voice_only:
        command += ["-t", str(duration)]
    command += ["-fs", str(MAX_BYTES), str(output)]
    await asyncio.wait_for(run_logged_command(command, work, "studio render"), timeout)
    if not output.is_file() or not 0 < output.stat().st_size < MAX_BYTES:
        raise RenderError("render produced empty or size-limited output")
    raw = await asyncio.wait_for(run_logged_command(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(output)],
        work, "studio output verification", capture_stdout=True), 30)
    measured = json.loads(raw)
    if not audio_only:
        output_video: dict[str, Any] = next((s for s in measured["streams"] if s["codec_type"] == "video"), dict[str, Any]())
        if (output_video.get("width"), output_video.get("height")) != (width, height):
            raise RenderError("output dimensions do not match requested dimensions")
        if options.format not in {"png", "gif"}:
            n, d = output_video["avg_frame_rate"].split("/")
            if abs(float(n)/float(d) - options.fps) > 0.01:
                raise RenderError("output frame rate does not match request")
    if options.format != "png":
        actual = float(measured["format"].get("duration", 0))
        if abs(actual - duration) > max(0.15, 2/options.fps):
            raise RenderError("output duration mismatch (possibly size-truncated)")
    public_probe: dict[str, Any] = {
        "streams": [{k: s[k] for k in ("codec_type", "codec_name", "width", "height", "avg_frame_rate", "sample_rate", "channels", "bit_rate", "color_space", "color_transfer", "color_primaries") if k in s} for s in measured["streams"]],
        "format": {k: measured["format"][k] for k in ("format_name", "duration", "size", "bit_rate") if k in measured["format"]},
    }
    result = {
        "file": output.name, "bytes": output.stat().st_size, "duration": duration,
        "probe": public_probe, "applied": options.model_dump(), "disclosure": disclosure,
        "png_semantics": "single selected frame; frame_time floors to output frame grid" if options.format == "png" else None,
        "frame_time": math.floor((options.frame_time or 0)*options.fps + 1e-9)/options.fps if options.format == "png" else None,
        "gif_duration_limit": 6 if options.format == "gif" else None,
        "gif_fps_semantics": "centisecond frame delays; requested rate is quantized" if options.format == "gif" else None,
    }
    transitions = transition_summary(timeline)
    if transitions is not None:
        result["transition_semantics"] = transitions
    if sequences is not None:
        result["sequence_semantics"] = sequences
    return result