"""Add confirmed stock-footage, generic tutorial, and in-game exclusions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/scoring-policy.v1.6.0.json"
DESTINATION = ROOT / "query-packs/scoring-policy.v1.7.0.json"
ADDITIONS = {
    "film_tv": (
        "stock footage",
        "stock video",
        "Videohive",
        "Shutterstock",
        "Pond5",
    ),
    "tutorial": (
        "how to make",
        "how to create",
        "que significa",
        "cómo hacer",
        "comment faire",
    ),
    "game": (
        "in-game",
        "Roblox",
        "Epic Minigames",
    ),
}


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    for category, terms in ADDITIONS.items():
        for term in terms:
            if term not in document["hard_exclusions"][category]:
                document["hard_exclusions"][category].append(term)
    document.update(
        {
            "policy_version": "surveillance_scoring_v1.7.0",
            "status": "frozen",
            "frozen_at": "2026-08-27",
            "frozen_by": "user",
            "revision_from": "surveillance_scoring_v1.6.0",
            "revision_reason": (
                "候选标题审计确认stock footage/Videohive、通用制作教程和"
                "Roblox/in-game漏过既有硬排除类别；只补词，不修改门槛。"
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
