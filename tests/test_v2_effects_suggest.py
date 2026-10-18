"""“AI 自动选效果”: whitelisted choices, the keyword fallback and the real endpoint (the model is replaced)."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from backend import effects_suggest as es, main
from backend.config import Settings
from backend.providers.llm import LLMProvider, LLMProviderError


class ChoiceTests(unittest.TestCase):
    def test_keywords_pick_a_fitting_complete_choice(self):
        festive, reason = es.rule_choice("迎春市集开幕\n市民赶集买年货。", "voiceover")
        self.assertEqual((festive["tone"], festive["music_mood"]), ("energetic", "uplifting"))
        self.assertTrue(reason)
        grief, _ = es.rule_choice("事故通报\n事故造成3人遇难。", "voiceover")
        self.assertEqual(grief["tone"], "solemn")
        self.assertFalse(grief["background_music"])
        self.assertFalse(grief["motion_effects"])
        plain, _ = es.rule_choice("社区新闻\n社区开展垃圾分类宣传。", "mixed")
        self.assertEqual(plain["tone"], "neutral")
        for key in (*es.CHOICES, *es.SWITCHES):
            self.assertIn(key, plain)

    def test_only_whitelisted_model_values_survive(self):
        picked, reason = es.parse_model_choice('好的：{"tone":"energetic","pacing":"warp","background_music":"yes",'
                                               '"music_mood":"uplifting","voice":"mine","custom_instructions":"x","reason":"节庆活动"}')
        self.assertEqual(picked, {"tone": "energetic", "music_mood": "uplifting"})
        self.assertEqual(reason, "节庆活动")
        self.assertEqual(es.parse_model_choice("不是 JSON"), ({}, ""))
        self.assertEqual(es.parse_model_choice('{"reason":"' + "长" * 40 + '"}')[1], "")

    def test_mode_rules_drop_options_the_mode_does_not_have(self):
        original = es.finalize(es.rule_choice("标题\n原话。", "original")[0], "original")
        self.assertNotIn("pacing", original)
        voice = es.finalize(es.rule_choice("标题\n正文。", "voiceover")[0], "voiceover")
        self.assertNotIn("lower_third", voice); self.assertNotIn("jump_cut_cover", voice)
        self.assertEqual(es.finalize({"caption_style": "none"}, "mixed")["caption_style"], "news")

    def test_model_failure_or_junk_falls_back_to_keywords(self):
        for provider in (AsyncMock(complete_text=AsyncMock(side_effect=LLMProviderError("down"))),
                         AsyncMock(complete_text=AsyncMock(return_value="无法回答"))):
            result = asyncio.run(es.suggest_effects("迎春市集\n热闹开市。", "voiceover", provider))
            self.assertEqual(result["source"], "rules")
            self.assertEqual(result["preferences"]["tone"], "energetic")


class EndpointTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="v2-effects-suggest-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        settings = Settings(_env_file=None, app_env="test", data_dir=root / "tasks", asr_cache_dir=root / "cache", min_free_disk_gb=0,
                            frontend_origins="http://localhost", enforce_origin_check=True)
        patcher = patch.object(main, "settings", settings)
        patcher.start(); self.addCleanup(patcher.stop)
        main._assist_calls.clear(); self.addCleanup(main._assist_calls.clear)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, client=("198.51.100.24", 12345)), base_url="http://localhost")
        self.addAsyncCleanup(self.client.aclose)
        self.valid = patch.object(LLMProvider, "validate_configuration", lambda self: None)

    async def post(self, body, origin="http://localhost"):
        return await self.client.post("/api/script/effects", json=body, headers={"Origin": origin})

    async def test_model_choice_is_applied_through_the_whitelist(self):
        answer = '{"tone":"solemn","pacing":"slow","background_music":false,"music_mood":"solemn","caption_style":"big","reason":"悼念活动"}'
        with self.valid, patch.object(LLMProvider, "complete_text", AsyncMock(return_value=answer)) as model:
            response = await self.post({"mode": "voiceover", "script": "清明祭扫\n市民前往烈士陵园悼念。"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["source"], "ai")
        self.assertEqual(body["reason"], "悼念活动")
        self.assertEqual((body["preferences"]["pacing"], body["preferences"]["caption_style"]), ("slow", "big"))
        self.assertFalse(body["preferences"]["background_music"])
        model.assert_awaited_once()

    async def test_without_a_configured_model_it_still_answers_from_keywords(self):
        response = await self.post({"mode": "mixed", "script": "迎春市集开幕\n市民赶集买年货。"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["source"], "rules")
        self.assertEqual(response.json()["preferences"]["tone"], "energetic")

    async def test_bad_bodies_and_foreign_origins_are_rejected(self):
        for body in ({"mode": "voiceover", "script": ""}, {"mode": "voiceover", "script": "x" * 8001},
                     {"mode": "other", "script": "标题\n正文"}, {"mode": "voiceover", "script": "标题", "extra": 1}):
            self.assertEqual((await self.post(body)).status_code, 422, body)
        self.assertEqual((await self.post({"mode": "voiceover", "script": "标题\n正文"}, origin="https://evil.example")).status_code, 403)


if __name__ == "__main__":
    unittest.main()
