"""Build constrained location-diversified v1.2 data-collection query packs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKS = (
    (
        ROOT / "query-packs/demand_action_v1/demand_action_v1.qp.v1.1.0.json",
        ROOT / "query-packs/demand_action_v1/demand_action_v1.qp.v1.2.0.json",
        "demand_action_v1.qp.v1.2.0",
        "dav12",
    ),
    (
        ROOT / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.1.0.json",
        ROOT / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.2.0.json",
        "fight_confounder_v1.qp.v1.2.0",
        "fcv12",
    ),
)
LOCATIONS = {
    "en": {"01": "outside store", "02": "parking lot", "03": "apartment entrance"},
    "es": {"01": "entrada de tienda", "02": "estacionamiento", "03": "entrada de apartamento"},
    "fr": {"01": "entrée de magasin", "02": "parking", "03": "entrée d'immeuble"},
}


def main() -> None:
    for source, destination, version, prefix in PACKS:
        document = json.loads(source.read_text(encoding="utf-8"))
        previous = document["query_pack_version"]
        queries = []
        for item in document["queries"]:
            revised = dict(item)
            suffix = item["query_id"].split("-", 1)[1]
            ordinal = item["query_id"].rsplit("-", 1)[1]
            revised["query_id"] = f"{prefix}-{suffix}"
            revised["query"] = (
                f"{item['query']} {LOCATIONS[item['lang']][ordinal]}"
            )
            revised["rationale_zh"] = (
                item["rationale_zh"]
                + " v1.2 仅增加受约束地点切片以收集更多数据；动作词、来源锚点和评分边界不变。"
            )
            if revised["source_anchor"] not in revised["query"]:
                raise ValueError("v1.2 query lost source anchor")
            if revised["action_or_scene_term"] not in revised["query"]:
                raise ValueError("v1.2 query lost action/scene term")
            queries.append(revised)
        canonical = json.dumps(
            queries,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
        document["query_pack_version"] = version
        document["queries"] = queries
        document["content_sha256"] = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
        document["created_at"] = "2026-08-26T09:00:00Z"
        document["created_by"] = "ai"
        document["frozen_at"] = "2026-08-26"
        document["frozen_by"] = "user"
        document["status"] = "frozen"
        document["revision_from"] = previous
        document["revision_reason"] = (
            "用户要求不再放宽语义，转为收集更多数据；仅增加多语言地点切片，"
            "保持动作词、来源锚点、分值和阈值不变。"
        )
        destination.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
