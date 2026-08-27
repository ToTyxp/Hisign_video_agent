"""Re-version small-scale settings after fixing mobile probe ranking."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.3.0.json"
DESTINATION = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.4.0.json"


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    queries = []
    for item in document["queries"]:
        revised = dict(item)
        revised["query_id"] = item["query_id"].replace("sav13-", "sav14-", 1)
        revised["rationale_zh"] += (
            " v1.4 查询语义不变；新版本只用于应用修复后的手机来源 probe 排序。"
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
            "query_pack_version": "sign_action_v1.qp.v1.4.0",
            "queries": queries,
            "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "created_at": "2026-08-27T07:00:00Z",
            "frozen_at": "2026-08-27",
            "revision_from": "sign_action_v1.qp.v1.3.0",
            "revision_reason": (
                "修复probe前错误使用surveillance-only评分器的问题；查询内容和"
                "冻结小规模语义不变，使用独立版本与预算验证排序修复。"
            ),
        }
    )
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
