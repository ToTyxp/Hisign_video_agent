"""Build the frozen mobile-adjacent sign campaign query pack."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONCEPTS = ROOT / "query-packs/chinese-concepts.v1.0.0.freeze.json"
DEMAND_PACK = ROOT / "query-packs/demand_action_v1/demand_action_v1.qp.v1.3.0.json"
DESTINATION = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.0.0.json"
TERMS = {
    "en": (
        ("phone video", "holding protest sign", "shorts"),
        ("cell phone video", "protesters with signs", "vertical"),
        ("mobile video", "protest banner", "short clip"),
        ("smartphone video", "holding placard", "raw"),
        ("vertical video", "picket sign", "protest"),
    ),
    "es": (
        ("video de teléfono", "manifestante con cartel", "corto"),
        ("grabado con celular", "pancarta de protesta", "vertical"),
        ("vídeo móvil", "manifestantes con carteles", "grabación"),
        ("video de smartphone", "sosteniendo pancarta", "protesta"),
        ("video vertical", "piquete con cartel", "manifestación"),
    ),
    "fr": (
        ("filmé au téléphone", "manifestant avec pancarte", "vidéo courte"),
        ("vidéo mobile", "banderole de protestation", "verticale"),
        ("vidéo smartphone", "manifestants avec pancartes", "manifestation"),
        ("filmée au smartphone", "tenant une pancarte", "protestation"),
        ("vidéo verticale", "piquet avec pancarte", "manifestation"),
    ),
}


def main() -> None:
    concept = json.loads(CONCEPTS.read_text(encoding="utf-8"))
    demand = json.loads(DEMAND_PACK.read_text(encoding="utf-8"))
    sign_semantics = next(
        item
        for item in demand["frozen_semantics_zh"]["subtypes"]
        if item["subtype"] == "举牌/横幅"
    )
    queries = []
    for lang in ("en", "es", "fr"):
        for ordinal, (anchor, action, qualifier) in enumerate(TERMS[lang], 1):
            query = f'{anchor} "{action}" {qualifier}'
            queries.append(
                {
                    "query_id": f"sav1-sign-banner-{lang}-{ordinal:02d}",
                    "campaign_id": "sign_action_v1",
                    "subtype": "举牌/横幅",
                    "lang": lang,
                    "query": query,
                    "source_anchor": anchor,
                    "source_pool": "mobile_adjacent",
                    "action_or_scene_term": action,
                    "rationale_zh": (
                        "用户明确允许举牌任务使用手机拍摄；查询同时保留手机来源锚点"
                        "和举牌/横幅动作短语。"
                    ),
                }
            )
    canonical = json.dumps(
        queries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    document = {
        "schema_version": 1,
        "status": "frozen",
        "query_pack_version": "sign_action_v1.qp.v1.0.0",
        "campaign_id": "sign_action_v1",
        "concept_pack_version": concept["concept_pack_version"],
        "concept_source_path": "surveillance-video-agent/query-packs/chinese-concepts.v1.0.0.freeze.json",
        "source_sha256": concept["source_sha256"],
        "frozen_semantics_zh": {
            "campaign_definition": (
                "固定监控或手机拍摄的真实场景中，人物手持、展示、高举牌子或展开横幅。"
            ),
            "subtypes": [sign_semantics],
        },
        "network_config": "default",
        "derived_languages": ["en", "es", "fr"],
        "created_at": "2026-08-27T03:00:00Z",
        "created_by": "ai",
        "frozen_at": "2026-08-27",
        "frozen_by": "user",
        "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "content_sha256_scope": "canonical jq -cS .queries",
        "content_sha256_status": "verified_frozen",
        "revision_reason": (
            "用户将举牌目标设为60，并明确允许手机拍摄；此例外仅限 sign_action_v1。"
        ),
        "queries": queries,
    }
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
