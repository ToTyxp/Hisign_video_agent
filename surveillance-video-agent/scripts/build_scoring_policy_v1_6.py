"""Encode the frozen 1-5 participant maximum as a numeric task rule."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/scoring-policy.v1.5.0.json"
DESTINATION = ROOT / "query-packs/scoring-policy.v1.6.0.json"


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    document["task_matching"]["sign_action_v1"]["举牌/横幅"][
        "max_direct_participants"
    ] = 5
    document.update(
        {
            "policy_version": "surveillance_scoring_v1.6.0",
            "status": "frozen",
            "frozen_at": "2026-08-27",
            "frozen_by": "user",
            "revision_from": "surveillance_scoring_v1.5.0",
            "revision_reason": (
                "将用户冻结的1至5人上限编码为多语言数字人数规则；"
                "数字大于5且邻接参与者词时任务硬排除。"
            ),
        }
    )
    content = dict(document)
    for key in (
        "status",
        "frozen_at",
        "frozen_by",
        "content_sha256",
        "content_sha256_scope",
    ):
        content.pop(key, None)
    canonical = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    document["content_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
