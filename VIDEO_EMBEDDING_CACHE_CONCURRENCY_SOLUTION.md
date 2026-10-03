# 视频 Embedding 跨任务缓存与并发治理方案

日期：2026-07-25
范围：语义匹配阶段的根因二（缺少跨任务缓存）和根因三（并发批次限制）

> 执行状态：本文件第 3 节“并发批次解决方案”已于 2026-07-25 实施；跨任务纯视频缓存仍按第 2 节作为后续独立改造，尚未混用当前元数据融合向量。

## 1. 当前事实与目标

最新真实任务 `dd562c52b6b34671a0d6876631e9c30e`：

- 阶段 6：523.848 秒；
- 候选视频：48/52；
- 云端视频 Embedding：48 次；
- 缓存命中：0；
- 云端视频窗口约 470 秒；
- 代理准备约 26 秒；
- 使用现有任务内向量重放时，阶段 6 的非冷视频部分仅约 25.565 秒。

该任务与旧任务 `c54d074d...` 的 48/48 个原素材 SHA-256 完全一致，镜头身份也一致；但当前缓存只位于任务目录，导致所有视频请求重算。

目标：

1. 相同视频内容和镜头区间跨任务复用视频向量；
2. 缓存命中必须不改变召回语义；
3. 多任务共享一个账号级并发上限，避免两个任务各自并发 4、合计达到 8；
4. 在确认 Provider 配额后，把冷启动并发从 4 安全提升到 6 或 8；
5. 每个镜头完成后立即 checkpoint，进程中断不丢失已付费结果；
6. 具备命中率、请求延迟、重试、429、超时和 request ID 可观测性。

---

## 2. 根因二解决方案：跨任务内容寻址缓存

## 2.1 不能直接按视频哈希复用当前向量

当前视频 Embedding 请求同时发送：

- 视频代理；
- `_shot_retrieval_text()` 生成的 Vision/OCR/ASR 元数据。

真实对比显示：两个完全相同素材任务的 52/52 个镜头身份一致，但 **0/52 的检索元数据文本完全一致**。Vision 输出具有非确定性，因此当前向量其实是“视频 + 当次元数据”的融合结果。

若只按视频哈希复用现有融合向量，会把旧任务的元数据语义带入新任务，存在隐蔽的召回偏移。质量安全的方案必须先拆分模态。

## 2.2 推荐架构：缓存纯视频向量，文本向量按任务生成

将镜头表示改为：

$$
V_{shot}=\operatorname{normalize}(w_t V_{text}+w_v V_{video})
$$

其中：

- $V_{text}$：当前任务的 Vision/OCR/ASR 元数据向量，每任务重新生成或另行文本缓存；
- $V_{video}$：只由视频内容和固定视频指令生成，可跨任务复用；
- 初始仍采用当前权重 $w_t=0.35, w_v=0.65$，再通过召回消融调参。

Provider 调整：

1. 视频向量请求只发送 `video_url`；
2. 如果接口要求文本输入，发送固定常量文本，例如“视频镜头视觉内容”，不能发送任务级 Vision/OCR/ASR 文本；
3. 新增独立、版本化的视频指令；
4. 现有文本向量继续使用当前 `_shot_retrieval_text()`。

这样 Vision 描述变化不会使视频缓存失效，也不会错误复用旧元数据。

### 兼容策略

- 当前 `video_embeddings.json` 标记为 `metadata_fused_v1`；
- 新缓存标记为 `video_only_v2`；
- 两类向量不能混用；
- 旧融合向量仅可继续用于同任务精确恢复，不能自动导入全局纯视频缓存；
- 第一个新版本任务会冷启动填充纯视频缓存，后续相同素材才能获得高命中。

## 2.3 缓存键设计

对 canonical JSON 做 SHA-256：

```json
{
  "schema": 2,
  "mode": "video_only",
  "source_sha256": "...",
  "start_us": 0,
  "end_us": 9233000,
  "normalization_recipe": "gm-normalize-v1",
  "proxy_recipe": "640x360-h264-10fps-crf28-v1",
  "embedding_provider": "volcengine",
  "model": "doubao-embedding-vision-251215",
  "dimensions": 2048,
  "video_fps": 0.5,
  "max_video_tokens": 10240,
  "instruction_sha256": "..."
}
```

关键规则：

