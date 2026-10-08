"""Offline: turn ONE finished task plus its original clips into input for prepare_sample_bundle.py.

Usage (operator-run, never by the server):
  python deploy/export_sample_input.py --task-dir data/tasks/<id> --media-dir examples/迎春市集范例/素材 \
      --script examples/迎春市集范例/稿件.txt --output /tmp/sample-input
  python deploy/prepare_sample_bundle.py --source /tmp/sample-input --output /tmp/sample-bundle --rights-reviewed
  then copy /tmp/sample-bundle/* into backend/assets/samples/.

The public report keeps only the minimal profile the preparer accepts (no provider, quality or
source metadata). The walkthrough records what the user did: the script, the mode and settings,
and the uploaded clips (re-numbered sources/NN.mp4 with a still frame each). Needs ffmpeg.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

MODE_SETTINGS = {
    "pacing": ("节奏", {"slow": "舒缓", "normal": "正常", "fast": "紧凑"}),
    "caption_style": ("字幕", {"news": "新闻样式", "plain": "简洁", "bold": "醒目"}),
    "background_music": ("背景音乐", {True: "自动配乐", False: "不加"}),
    "lower_third": ("标题条", {True: "显示", False: "不显示"}),
    "transitions": ("转场", {True: "开启", False: "关闭"}),
}
VOICE = {"ai": "AI 配音", "self": "本人录音"}


def write_json(path: Path, value: Any) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return entry(path)


def entry(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def probe_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         check=True, capture_output=True, text=True).stdout
    return round(float(out.strip()), 2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task-dir", required=True, type=Path)
    parser.add_argument("--media-dir", required=True, type=Path)
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    task, out = args.task_dir.resolve(), args.output.resolve()
    if out.exists():
        raise SystemExit("output must not exist")
    out.mkdir(parents=True)
    state = json.loads((task / "task_state.json").read_text(encoding="utf-8"))
    report = json.loads((task / "report.json").read_text(encoding="utf-8"))
    timings = json.loads((task / "timings.json").read_text(encoding="utf-8"))
    shots = {shot["shot_id"]: shot for shot in json.loads((task / "shots_annotated.json").read_text(encoding="utf-8"))}
    task_id = state["task_id"]
    script_lines = [line.strip() for line in args.script.read_text(encoding="utf-8").splitlines() if line.strip()]
    files: dict[str, Any] = {}

    rows = []
    for row in report["rows"]:
        rows.append({
            "sentence_id": row["sentence_id"], "sentence": row["sentence"], "kind": row.get("kind", "narration"),
            "shot_id": row["shot_id"], "thumb_url": None, "description": row.get("description") or "",
            "duration": row["duration"], "confidence": row["confidence"], "is_fallback": row["is_fallback"],
            "audio_kind": row["audio_kind"],
            "visual_beats": [{"beat_id": beat["beat_id"], "text": beat["text"], "shot_id": beat["shot_id"], "thumb_url": "",
                              "description": beat.get("description") or "", "confidence": beat["confidence"]}
                             for beat in row.get("visual_beats", [])],
        })
        thumb = task / "thumbs" / f"shot_{row['shot_id']}.jpg"
        if thumb.is_file():
            target = out / "thumbs" / f"{row['sentence_id']}.jpg"
            target.parent.mkdir(exist_ok=True)
            shutil.copyfile(thumb, target)
            files[f"thumbs/{row['sentence_id']}.jpg"] = entry(target)
    files["report.json"] = write_json(out / "report.json", {"task_id": task_id, "mode": state["mode"], "rows": rows})
    files["timings.json"] = write_json(out / "timings.json", [
        {"sentence_id": t["sentence_id"], "start": round(t["start"], 3), "end": round(t["end"], 3)} for t in timings])
    shutil.copyfile(task / "final.mp4", out / "final.mp4")
    files["final.mp4"] = entry(out / "final.mp4")

    used: dict[str, set[int]] = {}
    for row in report["rows"]:
        shot = shots.get(row["shot_id"])
        if shot is not None:
            used.setdefault(shot["source_name"], set()).add(row["sentence_id"])
    sources = []
    for index, clip in enumerate(sorted(args.media_dir.glob("*.mp4")), 1):
        name = f"sources/{index:02d}"
        (out / "sources").mkdir(exist_ok=True)
        shutil.copyfile(clip, out / f"{name}.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", str(clip), "-frames:v", "1",
                        "-vf", "scale=480:-2", "-q:v", "4", str(out / f"{name}.jpg")], check=True)
        files[f"{name}.mp4"], files[f"{name}.jpg"] = entry(out / f"{name}.mp4"), entry(out / f"{name}.jpg")
        sources.append({"file": f"{name}.mp4", "thumb": f"{name}.jpg", "name": clip.name,
                        "duration": probe_duration(clip), "used_by": sorted(used.get(clip.name, set()))})
    preferences = state.get("preferences") or {}
    settings = [{"label": "声音", "value": VOICE.get(preferences.get("voice", "ai"), "AI 配音")}]
    for key, (label, values) in MODE_SETTINGS.items():
        if preferences.get(key) in values:
            settings.append({"label": label, "value": values[preferences[key]]})
    files["walkthrough.json"] = write_json(out / "walkthrough.json", {
        "schema_version": 1, "script": "\n".join(script_lines), "mode": state["mode"],
        "settings": settings, "sources": sources})
    write_json(out / "reviewed-sample.json", {"schema_version": 1, "task_id": task_id,
                                              "title": script_lines[0][:80], "files": files})
    print(json.dumps({"status": "exported", "files": len(files)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
