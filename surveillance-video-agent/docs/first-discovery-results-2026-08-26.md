# 首轮双 Campaign 真实发现结果（2026-08-26）

状态：`stopped_for_query_pack_revision`  
下载：未启动  
来源门槛：保持 4，未降低  
probe 预算：两个 Campaign 均按版本累计用满 150

## 汇总

| 指标 | fight_confounder_v1 | demand_action_v1 |
|---|---:|---:|
| 冻结查询 | 36 | 27 |
| 三平台搜索请求 | 108/108 成功 | 81/81 成功 |
| 搜索命中 | 1,443 | 1,006 |
| Campaign 内唯一候选 | 1,322 | 814 |
| 搜索字段便宜硬排除 | 28 | 19 |
| probe 选择 | 150 | 150 |
| probe 成功 | 86 | 76 |
| probe 失败 | 64 | 74 |
| 来源合格 | 80 | 52 |
| 资源合格 | 77 | 67 |
| 任务合格记录 | 1 | 0 |
| 校准导出 | 冲突但未攻击 1 条 | 0 条 |

跨 Campaign 合并后 SQLite 中共有 2,042 个候选事实、132 个 `source_qualified`、144 个资源合格记录。候选状态没有进入 `task_queued` 或任何下载终态。

## probe 失败与平台偏斜

首轮 probe 前排序优先消费廉价来源高分，造成 Dailymotion 过度集中：

- fight：Dailymotion 101、YouTube 48、PeerTube 1；
- demand：Dailymotion 117、YouTube 33、PeerTube 0；
- Dailymotion 失败主要为 network、tool_error 和 not_found。

该选择事实不可删除或无审计重排。后续版本已修正为“每个平台内部按廉价来源分排序，平台之间轮转”，仍不设硬配额、不降低来源门。

## 任务门诊断

来源合格样本中大量是：

- 泛摄像头页面或只含“camera/surveillance”字样的记录；
- 摄像头安装、设置或设备内容；
- 新闻/评论包装；
- 搜索引擎仅因来源词命中，但标题、简介和标签不含目标动作。

因此任务分 0/2 是正确拒绝，不应把发现查询本身当成任务命中证据，也不应降低任务阈值 4。

## 停止结论

现有查询包无法为任一 subtype 提供至少 30 个可校准候选，更不可能满足每 subtype 至少 10 个正例/10 个 hard negative 的标签门。两个 Campaign 的 active Frontier Policy 保持 `null`，Batch 和下载均不可启动。

下一步必须创建新的 query-pack version，保持冻结中文需求不变，修订平台检索表达和更精确的动作/场景查询；不得复用旧版本的 150 probe 预算，不得降低来源、任务或未来向量阈值。

## 审计路径

- fight run：`discovery-9fc11408-9e88-4408-8593-b9a5f1977dcd`
- demand run：`discovery-69ca91a8-ad10-475a-86f5-10958e840dfb`
- 正式 SQLite：`.surveillance-pool/state/candidates.sqlite3`
- fight calibration：`.surveillance-pool/runs/discovery-9fc11408-9e88-4408-8593-b9a5f1977dcd/calibration/fight_confounder_v1.jsonl`
- demand calibration：`.surveillance-pool/runs/discovery-69ca91a8-ad10-475a-86f5-10958e840dfb/calibration/demand_action_v1.jsonl`

## v1.1 修订复跑

用户确认保持中文定义、动作词和评分阈值不变，只追加本语言原始录像线索并修复平台严格轮转。新 query-pack version 各使用独立 150 probe 预算，按 fight → demand 串行完成。

| 指标 | fight v1.1 | demand v1.1 |
|---|---:|---:|
| 查询 | 36 | 27 |
| 搜索请求成功 | 108/108 | 81/81 |
| 唯一候选 | 1,024 | 624 |
| probe 分布 | Dailymotion 75 / YouTube 75 | Dailymotion 75 / YouTube 75 |
| probe 失败 | 44 | 47 |
| 来源合格 | 65 | 32 |
| 资源合格 | 86 | 72 |
| 任务合格 | 0 | 0 |
| 校准导出 | 0 | 0 |

