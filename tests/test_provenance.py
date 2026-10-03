import json
import tempfile
import unittest
from pathlib import Path

from backend.config import Settings
from backend.models import EditingPreferences
from backend.provenance import PIPELINE_IMPLEMENTATION_VERSION, write_pipeline_manifest


class PipelineProvenanceTest(unittest.TestCase):
    def test_manifest_covers_output_affecting_sources_and_preferences(self) -> None:
        settings = Settings(
            kimi_api_key="do-not-persist",
            volcengine_vision_api_keys="also-secret",
        )
        preferences = EditingPreferences(
            pacing="fast",
            background_music=True,
            news_graphics=True,
            custom_instructions="多用全景交代环境",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = write_pipeline_manifest(
                Path(directory),
                settings,
                preferences=preferences.model_dump(mode="json"),
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            serialized = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(payload["implementation_version"], PIPELINE_IMPLEMENTATION_VERSION)
        self.assertEqual(payload["editing_preferences"]["pacing"], "fast")
        self.assertTrue(payload["editing_preferences"]["background_music"])
        for source in (
            "provenance.py",
            "rendering.py",
            "readiness.py",
            "remix.py",
            "music.py",
            "graphics.py",
            "providers/generative.py",
        ):
            self.assertRegex(payload["source_sha256"][source], r"^[0-9a-f]{64}$")
        self.assertNotIn("do-not-persist", serialized)
        self.assertNotIn("also-secret", serialized)


if __name__ == "__main__":
    unittest.main()