# Surveillance Video Agent

监控候选池 v2 的多平台候选工作流。当前已实现三平台适配器、SQLite 控制面、有限 probe 调度、来源/任务评分、Qdrant 派生索引、Qualified Frontier、Secondary Batch、串行下载、技术验证和 Manifest 重建。

## 离线回归

```bash
uv sync
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

测试全部使用 fake runner/HTTP client，不访问网络，也不下载视频。

## 在线 smoke

在线 smoke 现在使用正式 CLI，不再依赖临时长脚本。下面示例只做 `search → probe`：

```bash
PYTHONPATH=src python3 -m surveillance_video_agent.smoke \
  --query-pack query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.0.0.draft.json \
  --query-id fcv1-conflict-no-attack-en-01 \
  --limit 3 \
  --candidate-index 1 \
  --peertube-instance peertube.social
```

只有显式添加 `--download` 才会尝试下载。下载 smoke 固定顺序执行，并默认限制为最高 360p、最大 50 MiB：

```bash
PYTHONPATH=src python3 -m surveillance_video_agent.smoke \
  --query-pack query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.0.0.draft.json \
  --query-id fcv1-conflict-no-attack-en-01 \
  --limit 3 \
  --peertube-instance peertube.social \
  --download
```

安全规则：

- 必须使用内部状态为 `frozen` 的查询包和 `network_config=default`。
- `network_config=default` 继承启动进程的既有网络环境；程序不会主动设置、删除或切换代理，并且报告只记录代理变量名称，不记录地址。
- PeerTube 只释放明确 allowlist 中的实例；未知实例不会被 probe 或 download。
- 每个平台最多选择一个候选，三个平台下载全局串行。
- `--candidate-index` 可以在已返回的 Top-K 中选择一个明确候选；它不会增加下载数量。
- 只有 probe 未明确显示非公开或直播、时长 10–900 秒，且已知估计大小不超过 smoke 上限时才下载。成功的无认证 probe 可作为公开可访问证据；平台未提供 availability、直播标志或大小估计时允许受限尝试，但 yt-dlp 硬上限、适配器下载后检查和临时目录清理仍会阻止超限文件进入产物。
- 临时媒体默认自动清理；`--keep-temp` 仅用于故障诊断。
- 技术检查只执行 ffprobe、视频流存在性及首/中/尾解码，不做语义审核。

2026-08-26 的首次独立 smoke 曾强制清空进程已有代理变量，导致 YouTube 与 Dailymotion 裸直连超时；这不代表适配器故障。后续 smoke 应继承启动环境，但仍不得由应用代码主动切换代理、VPN或 cookies。PeerTube 的 allowlist 仍必须显式传入并记录。

## 已验证的在线结果

2026-08-26 在继承启动环境、无应用级网络覆盖的条件下，三个适配器均完成了真实 `search → probe → download → technical_check`：

| 平台 | Candidate key | 下载字节 | 输出分辨率 | 技术检查 |
|---|---|---:|---:|---|
| YouTube | `youtube:j1KPJf-LXH4` | 2,263,144 | 640×360 | ffprobe、视频流、首/中/尾解码通过 |
| Dailymotion | `dailymotion:x853l98` | 1,119,431 | 216×384 | ffprobe、视频流、首/中/尾解码通过 |
| PeerTube | `peertube:8dc076a0-45bf-4a11-9a69-5f4b49f6d764` | 9,114,981 | 640×360 | ffprobe、视频流、首/中/尾解码通过 |

Dailymotion 搜索使用官方公开 Platform API；probe/download 仍由 yt-dlp 完成。三个 smoke 均使用临时目录，报告 `temp_cleaned=true`，未保留媒体文件。

## 来源门与任务评分

评分实现位于 `surveillance_video_agent.scoring`，输入仅包含公开 probe 元数据：标题、视频简介、标签、上传者、频道和播放列表。

```python
from pathlib import Path
from surveillance_video_agent.scoring import (
    CandidateMetadata,
    load_scoring_bundle,
    score_all_tasks,
    score_source,
)

