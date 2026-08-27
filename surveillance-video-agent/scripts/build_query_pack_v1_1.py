"""Build frozen v1.1 query packs without changing task vocabulary semantics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKS = (
    (
        ROOT
        / "query-packs/demand_action_v1/demand_action_v1.qp.v1.0.0.draft.json",
        ROOT / "query-packs/demand_action_v1/demand_action_v1.qp.v1.1.0.json",
        "demand_action_v1.qp.v1.1.0",
        "dav11",
    ),
    (
        ROOT
        / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.0.0.draft.json",
        ROOT / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.1.0.json",
        "fight_confounder_v1.qp.v1.1.0",
        "fcv11",
    ),
)
RAWNESS = {
    "en": "raw footage",
    "es": "grabación original",
    "fr": "images brutes",
}


def main() -> None:
    for source, destination, version, id_prefix in PACKS:
        document = json.loads(source.read_text(encoding="utf-8"))
        previous_version = document["query_pack_version"]
        queries = []
        for item in document["queries"]:
            lang = item["lang"]
            revised = dict(item)
            suffix = item["query_id"].split("-", 1)[1]
            revised["query_id"] = f"{id_prefix}-{suffix}"
            revised["query"] = f"{item['query']} {RAWNESS[lang]}"
            revised["rationale_zh"] = (
                item["rationale_zh"]
                + " v1.1 追加本语言原始录像线索；动作词与中文语义边界不变。"
            )
            if revised["source_anchor"] not in revised["query"]:
                raise ValueError("revised query lost source anchor")
            if revised["action_or_scene_term"] not in revised["query"]:
                raise ValueError("revised query lost action/scene term")
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
        document["content_sha256_status"] = "verified_frozen"
        document["created_at"] = "2026-08-26T08:00:00Z"
        document["created_by"] = "ai"
        document["frozen_at"] = "2026-08-26"
        document["frozen_by"] = "user"
        document["status"] = "frozen"
        document["revision_from"] = previous_version
        document["revision_reason"] = (
            "首轮任务合格池不足；保持动作词和中文定义不变，追加多语言原始录像线索，"
            "配合平台间严格轮转。"
        )
        destination.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