- 时间使用整数微秒，禁止浮点字符串漂移；
- 必须包含模型、维度、fps、token 和指令版本；
- 必须包含代理/规格化配方版本；
- 不包含 API Key、任务 ID、文件名或访问令牌；
- 若暂时维持“视频 + 元数据”融合模式，键中必须额外包含 canonical metadata SHA-256；否则不安全。但实测元数据 0/52 完全一致，这种模式跨任务命中率接近零，不推荐。

## 2.4 在上传时生成内容哈希

修改上传存储路径：

- `save_uploads()` 写文件时同步更新 SHA-256，避免之后再次读取大文件；
- `UploadedAsset` 新增可持久化的 `sha256`；
- `upload_manifest.json` 和 `task_state.json` 保存哈希；
- 老任务缺少哈希时允许懒计算一次并回写；
- `Shot` 保存 `source_sha256`，或由 `source_index -> upload.sha256` 映射提供给阶段 6。

不能使用原文件名、任务内随机存储名、mtime 或文件大小代替内容哈希。

## 2.5 缓存存储格式

推荐使用 Python 标准库 SQLite，而不是每个镜头一个松散 JSON：

```sql
CREATE TABLE video_embedding_cache (
    cache_key TEXT PRIMARY KEY,
    vector BLOB NOT NULL,
    dimensions INTEGER NOT NULL,
    vector_sha256 TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_accessed_at REAL NOT NULL,
    hit_count INTEGER NOT NULL DEFAULT 0,
    source_size INTEGER NOT NULL,
    metadata_json TEXT NOT NULL
);
```

建议：

- 文件：`data/cache/video_embeddings_v2.sqlite3`；
- `PRAGMA journal_mode=WAL`；
- `PRAGMA busy_timeout=5000`；
- 向量按 little-endian float32 BLOB 保存，2048 维约 8KB，而当前 JSON 单向量约 40–50KB；
- 读取后验证维度、长度、所有值有限、向量校验和；
- 损坏条目视为 miss 并删除；
- 任务目录中的 `video_embeddings.json` 继续作为任务可追溯产物，但不再作为跨任务主缓存。

## 2.6 查询顺序必须调整

当前代码先 `_prepare_embedding_clips()`，再读取任务内缓存。全局缓存后必须改为：

1. 粗召回得到候选镜头；
2. 根据源哈希和入出点生成所有 cache key；
3. `cache.get_many(keys)`；
4. 只为 miss 生成代理片段；
5. 只为 miss 调用 Provider；
6. 每个成功结果立即写缓存；
7. 组装命中与新结果并继续终排。

否则即使云端缓存命中，仍会浪费代理视频编码时间。

## 2.7 单飞（single-flight）与并发写

`MAX_CONCURRENT_TASKS=2` 时，两个任务可能同时请求同一个镜头。必须保证相同 cache key 只有一个上游请求：

- 单 worker 当前可使用进程级 `dict[str, asyncio.Lock]` 或 Future；
- 第一个协程成为 owner，其他协程等待同一个 Future；
- owner 成功后写缓存并唤醒等待者；
- owner 失败后等待者收到失败，按各自 fail-open 策略回退文本向量；
- Future 完成后删除锁条目，避免内存泄漏；
- SQLite `PRIMARY KEY` 再提供落盘幂等保护。

未来若改成多进程/多实例，需使用 SQLite lease 表、Redis lock 或任务队列；当前项目要求单 Uvicorn worker，进程内 single-flight 足够。

## 2.8 增量 checkpoint

当前 `asyncio.gather()` 全部完成后才写任务缓存。改为每个镜头成功即：

1. 校验向量；
2. SQLite 单条事务 `INSERT OR IGNORE`；
3. 更新任务级命中/生成计数；
4. 写结构化日志。

这样处理 48 个镜头时即使第 47 个后进程退出，前 46 个结果仍可复用。

## 2.9 生命周期和清理

共享缓存不能跟随单个任务 TTL 删除。新增配置：

```dotenv
VIDEO_EMBEDDING_CACHE_ENABLED=true
VIDEO_EMBEDDING_CACHE_PATH=data/cache/video_embeddings_v2.sqlite3
VIDEO_EMBEDDING_CACHE_MAX_GB=10
VIDEO_EMBEDDING_CACHE_TTL_DAYS=90
```

清理策略：