bundle = load_scoring_bundle(
    Path("query-packs/scoring-policy.v1.0.0.draft.json"),
    (
        Path("query-packs/demand_action_v1/demand_action_v1.qp.v1.0.0.draft.json"),
        Path("query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.0.0.draft.json"),
    ),
)
candidate = CandidateMetadata(
    candidate_key="youtube:example",
    title="Uncut CCTV kneeling protest",
)
source = score_source(candidate, bundle)
tasks = score_all_tasks(candidate, source, bundle)
```

规则保持固定顺序：硬排除 → 来源评分/阈值4 → 独立任务评分/阈值4。任务高分不能覆盖来源门。评分策略 `surveillance_scoring_v1.0.0` 已由用户确认并冻结；任何词表或分值变化必须创建新版本。

## SQLite 控制面

`CandidateDatabase` 提供 v1 schema 初始化和事务入口。正式路径固定为项目根目录下 `.surveillance-pool/state/candidates.sqlite3`；测试只使用临时目录。

```python
from pathlib import Path
from surveillance_video_agent import CandidateDatabase

with CandidateDatabase(Path(".surveillance-pool/state/candidates.sqlite3")) as database:
    database.initialize()
    database.create_run("run-id", "discovery")
```

当前 schema 包含候选、冻结查询包、五元搜索缓存、probe缓存、评分证据、状态审计、Frontier、Secondary Batch、Qdrant outbox、重复簇、下载尝试、技术检查和可恢复发布意图。关键约束：

- `candidate_key = platform:source_id`。
- 状态只能按 `discovered → source_qualified → task_queued → downloaded | technical_failed | duplicate_suppressed` 前进。
- `task_queued` 必须已有任务分、Secondary Batch决定和队列归属。
- 冻结查询包、状态转换和评分证据不可原地修改。
- 注册查询包和评分策略时重新计算冻结哈希。

## 三平台发现与有限 probe

`DiscoveryService` 将撒网和昂贵资格计算拆为两个可独立重启的入口：

```python
discovery = service.discover(run_id=run_id, config=config)
qualification = service.qualify(run_id=run_id, config=config)
```

`discover` 对三个平台并行搜索，每个平台最多两个在途请求、每条查询最多前 20 条；网络线程不写 SQLite。主线程写入候选、查询归因和五元缓存，并先执行搜索字段可证明的硬排除。

`qualify` 从 SQLite 对尚未选择的候选排序，把选择事实持久化到 `probe_selections`，再消费 probe。默认 150 条是 `(campaign_id, query_pack_version)` 的累计唯一候选上限，重启或缓存命中不会重置预算；失败选择也不会自动重新排队。probe 完成后才使用视频简介、标签、频道和播放列表执行完整来源门与独立任务评分。所有搜索和 probe 都写入不可变 `adapter_calls` 审计记录。

### 完整发现在线 smoke

正式 smoke 会在临时 SQLite 中执行三个平台的 `search → discover → limited probe → qualify`，没有下载入口：

```bash
PYTHONPATH=src python3 -m surveillance_video_agent.discovery_smoke \
  --query-pack query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.0.0.draft.json \
  --scoring-policy query-packs/scoring-policy.v1.0.0.draft.json \
  --scoring-query-pack query-packs/demand_action_v1/demand_action_v1.qp.v1.0.0.draft.json \
  --scoring-query-pack query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.0.0.draft.json \
  --query-id fcv1-conflict-no-attack-en-01 \
  --peertube-instance peertube.social \
  --peertube-instance koreus.tv \
  --limit 3 \
  --probe-limit 9
