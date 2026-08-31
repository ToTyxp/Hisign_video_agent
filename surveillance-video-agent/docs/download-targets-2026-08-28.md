# 三个下载目标进度

日期：2026-08-28  
口径：只统计 SQLite 中状态为 `downloaded`、已发布且技术检查完整的唯一视频。

| 数据桶 | v2 Campaign | 目标 | 当前成功 | 剩余 |
| --- | --- | ---: | ---: | ---: |
| 小规模抗议正样本 | `sign_action_v1` | 300 | 122 | 178 |
| 打架正样本 | `fight_positive_v1` | 60 | 60 | 0 |
| 类打架对照 | `fight_confounder_v1` | 120 | 89 | 31 |

`protest_large_positive` 与 `protest_like_control` 继续明确排除，不建立查询、probe、Frontier 或下载队列。

## 打架正样本独立定义

`fight_positive_v1` 只接受固定监控、安防摄像头或门铃摄像头中的真实身体攻击或互殴，包括拳打、脚踢、掌掴、摔打、扭打和多人群殴。排除口角未攻击、玩闹、舞蹈、训练/对练、事故、影视游戏动画、教程广告、新闻包装，以及以枪击或持刀袭击为主的事件。

- 查询包：`fight_positive_v1.qp.v1.0.0` 至 `v1.4.0`；
- 评分策略：`surveillance_scoring_v1.9.0`；
- 来源门：固定监控来源分 `>=4`；
- 原始语义门：`>=0.40`；
- 下载结果：60 个唯一 candidate key、60 个唯一 SHA-256；
- 未计入目标：1 条技术失败、3 条 SHA-256 重复抑制。

## 当前审计

- 三个 Campaign 均没有 running run 或 active download attempt；
- 成功 Manifest 完整率均为 100%；
- 全局 published media 的重复 SHA-256 组为 0；
- 完整自动化测试：148/148 通过；
- 不生成标注页、不导入人工标签、不写 `accepted`；
- 不使用 VLM、抽帧语义审核、字幕或 ASR。

## 后续执行

小规模抗议与类打架当前新池分别已增至 122 和 89。后续继续从冻结定义派生新 en/es/fr 查询版本，执行三平台 discovery、每版本最多 150 probe、原始语义门激活和每批最多 20 条的全局串行下载；不得降低来源或语义门。