- 每小时或启动时运行一次，不在请求热路径执行；
- 先删除超过 TTL 的条目；
- 超过容量后按 `last_accessed_at` 做 LRU；
- 不缓存代理视频，只缓存向量与非敏感元数据；
- 备份/恢复时 SQLite 与 schema 版本一起管理；
- 提供命令或管理端点查看大小、命中率和清空缓存。

## 2.10 预期收益

最新任务与旧任务素材完全相同。如果已经有 `video_only_v2` 缓存：

- 48 个候选预计全部命中；
- 视频请求从 48 降为 0；
- 代理编码从 48 降为 0；
- 阶段 6 可接近缓存重放实测的约 25.6 秒。

现有旧缓存是“视频 + 非确定元数据”融合向量，不能质量安全地直接迁移。因此第一次部署 v2 后仍需冷填充；第二次相同素材才会获得上述收益。

---

## 3. 根因三解决方案：账号级全局并发控制

## 3.1 当前问题

当前 `VolcengineMultimodalEmbeddingProvider` 每个实例各自创建：

```python
asyncio.Semaphore(self.video_concurrency)
```

每个任务创建独立 Provider，而 `MAX_CONCURRENT_TASKS=2`。因此：

- `VIDEO_EMBEDDING_CONCURRENCY=4` 是每任务上限，不是账号全局上限；
- 两个任务可同时向同一账号产生 8 个视频请求；
- 当前只有 1 个 API Key；
- 多 Key 也未必代表独立配额，不能假设吞吐线性增加；
- 429 时每个请求独立退避，容易形成同步重试风暴。

## 3.2 两级并发模型

新增两个概念：

```dotenv
VIDEO_EMBEDDING_PER_TASK_CONCURRENCY=4
VIDEO_EMBEDDING_GLOBAL_CONCURRENCY=4
VIDEO_EMBEDDING_GLOBAL_MAX_CONCURRENCY=8
```

执行请求前必须同时获得：

1. 每任务 limiter：控制单任务公平性；
2. 进程全局 limiter：控制账号真实并发。

默认先保持全局 4，确保两个任务合计仍不超过 4。不要把当前字段直接升到 8，因为它会使双任务合计达到 16。

推荐新增 `EmbeddingConcurrencyController`，由应用启动时创建并注入所有 Provider，而不是在 Provider 构造函数内各自新建 Semaphore。

## 3.3 全局作业队列与公平调度

比单纯共享 Semaphore 更完整的方案是进程级队列：

```text
Task A misses ─┐
               ├─> Global Video Embedding Queue ─> N workers ─> Provider
Task B misses ─┘
```

队列要求：

- 不同任务 round-robin，避免一个 48 镜头任务长期阻塞另一个任务；
- 单任务内部优先处理预计较慢的长镜头（LPT），减少最后一批长尾；
- cache hit 不进入队列；
- 相同 key 先由 single-flight 合并；
- 任务取消后移除尚未启动的 job，运行中请求按 Provider 能力取消；
- 队列长度、等待时间和活跃 worker 数可观测。

## 3.4 429/限流的自适应并发

建议 AIMD（加性增大、乘性减小）：

- 初始全局并发 4；
- 连续 20 个请求成功、无 429、错误率低且 P95 未恶化时，并发 +1；
- 遇到 429 时，全局并发减半，最低 2；
- 读取并严格遵守 `Retry-After`；
- 没有 `Retry-After` 时使用指数退避 + 20% 随机抖动；
- 429 触发账号级共享 cooldown，不能只暂停单个请求；
- 5xx/网络错误按单请求重试，连续发生时再降低全局并发；
- 达到稳定窗口后缓慢恢复，不要立即回到最大值。

当前异常会在 `embed_video_corpus()` 中被吞成 `None`。需先保留结构化错误信息，否则控制器无法区分 429、超时、5xx 和输入错误。

## 3.5 重试模型

当前每个镜头最多尝试 3 次，固定 1、2 秒退避。建议：

- 4xx 输入错误：不重试；
- 401/403：轮换凭证一次，仍失败则停止该账号；
- 429：遵守 `Retry-After`，进入共享 cooldown 后重试；
- 5xx：指数退避 + jitter；
- connect/read timeout：有限重试，并记录阶段；
- 每个 job 保留总时间预算，避免 3 次 60 秒将单镜头拖到 3 分钟以上；
- 最终失败仍按现有逻辑回退文本向量，不阻断整任务。

## 3.6 超时配置