```

2026-08-26 的真实运行继承本机已有网络环境，三个搜索调用全部成功：YouTube `3/3` probe 成功、Dailymotion `2/3` probe 成功、PeerTube `1/1` probe 成功；共发现 7 个候选、来源合格 2 个、任务分合格 1 个。smoke 的三平台最小路径通过，`download_attempted=false`，临时数据库自动清理。`koreus.tv` 是此前已完成真实 PeerTube 下载和技术验证的实例；其余 Sepia Search 返回实例没有自动加入 allowlist。

## Qdrant 投影与 Qualified Frontier

项目固定 `qdrant-client==1.19.0`，使用本地磁盘模式；正式路径为 `.surveillance-pool/vector/qdrant/`。SQLite 始终是控制面，Qdrant 数据可通过 outbox 重建。

已实现：

- `relevance` 与 `duplicate` 两个 named vectors。
- embedding schema、输入模板和模型身份版本化。
- `candidate_key + schema version` 派生稳定 UUIDv5 point ID。
- SQLite outbox 的 pending/processing/completed/failed/superseded 生命周期。
- 元数据变化产生新投影 revision，旧事件不能覆盖新向量。
- 只有来源合格且至少一个任务合格的候选才生成向量。
- 向量近重复必须同时满足模型阈值、标题相似度和时长容差，且只生成疑似簇，不直接删除候选。
- dedupe refresh 完成后才能刷新 Qualified Frontier；重复簇不在这一阶段预选代表。

生产 provider 已由用户选择为 DashScope SDK 的 `qwen3.7-text-embedding`，固定 dense 1024 维、cosine 和 `unicode-nfc-whitespace-v1` schema。项目统一从根目录 `.env` 读取唯一变量 `DASHSCOPE_API_KEY`；缺少 key 时不会调用 SDK。候选使用 `text_type=document`，subtype 查询使用 `text_type=query` 和版本化 instruction。真实合成 API + 临时 Qdrant smoke 已通过；尚未根据人工标签冻结 subtype 语义阈值，因此仍禁止正式候选二筛。

API key 由用户填写到以下文件，该文件已被 Git 忽略且权限为 `0600`：

```text
/Users/yangxp/Desktop/Work_hisign/ytb异常视频下载/surveillance-video-agent/.env

DASHSCOPE_API_KEY=你的 key
```

填写后运行：

```bash
.venv/bin/surveillance-embedding-smoke
```

该 smoke 只向 API 发送 4 条合成的中/英/西/法文本和 1 条合成查询，并在临时 Qdrant 中验证 1024 维向量写入与回查；不会发送真实候选元数据，临时索引默认自动清理。

2026-08-26 的真实结果：document `4`、query `1`、Qdrant 写入/回查 `4/4`，合成目标英文文档排名第一（cosine `0.714840`），`real_candidate_metadata_sent=false`、`temp_cleaned=true`。

### 版本化 subtype 查询向量

`semantic-subtype-query-v1.0.0` 从冻结 query pack 确定性构造 subtype 查询文本，包含 campaign/subtype、中文定义与概念，以及 en/es/fr 动作词；来源锚点不会进入查询文本，因为候选必须已经通过来源门。查询文本、instruction、模板、schema 与 SHA-256 写入 SQLite，向量正文只存 Qdrant。

```bash
.venv/bin/surveillance-semantic-query-smoke
```

2026-08-26 的真实 smoke 为两个 campaign 生成 `3+4=7` 条 1024 维查询向量，只产生 2 次 API 调用；同一进程第二次准备时 `7/7` 从 Qdrant 回读，API 调用数没有增加。`candidate_metadata_sent=false`、`temp_cleaned=true`。

## Secondary Batch Generator

`batch_generator.py` 从 SQLite 固定的 ready Frontier 快照中按 subtype 调用 Qdrant，先应用模型专属语义阈值，再使用 Reciprocal Rank Fusion 合并：

```text
task_score/source_score 确定性排名
+ relevance 向量排名
→ RRF
→ subtype缺额轮转
→ platform/lang轮转
→ uploader与duplicate cluster限制
→ 最多20条不可变 Secondary Batch
```

生成和租约写入在 SQLite `BEGIN IMMEDIATE` 中重新检查候选状态、任务分、Campaign容量、上传者额度和重复簇。同一 run/Campaign 同时只允许一个未完成批次。向量阈值以下的候选不能被确定性高分重新带回。

## 串行下载、技术终态和 Manifest

`download_pipeline.py` 只允许一个全局 `running` attempt。Secondary Batch候选先在事务中重新检查 subtype容量并进入 `task_queued`，随后由单 worker 依次下载。

下载器固定调用项目 `.venv/bin/yt-dlp`，当前 lock 为 nightly `2026.08.25.233329`，并安装 `curl_cffi`/`yt-dlp-ejs`。提取请求间隔 1 秒，HTTP/fragment/extractor 使用指数退避，fragment并发为1；worker 对 network、rate-limit、timeout 额外执行最多2次20–60秒退避，每次重试写入 `download_retry_events`。YouTube 强制 IPv4并启用 Node JS runtime，但不覆盖 yt-dlp 默认 player client。

下载后只执行：

- ffprobe成功
- 视频流存在
- 首/中/尾解码
- SHA-256与文件大小计算

技术通过后先写 `media_publish_intents`，再原子移动文件，最后提交 `downloaded`；崩溃重启时先恢复 pending intent。相同 SHA-256 的第二个文件进入隐藏 quarantine 并提交 `duplicate_suppressed`，不会产生第二份公开媒体。

`manifest.py` 从 SQLite 确定性、原子重建 Campaign JSONL，覆盖所有进入 `task_queued` 的候选，包括技术失败与重复抑制。测试会检查每行必备键100%存在。

## 正式 v2 CLI

统一入口：

```bash
.venv/bin/surveillance-v2 --help
```

主要阶段：

```bash
# 初始化 SQLite、冻结策略和只读旧数据迁移
.venv/bin/surveillance-v2 init

