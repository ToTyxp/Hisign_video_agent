"""Re-version settings queries for short-duration mobile provenance scoring."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.4.0.json"
DESTINATION = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.5.0.json"


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    queries = []
    for item in document["queries"]:
        revised = dict(item)
        revised["query_id"] = item["query_id"].replace("sav14-", "sav15-", 1)
        revised["rationale_zh"] += (
            " v1.5 查询语义不变；手机锚点查询与10至90秒时长组成来源证据。"
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
            "query_pack_version": "sign_action_v1.qp.v1.5.0",
            "queries": queries,
            "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "created_at": "2026-08-27T08:00:00Z",
            "frozen_at": "2026-08-27",
            "revision_from": "sign_action_v1.qp.v1.4.0",
            "revision_reason": (
                "用户允许手机拍摄且观察到大量短视频；查询内容不变，使用独立版本"
                "验证手机查询来源加10至90秒时长的组合来源证据。"
            ),
        }
    )
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
