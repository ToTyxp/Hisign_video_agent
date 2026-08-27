"""Combine broad sign wording with current small-scale/mobile policy."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.5.0.json"
BROAD = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.1.0.json"
DESTINATION = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.6.0.json"


def main() -> None:
    document = json.loads(BASE.read_text(encoding="utf-8"))
    broad = json.loads(BROAD.read_text(encoding="utf-8"))
    queries = []
    for item in broad["queries"]:
        revised = dict(item)
        revised["query_id"] = item["query_id"].replace("sav11-", "sav16-", 1)
        revised["rationale_zh"] = (
            "复用人工效果最好的广义举牌动作表达；手机查询来源加10至90秒时长"
            "作为横屏手机来源证据，1至5人上限继续由评分策略硬排除。"
        )
        queries.append(revised)
    canonical = json.dumps(
        queries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    document.update(
        {
            "query_pack_version": "sign_action_v1.qp.v1.6.0",
            "queries": queries,
            "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "created_at": "2026-08-27T09:00:00Z",
            "frozen_at": "2026-08-27",
            "revision_from": "sign_action_v1.qp.v1.5.0",
            "revision_reason": (
                "用户确认60条人工可用目标和180候选预算；组合最佳广义举牌"
                "语义、修复后的手机排序及短时长来源证据以扩充下一批。"
            ),
        }
    )
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