# 查看安全状态、运行和开放批次
.venv/bin/surveillance-v2 status

# 三平台发现、有限 probe、候选向量投影并导出待标注校准 JSONL
.venv/bin/surveillance-v2 discover \
  --campaign fight_confounder_v1 \
  --peertube-instance peertube.social \
  --peertube-instance koreus.tv

# 外部人工把 calibration JSONL 的 usable 填成 true/false 后校准
.venv/bin/surveillance-v2 calibrate \
  --campaign fight_confounder_v1 \
  --labels /absolute/path/to/labeled.jsonl

# 只生成并审计 Secondary Batch，默认绝不下载
.venv/bin/surveillance-v2 batch --campaign fight_confounder_v1
```

真实下载必须额外同时提供 `--enable-downloads --confirm-downloads DOWNLOAD`。没有通过全部 subtype 校准、Qdrant 覆盖不完整、低有效率停止、资源门失败或存在未恢复发布意图时，CLI 会停止而不是放宽门槛。

正式状态库已初始化在 `.surveillance-pool/state/candidates.sqlite3`。只读迁移验证结果：旧 YouTube ID `1002` 条，其中 `accepted=957`、`downloaded=45`；生成 accepted-only 上传者正先验 `751` 个，缺少旧 metadata `26` 条。旧拒绝原因和频道封禁均未迁移。

## 校准与安全停止

- relevance 阈值按 Qwen schema + campaign + subtype 独立保存。
- 每 subtype 默认至少 30 个标签、10 个正例和 10 个 hard negative，并按 uploader 分组拆分训练/评估。
- evaluation 必须达到 usable recall `>=90%` 且 precision `>20%`；任一 subtype 不足会清空整组阈值并阻止 Frontier 激活。
- Batch 同时记录 `download_eligible` 与 `below_semantic_threshold`，所以 secondary yield 是真实比例而不是固定 100%。
- query/subtype 连续 3 个完整窗口 `<10%` 会暂停该分区；Campaign 连续 3 批 `<10%` 会停止。
- vector duplicate 尚无人工 pair 阈值时使用显式 `vector_enabled=false` 策略，只保留 candidate key、旧 ID 阻断和下载后 SHA-256 去重，不以经验阈值抑制候选。
- probe 后和下载后都执行资源门：10–900 秒、非直播、最高 1080p、单文件最多 2 GiB、Campaign 最多 30 GiB。

2026-08-26 的首轮双 Campaign 真实发现已按预算串行完成，但任务合格仅为 fight 1 条、demand 0 条，两个校准池均不足。系统已按契约停止在 query-pack 修订门，未生成 Batch、未下载。完整证据见 [`docs/first-discovery-results-2026-08-26.md`](docs/first-discovery-results-2026-08-26.md)。

后续 v1.1 查询复跑实现了平台 75/75 轮转但任务合格仍为 0；评分策略 v1.1 对 229 个来源合格候选扩展受约束多语言 aliases 后，最多的 subtype 也只有 7 条。生产路径继续保持停止状态，等待是否采用 calibration-only 语义召回的架构决策。

该架构已获用户确认并实现：248 条“来源合格 + 资源合格”元数据进入独立 calibration Qdrant；首轮可视 Pilot 下载10条、技术成功4条，人工标注4条。反馈策略 `pilot-feedback-semantic-gate-v1.1.0` 将语义补录来源分提高至6，demand阈值提高至`>0.44`，fight保留`>0.40`并为“冲突但未攻击”加入真实攻击负词门；131个唯一候选收紧为72个。历史失败不自动重排队，下一轮扩量前仍必须满足技术成功率`>90%`。
