"""Add confirmed scale and fall counterexamples for small sign collection."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/scoring-policy.v1.4.0.json"
DESTINATION = ROOT / "query-packs/scoring-policy.v1.5.0.json"
TERMS = (
    "thousands",
    "hundreds",
    "des milliers",
    "des centaines",
    "miles de manifestantes",
    "cientos de manifestantes",
    "fall on ice",
    "falls on ice",
    "falling down",
    "slips and falls",
    "cae en el hielo",
    "se cae",
    "tombe sur la glace",
    "chute sur la glace",
    "摔倒",
    "滑倒",
)


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    forbidden = document["task_matching"]["sign_action_v1"]["举牌/横幅"][
        "forbidden_terms"
    ]
    for term in TERMS:
        if term not in forbidden:
            forbidden.append(term)
    document.update(
        {
            "policy_version": "surveillance_scoring_v1.5.0",
            "status": "frozen",
            "frozen_at": "2026-08-27",
            "frozen_by": "user",
            "revision_from": "surveillance_scoring_v1.4.0",
            "revision_reason": (
                "人工反馈确认数百/数千人集会和跌倒视频为小规模举牌反例；"
                "仅补充任务负词，不修改来源门、分值或阈值。"
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
