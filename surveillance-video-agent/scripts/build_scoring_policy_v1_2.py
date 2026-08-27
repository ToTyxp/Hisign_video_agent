"""Add confirmed French game/walkthrough hard negatives to scoring v1.2."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/scoring-policy.v1.1.0.json"
DESTINATION = ROOT / "query-packs/scoring-policy.v1.2.0.json"
GAME_TERMS = (
    "but du jeu",
    "jeu vidéo",
    "solution de jeu",
    "soluce",
    "Steam",
)
TUTORIAL_TERMS = (
    "vous montre comment",
    "résoudre l'énigme",
    "résoudre ce casse-tête",
    "dans cet ordre",
    "guide de jeu",
)


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    for term in GAME_TERMS:
        if term not in document["hard_exclusions"]["game"]:
            document["hard_exclusions"]["game"].append(term)
    for term in TUTORIAL_TERMS:
        if term not in document["hard_exclusions"]["tutorial"]:
            document["hard_exclusions"]["tutorial"].append(term)
    document.update(
        {
            "policy_version": "surveillance_scoring_v1.2.0",
            "status": "frozen",
            "frozen_at": "2026-08-27",
            "frozen_by": "user",
            "revision_from": "surveillance_scoring_v1.1.0",
            "revision_reason": (
                "人工反馈确认法语游戏攻略漏过硬排除；只补充游戏/教程反例词，"
                "不修改来源正分、任务分或阈值。"
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
