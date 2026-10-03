"""Opt-in studio router; the host supplies task/session authorization.

Nothing in this module mounts routes or changes TaskRecord/pipeline state. Use a
single application worker, as required by the existing TaskManager.
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import math
import os
import re
import shutil
import time
import wave
from contextlib import asynccontextmanager
from fractions import Fraction
from itertools import islice
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import Field, ValidationError, model_validator
from starlette.requests import ClientDisconnect

from . import studio_assets, studio_proxy
from .media import MediaProcessingError
from .models import GeneratedMediaDisclosureManifest
from .operations import InsufficientDiskSpaceError
from .publication import require_publication
from .storage import write_json_atomic
from .studio_assets import contained, is_image_id
from .studio_render import (
    MAX_BYTES, MAX_DURATION, MAX_SUBTITLE_BYTES, MAX_TRANSITION_DURATION, Clip, ExportOptions, ID, Marker, Project, RenderError,
    StrictModel, Track, ModeExport, SimpleModeProject, MAX_MODE_DURATION, probe, render_project, transition_summary,
)
from .studio_sequences import (
    MAX_CLIPS, MAX_DECODER_INPUTS, MAX_EXPANDED_CLIPS, MAX_EXPANDED_TRACKS, MAX_MARKERS,
    MAX_NESTING_DEPTH, MAX_SEQUENCES, MAX_TOTAL_TRACKS, MAX_TRACKS,
    evaluate_project, project_timelines, sequence_summary,
)
from .task_operations import legacy_task_busy, task_operation_busy

MAX_JSON = 256 * 1024
MAX_STATE = 32 * 1024 * 1024
MAX_HISTORY = 100
MAX_JOBS = 20
MAX_TASK_BYTES = 512 * 1024 * 1024
_ASSET_BODY_TIMEOUT = 60
_ASSET_STORAGE_POLL_SECONDS = 0.25
# Shared between factory instances in this process; no unbounded per-task lock map.
_BUSY: set[str] = set()
_INGESTING: dict[str, asyncio.Task[Any]] = {}
_PREPARING: dict[str, asyncio.Task[Any]] = {}
_DISK_RESERVATIONS: dict[str, int] = {}


def studio_task_busy(task_dir: Path) -> bool:
    """Shared with pipeline mutations/deletion to protect in-flight media reads."""
    return str(Path(task_dir).resolve()) in _BUSY


def active_studio_tasks() -> list[asyncio.Task[Any]]:
    return [task for task in [*_RUNNING.values(), *_INGESTING.values(), *_PREPARING.values()] if not task.done()]
_RUNNING: dict[str, asyncio.Task[Any]] = {}


class ProjectWrite(StrictModel):
    expected_revision: int = Field(ge=0)
    project: Project


class RevisionRequest(StrictModel):
    expected_revision: int = Field(ge=0)


class RenderRequest(RevisionRequest):
    options: ExportOptions = Field(default_factory=ExportOptions)


class ProxyRequest(RevisionRequest):
    expected_revision: int = Field(strict=True, ge=0)
    source_id: ID


class SubtitleImport(RevisionRequest):
    track_id: ID
    srt: str = Field(min_length=1, max_length=64000)


def parse_srt(text: str) -> list[Clip]:
    """Strict plain-text SRT subset. No ASS, markup, paths or filter execution."""
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    blocks = re.split(r"\n[ \t]*\n", text)
    if not 1 <= len(blocks) <= 64:
        raise ValueError("SRT requires 1–64 cues")
    stamp = r"(\d{2}):([0-5]\d):([0-5]\d),(\d{3})"
    pattern = re.compile(r"(?:\d+\n)?" + stamp + r" --> " + stamp + r"\n(.+)", re.DOTALL)
    clips: list[Clip] = []
    previous_end = 0.0
    for block in blocks:
        match = pattern.fullmatch(block)
        if match is None:
            raise ValueError("invalid SRT cue; use HH:MM:SS,mmm --> HH:MM:SS,mmm")
        fields = match.groups()
        def seconds(offset: int) -> float:
            h, m, s, ms = (int(v) for v in fields[offset:offset+4])
            return h*3600 + m*60 + s + ms/1000
        start, end = seconds(0), seconds(4)
        plain = fields[8].strip()
        if start < previous_end or end <= start or end > MAX_DURATION:
            raise ValueError("SRT cues must be ordered, non-overlapping and within 120 seconds")
        if not plain or re.search(r"<[^>]*>|\{.*?\}|\\[Nnh]", plain):
            raise ValueError("SRT import accepts literal plain text, not HTML/ASS markup")
        clips.append(Clip(id="srt_" + uuid4().hex, start=start, duration=end-start, text=plain))
        previous_end = end
    return clips


class Action(RevisionRequest):
    op: Literal["split", "delete", "duplicate", "move", "trim", "ripple_delete", "ripple_trim", "slip", "roll", "slide", "marker", "clear_markers", "track", "undo", "redo"]
    clip_id: ID | None = None
    track_id: ID | None = None
    new_id: ID | None = None
    at: float | None = Field(default=None, ge=0, le=MAX_DURATION)
    trim: float | None = Field(default=None, ge=0, le=3600)
    duration: float | None = Field(default=None, gt=0, le=MAX_DURATION)
    label: str | None = Field(default=None, max_length=120)
    locked: bool | None = None
    hidden: bool | None = None
    solo: bool | None = None
    color: Literal["gold", "cyan", "red", "purple", "gray"] | None = None

    @model_validator(mode="after")
    def shape(self) -> Action:
        fields: dict[str, tuple[set[str], set[str]]] = {
            "split": ({"clip_id", "at", "new_id"}, set()),
            "delete": ({"clip_id"}, set()),
            "duplicate": ({"clip_id", "new_id", "at"}, {"track_id"}),
            "move": ({"clip_id", "at"}, {"track_id"}),
            "trim": ({"clip_id", "trim", "duration"}, set()),
            "ripple_delete": ({"clip_id"}, set()),
            "ripple_trim": ({"clip_id", "trim", "duration"}, set()),
            "slip": ({"clip_id", "trim"}, set()),
            "roll": ({"clip_id", "at"}, set()),
            "slide": ({"clip_id", "at"}, set()),
            "marker": ({"new_id", "at"}, {"label"}),
            "clear_markers": (set(), set()),
            "track": ({"track_id"}, {"locked", "hidden", "solo", "color"}),
            "undo": (set(), set()), "redo": (set(), set()),
        }
        required, optional = fields[self.op]
        provided = {k for k, v in self.model_dump().items() if v is not None} - {"op", "expected_revision"}
        if not required <= provided or provided - required - optional:
            raise ValueError(f"{self.op}: required={sorted(required)}, optional={sorted(optional)}")
        if self.op == "track" and not provided & optional:
            raise ValueError("track action requires at least one flag or color")
        return self


# Exact keys/labels from PRO_DEF, not a copy of the prototype JavaScript.
PROTOTYPE_TOOLS = {
    "timeline": "addTrack|无限轨道;trkGroup|轨道分组;trkColor|轨道颜色标记;proxy|代理剪辑;tc|时间码;marker|标记点;inout|入点 / 出点;snap|磁性吸附;ripple|波纹编辑;tlPick|多时间线;nest|时间线嵌套",
    "clip": "edit|基础编辑;undo|撤销 / 重做;trimMode|修剪模式;freeze|定格;reverse|倒放;mirror|镜像;rotate|旋转;frameCrop|帧精确裁剪;speed|变速;interp|智能补帧 Pro;kf|关键帧;kfCurve|关键帧曲线;kfParam|关键帧参数;adjLayer|调整图层",
    "cam": "camN|机位数;camAlign|对齐方式;camWave|波形对齐;camBatch|批量切换 / 调序;camNest|机位嵌套",
    "audio": "mixVol|混音台 · 音量;mixPan|声像;mixMute|静音 / 独奏;fxAudio|效果器;recMulti|多麦克风录音;recFx|录音处理;voiceLib|音色库;voiceClone|音色克隆 Pro;ttsBatch|批量配音 / 导出;wave|专业音频编辑;aiAudio|智能音频",
    "ai": "aiCut|AI 智能剪辑;aiNarr|AI 智能解说;aiKey|AI 智能抠像;aiErase|AI 消除;aiFix|AI 画质修复 Pro;aiLip|AI 对口型;aiTrans|AI 视频翻译;aiColor|AI 智能调色;aiFrame|AI 智能构图;aiSub|AI 字幕包装;aiTransition|AI 智能转场",
    "fx": "trLib|转场库;trDur|转场时长（秒）;trCustom|自定义 / 批量转场;beauty|美颜;body|美体;faceMode|人像模式;faceSticker|人像贴纸 / 滤镜;basicColor|基础调节;colorVal|参数值;proColor|专业调色面板;zoneColor|分区调色;lut|LUT;colorCopy|调色参数",
    "text": "fancy|花字模板;textFx|文字效果;textAlpha|透明度;textAnim|文字动画;perChar|逐字动画;textMask|文字蒙版;subTpl|字幕样式模板;subBatch|批量字幕编辑;subOps|字幕操作;subShift|时间轴偏移（秒）;subIO|字幕导入 / 导出;subLang|识别语种;stickerLib|贴纸库;stickerCustom|自定义贴纸;stickerAnim|贴纸动画 / 跟踪",
    "mask": "pip|画中画;pipLayer|层级;blend|混合模式;maskType|蒙版;maskFeather|羽化;maskInv|反转;maskTrack|蒙版跟踪 / 关键帧;bezier|钢笔贝塞尔;chroma|色度抠像;chromaPro|色度抠像 Pro;brushKey|自定义抠像;track|运动跟踪",
    "export": "expRes|分辨率;expFps|帧率;expBr|码率;expHdr|HDR;expCs|色彩空间;expFmt2|格式;expBatch|批量导出;expOpt|导出优化;sharePlat|分享到平台;shareHd|高清链接 / 工程文件",
    "tools": "libAuto|本地素材库;libRate|素材评分;importPro|导入;hotkey|快捷键;stab|稳定器;wm|去水印;splitMerge|批量分割 / 合并;draftOps|备份 / 云同步;extract|批量提取 / 转文字;quick|快捷工具;layout|界面自定义",
}

SUPPORTED = {
    "addTrack": ("partial", "video/audio/text/overlay/adjustment; max 8 tracks per timeline, 32 stored tracks and 64 stored clips across main plus sequences, not unlimited"),
    "tlPick": ("partial", "project.sequences: up to 4 named timelines in ONE snapshot, root tracks/markers remain main; active_sequence_id null=main; explicit project save selects the exported/edited timeline and participates in undo/redo/history"),
    "nest": ("partial", "video/overlay clip.sequence_id instead of source_id; full child active duration exactly, trim=0 and all default controls except start/mute; max depth 2, no cycles even inactive; bounded actual layer expansion, not cached/fake projects or intermediate media"),
    "camNest": ("partial", "manual camera tracks may be saved in an ordinary Sequence and identity-nested; real second-level editing, no automatic camera alignment, isolated group transforms or arbitrary nested effects"),
    "trkColor": ("metadata", "track.color; persisted only"),
    "trkGroup": ("metadata", "project.groups and track.group_id; no render grouping/nesting"),
    "proxy": ("partial", "manual private RAW-source preview proxy: one catalog video <=120s, fixed contained 640x360/30fps H.264 1000kbps/AAC128; durable shared jobs/cache, no automatic batch, media import, timeline substitution or export proxy use"),
    "libRate": ("metadata", "project.assets rating 0–5 and tags; authorized catalog IDs only"),
    "layout": ("metadata", "project.workspace layout/timeline_zoom; client must apply preferences"),
    "hotkey": ("metadata", "project.workspace.shortcuts typed action/chord map; client must bind keys"),
    "marker": ("metadata", "marker/clear_markers actions on the selected timeline; client next-marker and ruler navigation; max100 per timeline, no media changes"),
    "tc": ("partial", "workspace.timeline_fps 24/25/30/60 and time_display seconds/frames; clip action at and ripple_trim.duration round half-up to this grid; API times stay seconds, not source/export FPS or SMPTE drop-frame"),
    "inout": ("metadata", "workspace.source_marks: max 200 unique authorized source IDs, ordered source-second bounds 0..3600; no automatic clip trim or EOF probe on save; image out-point UI edits clip.duration with trim=0, never treat MAX_DURATION as image EOF"),
    "snap": ("metadata", "workspace.snap_enabled persisted; client chooses magnetic targets, server only frame-quantizes submitted clip at values; no hidden snapping"),
    "ripple": ("partial", "explicit ripple_delete/ripple_trim on one unlocked track; shift starts >= old end only, reject affected overlaps; ripple_enabled is a client preference, not an implicit delete/trim mode"),
    "edit": ("partial", "split/delete/duplicate/move actions; task-local full-parameter clipboard, cross-sequence copy, same-sequence cut; complete project JSON import/export; no cross-task media transfer"),
    "undo": ("partial", "persisted undo/redo, 100 mutations maximum; never advertised as infinite"),
    "trimMode": ("partial", "trim, ripple_trim/delete, media slip; roll needs 2 contiguous same-track clips, slide needs 3; resized clips >=1 timeline frame, source EOF checked at render"),
    "camN": ("partial", "client builds 2–4 manual camera angles as ordinary video/overlay tracks; 2x2 grid or hard-cut switch list, continuous dedicated master audio; max 16 decoder inputs, not 9/16-camera mode"),
    "camAlign": ("partial", "explicit per-source sync-in seconds only; stored as actual clip.trim/start, source EOF verified at render; no waveform, timecode metadata or automatic synchronization"),
    "camBatch": ("partial", "client compiles an ordered manual switch list of at most 12 cuts into saved clips, preserving continuous master audio; no AI switching; nesting uses ordinary named sequences"),
    "freeze": ("partial", "clip.freeze: one source frame at trim, muted output <=10s; no audio freeze"),
    "kf": ("partial", "clip.keyframes: x/y/scale/opacity only; 2–8 points per property in clip-local seconds"),
    "kfParam": ("partial", "video/overlay position, scale and opacity only; not color or audio automation"),
    "kfCurve": ("partial", "linear, quadratic ease_in/ease_out, cubic smoothstep ease_in_out; no arbitrary expressions/Bezier"),
    "maskType": ("partial", "clip.mask rectangle/ellipse in normalized post-transform coordinates"),
    "maskFeather": ("renderer", "clip.mask.feather: inward normalized edge ramp 0–0.5"),
    "maskInv": ("renderer", "clip.mask.invert: invert shape alpha, retaining source alpha"),
    "reverse": ("renderer", "clip.reverse; video and existing audio reversed, buffer capped at 128 MiB"),
    "mirror": ("renderer", "clip.mirror/flip: hflip/vflip"),
    "rotate": ("partial", "clip.rotation: 0/90/180/270 only"),
    "frameCrop": ("partial", "clip.trim/duration and normalized crop; client frame/timecode inputs and one-frame steps use workspace FPS, independent of source FPS; no lossless source-frame cutter"),
    "speed": ("partial", "constant 0.5–2x; setpts and pitch-preserving atempo; no curves/optical flow"),
    "adjLayer": ("partial", "time-bounded typed color controls, presets, RGB curves and imported unit-domain 3D LUT on already-composited lower tracks"),
    "mixVol": ("renderer", "clip.volume 0–2"),
    "mixPan": ("renderer", "clip.pan -1–1 stereo balance, not spatial audio"),
    "mixMute": ("renderer", "clip.mute, track.hidden/solo"),
    "fxAudio": ("partial", "compressor/limiter/denoise/invert, fixed echo-based reverb, single-pass loudnorm; bass_db/treble_db EQ"),
    "wave": ("partial", "fades, volume, atempo, invert, single-pass -16 LUFS normalization; no measured two-pass mastering"),
    "trLib": ("partial", "clip.transition_in: local dissolve/wipe_left/wipe_right/wipe_up/wipe_down; two distinct same-track video/overlay clips, opaque full-frame source RGB, scale=1 x=y=0 opacity=1 fit=cover, no masks/chroma/keyframes/fades/freeze; no AI or optical-flow transitions"),
    "trDur": ("partial", "explicit overlap must EXACTLY equal numeric duration >0 and <=1.2s, >=one workspace frame and <=half EACH clip; ordered starts/ends, no third clip in overlap; renderer never shifts/trims clips or changes project duration"),
    "trCustom": ("partial", "project save/import supports bounded transition choices, including explicit batches; easing defaults linear (quadratic ease_in/ease_out or cubic ease_in_out), audio defaults true with complementary LINEAR fades after atempo/effects; audio=false preserves unchanged overlap mix; transition_in defaults null; remove transition before deleting/splitting/moving bound endpoints; no arbitrary FX/expressions or AI"),
    "basicColor": ("partial", "brightness/contrast/saturation/sharpen/noise plus temperature/hue/shadows/highlights/fade_amount; deterministic local SDR filters, not AI"),
    "colorVal": ("partial", "typed numeric controls and none/warm/cool/cinema/mono presets; presets are not imported LUTs"),
    "proColor": ("partial", "rgb_curves red/green/blue: 2–8 numeric knots per supplied channel, strictly increasing x with endpoints 0/1; local FFmpeg curves, separate imported LUT via lut_id; no HDR/AI matching"),
    "colorCopy": ("partial", "task-local client clipboard copies applicable numeric color, presets, RGB curves and imported LUT ID; explicit project save; no cross-task LUT packaging"),
    "lut": ("partial", "real imported UTF-8 .cube LUT_3D_SIZE 2–33, unit domain, finite [0,1] RGB, FFmpeg lut3d tetrahedral AFTER typed color filters; clip.lut_id on video/overlay/adjustment only; no 1D LUT/HDR/paths/includes"),
    "stickerCustom": ("partial", "private raw PNG/JPEG import; one non-animated image <=8 MiB, <=4096 per side / 8847360 pixels; canonical metadata-free RGBA PNG, EXIF orientation ignored; clip duration authors a still hold, not 120-second source EOF"),
    "stickerLib": ("partial", "this task's imported, content-addressed asset collection; max64 images and LUTs combined; not an external, built-in or searchable sticker library"),
    "stickerAnim": ("partial", "manual x/y/scale/opacity keyframes on imported stills with masks and source alpha; no object/face/motion tracking or animated image import"),
    "textAlpha": ("renderer", "text clip.opacity via ASS alpha"),
    "textFx": ("partial", "bold, outline, shadow, translucent background; bundled font only"),
    "fancy": ("partial", "text_template custom/news/outline/gold/note; named styles override rendered color/bold/outline/shadow/background only, stored fields survive and custom restores them"),
    "subTpl": ("partial", "same five text_template styles; no external fonts/templates or automated recognition"),
    "textAnim": ("partial", "text_animation none/typewriter/fade/pop; normalized clip-local timing, ASS only (SRT remains plain text), no ASR word timing"),
    "perChar": ("partial", "bounded staged typewriter reveal (<=64 stages per clip, may reveal groups), not word timestamps; subtitle documents <=256 KiB"),
    "subBatch": ("partial", "plain text, fixed Noto Sans SC, color/font_size/x/y; project save"),
    "subOps": ("partial", "text split/delete/duplicate; manual merge via project save"),
    "subShift": ("renderer", "text clip.start or move action"),
    "subIO": ("partial", "POST subtitles/import for bounded plain-text SRT; timed SRT/ASS export; no ASS import"),
    "pip": ("partial", "ordinary overlay/video source clips support scale/x/y/opacity; a sequence reference itself is identity-only, edit the child clips for picture-in-picture"),
    "pipLayer": ("renderer", "selected timeline track order: child layers inserted at parent track position, last composited above first; text always above all media, not isolated nested text"),
    "blend": ("partial", "normal alpha compositing only"),
    "chroma": ("renderer", "clip.chroma_color/similarity/blend; no AI keying"),
    "expRes": ("partial", "360/720/1080 only; 2K/4K/8K rejected"),
    "expFps": ("partial", "24/25/30/60; GIF centisecond quantization; 120 rejected"),
    "expBr": ("partial", "target video bitrate 250–12000 kbps; audio 96/128/192/256/320; not exact CBR"),
    "expCs": ("partial", "SDR output BT.709 YUV matrix conversion and frame/encoder tags; HDR sources rejected, no color-managed HDR/wide-gamut conversion"),
    "expFmt2": ("partial", "mp4/mov/mkv/avi/gif/mp3/wav/png/srt/ass; GIF first <=6s; PNG frame_time, no sequence/alpha/TGA"),
    "expBatch": ("partial", "one asynchronous job per task; no batch queue"),
    "expOpt": ("partial", "background CPU encoding from originals only; preview proxies are never export inputs; no GPU/smart copy"),
    "shareHd": ("partial", "authenticated output download and project JSON export; no public share link"),
    "libAuto": ("partial", "real task uploads/norm/shots/report catalog; no global library or search engine"),
    "importPro": ("partial", "safe project JSON plus bounded private PNG/JPEG and unit-domain .cube import; task originals read-only; no arbitrary paths, remote URLs, SVG, GIF or animated image import"),
    "splitMerge": ("partial", "split and sequential clip composition (hard-cut concat semantics); no batch operation"),
    "draftOps": ("partial", "persisted snapshots and JSON local backup only; no cloud/team features"),
    "quick": ("partial", "reverse, adjacent timeline clips, crop, rotation, aspect export"),
}


def capabilities() -> dict[str, Any]:
    tools: list[dict[str, Any]] = []
    for group, entries in PROTOTYPE_TOOLS.items():
        for entry in entries.split(";"):
            key, label = entry.split("|")
            classification, reason = SUPPORTED.get(key, ("unsupported", "Not implemented in this local backend; no simulated success, cloud call, or AI substitute."))
            tools.append({"id": key, "group": group, "label": label, "available": key in SUPPORTED,
                          "classification": classification, "reason": reason})
    return {"schema_version": 1, "tools": tools, "tool_count": len(tools),
            "limits": {"tracks": MAX_TRACKS, "clips": MAX_CLIPS, "sequences": MAX_SEQUENCES,
                       "total_tracks": MAX_TOTAL_TRACKS, "markers_per_timeline": MAX_MARKERS,
                       "nesting_depth": MAX_NESTING_DEPTH, "expanded_tracks": MAX_EXPANDED_TRACKS,
                       "expanded_clips": MAX_EXPANDED_CLIPS, "decoder_inputs": MAX_DECODER_INPUTS,
                       "groups": 8, "asset_metadata": 200, "tags_per_asset": 16,
                       "duration_seconds": MAX_DURATION, "output_bytes": MAX_BYTES,
                       "task_output_bytes": MAX_TASK_BYTES, "jobs_per_task": MAX_JOBS, "history_mutations": MAX_HISTORY,
                       "source_marks": 200, "rgb_knots_per_channel": 8, "subtitle_bytes": MAX_SUBTITLE_BYTES,
                       "assets": dict(studio_assets.LIMITS),
                       "proxy_duration_seconds": studio_proxy.DURATION_SECONDS, "proxy_width": studio_proxy.WIDTH,
                       "proxy_height": studio_proxy.HEIGHT, "proxy_fps": studio_proxy.FPS,
                       "transition_seconds": MAX_TRANSITION_DURATION,
                       "threads": 2, "global_jobs": 2, "max_resolution": 1080, "request_bytes": MAX_JSON},
            "sequence_contract": {
                "mode": "full_length_identity_flatten",
                "selection": "POST /project or /project/import saves active_sequence_id; null=main; all actions and SRT import address ONLY the selection; /render exports it, simple pipeline /export is unchanged",
                "nest_fields": ["id", "sequence_id", "start", "duration", "mute"],
                "nest_defaults": "source_id=null; trim=0; speed=1; reverse/freeze=false; fit=contain; all other Clip controls MUST equal defaults; edit ordinary child clips for effects/transforms/audio",
                "duration": "full visible/solo-resolved child duration, >0 and <=120s; no clipping/resizing; update every affected ancestor descriptor in the same save, never auto-reset stale durations",
                "validation": "all timelines, hidden/solo-excluded references included; IDs unique across sequences/tracks/clips/markers/groups; max 2 reference edges from any timeline",
                "render_limits": "per selected expansion: 8 nonempty tracks, 64 leaf clip occurrences, 16 source/decoder occurrences; repeated references count each time; empty/hidden/solo-excluded lanes do not add render cost; stored bounds remain independent",
                "composition": "per-timeline visibility/solo, then insert child layers at parent track position; no overlapping same-track nest spans; empty sections retain time; one final mix/disclosure burn, no intermediate media",
                "limitations": "not isolated groups: transparent gaps expose parent layers, child adjustments affect the accumulated lower composite, and all text is above all media; no nested trim/speed/FX/transitions",
                "locks": "all timeline track content/order/container protected; locked nests also protect referenced track content/order transitively; explicit track action to unlock; undo/redo restore complete validated snapshots",
                "deletion": "remove all nested references in a separate save before deleting a referenced sequence; never silently drop references",
                "clone": "no dedicated timeline-clone UI; create an empty sequence, then use task-local clip copy or explicit project import with fresh unique IDs and remapped transition endpoints",
            },
            "export_schema": ExportOptions.model_json_schema(), "project_schema": Project.model_json_schema(),
            "action_schema": Action.model_json_schema(),
            "proxy_request_schema": ProxyRequest.model_json_schema(),
            "subtitle_import_schema": SubtitleImport.model_json_schema(),
            "unsupported": ["optical-flow transitions", "SVG/GIF/APNG/animated or multi-picture image import", "1D/non-unit-domain LUTs", "automatic sticker tracking", "color/audio keyframes", "optical flow", "HDR", "2K", "4K", "8K", "PNG sequence", "public sharing"],
            "render_available": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")),
            "quality": "Derived studio media is unreviewed; pipeline final/report/QC are never replaced."}


def local_json(root: Path, name: str, default: Any = None) -> Any:
    path = contained(root, root / name, exists=False)
    if not path.exists():
        return default
    if not path.is_file() or path.stat().st_size > MAX_STATE:
        raise HTTPException(422, "task metadata exceeds safe size")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, "invalid task metadata") from exc


def catalog(record: Any, index: studio_assets.AssetIndex | None = None) -> dict[str, Path]:
    # Do not erase a linked task root before contained() can inspect it.
    root = Path(record.task_dir).absolute()
    result: dict[str, Path] = {}

    def add(key: str, path: Path) -> None:
        safe = contained(root, path, exists=False)
        if safe.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".svg", ".cube"}:
            # Legacy raw task uploads are NOT sanitized Studio image/LUT
            # assets. Only the verified index below can authorize these kinds.
            return
        if safe.is_file():
            result[key] = safe

    for name in ("final.mp4", "video_only.mp4", "narration.m4a"):
        add(name.split(".")[0], root / name)
    for upload in record.uploads:
        path = Path(upload.path)
        # UploadedAsset.path is server-owned and normally already includes task_dir.
        if not path.is_absolute():
            path = path.absolute()
        relative = contained(root, path).relative_to(root).as_posix()
        add("upload_" + hashlib.sha256(relative.encode()).hexdigest()[:20], path)
    norm = contained(root, root / "norm", exists=False)
    if norm.is_dir():
        for path in sorted(norm.glob("norm_*.mp4"))[:200]:
            add("norm_" + hashlib.sha256(path.name.encode()).hexdigest()[:20], path)
    result.update(studio_assets.image_paths(root, index if index is not None else studio_assets.load_index(root)))
    return result


def validate_references(project: Project | SimpleModeProject, sources: dict[str, Path], luts: dict[str, Path] | None = None) -> None:
    if type(project) is SimpleModeProject:
        project = project.template()
    assert isinstance(project, Project)
    if any(asset.source_id not in sources for asset in project.assets):
        raise HTTPException(422, "asset metadata requires an authorized source_id")
    if any(mark.source_id not in sources for mark in project.workspace.source_marks):
        raise HTTPException(422, "source marks require an authorized source_id")
    for track in project.all_tracks():
        for clip in track.clips:
            if clip.source_id is not None and clip.source_id not in sources:
                raise HTTPException(422, "unknown source_id; paths and remote URLs are never accepted")
            if clip.lut_id is not None and clip.lut_id not in (luts or {}):
                raise HTTPException(422, "unknown lut_id; import a .cube asset first, paths are never accepted")
            if clip.source_id is not None and sources[clip.source_id].suffix.lower() == ".png" and not is_image_id(clip.source_id):
                raise HTTPException(422, "still images must be imported through studio/assets/image")


def check_qc(root: Path) -> None:
    quality = local_json(root, "quality_report.json", {})
    report = local_json(root, "report.json", {})
    candidates: list[dict[str, Any]] = [quality, report.get("quality") or {}]
    for item in candidates:
        if item.get("blocking_issue_count", 0) or any(i.get("severity") == "error" for i in item.get("issues", [])):
            raise HTTPException(409, "pipeline QC blockers must be resolved before studio render/export")


def requires_disclosure(root: Path) -> bool:
    shots = local_json(root, "shots_annotated.json", [])
    edl = local_json(root, "edl.json", [])
    generated = any(s.get("media_origin") == "generated" or str(s.get("norm_path", "")).replace("\\", "/").startswith("generated/") for s in shots)
    for row in edl:
        generated = generated or any(c.get("media_origin") == "generated" or str(c.get("src", "")).replace("\\", "/").startswith("generated/") for c in row.get("clips", []))
    manifest = local_json(root, "generated_media_disclosure.json")
    if manifest is not None:
        try:
            validated = GeneratedMediaDisclosureManifest.model_validate(manifest)
        except ValidationError as exc:
            raise HTTPException(422, "invalid generated-media disclosure manifest") from exc
        generated = generated or bool(validated.items)
        if generated and not validated.items:
            raise HTTPException(422, "generated footage has no disclosure entries")
    elif generated:
        raise HTTPException(422, "generated footage requires a valid disclosure manifest")
    return generated


def fingerprint(paths: dict[str, Path]) -> dict[str, tuple[int, int]]:
    return {key: (path.stat().st_size, path.stat().st_mtime_ns) for key, path in paths.items()}


def _pre_sequence_proxy_project(source_id: str, duration: float, has_audio: bool) -> dict[str, Any]:
    """Exact old fixed RAW profile, not a general legacy Project normalizer."""
    data = studio_proxy.project(source_id, duration, has_audio).model_dump()
    data.pop("sequences")
    data.pop("active_sequence_id")
    for track in data["tracks"]:
        for clip in track["clips"]:
            clip.pop("sequence_id")
    return data


def _pre_sequence_proxy_profile() -> dict[str, Any]:
    return {**studio_proxy.profile(), "project_template": _pre_sequence_proxy_project("source", 1, True)}


def _pre_sequence_proxy_key(source_sha256: str, pipeline_revision: int, disclosure: bool) -> str:
    document = {"source_sha256": source_sha256, "pipeline_revision": pipeline_revision,
                "profile": _pre_sequence_proxy_profile(), "disclosure": disclosure}
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def _validate_proxy_snapshot(job: dict[str, Any], snapshot: dict[str, Any]) -> tuple[studio_proxy.Binding, str]:
    """Keep already verified C25 proxies usable after additive empty fields.

    Only the EXACT old fixed template is recognized. No saved file is rewritten;
    all current immutable-profile checks still run, and the caller continues to
    compare the original binding/key plus rehash source/output bytes as before.
    Edited/nested projects, mixed old/new profiles and mismatched keys fail.
    """
    binding = studio_proxy.Binding.model_validate(snapshot["proxy"])
    if binding.profile != _pre_sequence_proxy_profile():
        binding = studio_proxy.validate_snapshot(job, snapshot)
        return binding, binding.key
    key = _pre_sequence_proxy_key(binding.source_sha256, binding.pipeline_revision, binding.disclosure)
    if job.get("cache_key") != key or snapshot.get("project") != _pre_sequence_proxy_project(binding.source_id, binding.duration, binding.has_audio):
        raise RenderError("proxy snapshot is not the immutable raw-source profile")
    upgraded = {**snapshot, "proxy": {**binding.model_dump(), "profile": studio_proxy.profile()},
                "project": studio_proxy.project(binding.source_id, binding.duration, binding.has_audio).model_dump()}
    studio_proxy.validate_snapshot({**job, "cache_key": binding.key}, upgraded)
    return binding, key


def state_default() -> dict[str, Any]:
    return {"revision": 0, "project": Project().model_dump(), "past": [], "future": [], "audit": []}


def audit(state: dict[str, Any], op: str, **details: Any) -> None:
    state["audit"].append({"id": uuid4().hex, "time": time.time(), "op": op, **details})


def read_state(root: Path) -> dict[str, Any]:
    return local_json(root, "studio/state.json", state_default())


def write_state(root: Path, state: dict[str, Any]) -> None:
    # Match write_json_atomic's actual on-disk encoding; a compact-size check
    # could commit pretty-printed history that local_json subsequently rejects.
    serialized = json.dumps(state, ensure_ascii=False, indent=2).replace("\n", os.linesep)
    size = len(serialized.encode("utf-8"))
    if size > MAX_STATE:
        raise HTTPException(413, "studio history size limit reached")
    # Atomic history replacement temporarily needs both old and new files.
    # Import/output/history share ONE Studio quota, not separate asset budgets.
    if studio_assets.studio_bytes(root) + size > MAX_TASK_BYTES:
        raise HTTPException(507, "task studio storage quota reached")
    write_json_atomic(contained(root, root / "studio/state.json", exists=False), state)


def revision_matches(state: dict[str, Any], expected: int) -> None:
    if state["revision"] != expected:
        raise HTTPException(409, {"message": "revision conflict", "current_revision": state["revision"]})


def public_state(state: dict[str, Any]) -> dict[str, Any]:
    # Hydrate additive schema-1 defaults on reads of older saved snapshots too.
    return {"revision": state["revision"], "project": Project.model_validate(state["project"]).model_dump(),
            "can_undo": bool(state["past"]), "can_redo": bool(state["future"])}


def commit(root: Path, state: dict[str, Any], project: Project, op: str, *, history: bool = True) -> dict[str, Any]:
    if state["revision"] >= MAX_HISTORY:
        raise HTTPException(409, "100-mutation revision limit reached; export project backup")
    if history:
        state["past"].append(state["project"])
        state["future"] = []
    state["project"] = project.model_dump()
    state["revision"] += 1
    audit(state, op, revision=state["revision"], project_sha256=hashlib.sha256(json.dumps(state["project"], sort_keys=True).encode()).hexdigest())
    write_state(root, state)
    return public_state(state)


def enforce_locks(old: Project, new: Project, *, unlock_track_id: str | None = None) -> None:
    timelines = {identifier: tracks for identifier, tracks, _ in project_timelines(new)}
    before = {sequence.id: sequence for sequence in old.sequences}
    after = {sequence.id: sequence for sequence in new.sequences}
    checked: set[str] = set()

    def protect_child(sequence_id: str) -> None:
        if sequence_id in checked:
            return
        checked.add(sequence_id)
        original, replacement = before[sequence_id], after.get(sequence_id)
        if replacement is None or [t.model_dump() for t in original.tracks] != [t.model_dump() for t in replacement.tracks]:
            raise HTTPException(409, "unlock the referencing nested track before editing/reordering its child tracks")
        for child in original.tracks:
            for clip in child.clips:
                if clip.sequence_id is not None:
                    protect_child(clip.sequence_id)

    for identifier, tracks, _ in project_timelines(old):
        replacements = timelines.get(identifier, [])
        for position, track in enumerate(tracks):
            if not track.locked:
                continue
            expected = track.model_dump()
            if track.id == unlock_track_id:
                expected["locked"] = False
            if position >= len(replacements) or replacements[position].model_dump() != expected:
                raise HTTPException(409, "unlock track with a track action before editing/reordering it or moving it to another timeline")
            if track.id != unlock_track_id:
                for clip in track.clips:
                    if clip.sequence_id is not None:
                        protect_child(clip.sequence_id)


def enforce_sequence_deletions(old: Project, new: Project) -> None:
    remaining = {sequence.id for sequence in new.sequences}
    if any(clip.sequence_id is not None and clip.sequence_id not in remaining
           for track in old.all_tracks() for clip in track.clips):
        raise HTTPException(422, "remove all nested references in a separate save before deleting a referenced sequence")


def enforce_transition_endpoints(old: Project, new: Project) -> None:
    """A save/import must not erase a binding by deleting its owning right clip.

    Removing transition_in explicitly while retaining the two endpoints is a
    separate supported mutation. Undo/redo restore complete validated snapshots
    under their existing semantics, rather than performing destructive edits.
    """
    locations = {clip.id: (identifier, track.id) for identifier, tracks, _ in project_timelines(new)
                 for track in tracks for clip in track.clips}
    for identifier, tracks, _ in project_timelines(old):
        for track in tracks:
            for right in track.clips:
                if right.transition_in is not None and any(
                        locations.get(clip_id) != (identifier, track.id) for clip_id in (right.id, right.transition_in.left_clip_id)):
                    raise HTTPException(422, "remove transition first before deleting or moving a bound endpoint or its track/timeline")


_TIME_EPSILON = Fraction(1, 10**12)  # Float serialization noise only, not a frame/gap tolerance.


def _time(value: float) -> Fraction:
    decimal = Fraction(str(value))
    # Recover serialized frame fractions (e.g. 61/30) ONLY when they have the
    # identical float representation. This avoids accumulated frame round-trip
    # drift, without rounding a legacy timestamp to a different numeric value.
    rational = decimal.limit_denominator(10**9)
    return rational if float(rational) == value else decimal


def _end(clip: dict[str, Any]) -> Fraction:
    return _time(clip["start"]) + _time(clip["duration"])


def _frame_boundary(value: float, fps: int) -> Fraction:
    """Half-up frame rounding of USER input only. Never round legacy geometry.

    Fraction arithmetic anchors all changes in one action to the original
    endpoints; no iterative float delta accumulation or resnapping neighbours.
    Source trims/marks remain source seconds, independent of workspace FPS.
    """
    ticks = _time(value) * fps
    return Fraction((2 * ticks.numerator + ticks.denominator) // (2 * ticks.denominator), fps)


def _require_frame(duration: Fraction, fps: int) -> None:
    if duration + _TIME_EPSILON < Fraction(1, fps):
        raise HTTPException(422, "edited clip must retain at least one timeline frame")


def _reject_affected_overlaps(track: dict[str, Any], affected: set[str]) -> None:
    # Same-track compositing is legal in legacy projects. Reject overlaps only
    # when at least one participant is being edited/shifted by this operation.
    for index, left in enumerate(track["clips"]):
        for right in track["clips"][index + 1:]:
            if left["id"] not in affected and right["id"] not in affected:
                continue
            overlap = min(_end(left), _end(right)) - max(_time(left["start"]), _time(right["start"]))
            if overlap > _TIME_EPSILON:
                if ((left.get("transition_in") or {}).get("left_clip_id") == right["id"]
                        or (right.get("transition_in") or {}).get("left_clip_id") == left["id"]):
                    raise HTTPException(422, "remove transition first before an edit affecting a bound overlap")
                raise HTTPException(422, "affected clips overlap; resolve same-track overlaps before this edit")


def _tail_duration(clip: dict[str, Any], duration: Fraction) -> None:
    # Keep the first played source sample fixed, including reverse playback.
    if clip["source_id"] and clip["reverse"] and not clip["freeze"]:
        clip["trim"] = float(_time(clip["trim"]) + (_time(clip["duration"]) - duration) * _time(clip["speed"]))
    clip["duration"] = float(duration)


def _head_boundary(clip: dict[str, Any], at: Fraction) -> None:
    # Keep the last played source sample fixed. Reverse uses the low trim bound.
    duration = _end(clip) - at
    if clip["source_id"] and not is_image_id(clip["source_id"]) and not clip["reverse"] and not clip["freeze"]:
        clip["trim"] = float(_time(clip["trim"]) + (at - _time(clip["start"])) * _time(clip["speed"]))
    clip.update(start=float(at), duration=float(duration))


def _source_bounds(clip: dict[str, Any]) -> None:
    if clip["source_id"] and not is_image_id(clip["source_id"]) and not clip["freeze"] and (
            _time(clip["trim"]) + _time(clip["duration"]) * _time(clip["speed"]) > 3600 + _TIME_EPSILON):
        raise HTTPException(422, "edited source range exceeds 3600 seconds; actual source EOF is also checked at render")


def _timeline_edit(track: dict[str, Any], clip: dict[str, Any], action: Action, fps: int) -> None:
    """Explicit local edits; preference flags never silently change the operation.

    Ripple closes/resizes just the selected interval, preserving existing gaps
    elsewhere. Roll/slide keep the exterior endpoints fixed. All old geometry,
    local keyframes, fades and marks survive unless explicitly edited; final
    Project validation rejects envelopes that no longer fit instead of trimming
    or retiming them. Actual source EOF validation remains the renderer's job.
    """
    affected = {clip["id"]}
    if action.op == "slip":
        if track["type"] not in {"video", "overlay", "audio"} or is_image_id(clip["source_id"]):
            raise HTTPException(422, "slip requires a media clip")
        _reject_affected_overlaps(track, affected)
        clip["trim"] = action.trim
    elif action.op in {"ripple_delete", "ripple_trim"}:
        old_end, old_duration = _end(clip), _time(clip["duration"])
        successors = [c for c in track["clips"] if c is not clip and _time(c["start"]) >= old_end - _TIME_EPSILON]
        affected.update(c["id"] for c in successors)
        _reject_affected_overlaps(track, affected)
        if action.op == "ripple_delete":
            delta = -old_duration
            track["clips"].remove(clip)
        else:
            assert action.duration is not None
            duration = _frame_boundary(action.duration, fps)
            _require_frame(duration, fps)
            delta = duration - old_duration
            clip.update(trim=action.trim, duration=float(duration))
        for successor in successors:
            successor["start"] = float(_time(successor["start"]) + delta)
    else:
        ordered = sorted(track["clips"], key=lambda c: (_time(c["start"]), c["id"]))
        index = ordered.index(clip)
        if index + 1 == len(ordered) or (action.op == "slide" and index == 0):
            raise HTTPException(422, "roll requires a next clip; slide requires previous and next clips on the same track")
        right = ordered[index + 1]
        participants = [clip, right] if action.op == "roll" else [ordered[index - 1], clip, right]
        if any(c["sequence_id"] is not None for c in participants):
            raise HTTPException(422, "roll/slide cannot resize nested clips; edit the child timeline instead")
        affected = {c["id"] for c in participants}
        _reject_affected_overlaps(track, affected)
        if any(abs(_end(a) - _time(b["start"])) > _TIME_EPSILON for a, b in zip(participants, participants[1:])):
            raise HTTPException(422, "roll/slide requires contiguous clips; gaps are not closed implicitly")
        assert action.at is not None
        at = _frame_boundary(action.at, fps)
        if action.op == "roll":
            _require_frame(at - _time(clip["start"]), fps)
            _require_frame(_end(right) - at, fps)
            _tail_duration(clip, at - _time(clip["start"]))
            _head_boundary(right, at)
        else:
            left = participants[0]
            right_at = at + _time(clip["duration"])
            _require_frame(at - _time(left["start"]), fps)
            _require_frame(_end(right) - right_at, fps)
            _tail_duration(left, at - _time(left["start"]))
            clip["start"] = float(at)  # Selected duration, trim and envelopes are untouched.
            _head_boundary(right, right_at)
    _reject_affected_overlaps(track, affected)
    for changed in track["clips"]:
        if changed["id"] in affected:
            _source_bounds(changed)


def _active_timeline_data(data: dict[str, Any]) -> dict[str, Any]:
    """Only for a validated Project dump; no fallback for stale selections."""
    if data["active_sequence_id"] is None:
        return data
    for sequence in data["sequences"]:
        if sequence["id"] == data["active_sequence_id"]:
            return sequence
    raise HTTPException(422, "unknown active_sequence_id; never implicitly reset to main")


def apply_action(project: Project, action: Action) -> Project:
    data = project.model_dump()
    timeline = _active_timeline_data(data)
    if action.op == "marker":
        assert action.new_id is not None and action.at is not None
        timeline["markers"].append(Marker(id=action.new_id, time=action.at, label=action.label or "").model_dump())
    elif action.op == "clear_markers":
        timeline["markers"] = []
    elif action.op == "track":
        track = next((t for t in timeline["tracks"] if t["id"] == action.track_id), None)
        if track is None:
            raise HTTPException(404, "track not found")
        changes = {k: getattr(action, k) for k in ("locked", "hidden", "solo", "color") if getattr(action, k) is not None}
        if track["locked"] and changes != {"locked": False}:
            raise HTTPException(409, "locked track may only be unlocked")
        track.update(changes)
    else:
        matches = [(t, c) for t in timeline["tracks"] for c in t["clips"] if c["id"] == action.clip_id]
        if not matches:
            raise HTTPException(404, "clip not found")
        track, clip = matches[0]
        if track["locked"]:
            raise HTTPException(409, "track is locked")
        if clip["sequence_id"] is not None and action.op not in {"move", "duplicate", "delete", "ripple_delete"}:
            raise HTTPException(422, "nested clips require full child duration; only move/duplicate/delete/ripple_delete apply, edit the child timeline for other changes")
        if action.op in {"delete", "ripple_delete", "split"} and any(
                c["transition_in"] is not None and clip["id"] in (c["id"], c["transition_in"]["left_clip_id"])
                for c in track["clips"]):
            raise HTTPException(422, "remove transition first before deleting or splitting a bound endpoint")
        fps = project.workspace.timeline_fps
        if action.op in {"ripple_delete", "ripple_trim", "slip", "roll", "slide"}:
            _timeline_edit(track, clip, action, fps)
        elif action.op == "delete":
            track["clips"].remove(clip)
        elif action.op in {"move", "duplicate"}:
            target = next((t for t in timeline["tracks"] if t["id"] == (action.track_id or track["id"])), None)
            if target is None or target["type"] != track["type"]:
                raise HTTPException(422, "destination track must exist and have the same type")
            if target["locked"]:
                raise HTTPException(409, "destination track is locked")
            assert action.at is not None
            new = dict(clip, start=float(_frame_boundary(action.at, fps)))
            if action.op == "move":
                track["clips"].remove(clip)
            else:
                new["id"] = action.new_id
            target["clips"].append(new)
        elif action.op == "trim":
            clip.update(trim=action.trim, duration=action.duration)
        elif action.op == "split":
            assert action.at is not None
            at = _frame_boundary(action.at, fps)
            offset = at - _time(clip["start"])
            remaining = _time(clip["duration"]) - offset
            if not 0 < offset < _time(clip["duration"]):
                raise HTTPException(422, "split point must be inside the clip")
            _require_frame(offset, fps)
            _require_frame(remaining, fps)
            if clip["fade_in"] or clip["fade_out"]:
                raise HTTPException(422, "remove fades before splitting (avoids silently changing fade envelopes)")
            if any(clip["keyframes"].values()):
                raise HTTPException(422, "remove keyframes before splitting; envelopes are not silently remapped")
            if clip["text_animation"] != "none":
                raise HTTPException(422, "remove text animation before splitting; reveal envelopes are not silently restarted")
            right = dict(clip, id=action.new_id, start=float(at), duration=float(remaining))
            if clip["source_id"] and not is_image_id(clip["source_id"]) and not clip["freeze"]:
                if clip["reverse"]:
                    clip["trim"] = float(_time(clip["trim"]) + remaining * _time(clip["speed"]))
                else:
                    right["trim"] = float(_time(clip["trim"]) + offset * _time(clip["speed"]))
            clip["duration"] = float(offset)
            track["clips"].append(right)
    try:
        candidate = Project.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    enforce_locks(project, candidate, unlock_track_id=action.track_id if action.op == "track" and action.locked is False else None)
    enforce_transition_endpoints(project, candidate)
    return candidate


async def parse_body(request: Request, model: type[StrictModel]) -> Any:
    body = bytearray()
    async for part in request.stream():
        body.extend(part)
        if len(body) > MAX_JSON:
            raise HTTPException(413, "studio JSON request exceeds 256 KiB")
    try:
        return model.model_validate_json(bytes(body))
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc


def safe_id(value: str) -> str:
    if len(value) != 32 or any(c not in "0123456789abcdef" for c in value):
        raise HTTPException(404, "job not found")
    return value


def create_studio_router(settings: Any, task_manager: Any, authorize: Any) -> APIRouter:
    """Return an unmounted router. authorize(request, task_id, write=False) may be async.

    Legacy immutable render/export outputs require current publication checks;
    mode-contract outputs additionally require their original pipeline revision.
    RAW preview proxies have independent source/hash checks, not a publish gate.
    """
    owned: set[str] = set()
    owned_imports: set[str] = set()
    owned_preparations: set[str] = set()

    @asynccontextmanager
    async def lifespan(_app: Any):
        yield
        if hasattr(task_manager, "begin_drain"):
            task_manager.begin_drain()
        tasks = ([_RUNNING[k] for k in owned if k in _RUNNING]
             + [_INGESTING[k] for k in owned_imports if k in _INGESTING]
             + [_PREPARING[k] for k in owned_preparations if k in _PREPARING])
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=getattr(settings, "shutdown_grace_seconds", 0))
            for task in pending:
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    router = APIRouter(prefix="/api/tasks/{task_id}/studio", tags=["studio"], lifespan=lifespan)

    def editable(record: Any, root: Path, *, import_owner: asyncio.Task[Any] | None = None) -> None:
        if getattr(task_manager, "_draining", False):
            raise HTTPException(503, "server is draining")
        if getattr(record.status, "value", record.status) != "done":
            raise HTTPException(409, "studio mutations require an idle completed task")
        if legacy_task_busy(root) or task_operation_busy(root):
            raise HTTPException(409, "task copy is active or legacy work awaits reconciliation")
        current = getattr(task_manager, "get", None)
        if callable(current) and current(record.task_id) is not record:
            raise HTTPException(409, "task was removed or replaced")
        background = getattr(record, "background", None)
        if background is not None and not background.done():
            raise HTTPException(409, "pipeline work is still active")
        owner = _INGESTING.get(str(root))
        if owner is not None and owner is not (import_owner or asyncio.current_task()):
            raise HTTPException(409, "studio asset import is active")

    async def access(request: Request, task_id: str, write: bool = False) -> tuple[Any, Path]:
        record = authorize(request, task_id, write=write)
        if inspect.isawaitable(record):
            record = await record
        if record is None or record.task_id != task_id:
            raise HTTPException(403, "task authorization required")
        data_root = Path(settings.data_dir).absolute()
        root = contained(data_root, Path(record.task_dir).absolute())
        if write:
            editable(record, root)
        return record, root

    async def reauthorize(request: Request, task_id: str, record: Any, root: Path) -> None:
        """Recheck the host's write policy; callers must check revisions AFTER this await."""
        latest, latest_root = await access(request, task_id, write=True)
        if latest is not record or latest_root != root:
            raise HTTPException(409, "task authorization changed during studio request")

    def reference_maps(record: Any, root: Path) -> tuple[dict[str, Path], dict[str, Path]]:
        index = studio_assets.load_index(root)
        return catalog(record, index), studio_assets.lut_paths(root, index)

    @router.get("/assets")
    async def get_assets(request: Request, task_id: str):
        _, root = await access(request, task_id)
        return JSONResponse(studio_assets.load_index(root).public(task_id), headers={"Cache-Control": "no-store"})

    async def import_asset(request: Request, task_id: str, kind: Literal["image", "lut"]):
        """Raw, bounded streaming; imports/dedup NEVER advance the project revision.

        Reauthorize with write=True (host private ownership/CSRF) after every
        input/media phase, including dedup. Final publish is synchronous with
        record identity/root inode/status/pipeline+project revision checks.
        """
        record, root = await access(request, task_id, True)
        revisions = request.query_params.getlist("expected_revision")
        if len(revisions) != 1 or not re.fullmatch(r"(?:0|[1-9][0-9]{0,17})", revisions[0]):
            raise HTTPException(422, "expected_revision is required once as a non-negative decimal integer")
        expected = int(revisions[0])
        revision_matches(read_state(root), expected)
        if "x-asset-name" in request.headers:
            raise HTTPException(400, "asset names are generated by the server; X-Asset-Name is not accepted")
        content_type = request.headers.get("content-type", "").strip().lower()
        if kind == "image":
            if content_type not in {"image/png", "image/jpeg"}:
                raise HTTPException(415, "raw image/png or image/jpeg required; multipart is not accepted")
        elif not re.fullmatch(r'text/plain(?:\s*;\s*charset\s*=\s*(?:utf-8|"utf-8"))?', content_type):
            raise HTTPException(415, "raw UTF-8 text/plain required; multipart is not accepted")
        if request.headers.get("content-encoding", "identity").lower() != "identity":
            raise HTTPException(415, "encoded/compressed upload bodies are not accepted")
        limit = studio_assets.IMAGE_BYTES if kind == "image" else studio_assets.LUT_BYTES
        lengths = request.headers.getlist("content-length")
        if len(lengths) > 1 or (lengths and not re.fullmatch(r"[0-9]{1,18}", lengths[0])):
            raise HTTPException(400, "invalid Content-Length")
        declared = int(lengths[0]) if lengths else None
        if declared is not None and declared > limit:
            raise HTTPException(413, "asset upload exceeds its byte limit")
        key = str(root)
        if key in _BUSY:
            raise HTTPException(409, "studio render/export/import is active")
        if len(_BUSY) >= 2:
            raise HTTPException(429, "global studio capacity reached")
        if kind == "image" and (shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None):
            raise HTTPException(503, "FFmpeg and ffprobe are required for image import")
        original = studio_assets.load_index(root)
        budget = studio_assets.IMAGE_WORK_BYTES if kind == "image" else studio_assets.LUT_WORK_BYTES
        initial_used = studio_assets.studio_bytes(root)
        if initial_used + budget > MAX_TASK_BYTES:
            raise HTTPException(507, "task studio storage quota reached (including ingestion workspace)")
        reserve = int(getattr(settings, "minimum_free_disk_bytes", 0))
        if shutil.disk_usage(root).free < reserve + sum(_DISK_RESERVATIONS.values()) + budget:
            raise HTTPException(507, "insufficient free disk for studio import")
        root_stat = root.stat()
        root_identity = (root_stat.st_dev, root_stat.st_ino)
        pipeline_revision = record.revision
        current_task = asyncio.current_task()
        assert current_task is not None
        # Synchronous admission before body receive OR any disk/media await.
        _BUSY.add(key)
        _INGESTING[key] = current_task
        _DISK_RESERVATIONS[key] = budget
        owned_imports.add(key)
        work: Path | None = None
        reservation: Any = None
        admitted = False

        def check() -> None:
            editable(record, root, import_owner=current_task)
            if _INGESTING.get(key) is not current_task or key not in _BUSY:
                raise HTTPException(409, "asset import lost its task reservation")
            if record.revision != pipeline_revision:
                raise HTTPException(409, "pipeline revision changed during asset import")
            safe_root = contained(Path(settings.data_dir).absolute(), Path(record.task_dir).absolute())
            now = safe_root.stat()
            if safe_root != root or (now.st_dev, now.st_ino) != root_identity:
                raise HTTPException(409, "task directory changed during asset import")
            revision_matches(read_state(root), expected)
            used = studio_assets.studio_bytes(root)
            if used + MAX_JSON > MAX_TASK_BYTES:
                raise HTTPException(507, "task studio storage quota reached")
            other = sum(value for task_key, value in _DISK_RESERVATIONS.items() if task_key != key)
            remaining = max(0, budget - max(0, used - initial_used))
            if shutil.disk_usage(root).free < reserve + other + remaining:
                raise HTTPException(507, "minimum free disk reserve reached")

        async def reauthorize() -> None:
            check()
            latest, latest_root = await access(request, task_id, True)
            if latest is not record or latest_root != root:
                raise HTTPException(409, "task authorization changed during asset import")
            check()

        async def prepare_image() -> studio_assets.PreparedAsset:
            # An external encoder can write a whole PNG packet past -fs; keep
            # the task/global/min-free reservations live while it runs, not just
            # before/after it. A failing guard discards the result and drains the
            # bounded worker before touching its workspace or releasing leases.
            assert work is not None
            worker = asyncio.create_task(studio_assets.prepare_image(root, raw, work, content_type, check))
            try:
                while not worker.done():
                    await asyncio.wait({worker}, timeout=_ASSET_STORAGE_POLL_SECONDS)
                    check()
                return worker.result()
            except BaseException:
                worker.cancel()
                try:
                    await studio_assets.drain(worker)
                except (Exception, asyncio.CancelledError):
                    pass
                raise

        try:
            # Share production pipeline/upload disk reservations when the host
            # supplies its existing guard (its conservative multiplier applies).
            guard = getattr(task_manager, "_upload_capacity_guard", None)
            if guard is not None:
                reservation = guard.reserve(budget)
                async def acquire() -> None:
                    nonlocal admitted
                    async with asyncio.timeout(30):
                        await reservation.__aenter__()
                        admitted = True
                acquiring = asyncio.create_task(acquire())
                try:
                    await asyncio.shield(acquiring)
                except asyncio.CancelledError:
                    # The existing guard's async finally must not be interrupted
                    # by repeated caller cancellation. A successful racing entry
                    # sets admitted in its worker, so outer finally releases it.
                    try:
                        await studio_assets.drain(acquiring)
                    except (Exception, asyncio.CancelledError):
                        pass
                    raise
                except InsufficientDiskSpaceError as exc:
                    raise HTTPException(507, "insufficient reserved disk for studio import") from exc
                check()
            work = contained(root, root / "studio/assets" / (".ingest-" + uuid4().hex), exists=False)
            work.mkdir(parents=True, exist_ok=False)
            raw = contained(root, work / "body.bin", exists=False)
            count = 0
            async with asyncio.timeout(_ASSET_BODY_TIMEOUT):
                with raw.open("xb") as destination:
                    async for chunk in request.stream():
                        check()  # Includes empty/final chunks and post-receive races.
                        if len(chunk) > limit - count:
                            raise HTTPException(413, "asset upload exceeds its byte limit")
                        destination.write(chunk)
                        count += len(chunk)
                    destination.flush()
                    os.fsync(destination.fileno())
            if not count:
                raise HTTPException(422, "asset upload is empty")
            if declared is not None and count != declared:
                raise HTTPException(400, "asset length differs from Content-Length")
            await reauthorize()
            if kind == "image":
                prepared = await prepare_image()
            else:
                prepared = studio_assets.prepare_lut(root, raw, work)
            await reauthorize()
            row, deduplicated = studio_assets.publish(root, prepared, original, check)
            return JSONResponse({"asset": row.public(task_id), "project_revision": expected, "deduplicated": deduplicated},
                                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        except studio_assets.AssetError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        except ClientDisconnect as exc:
            raise HTTPException(400, "asset upload disconnected") from exc
        except TimeoutError as exc:
            raise HTTPException(408, "asset upload timed out") from exc
        except OSError as exc:
            raise HTTPException(507, "local asset storage is unavailable") from exc
        finally:
            try:
                if work is not None:
                    studio_assets.cleanup(root, work, root_identity)
                if admitted:
                    async def release() -> None:
                        await reservation.__aexit__(None, None, None)
                    await studio_assets.drain(asyncio.create_task(release()))
            finally:
                owned_imports.discard(key)
                _DISK_RESERVATIONS.pop(key, None)
                _INGESTING.pop(key, None)
                _BUSY.discard(key)

    @router.post("/assets/image")
    async def import_image(request: Request, task_id: str):
        return await import_asset(request, task_id, "image")

    @router.post("/assets/lut")
    async def import_lut(request: Request, task_id: str):
        return await import_asset(request, task_id, "lut")

    @router.get("/capabilities")
    async def get_capabilities(request: Request, task_id: str):
        await access(request, task_id)
        return capabilities()

    @router.get("/project")
    @router.get("/project/export")
    async def get_project(request: Request, task_id: str):
        record, root = await access(request, task_id)
        state = read_state(root)
        validate_references(Project.model_validate(state["project"]), *reference_maps(record, root))
        return JSONResponse(public_state(state), headers={"Cache-Control": "no-store"})

    @router.post("/project")
    @router.post("/project/import")
    async def save_project(request: Request, task_id: str):
        record, root = await access(request, task_id, True)
        payload = await parse_body(request, ProjectWrite)
        await reauthorize(request, task_id, record, root)
        editable(record, root)  # Host authorization can await too; keep all commit guards below it.
        state = read_state(root)
        revision_matches(state, payload.expected_revision)
        validate_references(payload.project, *reference_maps(record, root))
        previous = Project.model_validate(state["project"])
        enforce_locks(previous, payload.project)
        enforce_sequence_deletions(previous, payload.project)
        enforce_transition_endpoints(previous, payload.project)
        return commit(root, state, payload.project, "project.save")

    @router.post("/actions")
    async def action_project(request: Request, task_id: str):
        record, root = await access(request, task_id, True)
        action = await parse_body(request, Action)
        await reauthorize(request, task_id, record, root)
        editable(record, root)
        state = read_state(root)
        revision_matches(state, action.expected_revision)
        if action.op in {"undo", "redo"}:
            source, target = ("past", "future") if action.op == "undo" else ("future", "past")
            if not state[source]:
                raise HTTPException(409, f"nothing to {action.op}")
            try:
                candidate = Project.model_validate(state[source][-1])
            except ValidationError as exc:
                raise HTTPException(422, str(exc)) from exc
            validate_references(candidate, *reference_maps(record, root))
            state[target].append(state["project"])
            state[source].pop()
            return commit(root, state, candidate, action.op, history=False)
        project = apply_action(Project.model_validate(state["project"]), action)
        validate_references(project, *reference_maps(record, root))
        return commit(root, state, project, action.op)

    @router.post("/subtitles/import")
    async def import_subtitles(request: Request, task_id: str):
        record, root = await access(request, task_id, True)
        payload = await parse_body(request, SubtitleImport)
        await reauthorize(request, task_id, record, root)
        editable(record, root)
        state = read_state(root)
        revision_matches(state, payload.expected_revision)
        try:
            previous = Project.model_validate(state["project"])
            data = previous.model_dump()
            # Always add a new track: never overwrite locked or existing captions.
            _active_timeline_data(data)["tracks"].append(Track(id=payload.track_id, type="text", name="Imported SRT", clips=parse_srt(payload.srt)).model_dump())
            project = Project.model_validate(data)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        enforce_locks(previous, project)
        validate_references(project, *reference_maps(record, root))
        return commit(root, state, project, "subtitles.import")

    @router.get("/audit")
    async def get_audit(request: Request, task_id: str):
        _, root = await access(request, task_id)
        return {"records": read_state(root)["audit"]}

    @router.get("/sources")
    async def get_sources(request: Request, task_id: str) -> dict[str, Any]:
        record, root = await access(request, task_id)
        index = studio_assets.load_index(root)
        images = {row.id: row for row in index.images}
        sources = catalog(record, index)
        paths = {p.relative_to(root).as_posix(): key for key, p in sources.items()}
        shots = local_json(root, "shots_annotated.json", local_json(root, "shots.json", []))
        report = local_json(root, "report.json", {})
        return {"sources": [{"id": key, "name": images[key].public(task_id)["name"] if key in images else path.name, "bytes": path.stat().st_size,
                              "url": f"/api/tasks/{task_id}/studio/sources/{key}",
                              "burned_subtitles": key == "final", "is_image": key in images,
                              **({"width": images[key].width, "height": images[key].height,
                                  "duration": MAX_DURATION, "duration_semantics": "still_hold_limit_not_source_eof"} if key in images else {})} for key, path in sources.items()],
                "shots": [{k: row[k] for k in ("shot_id", "start", "end", "duration", "description", "media_origin") if k in row} | {"source_id": paths.get(row.get("norm_path"))} for row in shots],
                "report": {"rows": [{k: row[k] for k in ("sentence_id", "sentence", "duration", "confidence", "is_fallback", "audio_kind") if k in row} for row in report.get("rows", [])], "quality": report.get("quality")}}

    @router.get("/sources/{source_id}")
    @router.get("/preview/{source_id}")
    async def get_source(request: Request, task_id: str, source_id: str):
        record, _ = await access(request, task_id)
        path = catalog(record).get(source_id)
        if path is None:
            raise HTTPException(404, "source not found")
        return FileResponse(path, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    def job_path(root: Path, job_id: str) -> Path:
        return contained(root, root / "studio/jobs" / f"{safe_id(job_id)}.json", exists=False)

    def check_job_root(root: Path, identity: tuple[int, int]) -> None:
        """Never clean or persist into a removed, linked or substituted task root."""
        safe_root = contained(Path(settings.data_dir).absolute(), root)
        now = safe_root.stat()
        if safe_root != root or (now.st_dev, now.st_ino) != identity:
            raise HTTPException(409, "task directory changed during studio job")

    def remove_job_work(root: Path, job_id: str, revision: int, identity: tuple[int, int]) -> None:
        """One removal attempt, confined to this job; errors are not suppressed here."""
        if type(revision) is not int or revision < 0:
            raise HTTPException(422, "invalid studio job revision")
        check_job_root(root, identity)
        work = contained(root, root / "studio/outputs" / f"r{revision}" / safe_id(job_id), exists=False)
        if work.exists():
            shutil.rmtree(work)
        check_job_root(root, identity)
        if contained(root, work, exists=False).exists():
            raise OSError("studio job workspace cleanup is incomplete")

    def cleanup_job(root: Path, job: dict[str, Any], identity: tuple[int, int]) -> None:
        """Normalize an unsuccessful job without confusing cleanup with its outcome."""
        job["output_id"] = None
        job.pop("result", None)
        try:
            remove_job_work(root, job["id"], job["revision"], identity)
        except (OSError, HTTPException):
            # Retain the workspace under studio/outputs: studio_bytes still
            # counts it. Never expose the filesystem exception, path or job ID.
            job.update(cleanup_pending=True, cleanup_error=
                       "Partial output cleanup is pending; retained files still count toward the Studio storage quota.")
        else:
            job.update(cleanup_pending=False, cleanup_error=None)

    def load_job(root: Path, job_id: str, *, retry_cleanup: bool = False) -> dict[str, Any]:
        job = local_json(root, f"studio/jobs/{safe_id(job_id)}.json")
        if job is None:
            raise HTTPException(404, "job not found")
        if not isinstance(job, dict) or job.get("id") != job_id:
            raise HTTPException(422, "invalid studio job metadata")
        key = str(job_path(root, job_id))
        interrupted = job["state"] in {"queued", "running"} and key not in _RUNNING
        retry = (retry_cleanup and job["state"] in {"failed", "cancelled", "interrupted"}
                 and job.get("cleanup_pending") is True and key not in _RUNNING)
        if interrupted or retry:
            previous = dict(job)
            root_stat = root.stat()
            identity = (root_stat.st_dev, root_stat.st_ino)
            if interrupted:
                job.update(state="interrupted", error="server restarted; job was not resumed", finished_at=time.time())
            cleanup_job(root, job, identity)
            # Only cleanup failures become metadata. Persistence failures must
            # propagate, and a replaced root must never be recreated or written.
            check_job_root(root, identity)
            if job != previous:
                write_json_atomic(job_path(root, job_id), job)
            if interrupted:
                state = read_state(root)
                audit(state, "job.interrupted", job_id=job_id)
                write_state(root, state)
            # A cleanup retry does not change error/finished_at/history/audit.
        return job

    def proxy_jobs(root: Path, *, retry_cleanup: bool = False) -> list[dict[str, Any]]:
        folder = contained(root, root / "studio/jobs", exists=False)
        paths = list(islice(folder.glob("*.json"), MAX_JOBS + 1)) if folder.exists() else []
        if len(paths) > MAX_JOBS:
            raise HTTPException(429, "task studio job quota exceeded (20)")
        jobs = []
        for path in sorted(paths):
            safe_path = contained(root, path)
            if not safe_path.is_file() or safe_path.stat().st_size > MAX_JSON:
                raise HTTPException(422, "studio job metadata exceeds its byte limit")
            job = local_json(root, path.relative_to(root).as_posix())
            if not isinstance(job, dict):
                raise HTTPException(422, "invalid studio job metadata")
            if job.get("kind") == "proxy":
                if (job.get("id") != path.stem or job.get("state") not in
                        {"queued", "running", "succeeded", "failed", "cancelled", "interrupted"}):
                    raise HTTPException(422, "invalid proxy job metadata")
                jobs.append(load_job(root, path.stem, retry_cleanup=retry_cleanup))
        return jobs

    def proxy_current(record: Any, root: Path, identity: tuple[int, int], pipeline_revision: int,
                      expected: int | None = None) -> None:
        # The host checks busy guards too; an awaited hash/probe rechecks here.
        current = getattr(task_manager, "get", None)
        safe_root = contained(Path(settings.data_dir).absolute(), Path(record.task_dir).absolute())
        now = safe_root.stat()
        background = getattr(record, "background", None)
        if (type(pipeline_revision) is not int or type(record.revision) is not int
                or (callable(current) and current(record.task_id) is not record) or safe_root != root
                or (now.st_dev, now.st_ino) != identity or record.revision != pipeline_revision
                or getattr(record.status, "value", record.status) != "done"
                or (background is not None and not background.done())
                or legacy_task_busy(root) or task_operation_busy(root)):
            raise HTTPException(409, "source/pipeline changed or task work is active; proxy unavailable")
        if expected is not None:
            revision_matches(read_state(root), expected)

    def proxy_source(record: Any, root: Path, source_id: str) -> Path:
        # Imported stills/LUTs are irrelevant here. Do not hash/decode the asset
        # library to resolve one RAW source, or accept any Studio output as input.
        path = catalog(record, studio_assets.AssetIndex()).get(source_id)
        if (path is None or is_image_id(source_id) or path.suffix.lower() not in studio_proxy.VIDEO_CONTAINERS
                or path.is_relative_to(root / "studio")):
            raise HTTPException(422, "proxy requires a known original/normalized/final video source_id")
        return contained(root, path)

    @asynccontextmanager
    async def proxy_resources(root: Path, task_key: str, budget: int):
        """One shared Studio slot from BEFORE hashing through final worker drain.

        The temporary _RUNNING key owns preparation/read verification too. On
        submission the same key is handed to the durable job's background task.
        No cache map, queue, retry loop, or per-task lock collection is added.
        """
        key = str(root)
        if key in _BUSY:
            raise HTTPException(409, "one studio render/export/import/proxy operation per task is allowed")
        if len(_BUSY) >= 2:
            raise HTTPException(429, "global studio capacity reached")
        initial_used = studio_assets.studio_bytes(root)
        reserve = int(getattr(settings, "minimum_free_disk_bytes", 0))
        if initial_used > MAX_TASK_BYTES:
            raise HTTPException(507, "task studio storage quota reached")
        if shutil.disk_usage(root).free < reserve + sum(_DISK_RESERVATIONS.values()) + budget:
            raise HTTPException(507, "insufficient free disk for studio proxy")
        owner = asyncio.current_task()
        assert owner is not None
        _BUSY.add(key)
        _DISK_RESERVATIONS[key] = budget
        _RUNNING[task_key] = owner
        owned.add(task_key)
        guard = getattr(task_manager, "_upload_capacity_guard", None)
        reservation: Any = None
        admitted = False
        host_bytes = 0

        def check_storage() -> None:
            if key not in _BUSY or _DISK_RESERVATIONS.get(key) != budget or task_key not in _RUNNING:
                raise HTTPException(409, "proxy operation lost its task reservation")
            used = studio_assets.studio_bytes(root)
            if used > MAX_TASK_BYTES:
                raise HTTPException(507, "task studio storage quota reached")
            other = sum(value for k, value in _DISK_RESERVATIONS.items() if k != key)
            # Non-Studio uploads/pipelines also own free space. Double counting
            # other Studio host reservations is conservative, never under-reserves.
            other += max(0, int(getattr(guard, "_reserved_bytes", 0)) - host_bytes)
            remaining = max(0, budget - max(0, used - initial_used))
            if shutil.disk_usage(root).free < reserve + other + remaining:
                raise HTTPException(507, "minimum free disk reserve reached during proxy work")

        try:
            if guard is not None:
                reservation = guard.reserve(budget)

                async def acquire() -> None:
                    nonlocal admitted, host_bytes
                    async with asyncio.timeout(30):
                        lease = await reservation.__aenter__()
                        host_bytes = int(getattr(lease, "reserved_bytes", 0))
                        admitted = True

                acquiring = asyncio.create_task(acquire())
                try:
                    await asyncio.shield(acquiring)
                except asyncio.CancelledError:
                    try:
                        await studio_assets.drain(acquiring)
                    except (Exception, asyncio.CancelledError):
                        pass
                    raise
                except InsufficientDiskSpaceError as exc:
                    raise HTTPException(507, "insufficient reserved disk for studio proxy") from exc
                except TimeoutError as exc:
                    raise HTTPException(503, "studio proxy disk admission timed out") from exc
            check_storage()
            yield check_storage
        finally:
            try:
                if admitted:
                    async def release() -> None:
                        await reservation.__aexit__(None, None, None)
                    await studio_assets.drain(asyncio.create_task(release()))
            finally:
                _RUNNING.pop(task_key, None)
                owned.discard(task_key)
                _DISK_RESERVATIONS.pop(key, None)
                _BUSY.discard(key)

    async def verify_proxy(record: Any, root: Path, job: dict[str, Any], check: Any,
                           source_digest: studio_proxy.FileDigest | None = None) -> tuple[Path, Any]:
        """Verify immutable profile + source AND output bytes before cache/Range.

        A stored hash without reading the bytes is not verification. Metadata is
        only a race fence, never a substitute for the streaming content hashes.
        """
        try:
            if job.get("kind") != "proxy" or job.get("state") != "succeeded":
                raise RenderError("proxy is not complete")
            if type(job.get("revision")) is not int or job["revision"] < 0 or job.get("output_id") != job.get("id"):
                raise RenderError("invalid proxy job binding")
            job_id = safe_id(job["id"])
            work = contained(root, root / "studio/outputs" / f"r{job['revision']}" / job_id)
            snapshot_path, manifest_path = work / "snapshot.json", work / "manifest.json"
            protected = {"job": job_path(root, job_id), "snapshot": snapshot_path, "manifest": manifest_path}
            protected_stamps = {k: studio_proxy.file_stamp(root, p) for k, p in protected.items()}
            if any(stamp[2] > MAX_JSON for stamp in protected_stamps.values()):
                raise RenderError("proxy metadata exceeds its byte limit")
            snapshot = local_json(root, snapshot_path.relative_to(root).as_posix())
            manifest = local_json(root, manifest_path.relative_to(root).as_posix())
            binding, verified_key = _validate_proxy_snapshot(job, snapshot)
            studio_proxy.validate_result(job["result"], binding)
            source = proxy_source(record, root, binding.source_id)
            if (binding.pipeline_revision != record.revision or source.relative_to(root).as_posix() != binding.source_path
                    or manifest.get("kind") != "proxy" or manifest.get("job") != job_id
                    or manifest.get("cache_key") != verified_key or manifest.get("proxy") != binding.model_dump()
                    or manifest.get("revision") != job["revision"]
                    or not re.fullmatch(r"[a-f0-9]{64}", str(job["result"].get("sha256", "")))
                    or any(manifest.get(name) != value for name, value in job["result"].items())):
                raise RenderError("proxy binding is stale or invalid")
            output = contained(root, work / job["result"]["file"])
            output_stamp = studio_proxy.file_stamp(root, output)
            if output_stamp != manifest.get("output_fingerprint") or output_stamp[2] != job["result"]["bytes"]:
                raise RenderError("proxy output changed")

            def stable() -> None:
                check()
                if (proxy_source(record, root, binding.source_id) != source
                        or studio_proxy.file_stamp(root, source) != binding.source_fingerprint
                        or studio_proxy.file_stamp(root, output) != output_stamp
                        or studio_proxy.metadata_stamp(root) != binding.metadata
                        or requires_disclosure(root) != binding.disclosure
                        or any(studio_proxy.file_stamp(root, p) != protected_stamps[k] for k, p in protected.items())):
                    raise HTTPException(409, "proxy source/output metadata changed; result unavailable")

            stable()
            actual_source = source_digest or await studio_proxy.guarded(studio_proxy.hash_file(root, source), stable)
            if actual_source.sha256 != binding.source_sha256 or actual_source.fingerprint != binding.source_fingerprint:
                raise RenderError("proxy source content changed")
            actual_output = await studio_proxy.guarded(studio_proxy.hash_file(root, output, max_bytes=MAX_BYTES), stable)
            if actual_output.sha256 != manifest["sha256"] or actual_output.fingerprint != output_stamp:
                raise RenderError("proxy output content changed")
            stable()
            return output, stable
        except (RenderError, ValidationError, KeyError, TypeError, ValueError, OSError, AttributeError) as exc:
            raise HTTPException(409, "proxy is unavailable, stale or failed integrity verification") from exc

    async def submit_proxy(request: Request, task_id: str, record: Any, root: Path, payload: ProxyRequest,
                           before: tuple[int, int, int]) -> JSONResponse:
        pipeline_revision, device, inode = before
        identity = (device, inode)
        expected = payload.expected_revision
        editable(record, root)
        proxy_current(record, root, identity, pipeline_revision, expected)
        source = proxy_source(record, root, payload.source_id)
        try:
            source_stamp = studio_proxy.file_stamp(root, source)
            watched = studio_proxy.metadata_stamp(root)
        except (RenderError, OSError) as exc:
            raise HTTPException(422, "proxy source or metadata is unavailable") from exc
        disclosure = requires_disclosure(root)  # Raw preview is NOT gated by final/report QC.
        job_id = uuid4().hex
        task_key = str(job_path(root, job_id))
        resources = proxy_resources(root, task_key, MAX_BYTES * 2 + MAX_STATE + MAX_JSON * 4)
        check_storage = await resources.__aenter__()
        work: Path | None = None
        handed_off = False
        released = False
        preparing = True

        def check() -> None:
            proxy_current(record, root, identity, pipeline_revision, expected if preparing else None)
            if not released:
                check_storage()
            if (proxy_source(record, root, payload.source_id) != source or studio_proxy.file_stamp(root, source) != source_stamp
                    or studio_proxy.metadata_stamp(root) != watched or requires_disclosure(root) != disclosure):
                raise HTTPException(409, "source/disclosure metadata changed during proxy work")

        async def reauthorize() -> None:
            check()
            latest, latest_root = await access(request, task_id, True)
            if latest is not record or latest_root != root:
                raise HTTPException(409, "task authorization changed during proxy submission")
            check()

        try:
            await reauthorize()  # Also checks races while the host disk lease entered.
            digest = await studio_proxy.guarded(studio_proxy.hash_file(root, source), check)
            await reauthorize()
            key = studio_proxy.cache_key(digest.sha256, pipeline_revision, disclosure)
            compatible_keys = (key, _pre_sequence_proxy_key(digest.sha256, pipeline_revision, disclosure))
            # Successful jobs ARE the bounded cache. A full 20-job task may
            # still reuse a verified result; no extra encoder/job/history entry.
            for previous in proxy_jobs(root):
                if previous.get("source_id") != payload.source_id or previous.get("cache_key") not in compatible_keys:
                    continue
                if previous["state"] in {"queued", "running"}:
                    raise HTTPException(409, "the source proxy is already pending")
                if previous["state"] == "succeeded":
                    try:
                        _, stable = await verify_proxy(record, root, previous, check, digest)
                    except HTTPException as exc:
                        if exc.status_code not in {403, 404, 409, 422}:
                            raise
                        check()  # A corrupt old cache entry is NOT a task-race waiver.
                        continue
                    await reauthorize()
                    stable()
                    # Host lease release can await too. No cached receipt may
                    # escape a revision/session change while that cleanup drains.
                    try:
                        await resources.__aexit__(None, None, None)
                    finally:
                        released = True
                    await reauthorize()
                    stable()
                    return JSONResponse({**previous, "cached": True}, headers={"Cache-Control": "no-store"})
            folder = contained(root, root / "studio/jobs", exists=False)
            if folder.exists() and len(list(islice(folder.glob("*.json"), MAX_JOBS))) >= MAX_JOBS:
                raise HTTPException(429, "task studio job quota reached (20)")
            if studio_assets.studio_bytes(root) + MAX_BYTES + MAX_STATE + MAX_JSON * 4 > MAX_TASK_BYTES:
                raise HTTPException(507, "task studio storage quota reached (including proxy workspace)")
            if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
                raise HTTPException(503, "FFmpeg and ffprobe are required for a source proxy")
            work = contained(root, root / "studio/outputs" / f"r{expected}" / job_id, exists=False)
            work.mkdir(parents=True, exist_ok=False)
            info = await studio_proxy.guarded(probe(source, work), check, cancel=False)
            await reauthorize()
            duration, has_audio = studio_proxy.source_facts(info)
            binding = studio_proxy.Binding(source_id=payload.source_id, source_path=source.relative_to(root).as_posix(),
                source_sha256=digest.sha256, source_fingerprint=digest.fingerprint, pipeline_revision=pipeline_revision,
                duration=duration, has_audio=has_audio, disclosure=disclosure, profile=studio_proxy.profile(), metadata=watched)
            project = studio_proxy.project(payload.source_id, duration, has_audio)
            render_request = RenderRequest(expected_revision=expected, options=studio_proxy.options())
            job: dict[str, Any] = {"id": job_id, "revision": expected, "pipeline_revision": pipeline_revision,
                "state": "queued", "kind": "proxy", "source_id": payload.source_id, "cache_key": key,
                "created_at": time.time(), "options": render_request.options.model_dump(), "output_id": None, "error": None,
                "cleanup_pending": False, "cleanup_error": None,
                "qc": "raw source preview only; not an edited or pipeline-approved export", "disclosure": disclosure}
            write_json_atomic(work / "snapshot.json", {"project": project.model_dump(), "options": job["options"],
                                                      "proxy": binding.model_dump()})
            write_json_atomic(job_path(root, job_id), job)
            state = read_state(root)
            audit(state, "job.submit", job_id=job_id, revision=expected, kind="proxy", source_id=payload.source_id, cache_key=key)
            write_state(root, state)
            started, proceed = asyncio.Event(), asyncio.Event()

            async def run() -> None:
                try:
                    started.set()
                    await proceed.wait()
                    job["state"] = "running"
                    write_json_atomic(job_path(root, job_id), job)
                    deadline = min(300, settings.media_command_timeout_seconds)
                    result = await studio_proxy.guarded(asyncio.wait_for(
                        render_project(project, render_request.options, {payload.source_id: source}, work,
                                       disclosure=disclosure, timeout=deadline, luts={}), deadline), check)
                    check()
                    studio_proxy.validate_result(result, binding)
                    # A restored mtime/size is not enough: hash the source AGAIN
                    # after encoding, then hash and bind the actual proxy bytes.
                    final_source = await studio_proxy.guarded(studio_proxy.hash_file(root, source), check)
                    if final_source != digest:
                        raise RenderError("source content changed during proxy render; result discarded")
                    output = contained(root, work / result["file"])
                    output_digest = await studio_proxy.guarded(studio_proxy.hash_file(root, output, max_bytes=MAX_BYTES), check)
                    check()
                    if output_digest.fingerprint[2] != result["bytes"]:
                        raise RenderError("proxy output size changed during verification")
                    if studio_assets.studio_bytes(root) + MAX_STATE + MAX_JSON * 2 > MAX_TASK_BYTES:
                        raise RenderError("studio storage quota exceeded during proxy job; result discarded")
                    result["sha256"] = output_digest.sha256
                    write_json_atomic(work / "manifest.json", {"job": job_id, "kind": "proxy", "revision": expected,
                        "cache_key": key, "proxy": binding.model_dump(), "output_fingerprint": output_digest.fingerprint,
                        "disclosure_intervals": [[0, duration]] if disclosure else [], "qc": job["qc"], **result})
                    job.update(state="succeeded", output_id=job_id, result=result)
                except asyncio.CancelledError:
                    job.update(state="cancelled", error="cancelled; proxy output is unavailable")
                except Exception:
                    # Even a codec exception or malformed persisted value may
                    # contain local paths: never reflect it in a public job.
                    job.update(state="failed", error="proxy render or source verification failed; output is unavailable")
                finally:
                    job["finished_at"] = time.time()
                    try:
                        if job["state"] != "succeeded":
                            cleanup_job(root, job, identity)
                        check_job_root(root, identity)
                        write_json_atomic(job_path(root, job_id), job)
                        latest = read_state(root)
                        audit(latest, "job." + job["state"], job_id=job_id, kind="proxy", source_id=payload.source_id)
                        write_state(root, latest)
                    finally:
                        await resources.__aexit__(None, None, None)

            task = asyncio.create_task(run())
            handed_off = True
            try:
                # Start run's finally before cancellation, without starting the
                # encoder while the final private/revision check is awaiting.
                # Until started, DELETE/lifespan cancels this preparing caller,
                # which drains the start signal then cancels the entered worker.
                await studio_assets.drain(asyncio.create_task(started.wait()))
                _RUNNING[task_key] = task
                await reauthorize()
            except BaseException:
                task.cancel()
                try:
                    await studio_assets.drain(task)
                except (Exception, asyncio.CancelledError):
                    pass
                raise
            preparing = False  # Later project edits do not alter a RAW snapshot.
            proceed.set()
            return JSONResponse(job, status_code=202, headers={"Cache-Control": "no-store"})
        except (MediaProcessingError, TimeoutError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            raise HTTPException(422, "source proxy preparation failed; use a valid video of at most 120 seconds") from exc
        finally:
            if not handed_off and not released:
                try:
                    if work is not None:
                        studio_assets.cleanup(root, work, identity)
                finally:
                    await resources.__aexit__(None, None, None)

    async def submit(request: Request, task_id: str, simple: bool, *, proxy_mode: bool = False):
        record, root = await access(request, task_id, True)
        before = None
        if proxy_mode:
            root_stat = root.stat()
            before = (record.revision, root_stat.st_dev, root_stat.st_ino)
            try:
                async with asyncio.timeout(30):
                    payload = await parse_body(request, ProxyRequest)
            except TimeoutError as exc:
                raise HTTPException(408, "studio proxy request body timed out") from exc
        else:
            payload = await parse_body(request, RenderRequest)
        if proxy_mode:
            assert before is not None
            return await submit_proxy(request, task_id, record, root, payload, before)
        await reauthorize(request, task_id, record, root)
        editable(record, root)
        key = str(root)
        if key in _BUSY:
            raise HTTPException(409, "one studio render/export per task is allowed")
        if len(_BUSY) >= 2:
            raise HTTPException(429, "global studio render capacity reached")
        state = read_state(root)
        revision_matches(state, payload.expected_revision)
        job_dir = contained(root, root / "studio/jobs", exists=False)
        existing = list(job_dir.glob("*.json")) if job_dir.exists() else []
        if len(existing) >= MAX_JOBS:
            raise HTTPException(429, "task studio job quota reached (20)")
        used = studio_assets.studio_bytes(root)
        if used + MAX_BYTES + MAX_STATE + MAX_JSON * 2 > MAX_TASK_BYTES:
            raise HTTPException(507, "task studio storage quota reached")
        reserve = int(getattr(settings, "minimum_free_disk_bytes", 0))
        if shutil.disk_usage(root).free < reserve + sum(_DISK_RESERVATIONS.values()) + MAX_BYTES * 2:
            raise HTTPException(507, "insufficient free disk for studio render")
        check_qc(root)
        require_publication(record)
        disclosure = requires_disclosure(root)
        if disclosure and payload.options.format in {"mp3", "wav", "srt", "ass"}:
            raise HTTPException(422, "export mode would strip AI disclosure")
        asset_index = studio_assets.load_index(root)
        sources = catalog(record, asset_index)
        luts = studio_assets.lut_paths(root, asset_index)
        project = Project.model_validate(state["project"])
        validate_references(project, sources, luts)
        if not simple:
            try:
                timeline = evaluate_project(project)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            if timeline.duration <= 0:
                raise HTTPException(422, "empty active timeline")
            if any(c.source_id == "final" for t in timeline.active_tracks() if t.type in {"video", "overlay"} for c in t.clips) and payload.options.subtitles != "standard":
                raise HTTPException(422, "final has burned subtitles; use video_only picture plus final audio and timed text instead")
        job_id = uuid4().hex
        work = contained(root, root / "studio/outputs" / f"r{state['revision']}" / job_id, exists=False)
        pipeline_revision = record.revision
        root_stat = root.stat()
        root_identity = (root_stat.st_dev, root_stat.st_ino)
        task_key = str(job_path(root, job_id))
        work_created = False
        handed_off = False
        mode_export: list[ModeExport] = []
        mode_inputs = mode_export_inputs(root) if simple and getattr(record, "mode_contract", False) else {}
        mode_hashes = mode_export_hashes(mode_inputs) if mode_inputs else {}
        _BUSY.add(key)  # Before the first awaited media operation: closes submission races.
        _DISK_RESERVATIONS[key] = MAX_BYTES * 2
        try:
            preparation = asyncio.current_task()
            assert preparation is not None
            _PREPARING[task_key] = preparation
            owned_preparations.add(task_key)
            work.mkdir(parents=True, exist_ok=False)
            work_created = True
            if simple:
                if mode_inputs:
                    project = await simple_project(root, sources, payload.options, work, mode_contract=True, mode_export=mode_export,
                                                   mode_duration_limit=min(MAX_MODE_DURATION, float(getattr(settings, "max_total_source_duration_seconds", MAX_MODE_DURATION))))
                else:
                    project = await simple_project(root, sources, payload.options, work)
                validate_references(project, sources, luts)
            try:
                timeline = project.evaluate() if isinstance(project, SimpleModeProject) else evaluate_project(project)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            if timeline.duration <= 0:
                raise HTTPException(422, "empty active timeline")
            # ffprobe/simple_project and the host policy can all await. There
            # must be no persistent admission until the last policy check AND
            # the subsequent synchronous identity/revision/status guards pass.
            await reauthorize(request, task_id, record, root)
            check_job_root(root, root_identity)
            editable(record, root)
            revision_matches(read_state(root), payload.expected_revision)
            if record.revision != pipeline_revision or getattr(record.status, "value", record.status) != "done":
                raise HTTPException(409, "pipeline changed while preparing studio export")
            check_qc(root)
            require_publication(record)
            if requires_disclosure(root) != disclosure:
                raise HTTPException(409, "disclosure metadata changed while preparing studio export")
            if mode_inputs and mode_export_hashes(mode_inputs) != mode_hashes:
                raise HTTPException(409, "mode sources changed while preparing export")
            # Expanded leaves determine actual decoding, but the immutable
            # snapshot authorizes/watches EVERY declared source/LUT, including
            # inactive sequences, hidden tracks and shared library metadata.
            used_sources = {source_id: sources[source_id] for source_id in project.source_ids()}
            used_luts = {lut_id: luts[lut_id] for lut_id in project.lut_ids()}
            asset_hashes = {row.id: row.sha256 for row in [*asset_index.images, *asset_index.luts]
                            if row.id in used_sources or row.id in used_luts}
            watched = dict(used_sources)
            watched.update(used_luts)
            for name in ("report.json", "quality_report.json", "generated_media_disclosure.json", "edl.json", "timings.json", "script_structure.json", "subtitle_manifest.json"):
                path = contained(root, root / name, exists=False)
                if path.exists():
                    watched["metadata_" + name] = path
            stamps = fingerprint(watched)
            job: dict[str, Any] = {"id": job_id, "revision": state["revision"], "pipeline_revision": pipeline_revision,
                   "state": "queued", "kind": "export" if simple else "render", "created_at": time.time(),
                   "options": payload.options.model_dump(), "output_id": None, "error": None,
                     "cleanup_pending": False, "cleanup_error": None,
                   "qc": "unreviewed derivative; not pipeline-approved", "disclosure": disclosure}
            transitions = transition_summary(timeline)
            if transitions is not None:
                job["transition_semantics"] = transitions
            sequences = sequence_summary(project.template() if isinstance(project, SimpleModeProject) else project, timeline)
            if sequences is not None:
                job["sequence_semantics"] = sequences
            if simple:
                text_export = payload.options.format in {"srt", "ass"}
                audio_export = payload.options.format in {"mp3", "wav"}
                restyled = not text_export and not audio_export and payload.options.subtitles != "standard"
                job["export_semantics"] = {
                    "picture": "none" if text_export or audio_export else ("clean_restyled" if restyled else "finished_final"),
                    "audio": "none" if payload.options.format in {"gif", "png", "srt", "ass"} else "finished_final_mix",
                    "framing": "not_applicable" if text_export or audio_export else "center_crop",
                    "warnings": (["Clean export cannot preserve optional burned graphics or finishing fades; title is reconstructed only from validated script/timing metadata."]
                                 if restyled else []),
                }
                if mode_inputs:
                    job["export_semantics"] = {
                        "picture": "none" if audio_export else "mode_clean_picture_canonical_overlays",
                        "audio": "none" if payload.options.format == "gif" else ("narration_and_original_voice_no_bgm" if audio_export else "finished_final_mix"),
                        "framing": "not_applicable" if audio_export else "center_crop_relayout_overlays",
                        "subtitle_size_1080p": 72 if payload.options.subtitles == "large" else 54,
                        "quote_policy": "preserve_confirmed_policy; none hides all dialogue",
                        "warnings": [],
                    }
            write_json_atomic(work / "snapshot.json", {"project": project.model_dump(), "options": payload.options.model_dump(), "sources": stamps,
                                                       "asset_sha256": asset_hashes, "mode_source_sha256": mode_hashes})
            write_json_atomic(job_path(root, job_id), job)
            state = read_state(root)
            audit(state, "job.submit", job_id=job_id, revision=job["revision"], options=job["options"])
            write_state(root, state)

            async def run() -> None:
                try:
                    job["state"] = "running"
                    write_json_atomic(job_path(root, job_id), job)
                    deadline = min(300, settings.media_command_timeout_seconds)
                    result = await asyncio.wait_for(
                        render_project(project, payload.options, used_sources, work, disclosure=disclosure, timeout=deadline, luts=used_luts,
                                       **({"mode_export": mode_export[0]} if mode_export else {})),
                        deadline,
                    )
                    # A graceful server drain must still allow an admitted job
                    # to finish. Recheck ownership, not new-admission/drain policy.
                    current = getattr(task_manager, "get", None)
                    if (callable(current) and current(task_id) is not record) or Path(record.task_dir).absolute() != root:
                        raise RenderError("source/pipeline changed during studio job; result discarded")
                    check_job_root(root, root_identity)
                    if fingerprint(watched) != stamps or record.revision != pipeline_revision or getattr(record.status, "value", record.status) != "done":
                        raise RenderError("source/pipeline changed during studio job; result discarded")
                    if mode_inputs and mode_export_hashes(mode_inputs) != mode_hashes:
                        raise RenderError("mode sources changed during studio job; result discarded")
                    if asset_hashes:
                        current_assets = studio_assets.load_index(root)
                        current_hashes = {row.id: row.sha256 for row in [*current_assets.images, *current_assets.luts] if row.id in asset_hashes}
                        if current_hashes != asset_hashes:
                            raise RenderError("asset content changed during studio job; result discarded")
                    for path in watched.values():
                        contained(root, path)
                    check_qc(root)
                    require_publication(record)
                    if requires_disclosure(root) != disclosure:
                        raise RenderError("disclosure metadata changed during studio job")
                    if studio_assets.studio_bytes(root) + MAX_STATE + MAX_JSON * 2 > MAX_TASK_BYTES:
                        raise RenderError("studio storage quota exceeded during job; result discarded")
                    if mode_inputs:
                        result["sha256"] = mode_export_hashes({"output": work / result["file"]})["output"]
                    write_json_atomic(work / "manifest.json", {"job": job_id, "revision": job["revision"], "disclosure_intervals": [[0, result["duration"]]] if disclosure else [], "qc": job["qc"], "export_semantics": job.get("export_semantics"), **result})
                    job.update(state="succeeded", output_id=job_id, result=result)
                except asyncio.CancelledError:
                    job.update(state="cancelled", error="cancelled; output is unavailable")
                except Exception as exc:
                    # No source paths, FFmpeg commands, or credentials in public errors.
                    detail = str(exc) if isinstance(exc, RenderError) else "local media render failed; inspect protected job logs before retry"
                    job.update(state="failed", error=detail)
                finally:
                    job["finished_at"] = time.time()
                    try:
                        if job["state"] != "succeeded":
                            cleanup_job(root, job, root_identity)
                        check_job_root(root, root_identity)
                        write_json_atomic(job_path(root, job_id), job)
                        latest = read_state(root)
                        audit(latest, "job." + job["state"], job_id=job_id)
                        write_state(root, latest)
                    finally:
                        _DISK_RESERVATIONS.pop(key, None)
                        _BUSY.discard(key)
                        _RUNNING.pop(task_key, None)
                        owned.discard(task_key)

            runner = run()
            try:
                task = asyncio.create_task(runner)
            except BaseException:
                runner.close()
                raise
            _RUNNING[task_key] = task
            owned.add(task_key)
            handed_off = True
            # Give run() its first turn so immediate cancellation executes its finally.
            await asyncio.sleep(0)
            return JSONResponse(job, status_code=202, headers={"Cache-Control": "no-store"})
        except BaseException:
            if not handed_off:
                try:
                    if work_created:
                        remove_job_work(root, job_id, payload.expected_revision, root_identity)
                finally:
                    _DISK_RESERVATIONS.pop(key, None)
                    _BUSY.discard(key)
            raise
        finally:
            _PREPARING.pop(task_key, None)
            owned_preparations.discard(task_key)

    @router.post("/render", status_code=202)
    async def render(request: Request, task_id: str):
        return await submit(request, task_id, False)

    @router.post("/export", status_code=202)
    async def export(request: Request, task_id: str):
        return await submit(request, task_id, True)

    @router.post("/proxies", status_code=202)
    async def proxy(request: Request, task_id: str):
        return await submit(request, task_id, False, proxy_mode=True)

    @router.get("/proxies")
    async def get_proxies(request: Request, task_id: str):
        _, root = await access(request, task_id)
        # No task creation, hashing, probing or resubmission on this read. The
        # only recovery writes are interruption and pending-cleanup metadata.
        return JSONResponse({"proxies": proxy_jobs(root, retry_cleanup=True)}, headers={"Cache-Control": "no-store"})

    @router.get("/proxies/{job_id}/media")
    async def get_proxy_media(request: Request, task_id: str, job_id: str):
        record, root = await access(request, task_id)
        job = load_job(root, job_id, retry_cleanup=True)
        if job.get("id") != job_id or job.get("kind") != "proxy":
            raise HTTPException(404, "proxy job not found")
        if job.get("state") != "succeeded" or type(job.get("pipeline_revision")) is not int:
            raise HTTPException(409, "proxy output is not available")
        stat = root.stat()
        identity = (stat.st_dev, stat.st_ino)
        pipeline_revision = job["pipeline_revision"]
        proxy_current(record, root, identity, pipeline_revision)
        # Reads share the same global/per-task bounded verification workers.
        # A random key cannot collide with or interrupt the durable job being read.
        resources = proxy_resources(root, str(job_path(root, uuid4().hex)), MAX_JSON)
        check_storage = await resources.__aenter__()
        transferred = False

        def check() -> None:
            proxy_current(record, root, identity, pipeline_revision)
            check_storage()

        async def release() -> None:
            await resources.__aexit__(None, None, None)

        try:
            path, stable = await verify_proxy(record, root, job, check)
            # Authorization may change during disk hashing; check it again.
            latest, latest_root = await access(request, task_id)
            if latest is not record or latest_root != root:
                raise HTTPException(409, "task authorization changed during proxy verification")
            stable()
            response = studio_proxy.VerifiedFileResponse(path, stable, release)
            transferred = True
            return response
        except (RenderError, OSError) as exc:
            raise HTTPException(409, "proxy is unavailable or failed integrity verification") from exc
        finally:
            if not transferred:
                await release()

    @router.get("/jobs/{job_id}")
    async def get_job(request: Request, task_id: str, job_id: str):
        _, root = await access(request, task_id)
        return JSONResponse(load_job(root, job_id, retry_cleanup=True), headers={"Cache-Control": "no-store"})

    @router.delete("/jobs/{job_id}")
    async def cancel_job(request: Request, task_id: str, job_id: str):
        _, root = await access(request, task_id, True)
        job = load_job(root, job_id)
        task = _RUNNING.get(str(job_path(root, job_id)))
        if task:
            task.cancel()
            # All jobs own media cleanup, not only RAW proxies. A disconnected
            # DELETE must not forward a second cancel into their cleanup.
            try:
                await studio_assets.drain(task)
            except asyncio.CancelledError:
                caller = asyncio.current_task()
                if caller is not None and caller.cancelling():
                    raise
            job = load_job(root, job_id)
        return job

    @router.get("/outputs/{output_id}")
    async def get_output(request: Request, task_id: str, output_id: str):
        record, root = await access(request, task_id)
        job = load_job(root, output_id, retry_cleanup=True)
        if job.get("kind") == "proxy":
            raise HTTPException(409, "raw source proxies require their verified private media route")
        if job["state"] != "succeeded":
            raise HTTPException(409, "output is not available")
        require_publication(record)
        if (getattr(record, "mode_contract", False) or getattr(record, "mode", "voiceover") != "voiceover") and (
            type(job.get("pipeline_revision")) is not int or job["pipeline_revision"] != record.revision
        ):
            raise HTTPException(409, "mode export belongs to an older pipeline revision; export the confirmed current version")
        # Legacy immutable derivatives retain historical downloads once CURRENT
        # recorded QC is cleared. Mode exports additionally bind the revision;
        # an old artifact cannot borrow a newer version's acknowledgements.
        options = ExportOptions.model_validate(job["options"])
        path = contained(root, root / "studio/outputs" / f"r{int(job['revision'])}" / safe_id(output_id) / f"output.{options.format}")
        if getattr(record, "mode_contract", False):
            if mode_export_hashes({"output": path})["output"] != job.get("result", {}).get("sha256"):
                raise HTTPException(409, "mode output integrity verification failed")
        return FileResponse(path, filename=f"studio-r{job['revision']}.{options.format}",
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    return router


def mode_export_inputs(root: Path) -> dict[str, Path]:
    # Include absence: inserting a previously absent file during an await also
    # invalidates the admission. No raw stored ASS is ever executed.
    paths = {name: contained(root, root / name, exists=False) for name in (
        "final.mp4", "video_only.mp4", "narration.m4a", "narration.wav",
        "subs.ass", "subtitle_manifest.json", "graphics.ass", "production_mode.json",
        "match_plan.json", "timings.json", "script_structure.json", "lower_thirds.json",
        "quality_report.json", "report.json", "generated_media_disclosure.json",
    )}
    for index, timing in enumerate(local_json(root, "timings.json", [])):
        paths[f"voice_unit_{index}"] = contained(root, root / timing["audio_path"])
    return paths


def mode_export_hashes(paths: dict[str, Path]) -> dict[str, str | None]:
    hashes: dict[str, str | None] = {}
    for name, path in paths.items():
        if not path.exists():
            hashes[name] = None
            continue
        contained(path.parent, path)
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        hashes[name] = digest.hexdigest()
    return hashes


def prepare_mode_export(root: Path, work: Path, options: ExportOptions) -> ModeExport:
    """Reproduce audited source documents, then change only subtitle policy.

    Never interpret a stored ASS program, nor infer words from legacy Clips.
    Omitted narration can be enabled from real timing/word metadata; quotes
    remain governed by the confirmed independent quote_caption preference.
    """
    from .graphics import generate_mode_graphics, generate_mode_subtitles, validate_mode_subtitle_artifacts, MODE_CAPTION_TEMPLATE
    from .models import EditingPreferences, MatchPlanItem, SentenceTiming
    from .production_modes import Speaker

    try:
        payload = local_json(root, "production_mode.json", {})
        if payload.get("mode_contract") is not True:
            raise ValueError("missing mode binding")
        preferences = EditingPreferences.model_validate(payload["preferences"])
        timings = [SentenceTiming.model_validate(t) for t in local_json(root, "timings.json", [])]
        plan = [MatchPlanItem.model_validate(p) for p in local_json(root, "match_plan.json", [])]
        if not timings or len(timings) != len(plan):
            raise ValueError("missing real timeline")
        title = local_json(root, "script_structure.json", {}).get("title") or ""
        original = validate_mode_subtitle_artifacts(contained(root, root / "subs.ass"), contained(root, root / "subtitle_manifest.json"))
        v2 = original["template_id"] == MODE_CAPTION_TEMPLATE
        generated = generate_mode_subtitles(work, timings, plan, preferences, title=title, v2=v2)
        quote_policy = "spoken" if v2 and preferences.quote_caption != "none" else preferences.quote_caption
        if (generated != original["events"] or preferences.caption_style != original["caption_style"]
            or quote_policy != original["quote_caption"]):
            raise ValueError("subtitle source metadata changed")
        graphics, receipts = generate_mode_graphics(
            work, timings, plan, [Speaker.model_validate(p) for p in payload["speakers"]], preferences,
            title=title, disclosure_intervals=[tuple(p) for p in payload.get("generated_intervals", [])],
        )
        if receipts != payload.get("lower_thirds", []) or receipts != local_json(root, "lower_thirds.json", []):
            raise ValueError("lower third binding changed")
        graphic_document = graphics.read_text(encoding="utf-8") if graphics else ""
        if graphics and graphics.read_bytes() != contained(root, root / "graphics.ass").read_bytes():
            raise ValueError("graphics binding changed")
        target = preferences.model_copy(update={"caption_style": {"standard": "news", "large": "big", "none": "none"}[options.subtitles]})
        if options.subtitles == "none":
            target.quote_caption = "none"
        generate_mode_subtitles(work, timings, plan, target, title=title, v2=v2)
        captions = (work / "subs.ass").read_text(encoding="utf-8")
        # These documents are canonical regenerated bytes; raw input ASS never
        # reaches a media decoder. Events keep the original global source clock.
        return ModeExport(captions, graphic_document, 0.5 if preferences.transitions else 0,
                          0.6 if preferences.transitions else 0, timings[-1].end)
    except (ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
        raise HTTPException(422, "mode subtitle/graphics integrity validation failed") from exc


async def simple_project(root: Path, sources: dict[str, Path], options: ExportOptions, work: Path,
                         *, mode_contract: bool = False, mode_export: list[ModeExport] | None = None,
                         mode_duration_limit: float = MAX_MODE_DURATION) -> Project | SimpleModeProject:
    if "final" not in sources:
        raise HTTPException(422, "completed final is unavailable")
    info = await probe(sources["final"], work)
    duration = info["duration"]
    if options.format == "gif":
        duration = min(duration, 6.0)
    if not mode_contract and duration > MAX_DURATION:
        raise HTTPException(422, "final exceeds 120-second export limit")
    if mode_contract:
        if options.format not in {"mp4", "mp3", "gif"}:
            raise HTTPException(422, "mode export currently supports MP4, MP3 and GIF only")
        if (not math.isfinite(mode_duration_limit) or not 0 < mode_duration_limit <= MAX_MODE_DURATION
                or not math.isfinite(info["duration"]) or not 0 < info["duration"] <= mode_duration_limit):
            raise HTTPException(422, "mode final exceeds configured duration budget (maximum 600 seconds)")
        prepared = prepare_mode_export(root, work, options)
        if abs(prepared.source_duration - info["duration"]) > max(.15, 2 / options.fps):
            raise HTTPException(422, "mode final and confirmed timeline duration disagree")
        if options.format == "mp3":
            audio, voice_duration = assemble_mode_voice(root, work, duration_limit=mode_duration_limit)
            sources["mode_voice"] = audio
            return SimpleModeProject("mp3", voice_duration, mode_duration_limit)
        if mode_export is None:
            raise HTTPException(422, "mode export requires canonical overlays")
        if "video_only" not in sources:
            raise HTTPException(422, "mode clean picture is unavailable")
        mode_export.append(prepared)
        return SimpleModeProject("gif" if options.format == "gif" else "mp4", duration, mode_duration_limit)
    tracks: list[Track] = []
    audio_only = options.format in {"mp3", "wav"}
    text_only = options.format in {"srt", "ass"}
    try:
        if audio_only:
            tracks.append(Track(id="audio", type="audio", clips=[Clip(id="sound", source_id="final", duration=duration)]))
        elif not text_only:
            if options.subtitles == "standard":
                # Keep the finished soundtrack, title, graphics and all burned effects.
                tracks.append(Track(id="video", type="video", clips=[Clip(id="picture", source_id="final", duration=duration, fit="cover")]))
            else:
                if "video_only" not in sources:
                    raise HTTPException(422, "only burned final exists; cannot remove or restyle its subtitles")
                tracks += [Track(id="video", type="video", clips=[Clip(id="picture", source_id="video_only", duration=duration, mute=True, fit="cover")]),
                           Track(id="audio", type="audio", clips=[Clip(id="sound", source_id="final", duration=duration)])]
        if text_only or (not audio_only and options.subtitles == "large"):
            timings = local_json(root, "timings.json", [])
            if not timings:
                raise HTTPException(422, "actual subtitle timings are missing; cannot fabricate subtitles")
            clips = [Clip(id=f"caption_{i}", start=t["start"], duration=t["end"]-t["start"], text=t["text"]) for i, t in enumerate(timings)]
            if any(c.start + c.duration > duration + 0.025 for c in clips):
                raise ValueError("caption exceeds final duration")
            tracks.append(Track(id="captions", type="text", clips=clips))
        if not audio_only and not text_only and options.subtitles != "standard":
            document = local_json(root, "script_structure.json", {})
            title = document.get("title")
            timings = local_json(root, "timings.json", [])
            if title and timings:
                # Reconstruct only plain title text/time, never execute stored raw ASS.
                from .subtitles import TITLE_DURATION_SECONDS
                end = min(duration, TITLE_DURATION_SECONDS, float(timings[-1]["end"]))
                tracks.append(Track(id="title", type="text", clips=[Clip(id="headline", duration=end, text=title,
                                    subtitle=False, font_size=64, color="FFFF00", bold=True, y=-0.65)]))
        return Project(tracks=tracks)
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        raise HTTPException(422, "invalid final subtitle/title metadata for bounded studio timeline") from exc


def assemble_mode_voice(root: Path, work: Path, *, duration_limit: float = MAX_DURATION,
                        v2_budget: Any = None) -> tuple[Path, float]:
    """Assemble exact already-processed PCM units + authored gaps, never BGM.

    The pipeline persists narration.m4a, not a lossless whole-film WAV. Build
    the lossless export input inside the immutable job from its hashed unit
    cache. No second denoise, loudnorm, edge fade, tempo, padding or speech trim.
    """
    from .models import SentenceTiming
    try:
        timings = [SentenceTiming.model_validate(t) for t in local_json(root, "timings.json", [])]
        ceiling = 3600 if v2_budget is not None else MAX_MODE_DURATION
        pcm_limit = v2_budget.pcm_bytes if v2_budget is not None else MAX_BYTES
        if (not math.isfinite(duration_limit) or not 0 < duration_limit <= ceiling
                or not timings or not 0 < timings[-1].end <= duration_limit
            or timings[-1].end * 48000 * 4 + 44 > pcm_limit):
            raise ValueError("voice duration/PCM storage budget exceeded")
        cache = local_json(root, "production_mode.json", {})["audio_cache"]
        destination = work / "narration.wav"
        cursor = 0
        with wave.open(str(destination), "wb") as target:
            target.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
            for index, timing in enumerate(timings):
                if abs(cursor / 48000 - timing.start) > 1 / 48000 or timing.end > duration_limit:
                    raise ValueError("voice clock mismatch")
                source = contained(root, root / timing.audio_path)
                if mode_export_hashes({"unit": source})["unit"] != cache[str(timing.sentence_id)]["sha256"]:
                    raise ValueError("voice unit hash mismatch")
                with wave.open(str(source), "rb") as audio:
                    frames = audio.getnframes()
                    if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) != (2, 2, 48000, "NONE") or frames != round(timing.duration * 48000):
                        raise ValueError("voice PCM clock mismatch")
                    if cursor + frames > round(duration_limit * 48000) or abs((cursor + frames) / 48000 - timing.end) > 1 / 48000:
                        raise ValueError("voice unit exceeds confirmed clock/budget")
                    count = 0
                    while chunk := audio.readframes(65536):
                        count += len(chunk)
                        target.writeframesraw(chunk)
                    if count != frames * 4:
                        raise ValueError("truncated PCM")
                cursor += frames
                if index < len(timings) - 1:
                    gap = round(timing.gap_after * 48000)
                    if not 0 <= gap <= 48000:
                        raise ValueError("invalid authored gap")
                    target.writeframesraw(bytes(gap * 4))
                    cursor += gap
        if not timings or abs(cursor / 48000 - timings[-1].end) > 1 / 48000:
            raise ValueError("empty or inconsistent voice timeline")
        return destination, cursor / 48000
    except (ValueError, KeyError, TypeError, AttributeError, OSError, wave.Error, EOFError) as exc:
        raise HTTPException(422, "mode voice-only PCM integrity validation failed") from exc