轮转修复有效降低了失败并增加 YouTube 覆盖，但任务池仍为 0，证明瓶颈不是平台偏斜或来源阈值，而是多语言任务词表只覆盖少量完整短语。继续创建仅修改检索表达的新 query-pack 不再有证据价值。

下一设计门应版本化扩展任务 aliases/conjunction 规则：保持分值 `标题 +4 / 描述标签 +2` 和阈值 4 不变，由冻结中文概念派生更完整的 en/es/fr 同义表达；冲突未攻击、下跪诉求和场景先验必须使用正负概念组合，不能用单一宽词绕过语义边界。

v1.1 审计：

- fight run：`discovery-9900c00a-6378-4c91-ad67-8bc57aa26d49`
- demand run：`discovery-7c3f4d3f-1bca-403d-9c2c-cf1835f6108e`
- fight pack：`fight_confounder_v1.qp.v1.1.0`
- demand pack：`demand_action_v1.qp.v1.1.0`

## 评分策略 v1.1 离线重评分

用户确认保持所有分值和阈值不变，从冻结中文定义派生多语言 aliases、同字段组合条件与明确反例词。`surveillance_scoring_v1.1.0` 对现有 229 个 `source_qualified` 候选进行了离线原子重评分，没有新增平台请求、Qwen 调用或下载。

结果：

| Campaign/subtype | 任务合格数 |
|---|---:|
| demand / 举牌横幅 | 0 |
| demand / 下跪 | 0 |
| demand / 静坐 | 0 |
| fight / 冲突但未攻击 | 1 |
| fight / 舞蹈玩闹训练 | 7 |
| fight / 非攻击性身体接触 | 1 |
| fight / 场景先验 | 0 |

重评分后仍没有任何 subtype 达到 30 条校准最低样本。继续加入更宽 aliases 会开始把普通摄像头内容、真实攻击或非诉求动作引入任务池，违背冻结中文定义。

当前安全停止点：不生成生产 Qwen 候选投影、不激活 Frontier、不创建 Batch、不下载。若继续，必须显式修订架构，允许对全部“来源合格 + 资源合格”候选执行 calibration-only 语义召回；该召回只导出人工标签池，不能直接赋予任务合格状态或进入下载。

重评分 run：`rescore-abc0a61a-c795-4735-af08-392c44298f46`

## Calibration-only 语义召回与 v1.2 数据收集

用户确认 calibration-only 语义召回，但不允许其修改 task score、候选状态、Frontier 或下载队列；随后要求不再放宽语义，只收集更多数据。

首轮语义召回：

- 来源合格且资源合格：208 条；
- Qwen document batch：11 次；
- demand：3 subtype × Top-50 = 150 对；
- fight：4 subtype × Top-50 = 200 对；
- 使用独立 calibration Qdrant collection；生产 `candidate_embeddings` 不受影响。

v1.2 保持动作词、来源锚点、分值和阈值不变，只增加多语言地点切片，并跳过所有已评分候选与永久 probe 失败：

| 指标 | fight v1.2 | demand v1.2 |
|---|---:|---:|
| 搜索成功 | 108/108 | 81/81 |
| 新候选 | 854 | 441 |
| probe 分布 | Daily 75 / YouTube 75 | Daily 75 / YouTube 75 |
| probe 失败 | 28 | 28 |
| 来源合格 | 33 | 8 |
| 资源合格 | 85 | 75 |
| 任务合格 | 舞蹈/训练 1 | 0 |

增量语义召回后，来源+资源合格交集为 248 条：旧向量缓存命中 208、新生成 40，仅新增 2 次 Qwen batch 调用。Top-50 标签池已按新数据重建。

当前停止原因不再是代码或数据抓取故障，而是缺少外部人工 `usable` 标签。系统不得自动猜测这些标签或用相似度分布自行冻结阈值。

最新标签池：

- demand：`.surveillance-pool/runs/semantic-recall-e41c6771-1c87-4c97-a9ea-639f37041962/semantic-recall/demand_action_v1.jsonl`
- fight：`.surveillance-pool/runs/semantic-recall-e41c6771-1c87-4c97-a9ea-639f37041962/semantic-recall/fight_confounder_v1.jsonl`
- semantic recall run：`semantic-recall-e41c6771-1c87-4c97-a9ea-639f37041962`