代表性视频请求曾接近 54–60 秒，而当前统一超时是 60 秒，容易在服务即将返回时触发昂贵重算。建议拆分：

```python
httpx.Timeout(connect=10, write=120, read=120, pool=30)
```

注意：提高 read timeout 不会使请求更快，只是减少临界超时和重复推理。最终值应根据新增指标的 P95/P99 设置，不能盲目无限增大。

## 3.7 Provider 级可观测性

每次尝试记录结构化字段：

```json
{
  "task_id": "...",
  "shot_id": 12,
  "cache_key_prefix": "ab12cd34",
  "attempt": 1,
  "queue_wait_ms": 120,
  "upload_bytes": 1320000,
  "provider_latency_ms": 48210,
  "status_code": 200,
  "request_id": "...",
  "global_limit": 4,
  "active_requests": 4,
  "cache": "miss"
}
```

抓取响应头中 Provider 实际返回的 request/trace ID；不存在时记录本地 UUID。日志不得写 API Key、完整 Authorization 或 Base64 视频正文。

核心指标：

- cache hit/miss/single-flight wait；
- Provider 请求数与节省请求数；
- queue wait P50/P95；
- Provider latency P50/P95/P99；
- 429、5xx、timeout、重试次数；
- 当前全局并发和降级次数；
- 每任务视频阶段墙钟；
- 每镜头上传字节和总费用估算。

## 3.8 并发验证方法

缓存先上线，再测试冷 miss 的并发，避免缓存命中掩盖真实配额。

固定同一套未命中 key 的镜头，分别测试全局 4、6、8，每档至少 3 次：

| 验收项 | 要求 |
|---|---|
| 成功率 | ≥99%，最终失败仍可文本回退 |
| 429 | 0；若出现则该档不可作为固定默认 |
| P95 延迟 | 相比前一档恶化不超过 20% |
| 吞吐 | 并发提升后请求/分钟必须实际增加 |
| 召回质量 | 与并发 4 完全相同；并发不能改变向量 |
| 双任务总并发 | 始终不超过全局 limiter |
| 取消 | 不遗留队列 job 或损坏缓存 |

按最新 48 miss、约 470 秒窗口做理想估算：

- 全局 4：12 批，实测约 470 秒；
- 全局 6：8 批，若单批延迟不变，约 313 秒；
- 全局 8：6 批，若单批延迟不变，约 235 秒。

这些只是理论上限，实际值必须以 429、P95 和吞吐实测为准。

## 3.9 已实施结果

已完成：

- 新增账号级、事件循环内共享的全局并发控制器；
- 保留每任务 limiter，并要求请求同时取得每任务和全局 slot；
- 全局等待队列按 task ID 轮转；
- 单任务按代理文件大小降序提交，降低最后一批长尾；
- HTTP 429 解析 `Retry-After`、触发共享冷却并将并发减半；
- 连续 3 次 5xx/网络瞬时错误触发保守降并发；
- 成功窗口支持 AIMD 加并发和 P95 恶化回滚；
- connect/write/read/pool 分阶段超时和单镜头总时间预算；
- 指数退避增加 20% 随机抖动；
- 每次尝试写入 `video_embedding_metrics.ndjson`；
- Pipeline Provider 注入 task ID/目录，多个任务共享同一账号 limiter；
- 实现版本升级为 `2026.07.25.3`。

受控真实 Provider 压测使用 8 个有效代理镜头，每档运行 1 次：

| 全局/单任务并发 | 成功 | 429 | 超时 | 墙钟 | 单请求 P50 | 单请求最大值 |
|---:|---:|---:|---:|---:|---:|---:|
| 4 | 8/8 | 0 | 0 | 64.128s | 31.015s | 52.641s |
| 6 | 8/8 | 0 | 0 | 49.361s | 33.148s | 49.328s |
| 8 | 8/8 | 0 | 0 | 46.683s | 29.547s | 46.656s |

与并发 4 返回向量的最小余弦相似度：并发 6 为 0.99841223，并发 8 为 0.99663718。模型结果存在轻微非确定性，但未观察到并发引入的格式、维度或召回接口错误。

这只是单轮、8 镜头的受控验证，不满足“每档至少 3 次”的生产默认升级门槛。因此：

- 当前默认账号全局并发仍保持 4；
- 自适应升并发默认关闭；
- 并发 6/8 已证明具备进一步灰度价值，但需累计至少 3 轮冷 miss 测试以及真实双任务测试后再改默认值；
- 无论未来单任务配置如何，两个同时运行任务的总并发现在都受账号级 limiter 控制，不会再简单相乘。

