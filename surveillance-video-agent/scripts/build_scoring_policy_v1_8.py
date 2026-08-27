"""Expand the packaging penalty using the latest visual pilot evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/scoring-policy.v1.7.0.json"
DESTINATION = ROOT / "query-packs/scoring-policy.v1.8.0.json"
ADDITIONS = (
    "news",
    "euronews",
    "breaking news",
    "press conference",
    "interview",
    "reporter",
    "journalist",
    "broadcast",
    "television report",
    "noticias",
    "rueda de prensa",
    "entrevista",
    "reportero",
    "reportera",
    "periodista",
    "informativo",
    "telediario",
    "diario",
    "actualités",
    "conférence de presse",
    "journaliste",
    "chaîne info",
    "新闻",
    "采访",
    "记者",
    "新闻发布会",
    "电视台",
)


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    penalties = document["source"]["packaging_penalties"]
    for term in ADDITIONS:
        if term not in penalties:
            penalties.append(term)
    document.update(
        {
            "policy_version": "surveillance_scoring_v1.8.0",
            "status": "frozen",
            "frozen_at": "2026-08-27",
            "frozen_by": "user_feedback",
            "revision_from": "surveillance_scoring_v1.7.0",
            "revision_reason": (
                "18条举牌视觉反馈显示新闻、采访、发布会和电视包装漏过既有-3规则；"
                "扩展多语言包装词并检查简介、标签和上传者，不改变来源阈值。"
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
