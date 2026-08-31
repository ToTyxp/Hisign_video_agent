# 五桶数据缺口：映射与容量快照

本报告只读 SQLite。DEV/EVAL 是外部仪表盘目标；当前 v2 schema 没有 split 字段，因此不会把本地人工标签臆写为 DEV 或 EVAL。

| 桶 | 剩余缺口 | 映射状态 | 本地人工证据 | 点估计 / 95% Wilson 保守下载预算 |
| --- | ---: | --- | --- | --- |
| `fight_positive` | 15 | proxy_only | 4/12 | 45 / 109 |
| `protest_small_positive` | 53 | direct | 24/65 | 144 / 203 |
| `protest_large_positive` | 52 | excluded_by_user | 无直接样本 | 待首批校准 |
| `fight_like_control` | 41 | direct_with_review | 7/12 | 71 / 129 |
| `protest_like_control` | 60 | excluded_by_user | 无直接样本 | 待首批校准 |

## 证据与边界

### `fight_positive`

- 映射：仅有 fight_confounder_v1 中被人工纠正为打架/斗殴的反例
- 边界：不存在独立的 fight-positive campaign、正样本查询包或 DEV/EVAL 分配。
- 预算解释：代理证据不可用于自动放宽门槛；未映射桶必须先有新冻结定义和首批人工校准。

### `protest_small_positive`

- 映射：sign_action_v1 / 举牌/横幅
- 边界：仅 1–5 名直接参与者；大规模游行/密集群众是硬排除。
- 预算解释：以人工 task_usable 为成功，预算是需要进入人工标注的技术成功下载数。

### `protest_large_positive`

- 映射：不纳入当前下载范围
- 边界：用户已明确排除；不得搜索、probe、激活或下载。
- 预算解释：用户已排除，不建立统计容量或下载队列。

### `fight_like_control`

- 映射：fight_confounder_v1 / 四个非攻击性对照 subtype
- 边界：只计人工 task_usable=true；被纠正为打架/斗殴者不可计入对照。
- 预算解释：以人工 task_usable 为成功，预算是需要进入人工标注的技术成功下载数。

### `protest_like_control`

- 映射：不纳入当前下载范围
- 边界：用户已明确排除；不得搜索、probe、激活或下载。
- 预算解释：用户已排除，不建立统计容量或下载队列。

## 技术成功（SQLite）

| Campaign | 已入队 | 技术成功下载 |
| --- | ---: | ---: |
| `demand_action_v1` | 15 | 11 |
| `fight_confounder_v1` | 40 | 37 |
| `sign_action_v1` | 100 | 100 |

## sign_action_v1 最新批次运行指标

- 累计人工可用：24/60；剩余 36。
- 累计人工任务可用率：24/60 = 40.0%（按全部 65 个已展示候选保守计为 36.9%）；来源正确率：29/62 = 46.8%。
- 候选预算：已消费 100/180；余量 80；余量所需人工可用率 45.0%。
- Manifest：100/100 必填字段完整，100 个唯一 candidate key，100 个唯一 SHA-256。
- SHA-256 重复边：0。
- 最新批次 `209a4054-533c-4572-b934-50296085ef6a`：二次筛选 0/12 = 0.0%；技术成功 0/0。
- 该批人工反馈：0 条；尚未形成该批 task/source 人工可用率（零标签不按 0% 处理）。