---

## 4. 推荐实施顺序

### 阶段 A：先补观测和固定全局并发

1. 引入全局 limiter，默认 4；
2. 保留每任务上限 4；
3. 结构化保存状态码、request ID、延迟、重试；
4. 处理 `Retry-After` 和 jitter；
5. 验证双任务总并发不超过 4。

风险最低，可立即防止账号过载。

### 阶段 B：纯视频向量与跨任务缓存

1. 上传时保存原素材 SHA-256；
2. 新增 `video_only_v2` Provider 请求；
3. SQLite 内容寻址缓存；
4. cache lookup 前置到代理生成之前；
5. single-flight；
6. 每镜头增量 checkpoint；
7. 保留旧模式 feature flag 方便回滚。

### 阶段 C：质量验收

在接受样本和新增采访/弱光/远景样本上比较：

- 参考镜头 Recall@10、候选召回；
- 显式实体覆盖；
- MRR；
- LLM 最终选择；
- fallback 数；
- 冷/暖阶段 6 耗时。

最低门禁：现有接受样本参考候选召回保持 12/12，显式实体 5/5。

### 阶段 D：提高全局并发

按 4 → 6 → 8 灰度测试。只有配额、错误率和 P95 都通过才提升默认值；失败时只回滚并发配置，不回滚缓存。

---

## 5. 建议配置

```dotenv
# 跨任务纯视频向量缓存
VIDEO_EMBEDDING_CACHE_ENABLED=true
VIDEO_EMBEDDING_CACHE_PATH=data/cache/video_embeddings_v2.sqlite3
VIDEO_EMBEDDING_CACHE_MAX_GB=10
VIDEO_EMBEDDING_CACHE_TTL_DAYS=90

# 账号级并发；先从 4 开始
VIDEO_EMBEDDING_PER_TASK_CONCURRENCY=4
VIDEO_EMBEDDING_GLOBAL_CONCURRENCY=4
VIDEO_EMBEDDING_GLOBAL_MAX_CONCURRENCY=8
VIDEO_EMBEDDING_ADAPTIVE_CONCURRENCY=true
VIDEO_EMBEDDING_MIN_CONCURRENCY=2
```

原 `VIDEO_EMBEDDING_CONCURRENCY` 可保留一个版本做兼容映射，之后弃用。

---

## 6. 测试清单

### 缓存正确性

- 同一源内容、区间和配置命中；
- 改 1 字节源文件必须 miss；
- 改 start/end、模型、维度、fps、token、指令或代理版本必须 miss；
- Vision/OCR/ASR 元数据变化时，纯视频缓存仍命中；
- 损坏 BLOB、NaN、维度不匹配自动删除并 miss；
- 相同 key 两任务并发只调用 Provider 一次；
- 删除任务不删除共享缓存；
- TTL/LRU 正确回收；
- 进程在第 N 个镜头退出后，前 N-1 个结果仍可命中。

### 并发正确性

- 两任务总 active 请求不超过全局值；
- 单任务不超过 per-task 值；
- 429 触发共享 cooldown 和并发减半；
- `Retry-After` 被遵守；
- jitter 避免同步重试；
- 任务取消清理等待 job；
- 多 Key 轮换不绕过账号全局 limiter；
- 并发 4/6/8 产出的向量和召回结果一致。

### 性能验收

- 完全重复素材暖缓存命中率 ≥95%；
- 暖缓存阶段 6 P50 <45 秒；
- 暖缓存不生成代理视频；
- 冷启动在无 429 前提下，提升并发后墙钟显著下降；
- 接受样本候选参考召回 12/12、显式实体 5/5。

---

## 7. 最终建议

优先级应为：

1. **先把当前每任务并发改成账号级全局并发 4，并补齐 429/延迟指标；**
2. **把视频向量改为纯视频表示并建立内容寻址的跨任务 SQLite 缓存；**
3. **缓存查询前置、single-flight、每镜头增量写入；**
4. **缓存稳定后，再通过 4/6/8 压测决定并发默认值。**

对重复素材，缓存的收益远大于提高并发：提高并发只能把 48 次请求做得更快，跨任务缓存则可以把 48 次请求直接降到 0，同时降低费用和限流风险。
