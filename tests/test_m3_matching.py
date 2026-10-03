import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from backend.config import Settings
from backend.matching import (
    MatchingError,
    apply_beat_decisions,
    apply_fallbacks,
    build_match_plan,
    cosine_similarity,
    extract_visual_beats,
    parse_script,
    parse_segmented_script,
    split_script,
)
from backend.models import (
    AnnotatedShot,
    EditingPreferences,
    MatchCandidate,
    RerankDecision,
    Sentence,
    ShotTranscriptSpan,
    UploadedAsset,
    VisualEntityVerification,
    VisionQuality,
    VisualBeat,
)
from backend.providers.embedding import (
    VOLCENGINE_CORPUS_INSTRUCTIONS,
    VOLCENGINE_QUERY_INSTRUCTIONS,
    VOLCENGINE_STS_INSTRUCTIONS,
    VOLCENGINE_VIDEO_CORPUS_INSTRUCTIONS,
    EmbeddingProvider,
    VolcengineMultimodalEmbeddingProvider,
)
from backend.pipeline import PipelineTask, _run_semantic_matching, _run_sentence_split  # pyright: ignore[reportPrivateUsage]
from backend.providers.llm import (
    LLMProvider,
    LLMProviderError,
    PRONUNCIATION_SYSTEM_PROMPT,
    RERANK_SYSTEM_PROMPT,
    SCRIPT_SEGMENTATION_MAX_TOKENS,
    SCRIPT_SEGMENTATION_MODEL,
    SCRIPT_SEGMENTATION_SYSTEM_PROMPT,
)
from backend.script_segmentation import (
    SCRIPT_SEGMENTATION_DELIMITER,
    ScriptSegmentationValidationError,
    reconcile_segmented_script,
    validate_segmented_script,
)


class SentenceSplittingTest(unittest.TestCase):
    def test_screen_units_share_natural_sentence_retrieval_group(self) -> None:
        script = "新闻标题\r\n\r\n马年新春将至，位于南宁信息港广场举办迎春市集。\r\n活动持续到1月31日。"
        segmented = "新闻标题\r\n\r\n马年新春将至， / 位于南宁信息港广场 / 举办迎春市集。\r\n活动持续到1月31日。"

        document = parse_segmented_script(script, segmented)

        self.assertEqual([sentence.sentence_id for sentence in document.sentences], [0, 1, 2])
        self.assertEqual([sentence.visual_group_id for sentence in document.sentences], [0, 0, 1])
        self.assertEqual(
            {sentence.retrieval_context for sentence in document.sentences[:2]},
            {"马年新春将至，位于南宁信息港广场举办迎春市集。"},
        )
        self.assertEqual(document.sentences[2].visual_beats[0].intent_type, "date")

    def test_coalesces_break_without_punctuation_before_tts_and_shot_matching(self) -> None:
        script = "新闻标题\n\n为市民打造了一个集购物、体验、娱乐于一体的迎春平台。"
        model_output = "新闻标题\n\n为市民打造了 / 一个集购物、体验、娱乐于一体的迎春平台。"

        reconciled = reconcile_segmented_script(script, model_output)
        document = parse_segmented_script(script, model_output)

        self.assertEqual(reconciled, script)
        self.assertEqual(
            [sentence.text for sentence in document.sentences],
            ["为市民打造了一个集购物、体验、娱乐于一体的迎春平台。"],
        )

    def test_detects_title_preserves_quoted_name_and_extracts_visual_beats(self) -> None:
        script = (
            "迎春市集开张备年货\n\n"
            "马年新春将至，位于南宁信息港广场举办了“金马贺岁，高新同驰”迎春市集，"
            "吸引众多市民前来赶集购年货。\n"
            "灌阳油茶、现做寿司等特色小吃引来众多品尝者。"
        )

        document = parse_script(script)
        sentences = document.sentences

        self.assertEqual(document.title, "迎春市集开张备年货")
        self.assertNotIn(document.title, [sentence.text for sentence in sentences])
        self.assertIn("“金马贺岁，高新同驰”", sentences[0].text)
        self.assertEqual([beat.text for beat in sentences[-1].visual_beats], ["灌阳油茶", "现做寿司"])
        self.assertEqual(sentences[-1].visual_beats[0].entities, ["灌阳油茶", "油茶"])
        self.assertTrue(all(beat.requires_entity_coverage for beat in sentences[-1].visual_beats))

    def test_builds_screen_units_from_validated_model_delimiters(self) -> None:
        script = (
            "迎春市集开张备年货\n\n"
            "马年新春将至，位于南宁市西乡塘区鲁班路95号的南宁信息港广场1月30日举办了"
            '"金马贺岁，高新同驰"迎春市集，吸引众多市民前来赶集购年货、品年味。\n'
            "活动以市集为纽带，融合高新科技与传统节庆，为市民打造了一个集购物、体验、娱乐于一体的迎春平台。"
        )
        segmented = (
            "迎春市集开张备年货\n\n"
            "马年新春将至， / 位于南宁市西乡塘区鲁班路95号的南宁信息港广场 / 1月30日举办了"
            '"金马贺岁，高新同驰"迎春市集， / 吸引众多市民前来赶集购年货、品年味。\n'
            "活动以市集为纽带， / 融合高新科技与传统节庆， / 为市民打造了 / "
            "一个集购物、体验、娱乐于一体的迎春平台。"
        )

        document = parse_segmented_script(script, segmented)

        self.assertEqual(document.title, "迎春市集开张备年货")
        self.assertEqual(
            [sentence.text for sentence in document.sentences],
            [
                "马年新春将至，",
                '位于南宁市西乡塘区鲁班路95号的南宁信息港广场1月30日举办了"金马贺岁，高新同驰"迎春市集，',
                "吸引众多市民前来赶集购年货、品年味。",
                "活动以市集为纽带，",
                "融合高新科技与传统节庆，",
                "为市民打造了一个集购物、体验、娱乐于一体的迎春平台。",
            ],
        )
        self.assertEqual(
            [sentence.paragraph_index for sentence in document.sentences],
            [0, 0, 0, 1, 1, 1],
        )

    def test_preserves_an_existing_literal_segmentation_delimiter(self) -> None:
        script = "栏目 / 新闻\n\n本市今天，发布政策。"
        segmented = "栏目 / 新闻\n\n本市今天， / 发布政策。"

        offsets = validate_segmented_script(script, segmented)
        document = parse_segmented_script(script, segmented)

        self.assertEqual(len(offsets), 1)
        self.assertEqual(document.title, "栏目 / 新闻")
        self.assertEqual([sentence.text for sentence in document.sentences], ["本市今天，", "发布政策。"])

    def test_reconciles_crlf_and_a_delimiter_replacing_a_title_space(self) -> None:
        script = (
            "数十家企业汇聚南宁信息港 迎春市集开张备年货\r\n\r\n"
            "本市今天发布政策。有关部门将完善配套服务。"
        )
        model_output = (
            "数十家企业汇聚南宁信息港 / 迎春市集开张备年货\n\n"
            "本市今天发布政策。 / 有关部门将完善配套服务。"
        )

        reconciled = reconcile_segmented_script(script, model_output)
        document = parse_segmented_script(script, reconciled)

        self.assertEqual(reconciled.replace(SCRIPT_SEGMENTATION_DELIMITER, ""), script)
        self.assertEqual(reconciled.count("\r\n"), 2)
        self.assertIn("南宁信息港 /  迎春市集", reconciled)
        self.assertEqual(document.title, "数十家企业汇聚南宁信息港 迎春市集开张备年货")
        self.assertEqual(
            [sentence.text for sentence in document.sentences],
            ["本市今天发布政策。", "有关部门将完善配套服务。"],
        )

    def test_reconciliation_still_rejects_a_real_model_rewrite(self) -> None:
        script = "新闻标题 副标题\r\n\r\n本市今天发布政策。"
        model_output = "新闻标题 / 副标题\n\n本市今日发布政策。"

        with self.assertRaisesRegex(ScriptSegmentationValidationError, "增删改"):
            reconcile_segmented_script(script, model_output)

    def test_rejects_any_model_rewrite(self) -> None:
        with self.assertRaisesRegex(ScriptSegmentationValidationError, "增删改"):
            validate_segmented_script("本市今天发布政策。", "本市今日 / 发布政策。")


class ProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_kimi_music_mood_uses_reasoning_aware_completion_budget(self) -> None:
        request_payload: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            request_payload.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": "uplifting"},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = LLMProvider(
                base_url="https://api.moonshot.cn/v1",
                api_key="test-key",
                model="kimi-k3",
                client=client,
                reasoning_effort="low",
                supports_temperature=False,
            )
            mood = await provider.classify_music_mood("迎春市集年货丰富。")

        self.assertEqual(mood, "uplifting")
        self.assertEqual(request_payload["max_completion_tokens"], 128)
        self.assertNotIn("max_tokens", request_payload)

    async def test_kimi_music_mood_reports_length_truncation(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": ""},
                            "finish_reason": "length",
                        }
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = LLMProvider(
                base_url="https://api.moonshot.cn/v1",
                api_key="test-key",
                model="kimi-k3",
                client=client,
                reasoning_effort="low",
                supports_temperature=False,
            )
            with self.assertRaisesRegex(LLMProviderError, "长度限制截断"):
                await provider.classify_music_mood("迎春市集年货丰富。")

    async def test_embedding_is_batched_and_restores_index_order(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            payload = json.loads(request.content)
            self.assertEqual(payload["input"], ["句子一", "句子二"])
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "embedding": [0.0, 1.0]},
                        {"index": 0, "embedding": [1.0, 0.0]},
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = EmbeddingProvider(
                base_url="https://embed.example/v1",
                api_key="test-key",
                model="embed-test",
                client=client,
                backoff_base=0,
            )
            vectors = await provider.embed(["句子一", "句子二"])

        self.assertEqual(calls, 1)
        self.assertEqual(vectors, [[1.0, 0.0], [0.0, 1.0]])

    async def test_volcengine_multimodal_embedding_preserves_input_order(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            payload = json.loads(request.content)
            text = payload["input"][0]["text"]
            value = 1.0 if text == "句子一" else 2.0
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": {"object": "embedding", "embedding": [value] * 1024},
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = VolcengineMultimodalEmbeddingProvider(
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_keys="test-key",
                model="doubao-embedding-vision-251215",
                dimensions=1024,
                client=client,
                backoff_base=0,
            )
            vectors = await provider.embed(["句子一", "句子二"])

        self.assertEqual(len(requests), 2)
        self.assertTrue(all(request.url.path == "/api/v3/embeddings/multimodal" for request in requests))
        self.assertTrue(all(request.headers["Authorization"] == "Bearer test-key" for request in requests))
        first_payload = json.loads(requests[0].content)
        self.assertEqual(first_payload["model"], "doubao-embedding-vision-251215")
        self.assertEqual(first_payload["encoding_format"], "float")
        self.assertEqual(first_payload["dimensions"], 1024)
        self.assertEqual(first_payload["instructions"], VOLCENGINE_STS_INSTRUCTIONS)
        self.assertEqual(first_payload["input"], [{"type": "text", "text": "句子一"}])
        self.assertEqual(vectors[0], [1.0] * 1024)
        self.assertEqual(vectors[1], [2.0] * 1024)

    async def test_volcengine_multimodal_embedding_rotates_api_keys(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(401, json={"error": {"message": "invalid api key"}})
            return httpx.Response(
                200,
                json={"data": {"object": "embedding", "embedding": [0.5] * 1024}},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = VolcengineMultimodalEmbeddingProvider(
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_keys="first-key, second-key",
                model="doubao-embedding-vision-251215",
                dimensions=1024,
                client=client,
                max_retries=1,
                backoff_base=0,
            )
            vectors = await provider.embed(["新闻句子"])

        self.assertEqual(len(vectors[0]), 1024)
        self.assertEqual(requests[0].headers["Authorization"], "Bearer first-key")
        self.assertEqual(requests[1].headers["Authorization"], "Bearer second-key")

    async def test_volcengine_embedding_separates_query_and_corpus_instructions(self) -> None:
        instructions: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            instructions.append(payload["instructions"])
            return httpx.Response(
                200,
                json={"data": {"object": "embedding", "embedding": [0.5] * 1024}},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = VolcengineMultimodalEmbeddingProvider(
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_keys="test-key",
                model="doubao-embedding-vision-251215",
                dimensions=1024,
                client=client,
            )
            await provider.embed_queries(["灌阳油茶"])
            await provider.embed_corpus(["现场语音识别：正在制作油茶"])

        self.assertEqual(instructions, [VOLCENGINE_QUERY_INSTRUCTIONS, VOLCENGINE_CORPUS_INSTRUCTIONS])

    async def test_volcengine_embedding_accepts_fused_video_and_metadata(self) -> None:
        captured: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"data": {"object": "embedding", "embedding": [0.25] * 1024}},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = VolcengineMultimodalEmbeddingProvider(
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_keys="test-key",
                model="doubao-embedding-vision-251215",
                dimensions=1024,
                video_fps=0.5,
                video_max_tokens=10240,
                client=client,
            )
            with tempfile.TemporaryDirectory() as directory:
                video_path = Path(directory) / "shot.mp4"
                video_path.write_bytes(b"small-video")
                vectors = await provider.embed_video_corpus([(video_path, "现场语音识别：正在制作油茶")])

        self.assertIsNotNone(vectors[0])
        self.assertEqual(captured["instructions"], VOLCENGINE_VIDEO_CORPUS_INSTRUCTIONS)
        inputs = captured["input"]
        assert isinstance(inputs, list)
        self.assertEqual(inputs[0]["type"], "video_url")
        self.assertTrue(inputs[0]["video_url"]["url"].startswith("data:video/mp4;base64,"))
        self.assertEqual(inputs[0]["video_url"]["fps"], 0.5)
        self.assertEqual(inputs[0]["video_url"]["max_video_tokens"], 10240)

    async def test_llm_segments_script_with_required_prompt_and_fixed_model(self) -> None:
        script = "活动以市集为纽带，融合高新科技与传统节庆，为市民打造了迎春平台。"
        segmented = "活动以市集为纽带， / 融合高新科技与传统节庆， / 为市民打造了迎春平台。"
        request_payload: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            request_payload.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": segmented}}]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = LLMProvider(
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_key="test-key",
                model=SCRIPT_SEGMENTATION_MODEL,
                client=client,
                reasoning_effort="low",
                supports_temperature=False,
            )
            result = await provider.segment_script(script)

        self.assertEqual(result, segmented)
        self.assertEqual(request_payload["model"], SCRIPT_SEGMENTATION_MODEL)
        messages = request_payload["messages"]
        assert isinstance(messages, list)
        self.assertEqual(messages[0], {"role": "system", "content": SCRIPT_SEGMENTATION_SYSTEM_PROMPT})
        self.assertEqual(messages[1], {"role": "user", "content": script})
        self.assertNotIn("temperature", request_payload)
        self.assertEqual(request_payload["max_tokens"], SCRIPT_SEGMENTATION_MAX_TOKENS)
        self.assertEqual(request_payload["reasoning_effort"], "low")
        self.assertNotIn("thinking", request_payload)

    async def test_llm_retries_when_segmentation_rewrites_source(self) -> None:
        script = "本市今天发布政策。有关部门将完善配套服务。"
        segmented = "本市今天发布政策。 / 有关部门将完善配套服务。"
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            content = "本市今日发布政策。 / 有关部门将完善配套服务。" if calls == 1 else segmented
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": content}}]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = LLMProvider(
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_key="test-key",
                model=SCRIPT_SEGMENTATION_MODEL,
                client=client,
                max_retries=1,
                backoff_base=0,
                reasoning_effort="low",
                supports_temperature=False,
            )
            result = await provider.segment_script(script)

        self.assertEqual(result, segmented)
        self.assertEqual(calls, 2)

    async def test_llm_normalizes_model_input_and_rebuilds_original_script(self) -> None:
        script = (
            "数十家企业汇聚南宁信息港 迎春市集开张备年货\r\n\r\n"
            "本市今天发布政策。有关部门将完善配套服务。"
        )
        model_output = (
            "数十家企业汇聚南宁信息港 / 迎春市集开张备年货\n\n"
            "本市今天发布政策。 / 有关部门将完善配套服务。"
        )
        request_payload: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            request_payload.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": model_output}}]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = LLMProvider(
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_key="test-key",
                model=SCRIPT_SEGMENTATION_MODEL,
                client=client,
                max_retries=0,
                reasoning_effort="low",
                supports_temperature=False,
            )
            result = await provider.segment_script(script)

        messages = request_payload["messages"]
        assert isinstance(messages, list)
        self.assertEqual(messages[1], {"role": "user", "content": script.replace("\r\n", "\n")})
        self.assertEqual(result.replace(SCRIPT_SEGMENTATION_DELIMITER, ""), script)
        self.assertIn("南宁信息港 /  迎春市集", result)

    async def test_script_segmentation_always_uses_kimi_configuration(self) -> None:
        settings = Settings(
            kimi_api_key="kimi-test-key",
            kimi_model="kimi-k3",
            volcengine_llm_base_url="https://ark.cn-beijing.volces.com/api/v3",
            volcengine_llm_api_keys="first-key,second-key",
            volcengine_llm_model="another-rerank-model",
        )
        provider = LLMProvider.for_script_segmentation(settings)
        try:
            self.assertEqual(provider.model, SCRIPT_SEGMENTATION_MODEL)
            self.assertEqual(provider.api_key, "kimi-test-key")
            self.assertFalse(provider.disable_thinking)
            self.assertFalse(provider.supports_temperature)
            self.assertEqual(provider.reasoning_effort, "low")
        finally:
            await provider.aclose()

    async def test_kimi_provider_uses_reasoning_effort_without_fixed_temperature(self) -> None:
        request_payload: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            request_payload.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    [{
                                        "sentence_id": 0,
                                        "beat_id": 0,
                                        "shot_id": 1,
                                        "confidence": 0.9,
                                        "alternates": [],
                                    }]
                                )
                            }
                        }
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = LLMProvider(
                base_url="https://api.moonshot.cn/v1",
                api_key="test-key",
                model="kimi-k3",
                reasoning_effort="low",
                supports_temperature=False,
                client=client,
                max_retries=0,
            )
            decisions = await provider.rerank(
                [{"sentence_id": 0, "beat_id": 0, "candidates": [{"shot_id": 1}]}]
            )

        self.assertEqual(decisions[0].shot_id, 1)
        self.assertEqual(request_payload["reasoning_effort"], "low")
        self.assertNotIn("temperature", request_payload)
        self.assertNotIn("thinking", request_payload)

    async def test_stage_five_persists_model_segmented_script(self) -> None:
        script = "新闻标题\r\n\r\n本市今天发布政策。有关部门将完善配套服务。"
        segmented = "新闻标题\r\n\r\n本市今天发布政策。 / 有关部门将完善配套服务。"

        class FakeSegmentationProvider:
            received_script = ""

            async def __aenter__(self) -> "FakeSegmentationProvider":
                return self

            async def __aexit__(self, *args: object) -> None:
                return None

            async def segment_script(self, value: str) -> str:
                self.received_script = value
                return segmented

        class FakeReporter:
            def start_stage(self, record: PipelineTask, stage_number: int, message: str) -> None:
                return None

            def update_stage(
                self,
                record: PipelineTask,
                stage_number: int,
                fraction: float,
                message: str,
            ) -> None:
                return None

            def complete_stage(self, record: PipelineTask, stage_number: int, message: str) -> None:
                return None

        class FakeRecord:
            def __init__(self, task_dir: Path, value: str) -> None:
                self.task_id = "segmentation-test"
                self.task_dir = task_dir
                self.script = value
                self.uploads: list[UploadedAsset] = []

        provider = FakeSegmentationProvider()
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            record = FakeRecord(task_dir, script)
            with patch(
                "backend.pipeline.LLMProvider.for_script_segmentation",
                return_value=provider,
            ):
                sentences = await _run_sentence_split(record, FakeReporter(), Settings())

            self.assertEqual(
                (task_dir / "script_segmented.txt").read_bytes(),
                segmented.encode("utf-8"),
            )
            persisted = json.loads((task_dir / "sentences.json").read_text(encoding="utf-8"))

        self.assertEqual(provider.received_script, script)
        self.assertEqual([sentence.text for sentence in sentences], ["本市今天发布政策。", "有关部门将完善配套服务。"])
        self.assertEqual([item["text"] for item in persisted], ["本市今天发布政策。", "有关部门将完善配套服务。"])

    async def test_llm_reranks_all_sentences_in_one_fenced_response(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            payload = json.loads(request.content)
            self.assertEqual(payload["messages"][0]["content"], RERANK_SYSTEM_PROMPT)
            user_payload = json.loads(payload["messages"][1]["content"])
            self.assertEqual(len(user_payload["sentences"]), 2)
            result = [
                {"sentence_id": 0, "shot_id": 0, "confidence": 0.9, "alternates": [1]},
                {"sentence_id": 1, "shot_id": 1, "confidence": 0.8, "alternates": [0]},
            ]
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": f"```json\n{json.dumps(result)}\n```"}}]},
            )

        items = [
            {
                "sentence_id": index,
                "text": f"句子{index}",
                "candidates": [
                    {"shot_id": 0, "description": "镜头零"},
                    {"shot_id": 1, "description": "镜头一"},
                ],
            }
            for index in range(2)
        ]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = LLMProvider(
                base_url="https://llm.example/v1",
                api_key="test-key",
                model="llm-test",
                client=client,
                backoff_base=0,
            )
            decisions = await provider.rerank(items)

        self.assertEqual(calls, 1)
        self.assertEqual([decision.shot_id for decision in decisions], [0, 1])

    async def test_llm_uses_full_script_to_disambiguate_number_readings(self) -> None:
        full_script = "新设备型号为M20。首批共有20台设备投入使用。"
        sentences = [
            Sentence(sentence_id=0, text="新设备型号为M20。"),
            Sentence(sentence_id=1, text="首批共有20台设备投入使用。"),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            self.assertEqual(payload["messages"][0]["content"], PRONUNCIATION_SYSTEM_PROMPT)
            user_payload = json.loads(payload["messages"][1]["content"])
            self.assertEqual(user_payload["full_script"], full_script)
            self.assertEqual(
                [item["numbers"] for item in user_payload["target_sentences"]],
                [
                    [{"index": 0, "source": "20"}],
                    [{"index": 0, "source": "20"}],
                ],
            )
            result = [
                {"sentence_id": 0, "readings": [{"source": "20", "spoken": "二零"}]},
                {"sentence_id": 1, "readings": [{"source": "20", "spoken": "二十"}]},
            ]
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = LLMProvider(
                base_url="https://llm.example/v1",
                api_key="test-key",
                model="llm-test",
                client=client,
                backoff_base=0,
            )
            decisions = await provider.plan_pronunciations(full_script, sentences)

        self.assertEqual(decisions[0].readings[0].spoken, "二零")
        self.assertEqual(decisions[1].readings[0].spoken, "二十")

    async def test_llm_skips_pronunciation_request_without_arabic_numbers(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = LLMProvider(
                base_url="https://llm.example/v1",
                api_key="test-key",
                model="llm-test",
                client=client,
            )
            decisions = await provider.plan_pronunciations(
                "本市今天发布新政策。",
                [Sentence(sentence_id=0, text="本市今天发布新政策。")],
            )

        self.assertEqual(decisions, [])
        self.assertEqual(calls, 0)

    async def test_llm_ignores_null_alternate_placeholders(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            payload = json.loads(request.content)
            self.assertIn("数组中不得填写 null", payload["messages"][0]["content"])
            result = [
                {"sentence_id": 0, "shot_id": 0, "confidence": 0.9, "alternates": [None, 1, None]},
                {"sentence_id": 1, "shot_id": None, "confidence": 0.0, "alternates": None},
            ]
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(result)}}]},
            )

        items = [
            {
                "sentence_id": index,
                "text": f"句子{index}",
                "candidates": [
                    {"shot_id": 0, "description": "镜头零"},
                    {"shot_id": 1, "description": "镜头一"},
                ],
            }
            for index in range(2)
        ]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = LLMProvider(
                base_url="https://llm.example/v1",
                api_key="test-key",
                model="llm-test",
                client=client,
                backoff_base=0,
            )
            decisions = await provider.rerank(items)

        self.assertEqual(calls, 1)
        self.assertEqual(decisions[0].alternates, [1])
        self.assertEqual(decisions[1].alternates, [])

    async def test_llm_filters_invalid_duplicate_and_selected_alternates(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            result = [
                {
                    "sentence_id": 0,
                    "shot_id": 0,
                    "confidence": 0.9,
                    "alternates": [0, 1, 1, 99],
                }
            ]
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(result)}}]},
            )

        items = [
            {
                "sentence_id": 0,
                "text": "句子0",
                "candidates": [
                    {"shot_id": 0, "description": "镜头零"},
                    {"shot_id": 1, "description": "镜头一"},
                ],
            }
        ]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = LLMProvider(
                base_url="https://llm.example/v1",
                api_key="test-key",
                model="llm-test",
                client=client,
                backoff_base=0,
            )
            decisions = await provider.rerank(items)

        self.assertEqual(decisions[0].alternates, [1])

    async def test_llm_degrades_out_of_candidate_primary_to_fallback(self) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            result = [
                {
                    "sentence_id": 3,
                    "beat_id": 0,
                    "shot_id": 99,
                    "confidence": 0.91,
                    "alternates": [2, 99, 3],
                }
            ]
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps(result)}}]},
            )

        items = [
            {
                "sentence_id": 3,
                "beat_id": 0,
                "text": "活动现场汇聚几十家参展企业，",
                "candidates": [
                    {"shot_id": 2, "description": "企业展位"},
                    {"shot_id": 3, "description": "市集全景"},
                ],
            }
        ]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = LLMProvider(
                base_url="https://llm.example/v1",
                api_key="test-key",
                model="llm-test",
                client=client,
                backoff_base=0,
            )
            decisions = await provider.rerank(items)

        self.assertEqual(calls, 1)
        self.assertIsNone(decisions[0].shot_id)
        self.assertEqual(decisions[0].confidence, 0.0)
        self.assertEqual(decisions[0].alternates, [2, 3])

    async def test_volcengine_llm_disables_thinking_for_reranking(self) -> None:
        request_payload: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            request_payload.update(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    [
                                        {
                                            "sentence_id": 0,
                                            "shot_id": 1,
                                            "confidence": 0.9,
                                            "alternates": [],
                                        }
                                    ]
                                )
                            }
                        }
                    ]
                },
            )

        items = [
            {
                "sentence_id": 0,
                "text": "新闻句子",
                "candidates": [{"shot_id": 1, "description": "相关镜头"}],
            }
        ]
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = LLMProvider(
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_key="test-key",
                model="doubao-test",
                client=client,
                disable_thinking=True,
            )
            await provider.rerank(items)

        self.assertEqual(request_payload["thinking"], {"type": "disabled"})


