# 改动清单：检查码、AI 服务商、导出格式

本文依据提交 af1e4e7 的代码整理（2026-10-21）。以函数名为准，行号会变。改完按 gm-verify 验证。

## 1. 新增发布检查码

1. 先确定检查在哪里产生：
   - **规则模板类**：在 `backend/mode_rules.json` 的 `checks` 或 `safety_checks` 里新增条目，再由 `production_modes.evaluate_mode_checks` 通过 `check_definition` 发出。
   - **质量检查类**：在 `quality.generate_quality_report` 里调用 `_issue()` 发出。改了名的旧码要加进 `MODE_CHECK_CODE_ALIASES`（`quality.py`，`reporting.py` 也有引用）。
   - **只在发布环节检查**：在 `publication._canonical` 里调用 `_notice()`。
2. 约束：
   - 检查码必须匹配 `^[A-Za-z][A-Za-z0-9_]{0,119}$`（`publication.py`）。
   - 级别 1 可以由用户确认；级别 0 不能确认，唯一例外是派生出来的 `generated_media`。
   - 新检查不能削弱已有的门禁。级别、能否确认、怎么展示，要先对照 gm-news-integrity。
3. 前端：界面直接显示服务端返回的 `message`，大多数检查码不需要逐个改前端。
   - 唯一按检查码区分的逻辑在 `frontend/src/lib/workbenchApi.ts` 的 `canConfirmCheck`。
   - 操作按钮的文字按 action 映射，在 `ResultWorkbench.tsx` 里。
   - `productionModes.ts` 在构建时导入 `mode_rules.json`，所以改完要重新构建前端。
4. 测试：
   - 在 `tests/cases/text-rules.json` 的 `checks` 数组里加用例。
   - 检查码总数 **17** 写死在 `tests/test_v2_text_rules.py` 和 `frontend/scripts/test-v2-text-rules.mjs` 里，要一起更新。
   - 在 `test_production_modes`、`test_quality`、`test_publication` 里覆盖新检查的行为。
5. 文档：`docs/DESIGN-MAP.md`（写着"17 个设计检查码"）、`docs/USER_GUIDE.md` 的发布检查章节。

## 2. 新增 AI 服务商或服务

1. 写服务商类：实现 `validate_configuration()` 和异步 close（可参照 `providers/vision.py`），并在对应工厂函数里加分支：

   | 类型 | 工厂函数 |
   |---|---|
   | ASR | `providers/asr.py` 的 `create_asr_provider` |
   | TTS | `providers/tts.py` 的 `create_tts_provider` |
   | 视觉 | `providers/vision.py` 的 `create_vision_provider` |
   | 向量 | `providers/embedding.py` 的 `create_embedding_provider` |
   | LLM | `providers/llm.py` 的 `LLMProvider.from_settings` |
   | 生成画面 | `providers/generative.py` |

   新类要从 `providers/__init__.py` 导出。
2. `backend/config.py`：
   - 新增 Settings 字段。
   - 把服务地址加进生产环境的 `provider_urls` 检查。
   - 把凭证加进 `sensitive_values`。
3. **凭证命名**：`provenance.py` 只会剔除名字里含 `api_key`、`api_keys`、`access_token`、`app_key`、`secret` 的设置。其他命名的凭证会写进 `pipeline_manifest.json`，造成泄露。新源文件和提示词也要加进 provenance 的散列清单。
4. **缓存键**：新增的 TTS 设置必须进配音签名（`mode_pipeline.py` 里组装 voice signature 的地方），ASR 上传缓存的作用域在 `uploads.py`。`_stage_signature` 具体包含哪些字段：TODO(待确认)。
5. 启动校验：`pipeline._validate_pipeline_configuration`，由 `readiness.py` 调用。
6. 调用点：`mode_pipeline.py`、`uploads.py`、`workbench.py`、`main.py`、`media_input.py` 里创建服务商的地方。用 `grep -n "create_.*_provider\|from_settings"` 找全。
7. 配置模板：`.env.example` 和 `deploy/golden-mic.env.production.example` 都要更新，由 `tests/test_v2_config_template.py` 约束（只改字段名和说明，不写真实值）。CI 的占位值在 `.github/workflows/linux-ci.yml`，改 CI 只提建议，不直接改。
8. 文档：`docs/CONFIGURATION.md`、`docs/V2_MODEL_SELECTION_20261002.md` 及其 JSON、`README.md`。
9. 测试：test_asr、test_m2_vision、test_m3_matching、test_m4_tts、test_embedding_concurrency、test_editing_enhancements、test_provenance、test_v2_config_template、test_mode_pipeline。
10. 计费：写明超时、最多重试次数和幂等方式。不能在失败后自动重放已经收费的请求。

## 3. 新增导出格式

1. `backend/studio_render.py`：
   - 在 `V2ExportOptions.fmt` 和它的校验器里加上新格式。
   - 在 `v2_export_budget` 里给出磁盘预算。
   - 在 `render_v2_export` 里加 FFmpeg 命令和输出探测校验。
2. `backend/v2_editing.py`：
   - `POST /exports` 处理新格式。
   - **会丢失烧录 AI 披露的格式（纯音频、纯字幕等）必须加进"需要披露时拒绝"的列表**（现在拒绝的是 mp3、srt）。
   - 下载映射里加上 MIME 类型。
3. 旧路由：`main.py` 的旧 `/export` 走 Studio 的 `ExportOptions`，披露拒绝逻辑在 `studio.py`。
4. 前端：`frontend/src/lib/workbenchApi.ts` 的格式类型和请求；`ResultWorkbench.tsx` 的导出选项和说明文字。含 AI 画面的作品，界面目前仍显示 MP3/SRT，由服务端用 422 拒绝。
5. 测试：test_v2_exports、test_mode_exports、test_v2_export_download_auth、test_v2_export_unresolved；前端 `scripts/test-v2-result.mjs`。
6. 文档：`docs/USER_GUIDE.md` 的导出章节、`docs/THREE_MODE_API_20260928.md`。
