"""Export the auditable five-bucket mapping and conservative capacity snapshot.

This is deliberately read-only with respect to SQLite.  It makes the boundary
between the external DEV/EVAL dashboard and locally human-labelled candidates
explicit: the current v2 schema has no split-assignment column, so this report
never invents one.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / ".surveillance-pool/state/candidates.sqlite3"
MANIFEST_ROOT = ROOT.parent / "Candidate_Downloads"

# User-provided dashboard gaps.  They are an external target, not SQLite facts.
DASHBOARD = {
    "fight_positive": {"dev_current": 13, "dev_target": 10, "eval_current": 35, "eval_target": 50},
    "protest_small_positive": {"dev_current": 7, "dev_target": 10, "eval_current": 0, "eval_target": 50},
    "protest_large_positive": {"dev_current": 8, "dev_target": 10, "eval_current": 0, "eval_target": 50},
    "fight_like_control": {"dev_current": 10, "dev_target": 10, "eval_current": 9, "eval_target": 50},
    "protest_like_control": {"dev_current": 0, "dev_target": 10, "eval_current": 0, "eval_target": 50},
}
EXCLUDED_DOWNLOAD_BUCKETS = frozenset(
    {"protest_large_positive", "protest_like_control"}
)


@dataclass(frozen=True)
class Evidence:
    campaign_id: str
    subtype: str
    successes: int
    determinate: int
    description: str


def wilson_lower(successes: int, total: int, z: float = 1.959963984540054) -> float | None:
    """Two-sided 95% Wilson lower bound; None means no observed sample."""

    if total == 0:
        return None
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = proportion + z * z / (2 * total)
    spread = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
    return max(0.0, (centre - spread) / denominator)


def _latest_labels(connection: sqlite3.Connection, campaign_id: str) -> list[sqlite3.Row]:
    return connection.execute(
        """
        WITH latest AS (
            SELECT l.*, i.imported_at,
                   ROW_NUMBER() OVER (
                       PARTITION BY l.candidate_key, l.campaign_id, l.shown_subtype
                       ORDER BY i.imported_at DESC, l.import_id DESC
                   ) AS row_number
            FROM pilot_feedback_labels l
            JOIN pilot_feedback_imports i ON i.import_id = l.import_id
            WHERE l.campaign_id = ?
        )
        SELECT * FROM latest WHERE row_number = 1
        ORDER BY candidate_key
        """,
        (campaign_id,),
    ).fetchall()


def _evidence(connection: sqlite3.Connection) -> dict[str, Evidence]:
    sign = _latest_labels(connection, "sign_action_v1")
    fight = _latest_labels(connection, "fight_confounder_v1")
    sign_determinate = [row for row in sign if row["task_usable"] is not None]
    fight_determinate = [row for row in fight if row["task_usable"] is not None]
    # Positive fights are observed only as reviewer corrections to a confounder
    # campaign; retain that provenance so it is never silently treated as a
    # calibrated positive-discovery yield.
    fight_positive = [
        row
        for row in fight
        if row["task_usable"] == 1
        and any(term in row["corrected_subtype"] for term in ("打架", "斗殴"))
    ]
    return {
        "fight_positive": Evidence(
            "fight_confounder_v1",
            "reviewer-corrected 打架/打架斗殴",
            len(fight_positive),
            len(fight_determinate),
            "探索性代理：来自原本的类打斗对照 campaign，不能替代正样本查询的校准。",
        ),
        "protest_small_positive": Evidence(
            "sign_action_v1",
            "举牌/横幅（冻结为 1–5 名直接参与者）",
            sum(row["task_usable"] == 1 for row in sign_determinate),
            len(sign),
            "直接语义映射；未判定的 5 条按失败计入分母，作为保守口径。",
        ),
        "fight_like_control": Evidence(
            "fight_confounder_v1",
            "四个非攻击性对照 subtype",
            sum(row["task_usable"] == 1 for row in fight_determinate),
            len(fight_determinate),
            "直接任务映射；样本仍小，预算仅可作先验。",
        ),
    }


def _mapping(bucket: str, evidence: Evidence | None) -> dict[str, str]:
    if bucket in EXCLUDED_DOWNLOAD_BUCKETS:
        return {
            "status": "excluded_by_user",
            "mapping": "不纳入当前下载范围",
            "boundary": "用户已明确排除；不得搜索、probe、激活或下载。",
        }
    if bucket == "protest_small_positive":
        return {
            "status": "direct",
            "mapping": "sign_action_v1 / 举牌/横幅",
            "boundary": "仅 1–5 名直接参与者；大规模游行/密集群众是硬排除。",
        }
    if bucket == "fight_like_control":
        return {
            "status": "direct_with_review",
            "mapping": "fight_confounder_v1 / 四个非攻击性对照 subtype",
            "boundary": "只计人工 task_usable=true；被纠正为打架/斗殴者不可计入对照。",
        }
    if bucket == "fight_positive":
        return {
            "status": "proxy_only",
            "mapping": "仅有 fight_confounder_v1 中被人工纠正为打架/斗殴的反例",
            "boundary": "不存在独立的 fight-positive campaign、正样本查询包或 DEV/EVAL 分配。",
        }
    if bucket == "protest_large_positive":
        return {
            "status": "unmapped",
            "mapping": "无现有 campaign/subtype",
            "boundary": "sign_action_v1 明确硬排除大规模抗议；demand_action_v1 不记录人数尺度且处于 hold。",
        }
    return {
        "status": "unmapped",
        "mapping": "无现有 campaign/subtype",
        "boundary": "当前三个冻结 query pack 都没有“类抗议非抗议对照”的定义或人工标签。",
    }


def report(connection: sqlite3.Connection) -> dict:
    evidence = _evidence(connection)
    buckets = []
    for bucket, dashboard in DASHBOARD.items():
        gap = max(0, dashboard["dev_target"] - dashboard["dev_current"]) + max(0, dashboard["eval_target"] - dashboard["eval_current"])
        item: dict = {
            "bucket": bucket,
            "dashboard": {**dashboard, "remaining_gap": gap},
            "mapping": _mapping(bucket, evidence.get(bucket)),
            "candidate_budget": None,
        }
        item["dashboard"]["split_assignable_in_sqlite"] = False
        item["download_scope"] = (
            "excluded" if bucket in EXCLUDED_DOWNLOAD_BUCKETS else "included_or_pending_definition"
        )
        observed = evidence.get(bucket)
        if bucket in EXCLUDED_DOWNLOAD_BUCKETS:
            item["evidence"] = None
            item["candidate_budget"] = {
                "observed_human_usable_rate": None,
                "wilson_95_lower": None,
                "downloads_at_point_estimate": None,
                "downloads_at_wilson_lower": None,
                "interpretation": "用户已排除，不建立统计容量或下载队列。",
            }
        elif observed is not None:
            rate = observed.successes / observed.determinate if observed.determinate else None
            lower = wilson_lower(observed.successes, observed.determinate)
            item["evidence"] = asdict(observed)
            item["candidate_budget"] = {
                "observed_human_usable_rate": rate,
                "wilson_95_lower": lower,
                "downloads_at_point_estimate": math.ceil(gap / rate) if rate else None,
                "downloads_at_wilson_lower": math.ceil(gap / lower) if lower else None,
                "interpretation": "代理证据不可用于自动放宽门槛；未映射桶必须先有新冻结定义和首批人工校准。"
                if item["mapping"]["status"] == "proxy_only"
                else "以人工 task_usable 为成功，预算是需要进入人工标注的技术成功下载数。",
            }
        else:
            item["evidence"] = None
            item["candidate_budget"] = {
                "observed_human_usable_rate": None,
                "wilson_95_lower": None,
                "downloads_at_point_estimate": None,
                "downloads_at_wilson_lower": None,
                "interpretation": "零直接样本：先完成新定义下的首批人工校准，不能由其他桶借用命中率。",
            }
        buckets.append(item)

    technical = connection.execute(
        """
        SELECT q.campaign_id, COUNT(*) AS assigned,
               SUM(c.status = 'downloaded') AS downloaded
        FROM queue_assignments q
        JOIN candidates c ON c.candidate_key = q.candidate_key
        GROUP BY q.campaign_id ORDER BY q.campaign_id
        """
    ).fetchall()
    sign_target = connection.execute(
        """
        SELECT target_count, candidate_budget FROM campaign_human_targets
        WHERE campaign_id = 'sign_action_v1' AND target_kind = 'task_usable'
        ORDER BY created_at DESC LIMIT 1
        """
    ).fetchone()
    sign_labels = _latest_labels(connection, "sign_action_v1")
    sign_usable = sum(row["task_usable"] == 1 for row in sign_labels)
    sign_task_determinate = sum(row["task_usable"] is not None for row in sign_labels)
    sign_source_correct = sum(row["source_correct"] == 1 for row in sign_labels)
    sign_source_determinate = sum(
        row["source_correct"] is not None for row in sign_labels
    )
    sign_consumed = connection.execute(
        "SELECT COUNT(*) FROM queue_assignments WHERE campaign_id = 'sign_action_v1'"
    ).fetchone()[0]
    latest_batch = connection.execute(
        """
        SELECT b.batch_id, b.run_id, b.status,
               COUNT(i.candidate_key) AS released_count,
               SUM(d.decision = 'download_eligible') AS eligible_count,
               SUM(c.status = 'downloaded') AS technical_success_count
        FROM secondary_batches b
        JOIN secondary_batch_items i ON i.batch_id = b.batch_id
        JOIN secondary_filter_decisions d
          ON d.batch_id = i.batch_id AND d.candidate_key = i.candidate_key
        LEFT JOIN candidates c ON c.candidate_key = i.candidate_key
        WHERE b.campaign_id = 'sign_action_v1'
        GROUP BY b.batch_id, b.run_id, b.status
        ORDER BY b.created_at DESC LIMIT 1
        """
    ).fetchone()
    latest_batch_metrics = dict(latest_batch) if latest_batch is not None else None
    if latest_batch_metrics is not None:
        run_id = latest_batch_metrics["run_id"]
        batch_labels = connection.execute(
            """
            WITH latest AS (
                SELECT l.*, ROW_NUMBER() OVER (
                    PARTITION BY l.candidate_key, l.campaign_id, l.shown_subtype
                    ORDER BY i.imported_at DESC, l.import_id DESC
                ) AS row_number
                FROM pilot_feedback_labels l
                JOIN pilot_feedback_imports i ON i.import_id = l.import_id
            )
            SELECT COUNT(*) AS label_count,
                   SUM(task_usable = 1) AS task_usable_count,
                   SUM(task_usable IS NOT NULL) AS task_determinate_count,
                   SUM(source_correct = 1) AS source_correct_count,
                   SUM(source_correct IS NOT NULL) AS source_determinate_count
            FROM latest l JOIN queue_assignments q
              ON q.candidate_key = l.candidate_key AND q.campaign_id = l.campaign_id
            WHERE l.row_number = 1 AND q.run_id = ?
            """,
            (run_id,),
        ).fetchone()
        latest_batch_metrics["human_labels"] = dict(batch_labels)
    manifest_path = MANIFEST_ROOT / "sign_action_v1/manifest.jsonl"
    manifest = _manifest_metrics(manifest_path)
    target_count = int(sign_target["target_count"]) if sign_target else None
    candidate_budget = int(sign_target["candidate_budget"]) if sign_target else None
    remaining_target = max(0, target_count - sign_usable) if target_count is not None else None
    remaining_budget = max(0, candidate_budget - sign_consumed) if candidate_budget is not None else None
    return {
        "schema_version": "five_bucket_capacity_v1",
        "source_of_truth": "SQLite human labels and auditable queue/download records",
        "external_dashboard_notice": "DEV/EVAL counts are user-provided targets. The current SQLite schema has no split assignment; this report does not synthesize split labels.",
        "buckets": buckets,
        "technical_success": [dict(row) for row in technical],
        "sign_action_operational_metrics": {
            "human_usable_target": target_count,
            "human_usable_confirmed": sign_usable,
            "cumulative_task_determinate": sign_task_determinate,
            "cumulative_source_correct": sign_source_correct,
            "cumulative_source_determinate": sign_source_determinate,
            "remaining_human_usable_gap": remaining_target,
            "candidate_budget": candidate_budget,
            "candidate_budget_consumed": sign_consumed,
            "candidate_budget_remaining": remaining_budget,
            "required_future_human_usable_rate": (
                remaining_target / remaining_budget
                if remaining_target is not None and remaining_budget
                else None
            ),
            "latest_batch": latest_batch_metrics,
            "manifest": manifest,
            "sha256_duplicate_edges": connection.execute(
                "SELECT COUNT(*) FROM duplicate_edges WHERE kind = 'sha256'"
            ).fetchone()[0],
        },
    }


def _manifest_metrics(path: Path) -> dict:
    required = {"candidate_key", "campaign_id", "subtype", "technical_status", "sha256"}
    if not path.is_file():
        return {"path": str(path), "record_count": 0, "complete_record_count": 0}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {
        "path": str(path),
        "record_count": len(rows),
        "complete_record_count": sum(required <= set(row) for row in rows),
        "unique_candidate_keys": len({row.get("candidate_key") for row in rows}),
        "unique_sha256": len({row.get("sha256") for row in rows if row.get("sha256")}),
    }


def markdown(payload: dict) -> str:
    lines = [
        "# 五桶数据缺口：映射与容量快照",
        "",
        "本报告只读 SQLite。DEV/EVAL 是外部仪表盘目标；当前 v2 schema 没有 split 字段，因此不会把本地人工标签臆写为 DEV 或 EVAL。",
        "",
        "| 桶 | 剩余缺口 | 映射状态 | 本地人工证据 | 点估计 / 95% Wilson 保守下载预算 |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for item in payload["buckets"]:
        evidence = item["evidence"]
        budget = item["candidate_budget"]
        sample = "无直接样本" if evidence is None else f"{evidence['successes']}/{evidence['determinate']}"
        estimate = "待首批校准" if budget["downloads_at_point_estimate"] is None else f"{budget['downloads_at_point_estimate']} / {budget['downloads_at_wilson_lower']}"
        lines.append(f"| `{item['bucket']}` | {item['dashboard']['remaining_gap']} | {item['mapping']['status']} | {sample} | {estimate} |")
    lines += ["", "## 证据与边界", ""]
    for item in payload["buckets"]:
        lines += [
            f"### `{item['bucket']}`",
            "",
            f"- 映射：{item['mapping']['mapping']}",
            f"- 边界：{item['mapping']['boundary']}",
            f"- 预算解释：{item['candidate_budget']['interpretation']}",
            "",
        ]
    lines += ["## 技术成功（SQLite）", "", "| Campaign | 已入队 | 技术成功下载 |", "| --- | ---: | ---: |"]
    for row in payload["technical_success"]:
        lines.append(f"| `{row['campaign_id']}` | {row['assigned']} | {row['downloaded'] or 0} |")
    metrics = payload["sign_action_operational_metrics"]
    latest = metrics["latest_batch"]
    manifest = metrics["manifest"]
    lines += [
        "",
        "## sign_action_v1 最新批次运行指标",
        "",
        f"- 累计人工可用：{metrics['human_usable_confirmed']}/{metrics['human_usable_target']}；剩余 {metrics['remaining_human_usable_gap']}。",
        f"- 累计人工任务可用率：{metrics['human_usable_confirmed']}/{metrics['cumulative_task_determinate']} = {metrics['human_usable_confirmed'] / metrics['cumulative_task_determinate']:.1%}（按全部 65 个已展示候选保守计为 36.9%）；来源正确率：{metrics['cumulative_source_correct']}/{metrics['cumulative_source_determinate']} = {metrics['cumulative_source_correct'] / metrics['cumulative_source_determinate']:.1%}。",
        f"- 候选预算：已消费 {metrics['candidate_budget_consumed']}/{metrics['candidate_budget']}；余量 {metrics['candidate_budget_remaining']}；余量所需人工可用率 {metrics['required_future_human_usable_rate']:.1%}。",
        f"- Manifest：{manifest['complete_record_count']}/{manifest['record_count']} 必填字段完整，{manifest.get('unique_candidate_keys', 0)} 个唯一 candidate key，{manifest.get('unique_sha256', 0)} 个唯一 SHA-256。",
        f"- SHA-256 重复边：{metrics['sha256_duplicate_edges']}。",
    ]
    if latest is not None:
        labels = latest["human_labels"]
        automatic_yield = latest["eligible_count"] / latest["released_count"]
        lines += [
            f"- 最新批次 `{latest['batch_id']}`：二次筛选 {latest['eligible_count']}/{latest['released_count']} = {automatic_yield:.1%}；技术成功 {latest['technical_success_count']}/{latest['eligible_count']}。",
            f"- 该批人工反馈：{labels['label_count']} 条；尚未形成该批 task/source 人工可用率（零标签不按 0% 处理）。",
        ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    args = parser.parse_args()
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    try:
        payload = report(connection)
    finally:
        connection.close()
    args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(markdown(payload), encoding="utf-8")
    print(json.dumps({"json": str(args.json), "markdown": str(args.markdown)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