class FakeEmbeddingProvider:
    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def validate_configuration(self) -> None:
        return None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.batches.append(list(texts))
        return [[float(index + 1), 1.0] for index in range(len(texts))]


class FakeLLMProvider:
    def __init__(self) -> None:
        self.calls = 0

    def validate_configuration(self) -> None:
        return None

    async def rerank(self, items: list[dict[str, object]]) -> list[RerankDecision]:
        self.calls += 1
        return [
            RerankDecision(sentence_id=0, shot_id=0, confidence=0.91, alternates=[1]),
            RerankDecision(sentence_id=1, shot_id=1, confidence=0.83, alternates=[0]),
            RerankDecision(sentence_id=2, shot_id=None, confidence=0.2, alternates=[2]),
            RerankDecision(sentence_id=3, shot_id=2, confidence=0.34, alternates=[3]),
        ]


class MatchingPipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_stage_six_rejects_insufficient_unique_shots_before_providers(self) -> None:
        class Record:
            def __init__(self, task_dir: Path) -> None:
                self.task_id = "capacity-test"
                self.task_dir = task_dir
                self.script = "第一句。第二句。"
                self.uploads: list[UploadedAsset] = []
                self.preferences = EditingPreferences()

        class Reporter:
            def start_stage(self, record: PipelineTask, stage_number: int, message: str) -> None:
                return None

            def update_stage(self, record: PipelineTask, stage_number: int, fraction: float, message: str) -> None:
                return None

            def complete_stage(self, record: PipelineTask, stage_number: int, message: str) -> None:
                return None

        with tempfile.TemporaryDirectory() as directory:
            record = Record(Path(directory))
            sentences = [
                Sentence(sentence_id=0, text="第一句。", visual_beats=[VisualBeat(beat_id=0, text="第一句")]),
                Sentence(sentence_id=1, text="第二句。", visual_beats=[VisualBeat(beat_id=0, text="第二句")]),
            ]
            shots = _make_shots(1)
            with patch("backend.pipeline.EmbeddingProvider.from_settings") as embedding_factory:
                with self.assertRaisesRegex(MatchingError, "视觉节拍数量 2 超过可用镜头数量 1"):
                    await _run_semantic_matching(
                        record,
                        Reporter(),  # type: ignore[arg-type]
                        sentences,
                        shots,
                        [],
                        Settings(_env_file=None),
                    )

        embedding_factory.assert_not_called()

    async def test_batch_retrieval_one_rerank_and_quality_fallbacks(self) -> None:
        sentences = [Sentence(sentence_id=index, text=f"第{index}条新闻句子。") for index in range(4)]
        shots = _make_shots(6)
        embedding = FakeEmbeddingProvider()
        llm = FakeLLMProvider()
        progress: list[str] = []

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            plan = await build_match_plan(
                task_dir,
                sentences,
                shots,
                embedding,
                llm,
                lambda fraction, message: progress.append(message),
            )
            persisted = json.loads((task_dir / "match_plan.json").read_text(encoding="utf-8"))

        self.assertEqual(len(embedding.batches), 2)
        self.assertEqual(len(embedding.batches[0]), 4)
        self.assertEqual(len(embedding.batches[1]), 6)
        self.assertEqual(llm.calls, 1)
        self.assertEqual([item.shot_id for item in plan], [0, 1, 2, 3])
        self.assertEqual([item.is_fallback for item in plan], [False, False, True, True])
        self.assertEqual(len({item.shot_id for item in plan}), 4)
        self.assertTrue(all(item.shot_id is not None for item in plan))
        self.assertTrue(all(len(item.candidates) == 6 for item in plan))
        self.assertEqual([item["shot_id"] for item in persisted], [0, 1, 2, 3])
        self.assertEqual(progress[-1], "语义匹配完成，共 4 个句子")

    def test_fallback_rejects_more_sentences_than_unique_shots(self) -> None:
        sentences = [Sentence(sentence_id=index, text=f"句子{index}内容。") for index in range(3)]
        shots = _make_shots(2)
        candidates = {
            index: [MatchCandidate(shot_id=0, similarity=0.9), MatchCandidate(shot_id=1, similarity=0.8)]
            for index in range(3)
        }
        decisions = [
            RerankDecision(sentence_id=0, shot_id=0, confidence=0.9),
            RerankDecision(sentence_id=1, shot_id=1, confidence=0.9),
            RerankDecision(sentence_id=2, shot_id=None, confidence=0.1),
        ]

        with self.assertRaisesRegex(MatchingError, "每个镜头只使用一次"):
            apply_fallbacks(sentences, shots, candidates, decisions)

    def test_diversifies_overused_and_short_interval_shots(self) -> None:
        sentences = [Sentence(sentence_id=index, text=f"第{index}个视觉节拍。") for index in range(6)]
        shots = _make_shots(7)
        candidates = {
            (index, 0): [
                MatchCandidate(shot_id=0, similarity=0.9, combined_score=0.8),
                MatchCandidate(shot_id=index + 1, similarity=0.89, combined_score=0.79),
            ]
            for index in range(6)
        }
        decisions = [
            RerankDecision(
                sentence_id=index,
                shot_id=0,
                confidence=0.9,
                alternates=[index + 1],
            )
            for index in range(6)
        ]

        plan = apply_beat_decisions(sentences, shots, candidates, decisions)
        selected = [item.shot_id for item in plan]

        self.assertEqual(len(selected), len(set(selected)))
        self.assertTrue(
            all(
                selected[index] not in selected[max(0, index - 2) : index]
                for index in range(len(selected))
            )
        )

    def test_rejects_reused_entity_shot_without_supported_alternative(self) -> None:
        sentences = [
            Sentence(
                sentence_id=index,
                text=f"关键实体第{index}句。",
                visual_beats=[
                    VisualBeat(
                        beat_id=0,
                        text="关键实体",
                        entities=["关键实体"],
                        requires_entity_coverage=True,
                    )
                ],
            )
            for index in range(3)
        ]
        shots = _make_shots(3)
        candidates = {
            (index, 0): [
                MatchCandidate(
                    shot_id=0,
                    similarity=0.9,
                    combined_score=0.8,
                    matched_terms=["关键实体"],
                ),
                MatchCandidate(shot_id=1, similarity=0.88, combined_score=0.78),
            ]
            for index in range(3)
        }
        decisions = [
            RerankDecision(sentence_id=index, shot_id=0, confidence=0.9, alternates=[1])
            for index in range(3)
        ]

        with self.assertRaisesRegex(MatchingError, "一对一全局分配"):
            apply_beat_decisions(sentences, shots, candidates, decisions)

    def test_same_visual_group_still_obeys_actual_reuse_capacity(self) -> None:
        sentences = [
            Sentence(
                sentence_id=index,
                text=f"同一自然句的第{index + 1}个播音单元",
                visual_group_id=4,
                visual_beats=[VisualBeat(beat_id=0, text="迎春市集入口")],
            )
            for index in range(3)
        ]
        shots = _make_shots(3)
        candidates = {
            (index, 0): [
                MatchCandidate(shot_id=0, similarity=0.9, combined_score=0.8),
                MatchCandidate(shot_id=index + 1, similarity=0.84, combined_score=0.74),
            ]
            for index in range(2)
        }
        candidates[(2, 0)] = [
            MatchCandidate(shot_id=0, similarity=0.9, combined_score=0.8),
            MatchCandidate(shot_id=2, similarity=0.84, combined_score=0.74),
        ]
        decisions = [
            RerankDecision(sentence_id=index, shot_id=0, confidence=0.9)
            for index in range(3)
        ]

        plan = apply_beat_decisions(sentences, shots, candidates, decisions)

        selected = [item.shot_id for item in plan]
        self.assertEqual(selected.count(0), 1)
        self.assertEqual(len(set(selected)), 3)
        self.assertEqual([item.visual_group_id for item in plan], [4, 4, 4])

    def test_global_assignment_caps_event_identity_shot_across_groups_and_overlays(self) -> None:
        sentences = [
            Sentence(
                sentence_id=index,
                text=f"第{index + 1}个播音单元",
                visual_group_id=0 if index < 3 else 4,
                visual_beats=[
                    VisualBeat(
                        beat_id=0,
                        text="活动信息" if index < 3 else "抽象总结",
                        intent_type="general" if index < 3 else "abstract",
                    )
                ],
            )
            for index in range(8)
        ]
        shots = _make_shots(9)
        candidates = {
            (index, 0): [
                MatchCandidate(shot_id=0, similarity=0.9, combined_score=0.80),
                MatchCandidate(
                    shot_id=index + 1,
                    similarity=0.86,
                    combined_score=0.74,
                ),
            ]
            for index in range(8)
        }
        decisions = [
            RerankDecision(sentence_id=index, shot_id=0, confidence=0.9)
            for index in range(8)
        ]

        plan = apply_beat_decisions(sentences, shots, candidates, decisions)
        selected = [item.shot_id for item in plan]

        self.assertEqual(selected.count(0), 1)
        self.assertEqual(len(set(selected)), 8)
        self.assertTrue(
            all(
                selected[index] not in selected[max(0, index - 2) : index]
                for index in range(len(selected))
            )
        )

    def test_cosine_similarity_handles_zero_vector(self) -> None:
        self.assertEqual(cosine_similarity([0.0, 0.0], [1.0, 2.0]), 0.0)
        self.assertAlmostEqual(cosine_similarity([1.0, 0.0], [1.0, 0.0]), 1.0)

    async def test_oil_tea_asr_evidence_is_rescued_and_planned_with_sushi(self) -> None:
        class EvidenceEmbeddingProvider:
            def validate_configuration(self) -> None:
                return None

            async def embed(self, texts: list[str]) -> list[list[float]]:
                return [[1.0, 0.0] for _ in texts]

            async def embed_queries(self, texts: list[str]) -> list[list[float]]:
                return [[0.0, 1.0], [1.0, 0.0]]

            async def embed_corpus(self, texts: list[str]) -> list[list[float]]:
                return [
                    [1.0, 0.0],
                    [0.0, 1.0],
                    [0.0, 1.0],
                    [0.0, 1.0],
                    [0.0, 1.0],
                    [0.0, 1.0],
                    [0.0, -1.0],
                ]

        class EvidenceReranker:
            def validate_configuration(self) -> None:
                return None

            async def rerank(self, items: list[dict[str, object]]) -> list[RerankDecision]:
                decisions: list[RerankDecision] = []
                for item in items:
                    candidates = item["candidates"]
                    assert isinstance(candidates, list)
                    selected = next(
                        (candidate for candidate in candidates if candidate.get("matched_terms")),
                        candidates[0],
                    )
                    decisions.append(
                        RerankDecision(
                            sentence_id=int(item["sentence_id"]),
                            beat_id=int(item["beat_id"]),
                            shot_id=int(selected["shot_id"]),
                            confidence=0.9,
                        )
                    )
                return decisions

        sentence = Sentence(
            sentence_id=0,
            text="灌阳油茶、现做寿司等特色小吃引来众多品尝者。",
            visual_beats=extract_visual_beats("灌阳油茶、现做寿司等特色小吃引来众多品尝者。"),
        )
        shots = _make_shots(7)
        shots[0] = shots[0].model_copy(
            update={"description": "寿司摊位提供试吃", "search_text": "寿司摊位 现做寿司 免费试吃"}
        )
        shots[6] = shots[6].model_copy(
            update={
                "description": "女孩在锅中添加食材",
                "source_transcript": "正在制作油茶，油茶很好喝",
                "source_transcript_spans": [
                    ShotTranscriptSpan(
                        text="正在制作油茶，油茶很好喝",
                        start=6.2,
                        end=6.9,
                    )
                ],
                "search_text": "女孩烹饪 现场语音识别：正在制作油茶，油茶很好喝",
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            plan = await build_match_plan(
                Path(directory),
                [sentence],
                shots,
                EvidenceEmbeddingProvider(),
                EvidenceReranker(),
                lambda *args: None,
                top_k=5,
                lexical_rescue_k=2,
            )

        self.assertEqual([beat.shot_id for beat in plan[0].beat_matches], [6, 0])
        self.assertIn(6, [candidate.shot_id for candidate in plan[0].beat_matches[0].candidates])
        oil_candidate = next(
            candidate
            for candidate in plan[0].beat_matches[0].candidates
            if candidate.shot_id == 6
        )
        self.assertIsNotNone(oil_candidate.preferred_in_time)
        self.assertEqual(len({beat.shot_id for beat in plan[0].beat_matches}), 2)

    async def test_missing_entity_uses_bounded_visual_verification_rescue(self) -> None:
        class VerificationEmbeddingProvider:
            def validate_configuration(self) -> None:
                return None

            async def embed(self, texts: list[str]) -> list[list[float]]:
                return await self.embed_queries(texts)

            async def embed_queries(self, texts: list[str]) -> list[list[float]]:
                return [[1.0, 0.0] for _ in texts]

            async def embed_corpus(self, texts: list[str]) -> list[list[float]]:
                return [[1.0 - index * 0.1, index * 0.1] for index in range(len(texts))]

        class VerificationReranker:
            def validate_configuration(self) -> None:
                return None

            async def rerank(self, items: list[dict[str, object]]) -> list[RerankDecision]:
                decisions: list[RerankDecision] = []
                for item in items:
                    candidates = item["candidates"]
                    assert isinstance(candidates, list)
                    selected = next(
                        (
                            candidate
                            for candidate in candidates
                            if candidate.get("verified_terms") or candidate.get("matched_terms")
                        ),
                        candidates[0],
                    )
                    decisions.append(
                        RerankDecision(
                            sentence_id=int(item["sentence_id"]),
                            beat_id=int(item["beat_id"]),
                            shot_id=int(selected["shot_id"]),
                            confidence=0.9,
                        )
                    )
                return decisions

        class VerificationProvider:
            def __init__(self) -> None:
                self.calls: list[int] = []

            async def verify_visual_entities(
                self,
                image_path: Path,
                visual_beat: str,
                entities: list[str],
            ) -> VisualEntityVerification:
                shot_id = int(image_path.stem.split("_")[-1])
                self.calls.append(shot_id)
                if shot_id == 6:
                    return VisualEntityVerification(
                        verified=True,
                        confidence=0.94,
                        matched_entities=["油茶"],
                        evidence="画面显示人物从锅中盛汤并加入炒米配料",
                        preferred_relative_time=0.6,
                    )
                return VisualEntityVerification(
                    verified=False,
                    confidence=0.2,
                    evidence="未看到目标实体",
                )

        sentence = Sentence(
            sentence_id=0,
            text="灌阳油茶、现做寿司等特色小吃。",
            visual_beats=extract_visual_beats("灌阳油茶、现做寿司等特色小吃。"),
        )
        shots = _make_shots(7)
        shots[0] = shots[0].model_copy(
            update={"description": "寿司摊位", "search_text": "现做寿司 寿司摊位"}
        )
        shots[6] = shots[6].model_copy(
            update={
                "description": "女子从锅中盛汤并加入配料",
                "actions": ["盛汤", "添加配料"],
                "entities": ["金属汤锅", "塑料碗", "炒米"],
                "search_text": "女子盛汤 添加配料 金属汤锅 塑料碗 炒米",
            }
        )
        verifier = VerificationProvider()

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            sheets = task_dir / "thumbs" / "contact_sheets"
            sheets.mkdir(parents=True)
            for shot in shots:
                (sheets / f"shot_{shot.shot_id}.jpg").write_bytes(b"image")
            plan = await build_match_plan(
                task_dir,
                [sentence],
                shots,
                VerificationEmbeddingProvider(),
                VerificationReranker(),
                lambda *args: None,
                top_k=5,
                lexical_rescue_k=1,
                entity_verifier=verifier,
                entity_verification_max_shots=6,
            )
            artifact = json.loads(
                (task_dir / "entity_verification.json").read_text(encoding="utf-8")
            )

        oil_tea = plan[0].beat_matches[0]
        self.assertEqual(oil_tea.shot_id, 6)
        self.assertFalse(oil_tea.is_fallback)
        selected = next(candidate for candidate in oil_tea.candidates if candidate.shot_id == 6)
        self.assertEqual(selected.verified_terms, ["油茶"])
        self.assertAlmostEqual(selected.preferred_in_time or 0.0, 6.2)
        self.assertLessEqual(len(verifier.calls), 6)
        self.assertEqual(artifact["accepted_count"], 1)

    async def test_fielded_retrieval_prioritizes_organization_ocr_evidence(self) -> None:
        class EqualEmbeddingProvider:
            def validate_configuration(self) -> None:
                return None

            async def embed(self, texts: list[str]) -> list[list[float]]:
                return [[1.0, 0.0] for _ in texts]

        class FirstCandidateReranker:
            def validate_configuration(self) -> None:
                return None

            async def rerank(self, items: list[dict[str, object]]) -> list[RerankDecision]:
                candidates = items[0]["candidates"]
                assert isinstance(candidates, list)
                return [
                    RerankDecision(
                        sentence_id=0,
                        beat_id=0,
                        shot_id=int(candidates[0]["shot_id"]),
                        confidence=0.9,
                    )
                ]

        sentence = Sentence(
            sentence_id=0,
            text="本次活动由南宁信息港主办。",
            retrieval_context="本次活动由南宁信息港主办。",
            visual_beats=[
                VisualBeat(
                    beat_id=0,
                    text="本次活动由南宁信息港主办",
                    entities=["南宁信息港", "信息港"],
                    intent_type="organization",
                )
            ],
        )
        shots = _make_shots(6)
        shots[4] = shots[4].model_copy(
            update={
                "description": "活动入口展板",
                "ocr_texts": ["南宁信息港迎春市集"],
                "entities": ["南宁信息港"],
                "search_text": "活动入口展板 南宁信息港迎春市集",
            }
        )

        with tempfile.TemporaryDirectory() as directory:
            plan = await build_match_plan(
                Path(directory),
                [sentence],
                shots,
                EqualEmbeddingProvider(),
                FirstCandidateReranker(),
                lambda *args: None,
                top_k=5,
                lexical_rescue_k=1,
            )

        self.assertEqual(plan[0].shot_id, 4)
        self.assertEqual(plan[0].overlay_kind, "organization")
        selected = next(candidate for candidate in plan[0].candidates if candidate.shot_id == 4)
        self.assertGreater(selected.ocr_score, 0.0)
        self.assertGreater(selected.entity_score, 0.0)

    async def test_video_embedding_only_processes_coarse_and_lexical_candidates(self) -> None:
        class CandidateEmbeddingProvider:
            def __init__(self) -> None:
                self.video_batches: list[list[int]] = []

            def validate_configuration(self) -> None:
                return None

            async def embed(self, texts: list[str]) -> list[list[float]]:
                return await self.embed_queries(texts)

            async def embed_queries(self, texts: list[str]) -> list[list[float]]:
                return [[1.0, 0.0] for _ in texts]

            async def embed_video_queries(self, texts: list[str]) -> list[list[float]]:
                return [[1.0, 0.0] for _ in texts]

            async def embed_corpus(self, texts: list[str]) -> list[list[float]]:
                vectors = [
                    [1.0, 0.0],
                    [0.9, 0.1],
                    [0.8, 0.2],
                    [0.0, 1.0],
                    [-0.8, 0.2],
                    [-1.0, 0.0],
                ]
                return vectors[: len(texts)]

            async def embed_video_corpus(
                self,
                items: list[tuple[Path, str]],
                progress_callback: object | None = None,
            ) -> list[list[float] | None]:
                ids = [int(path.stem.split("_")[-1]) for path, _ in items]
                self.video_batches.append(ids)
                if callable(progress_callback):
                    for completed in range(1, len(items) + 1):
                        progress_callback(completed, len(items))
                return [[1.0, 0.0] for _ in items]

        class CandidateReranker:
            def validate_configuration(self) -> None:
                return None

            async def rerank(self, items: list[dict[str, object]]) -> list[RerankDecision]:
                candidates = items[0]["candidates"]
                assert isinstance(candidates, list)
                return [
                    RerankDecision(
                        sentence_id=0,
                        beat_id=0,
                        shot_id=int(candidates[0]["shot_id"]),
                        confidence=0.9,
                    )
                ]

        sentence = Sentence(
            sentence_id=0,
            text="目标实体正在活动现场展示。",
            visual_beats=extract_visual_beats("目标实体正在活动现场展示。"),
        )
        shots = _make_shots(6)
        shots[5] = shots[5].model_copy(
            update={
                "description": "另一个普通镜头",
                "search_text": "目标实体 特写展示",
            }
        )
        embedding = CandidateEmbeddingProvider()
        prepared_batches: list[list[int]] = []

        async def fake_prepare(
            task_dir: Path,
            selected_shots: list[AnnotatedShot],
            *,
            concurrency: int,
        ) -> list[Path | None]:
            prepared_batches.append([shot.shot_id for shot in selected_shots])
            return [task_dir / f"shot_{shot.shot_id}.mp4" for shot in selected_shots]

        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            with patch("backend.matching._prepare_embedding_clips", side_effect=fake_prepare):
                plan = await build_match_plan(
                    task_dir,
                    [sentence],
                    shots,
                    embedding,
                    CandidateReranker(),
                    lambda *args: None,
                    top_k=2,
                    lexical_rescue_k=1,
                    video_embedding_enabled=True,
                    video_embedding_candidate_top_k=2,
                )
                await build_match_plan(
                    task_dir,
                    [sentence],
                    shots,
                    embedding,
                    CandidateReranker(),
                    lambda *args: None,
                    top_k=2,
                    lexical_rescue_k=1,
                    video_embedding_enabled=True,
                    video_embedding_candidate_top_k=2,
                )
            selection = json.loads(
                (task_dir / "video_embedding_selection.json").read_text(encoding="utf-8")
            )

        self.assertEqual(prepared_batches, [[0, 1, 5], [0, 1, 5]])
        self.assertEqual(embedding.video_batches, [[0, 1, 5]])
        self.assertEqual(selection["selected_shot_ids"], [0, 1, 5])
        self.assertEqual(selection["selected_shot_count"], 3)
        self.assertEqual(selection["available_shot_count"], 6)
        self.assertEqual(selection["cache_hit_count"], 3)
        self.assertEqual(selection["requested_count"], 0)
        self.assertTrue(
            all(candidate.shot_id in {0, 1, 5} for candidate in plan[0].candidates)
        )

    async def test_video_candidate_top_k_cannot_be_smaller_than_final_top_k(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(Exception, "不能小于最终稠密召回数量"):
                await build_match_plan(
                    Path(directory),
                    [Sentence(sentence_id=0, text="测试句子。")],
                    _make_shots(6),
                    FakeEmbeddingProvider(),
                    FakeLLMProvider(),
                    lambda *args: None,
                    top_k=5,
                    video_embedding_candidate_top_k=4,
                )


def _make_shots(count: int) -> list[AnnotatedShot]:
    return [
        AnnotatedShot(
            shot_id=index,
            source_index=0,
            source_scene_index=index,
            source_name="input.mp4",
            norm_path="norm/norm_0.mp4",
            start=float(index),
            end=float(index + 1),
            duration=1.0,
            thumb_path=f"thumbs/shot_{index}.jpg",
            status="available",
            description=f"镜头{index}的新闻现场",
            scene_type="outdoor",
            subjects=["市民"],
            actions=["行走"],
            keywords=["新闻", "现场", f"镜头{index}"],
            quality=VisionQuality(
                sharp=min(1.0, 0.5 + index * 0.08),
                bright=min(1.0, 0.5 + index * 0.08),
            ),
        )
        for index in range(count)
    ]


if __name__ == "__main__":
    unittest.main()
