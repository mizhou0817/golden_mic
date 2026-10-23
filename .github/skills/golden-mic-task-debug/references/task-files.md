# 任务目录里的文件

## 位置

- **任务目录**：`DATA_DIR/<task_id>/`。
  - 本机 `DATA_DIR` 默认是 `data/tasks`（`config.py`）。
  - 生产模板是 `/srv/golden-mic-data/tasks`（`deploy/golden-mic.env.production.example`）。
  - Docker：宿主机 `/opt/goldmic/data` 挂载为容器内 `/data`（`deploy/docker/docker-compose.yml`）。
- **上传暂存**：`DATA_DIR/_uploads/<upload_id>/`（`uploads.py`）。
- **准入账本**：`DATA_DIR/_v2/admission.sqlite3`，有 `accepted` 和 `tombstones` 两张表（`admission.py`）。

这些都只读，不改也不删。2026-10-22 按代码核对。

## 文件一览

| 文件 | 什么时候写 | 排查时看什么 |
|---|---|---|
| `task_state.json` | 阶段开始、完成、失败时（`task_manager.py::_persist_record`），不是实时的 | `status`、`current_stage`、`stage_name`、`error_stage`、`error_kind`、`bad_rows`、`error_message`、`revision`、`updated_at`、`stages[]`。**`draft_context` 里有上传凭据原文，只取字段** |
| `task.log` | 随时追加，写入时已脱敏 | 阶段起止、命令行、FFmpeg stderr、首版失败和 apply 失败的堆栈、提前清理记录 |
| `pretranscripts.json` | 开始制作或恢复草稿时写入转写快照（`drafts.py`）。阶段 2 给失败的文件补跑 ASR 时，更新每项的 `task_asr_status` | 哪个文件的转写是 started、completed、cancelled、failed |
| `mode_stage_cache/stage-{2,3,4,6,7,9}.json` | 阶段开始写 `started`，完成写 `complete`；阶段 6 收费调用结束后在本地失败，改写为 `retry_safe`（`mode_pipeline.py::ModeStageCache`） | `state` 和 `signature`，它们决定 RetrySame 能不能进行 |
| `shots_annotated.json` | 画面理解（阶段 4）之后 | 每个镜头的理解结果；理解失败的镜头带原始错误 |
| `video_embeddings.json`、`video_embedding_metrics.ndjson` | 匹配（阶段 6） | Embedding 结果和每次调用的指标 |
| `tts_manifest.json` | 配音（阶段 7） | `provider_request_count_this_render`：这次渲染实际发出的 TTS 请求数 |
| `quality_report.json`、`report.json` | 质量检查（阶段 10） | 检查项和发布门禁的依据 |
| `revisions/r<n>/` | 每个版本一份 | 版本快照；恢复旧版本就是复制它 |
| `v2_operations/<operation_id>.json` | 每次 apply | 修改回执：`state`、`steps`、`replace_failures`。最多 200 份，不会自动清理 |
| `v2_exports/<export_id>/job.json` | 每次导出 | 导出状态；失败时 `error` 为 `export_not_published` |
| `.incomplete-copy` | 复制作品副本期间 | 文件还在，说明复制没完成（`task_operations.py`） |

## 安全读取的写法（PowerShell，仓库根目录）

先设任务目录，下面三条都用它。生产环境把路径换成 `/srv/golden-mic-data/tasks/<task_id>`，在服务器上用有权限的账号运行同样的 Python 一行命令。

```powershell
$t = 'data\tasks\<task_id>'
```

**状态字段。** 只打印白名单字段，不打印 `draft_context`：

```powershell
& .\.venv\Scripts\python.exe -I -c "import json,sys; s=json.load(open(sys.argv[1],encoding='utf-8')); print(json.dumps({k: s.get(k) for k in ('status','current_stage','stage_name','error_stage','error_kind','bad_rows','error_message','revision','updated_at','lifecycle_v2')}, ensure_ascii=False, indent=1))" "$t\task_state.json"
```

**日志末尾：**

```powershell
Get-Content "$t\task.log" -Tail 80 -Encoding utf8
```

**阶段缓存状态。** 判断 RetrySame 能否进行：

```powershell
& .\.venv\Scripts\python.exe -I -c "import json,sys,pathlib; [print(p.name, json.load(open(p,encoding='utf-8')).get('state')) for p in sorted(pathlib.Path(sys.argv[1]).glob('stage-*.json'))]" "$t\mode_stage_cache"
```

## 注意

- **`task_state.json` 不是实时的。** 它只在阶段边界落盘，运行中的进度以状态接口为准。
- **运行中别碰任务目录。** 不要在服务运行时移动、改名或"修复"任务目录。存储身份变了，服务会返回 503（"V2 storage identity changed; restart required"）。
- **清理循环跳过忙的任务。** 它在启动时运行一次，之后每 3600 秒一次，跳过正在处理的记录。墓碑的原因写 `draft_idle_expiry` 或 `task_retention_expiry`。
