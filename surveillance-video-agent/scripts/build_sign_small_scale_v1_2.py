"""Freeze small-scale sign semantics and derive mobile query pack v1.2."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONCEPT_PATH = ROOT / "query-packs/sign-action-concepts.v1.0.0.freeze.json"
SOURCE_PACK = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.1.0.json"
QUERY_PATH = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.2.0.json"
DEFINITION = {
    "campaign_definition": (
        "固定监控或手机拍摄的真实场景中，1至5名直接参与者手持、展示、"
        "高举牌子或展开横幅；背景路人不计入人数。"
    ),
    "subtypes": [
        {
            "subtype": "举牌/横幅",
            "target_definition": (
                "直接举牌或展示横幅者为1至5人；排除大型游行、密集群众集会"
                "以及主要画面不是小规模举牌者的视频。"
            ),
            "core_concepts": [
                "单人举牌",
                "独自举牌抗议",
                "个人手持标语牌",
                "个人展示牌子",
                "两人举牌",
                "小组举牌",
                "少数人拉横幅",
                "街边举牌",
                "路边举牌",
                "门口举牌",
                "店外举牌",
                "小规模纠察",
            ],
            "forbidden_scale_concepts": [
                "大型游行",
                "大规模抗议",
                "密集群众集会",
                "数百人集会",
                "数千人游行",
            ],
        }
    ],
}
TERMS = {
    "en": (
        ("shorts", "lone protester holding sign", "street"),
        ("phone video", "one person with protest sign", "sidewalk"),
        ("vertical video", "small group holding signs", "outside store"),
        ("reel", "individual holding placard", "roadside"),
        ("mobile video", "few protesters with banner", "entrance"),
    ),
    "es": (
        ("shorts", "manifestante solo con cartel", "calle"),
        ("video de teléfono", "una persona con cartel de protesta", "acera"),
        ("video vertical", "grupo pequeño con carteles", "entrada de tienda"),
        ("reel", "persona sosteniendo pancarta", "carretera"),
        ("vídeo móvil", "pocos manifestantes con pancarta", "entrada"),
    ),
    "fr": (
        ("shorts", "manifestant seul avec pancarte", "rue"),
        ("filmé au téléphone", "une personne avec pancarte de protestation", "trottoir"),
        ("vidéo verticale", "petit groupe avec pancartes", "entrée de magasin"),
        ("reel", "personne tenant une pancarte", "bord de route"),
        ("vidéo mobile", "quelques manifestants avec banderole", "entrée"),
    ),
}


def main() -> None:
    definition_canonical = json.dumps(
        DEFINITION,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    concept_hash = hashlib.sha256(definition_canonical.encode("utf-8")).hexdigest()
    concept = {
        "schema_version": 1,
        "status": "frozen",
        "concept_pack_version": "sign_action_zh_concepts_v1.0.0",
        "source_language": "zh",
        "source_sha256": concept_hash,
        "campaigns": ["sign_action_v1"],
        "frozen_semantics_zh": DEFINITION,
        "frozen_at": "2026-08-27",
        "frozen_by": "user",
        "immutability_rule": (
            "Any small-scale sign semantic change requires a new concept version."
        ),
    }
    CONCEPT_PATH.write_text(
        json.dumps(concept, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    document = json.loads(SOURCE_PACK.read_text(encoding="utf-8"))
    queries = []
    for lang in ("en", "es", "fr"):
        for ordinal, (anchor, action, location) in enumerate(TERMS[lang], 1):
            queries.append(
                {
                    "query_id": f"sav12-small-sign-{lang}-{ordinal:02d}",
                    "campaign_id": "sign_action_v1",
                    "subtype": "举牌/横幅",
                    "lang": lang,
                    "query": f"{action} {anchor} {location}",
                    "source_anchor": anchor,
                    "source_pool": "mobile_adjacent",
                    "action_or_scene_term": action,
                    "rationale_zh": (
                        "用户冻结1至5人小规模定义；查询同时包含手机/短视频来源锚点、"
                        "单人或小组举牌动作和自然场景词。"
                    ),
                }
            )
    canonical = json.dumps(
        queries,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    document.update(
        {
            "query_pack_version": "sign_action_v1.qp.v1.2.0",
            "concept_pack_version": concept["concept_pack_version"],
            "concept_source_path": (
                "surveillance-video-agent/query-packs/"
                "sign-action-concepts.v1.0.0.freeze.json"
            ),
            "source_sha256": concept_hash,
            "frozen_semantics_zh": DEFINITION,
            "queries": queries,
            "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "created_at": "2026-08-27T05:00:00Z",
            "frozen_at": "2026-08-27",
            "revision_from": "sign_action_v1.qp.v1.1.0",
            "revision_reason": (
                "用户明确冻结1至5人小规模举牌；第三组查询用于估计是否能将"
                "潜在可用量从约32扩展到60。"
            ),
        }
    )
    QUERY_PATH.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
