"""Move frozen fight-control actions into unused surveillance locations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.2.0.json"
DESTINATION = ROOT / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.3.0.json"
REPLACEMENTS = {
    "en": {
        "outside store": "warehouse aisle",
        "parking lot": "elevator lobby",
        "apartment entrance": "hotel entrance",
    },
    "es": {
        "entrada de tienda": "pasillo de almacén",
        "estacionamiento": "vestíbulo de ascensor",
        "entrada de apartamento": "entrada de hotel",
    },
    "fr": {
        "entrée de magasin": "allée d'entrepôt",
        "parking": "hall d'ascenseur",
        "entrée d'immeuble": "entrée d'hôtel",
    },
}


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    queries = []
    for item in document["queries"]:
        revised = dict(item)
        revised["query_id"] = item["query_id"].replace("fcv12-", "fcv13-", 1)
        query = item["query"]
        action_term = item["action_or_scene_term"]
        replaced = False
        for old, new in REPLACEMENTS[item["lang"]].items():
            if old in query:
                query = query.replace(old, new)
                action_term = action_term.replace(old, new)
                replaced = True
                break
        if not replaced:
            raise ValueError(f"no v1.3 location replacement: {item['query_id']}")
        revised["query"] = query
        revised["action_or_scene_term"] = action_term
        revised["rationale_zh"] = (
            item["rationale_zh"]
            + " v1.3只替换为仓库、电梯厅和酒店入口长尾地点；非攻击边界不变。"
        )
        if revised["source_anchor"] not in query:
            raise ValueError("v1.3 query lost source anchor")
        if action_term not in query:
            raise ValueError("v1.3 query lost action term")
        queries.append(revised)
    canonical = json.dumps(
        queries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n"
    document.update(
        {
            "query_pack_version": "fight_confounder_v1.qp.v1.3.0",
            "queries": queries,
            "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "created_at": "2026-08-27T20:00:00Z",
            "frozen_at": "2026-08-27",
            "frozen_by": "derived_from_frozen_user_concepts",
            "revision_from": "fight_confounder_v1.qp.v1.2.0",
            "revision_reason": (
                "用户将类打架技术成功目标设为120；旧激活池耗尽，仅增加长尾地点，"
                "保持四个非攻击subtype、固定监控来源和0.40语义门不变。"
            ),
        }
    )
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
