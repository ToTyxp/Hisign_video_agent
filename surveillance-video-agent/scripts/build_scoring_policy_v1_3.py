"""Bind sign_action task aliases and add AI-video hard negatives."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/scoring-policy.v1.2.0.json"
DESTINATION = ROOT / "query-packs/scoring-policy.v1.3.0.json"
AI_TERMS = (
    "AI generated video",
    "AI-generated video",
    "created with AI",
    "creado con IA",
    "vídeo generado por IA",
    "créé avec IA",
    "généré par IA",
    "vidéo générée par IA",
    "AI生成视频",
    "人工智能生成视频",
)


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    document["task_matching"]["sign_action_v1"] = {
        "举牌/横幅": deepcopy(
            document["task_matching"]["demand_action_v1"]["举牌/横幅"]
        )
    }
    for term in AI_TERMS:
        if term not in document["hard_exclusions"]["animation"]:
            document["hard_exclusions"]["animation"].append(term)
    document.update(
        {
            "policy_version": "surveillance_scoring_v1.3.0",
            "status": "frozen",
            "frozen_at": "2026-08-27",
            "frozen_by": "user",
            "revision_from": "surveillance_scoring_v1.2.0",
            "revision_reason": (
                "复用冻结的举牌任务规则到 sign_action_v1，并补充明确AI生成视频"
                "硬排除；不修改分值或阈值。"
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
