"""The sample's walkthrough: script, settings and source clips, bound into the same hash registry.

Synthetic bytes only (an ftyp header / JPEG marker is all the preparer checks); no real media,
no network, no task fallback.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy import prepare_sample_bundle as bundle
from tests.workspace_fixture import sha256

MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x01" * 64


class SampleWalkthroughTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="gm-sample-walkthrough-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.source, self.output = self.root / "input", self.root / "staging"
        self.identity = "b" * 32

    def write(self, name: str, data: bytes | object) -> None:
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode())

    def build(self, *, walkthrough: dict | None = None, extra: dict[str, bytes] | None = None) -> None:
        rows = [{"sentence_id": sid, "sentence": f"第{sid}句", "shot_id": sid, "thumb_url": None, "description": "画面",
                 "duration": 1.0, "confidence": 0.8, "is_fallback": False, "kind": "narration", "audio_kind": "tts",
                 "visual_beats": [{"beat_id": 0, "text": f"第{sid}句", "shot_id": sid, "thumb_url": "",
                                   "description": "画面", "confidence": 0.8}]} for sid in range(2)]
        self.write("report.json", {"task_id": self.identity, "mode": "voiceover", "rows": rows})
        self.write("timings.json", [{"sentence_id": sid, "start": float(sid), "end": float(sid + 1)} for sid in range(2)])
        self.write("final.mp4", MP4)
        self.write("thumbs/1.jpg", JPG)
        self.write("sources/01.mp4", MP4 + b"1")
        self.write("sources/01.jpg", JPG)
        self.write("walkthrough.json", walkthrough if walkthrough is not None else {
            "schema_version": 1, "script": "标题\n第0句\n第1句", "mode": "voiceover",
            "settings": [{"label": "声音", "value": "AI 配音"}],
            "sources": [{"file": "sources/01.mp4", "thumb": "sources/01.jpg", "name": "01-市集.mp4",
                         "duration": 8.0, "used_by": [0, 1]}]})
        for name, data in (extra or {}).items():
            self.write(name, data)
        names = [p.relative_to(self.source).as_posix() for p in self.source.rglob("*") if p.is_file()]
        files = {name: {"sha256": sha256(self.source / name), "bytes": (self.source / name).stat().st_size} for name in names}
        self.write(bundle.DESCRIPTOR, {"schema_version": 1, "task_id": self.identity, "title": "范例", "files": files})

    def served(self, **kwargs):
        from backend import v2_editing
        with patch.object(v2_editing, "SAMPLE_ASSETS", self.output):
            return v2_editing.packaged_sample(**kwargs)

    def test_walkthrough_sources_and_sentence_pictures_are_served_only_from_the_registry(self):
        self.build()
        summary = bundle.prepare(self.source, self.output, rights_reviewed=True)
        self.assertEqual(summary["file_count"], 7)
        registry = json.loads((self.output / "registry.json").read_text())
        self.assertIn("sources/01.mp4", registry["default"]["files"])
        result = self.served()
        self.assertEqual(result["walkthrough"]["mode"], "voiceover")
        self.assertEqual(result["walkthrough"]["sources"], [{
            "index": 1, "name": "01-市集.mp4", "duration": 8.0, "used_by": [0, 1],
            "video_url": "/api/samples/default/sources/01.mp4", "thumb_url": "/api/samples/default/sources/01.jpg"}])
        thumbs = [row["thumb_url"] for row in result["report"]["rows"]]
        self.assertEqual(thumbs, [None, "/api/samples/default/thumbs/1.jpg"], "only registered pictures are linked")
        media = self.served(media="sources/01.mp4")
        self.assertEqual((str(media.path), media.media_type), (str(self.output / "default/sources/01.mp4"), "video/mp4"))
        for name in ("sources/02.mp4", "thumbs/0.jpg", "report.json", "../registry.json"):
            self.assertEqual(self.served(media=name).status_code, 404, name)
        # Tampering with any registered file withdraws the whole sample.
        (self.output / "default/sources/01.mp4").write_bytes(MP4 + b"tampered")
        self.assertEqual(self.served().status_code, 503)
        self.assertEqual(self.served(media="sources/01.mp4").status_code, 503)

    def test_preparer_rejects_unreferenced_sources_and_unknown_sentences(self):
        self.build(extra={"sources/02.mp4": MP4, "sources/02.jpg": JPG})
        with self.assertRaisesRegex(bundle.BundleError, "unreferenced_source"):
            bundle.prepare(self.source, self.output, rights_reviewed=True)
        self.assertFalse(self.output.exists())

        for path in self.source.rglob("*"):
            if path.is_file():
                path.unlink()
        self.build(walkthrough={"schema_version": 1, "script": "标题", "mode": "voiceover", "sources": [
            {"file": "sources/01.mp4", "thumb": "sources/01.jpg", "name": "a.mp4", "duration": 1.0, "used_by": [7]}]})
        with self.assertRaisesRegex(bundle.BundleError, "walkthrough_used_by"):
            bundle.prepare(self.source, self.output, rights_reviewed=True)

    def test_the_media_route_is_registered(self):
        from backend import main
        self.assertIn("/api/samples/default/{kind}/{name}", {getattr(route, "path", "") for route in main.app.routes})


if __name__ == "__main__":
    unittest.main()
