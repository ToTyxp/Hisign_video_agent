# 五桶数据缺口：映射与容量快照

本报告只读 SQLite。DEV/EVAL 是外部仪表盘目标；当前 v2 schema 没有 split 字段，因此不会把本地人工标签臆写为 DEV 或 EVAL。

| 桶 | 剩余缺口 | 映射状态 | 本地人工证据 | 点估计 / 95% Wilson 保守下载预算 |
| --- | ---: | --- | --- | --- |
| `fight_positive` | 15 | proxy_only | 4/12 | 45 / 109 |
| `protest_small_positive` | 53 | direct | 24/65 | 144 / 203 |
| `protest_large_positive` | 52 | unmapped | 无直接样本 | 待首批校准 |
| `fight_like_control` | 41 | direct_with_review | 7/12 | 71 / 129 |
| `protest_like_control` | 60 | unmapped | 无直接样本 | 待首批校准 |

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

- 映射：无现有 campaign/subtype
- 边界：sign_action_v1 明确硬排除大规模抗议；demand_action_v1 不记录人数尺度且处于 hold。
- 预算解释：零直接样本：先完成新定义下的首批人工校准，不能由其他桶借用命中率。

### `fight_like_control`

- 映射：fight_confounder_v1 / 四个非攻击性对照 subtype
- 边界：只计人工 task_usable=true；被纠正为打架/斗殴者不可计入对照。
- 预算解释：以人工 task_usable 为成功，预算是需要进入人工标注的技术成功下载数。

### `protest_like_control`

- 映射：无现有 campaign/subtype
- 边界：当前三个冻结 query pack 都没有“类抗议非抗议对照”的定义或人工标签。
- 预算解释：零直接样本：先完成新定义下的首批人工校准，不能由其他桶借用命中率。

## 技术成功（SQLite）

| Campaign | 已入队 | 技术成功下载 |
| --- | ---: | ---: |
| `demand_action_v1` | 15 | 11 |
| `fight_confounder_v1` | 15 | 12 |
| `sign_action_v1` | 91 | 91 |

## sign_action_v1 最新批次运行指标

- 累计人工可用：24/60；剩余 36。
- 累计人工任务可用率：24/60 = 40.0%（按全部 65 个已展示候选保守计为 36.9%）；来源正确率：29/62 = 46.8%。
- 候选预算：已消费 91/180；余量 89；余量所需人工可用率 40.4%。
- Manifest：91/91 必填字段完整，91 个唯一 candidate key，91 个唯一 SHA-256。
- SHA-256 重复边：0。
- 最新批次 `0875d625-72be-4de5-af01-b8fcd95ed805`：二次筛选 16/20 = 80.0%；技术成功 16/16。
- 该批人工反馈：0 条；尚未形成该批 task/source 人工可用率（零标签不按 0% 处理）。
