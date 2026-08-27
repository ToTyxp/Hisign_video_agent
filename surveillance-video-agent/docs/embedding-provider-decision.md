# 生产 embedding provider 决策

状态：`provider_smoke_passed`  
日期：2026-08-26  
决策人：user

## 1. 已确认 provider

| 字段 | 冻结值 |
|---|---|
| provider | `dashscope-sdk` |
| model | `qwen3.7-text-embedding` |
| dimensions | `1024` |
| distance | `cosine` |
| output type | `dense` |
| document role | `text_type=document` |
| query role | `text_type=query` |
| query instruct | `Given a surveillance-video task definition, retrieve public video metadata relevant to the described event subtype.` |
| embedding schema | `qwen3.7-text-embedding-dense-1024-dashscope-sdk-v1.0.0` |
| API credential | 项目 `.env` 中唯一变量 `DASHSCOPE_API_KEY` |
| SDK | `dashscope==1.27.1` |
| dotenv | `python-dotenv==1.2.3` |
| API base | `https://dashscope.aliyuncs.com/api/v1` |
| normalization | `unicode-nfc-whitespace-v1` |

官方文档显示该模型支持 256–2560 维，默认并推荐通用语义检索使用 1024 维；单批最多 20 条、每条最多 128,000 tokens，覆盖中文、英语、西班牙语、法语等 201 种语言和方言。

本项目使用用户指定的 `dashscope.TextEmbedding.call(...)` SDK 调用，而不是 OpenAI 兼容层，因为 DashScope SDK 支持 `text_type`、`instruct` 和 `output_type`。候选 relevance 和 duplicate 文本以 document role 生成；subtype 查询以 query role 和固定英文 instruction 生成。

## 2. 凭据与网络边界

1. API key 统一保存在项目根目录 `.env` 的 `DASHSCOPE_API_KEY`，不接受第二套 key 名称、构造参数或命令行参数。
2. `.env` 已被 Git 忽略且权限为 `0600`；已有 shell 环境变量优先，dotenv 不覆盖它。
3. 缺少 key 时在调用 SDK 前失败；SDK 每次调用前固定使用阿里云中国区官方 API base。
4. 错误日志只保存分类和 HTTP 状态，不保存 key、请求正文、候选简介或 API 原始错误正文。
5. 单批硬上限 20；响应必须严格匹配输入数量、`text_index`、1024 维有限数值，并在本地执行 L2 normalization。

## 3. 已修正的 schema 一致性问题

旧默认值声明 `nfkd-casefold-v1`，但代码只执行 `strip()`。生产 schema 已改为并真实执行 `unicode-nfc-whitespace-v1`：

- Unicode NFC；
- CRLF/CR 统一为 LF；
- 行内连续空白折叠；
- 删除空行和首尾空白；
- 保留大小写、重音符号和字段标签。

模型、维度、查询 instruction、文本模板或规范化规则发生变化时必须创建新 embedding schema 和 Qdrant collection，不能覆盖本版本。

## 4. 尚未冻结的阈值

官方文档明确说明相似度没有通用固定阈值，应根据业务数据校准。因此本决策不填写经验阈值：

- relevance 阈值按 `(embedding_schema_version, campaign_id, subtype)` 校准；
- 每 subtype 样本不足时停止，不启用真实二筛；
- duplicate 阈值必须与标题相似度和时长容差联合校准；
- 向量只生成疑似重复证据，不直接删除；
- 任一不足不得降低来源门槛 4。

## 5. API smoke 与证据

统一凭据文件为：

```text
/Users/yangxp/Desktop/Work_hisign/ytb异常视频下载/surveillance-video-agent/.env

DASHSCOPE_API_KEY=由用户填写
```

smoke 只发送少量合成的中/英/西/法文本，验证 model ID、1024 维、document/query role、Qdrant 写入与回查；不得发送真实候选元数据。

2026-08-26 真实 smoke 已通过：

- `qwen3.7-text-embedding` document 4 条、query 1 条；
- 所有向量 1024 维并通过 L2 normalization 校验；
- 临时 Qdrant 写入 4 条、回查 4 条；
- `real_candidate_metadata_sent=false`；
- `temp_cleaned=true`；
- 合成“冲突但未攻击”查询将英文对应文档排在第一位，cosine `0.714840`；西班牙语、法语和中文非对应文档依次返回，证明多语言最小路径可用。

该 smoke 只证明 provider、角色参数和 Qdrant 技术路径可用，不构成业务阈值或 pilot 指标证据。

### 5.1 subtype 查询向量 smoke

`semantic-subtype-query-v1.0.0` 已完成真实 API 验证：

- `demand_action_v1` 生成 3 条 subtype 查询向量；
- `fight_confounder_v1` 生成 4 条 subtype 查询向量；
- 所有向量均为 1024 维；
- 两个 campaign 合计只进行 2 次 API 调用；
- 第二次准备时 7 条全部从 Qdrant 回读，未重复调用 API；
- `candidate_metadata_sent=false`、`temp_cleaned=true`。

SQLite 保存模板、instruction、query text、输入哈希和 Qdrant point 状态，但不保存向量正文。该结果仍不提供 relevance 或 duplicate 业务阈值。

## 6. 官方依据

- 千问文本向量文档：<https://platform.qianwenai.com/docs/developer-guides/embeddings/embedding>
- 阿里云同步 API：<https://help.aliyun.com/en/model-studio/text-embedding-synchronous-api>
