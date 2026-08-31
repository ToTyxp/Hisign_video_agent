"""Create the independent frozen fight-positive concept, query, and scoring policy."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONCEPT_PATH = ROOT / "query-packs/fight-positive-concepts.v1.0.0.freeze.json"
QUERY_PATH = ROOT / "query-packs/fight_positive_v1/fight_positive_v1.qp.v1.0.0.json"
POLICY_SOURCE = ROOT / "query-packs/scoring-policy.v1.8.0.json"
POLICY_PATH = ROOT / "query-packs/scoring-policy.v1.9.0.json"

SUBTYPE = "真实打架/斗殴"
CORE_CONCEPTS = [
    "真实打架",
    "实际斗殴",
    "互相殴打",
    "拳打脚踢",
    "挥拳击打",
    "连续踢打",
    "掌掴",
    "摔打",
    "扭打",
    "街头斗殴",
    "多人群殴",
    "身体攻击",
]
CAMPAIGN_DEFINITION = (
    "固定监控、安防摄像头或门铃摄像头记录的真实现场中，真实人物发生实际身体"
    "攻击或互殴，包括拳打、脚踢、掌掴、摔打、扭打和多人群殴。排除只有口角或"
    "对峙、玩闹、舞蹈、训练/对练、事故、影视游戏动画、教程广告、新闻包装，"
    "以及以枪击或持刀袭击为主而非徒手打架的事件。"
)

QUERIES = (
    ("en", "CCTV fist fight raw footage", "CCTV", "fist fight"),
    ("en", "security camera people punching and kicking raw video", "security camera", "people punching and kicking"),
    ("en", "surveillance camera street brawl uncut", "surveillance camera", "street brawl"),
    ("en", "CCTV group fight caught on camera", "CCTV", "group fight"),
    ("en", "security footage physical fight parking lot", "security footage", "physical fight"),
    ("es", "CCTV pelea a puñetazos grabación original", "CCTV", "pelea a puñetazos"),
    ("es", "cámara de seguridad personas golpeándose y pateando video sin editar", "cámara de seguridad", "personas golpeándose y pateando"),
    ("es", "cámara de vigilancia pelea callejera grabación completa", "cámara de vigilancia", "pelea callejera"),
    ("es", "CCTV pelea grupal captada por cámara", "CCTV", "pelea grupal"),
    ("es", "grabación de vigilancia pelea física estacionamiento", "grabación de vigilancia", "pelea física"),
    ("fr", "CCTV bagarre à coups de poing vidéo brute", "CCTV", "bagarre à coups de poing"),
    ("fr", "caméra de sécurité personnes donnant coups de poing et de pied vidéo brute", "caméra de sécurité", "personnes donnant coups de poing et de pied"),
    ("fr", "caméra de surveillance bagarre de rue séquence complète", "caméra de surveillance", "bagarre de rue"),
    ("fr", "CCTV bagarre de groupe filmée par caméra", "CCTV", "bagarre de groupe"),
    ("fr", "enregistrement de surveillance bagarre physique parking", "enregistrement de surveillance", "bagarre physique"),
)


def _canonical_hash(document: dict, excluded: tuple[str, ...]) -> str:
    content = dict(document)
    for key in excluded:
        content.pop(key, None)
    canonical = json.dumps(
        content, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def main() -> None:
    concept = {
        "schema_version": 1,
        "status": "frozen",
        "concept_pack_version": "fight_positive_zh_concepts_v1.0.0",
        "campaign_id": "fight_positive_v1",
        "campaign_definition": CAMPAIGN_DEFINITION,
        "subtypes": [{"subtype": SUBTYPE, "core_concepts": CORE_CONCEPTS}],
        "frozen_at": "2026-08-27",
        "frozen_by": "user",
        "freeze_evidence": (
            "用户明确要求同时下载60条打架视频；旧只读工作流将目标限定为"
            "CCTV打架、不是电视包装，本版本将其迁移为独立v2正样本Campaign。"
        ),
        "content_sha256_scope": "canonical JSON excluding content_sha256",
    }
    concept["content_sha256"] = _canonical_hash(concept, ("content_sha256",))
    CONCEPT_PATH.write_text(
        json.dumps(concept, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    queries = []
    counters = {"en": 0, "es": 0, "fr": 0}
    for lang, query, source_anchor, action_term in QUERIES:
        counters[lang] += 1
        queries.append(
            {
                "query_id": f"fpv1-fight-{lang}-{counters[lang]:02d}",
                "campaign_id": "fight_positive_v1",
                "subtype": SUBTYPE,
                "lang": lang,
                "query": query,
                "source_anchor": source_anchor,
                "source_pool": "surveillance",
                "action_or_scene_term": action_term,
                "rationale_zh": "固定监控来源强锚点与真实身体打架动作同时出现；不检索非攻击对照。",
            }
        )
    query_hash = hashlib.sha256(
        (
            json.dumps(
                queries,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()
    query_pack = {
        "schema_version": 1,
        "status": "frozen",
        "query_pack_version": "fight_positive_v1.qp.v1.0.0",
        "campaign_id": "fight_positive_v1",
        "concept_pack_version": concept["concept_pack_version"],
        "concept_source_path": "surveillance-video-agent/query-packs/fight-positive-concepts.v1.0.0.freeze.json",
        "source_sha256": concept["content_sha256"],
        "frozen_semantics_zh": {
            "campaign_definition": CAMPAIGN_DEFINITION,
            "subtypes": [{"subtype": SUBTYPE, "core_concepts": CORE_CONCEPTS}],
        },
        "network_config": "default",
        "derived_languages": ["en", "es", "fr"],
        "created_at": "2026-08-27T20:30:00Z",
        "created_by": "ai",
        "frozen_at": "2026-08-27",
        "frozen_by": "user",
        "content_sha256": query_hash,
        "content_sha256_scope": "canonical jq -cS .queries",
        "content_sha256_status": "verified_frozen",
        "revision_reason": "用户要求独立下载60条真实打架正样本；从旧只读CCTV打架边界迁移。",
        "queries": queries,
    }
    QUERY_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUERY_PATH.write_text(
        json.dumps(query_pack, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    policy = json.loads(POLICY_SOURCE.read_text(encoding="utf-8"))
    policy.setdefault("compatible_concept_packs", {})[
        concept["concept_pack_version"]
    ] = concept["content_sha256"]
    policy.setdefault("task_matching", {})["fight_positive_v1"] = {
        SUBTYPE: {
            "direct_aliases": [
                "fight", "fighting", "fist fight", "physical fight", "brawl",
                "street brawl", "group fight", "punching", "throws punches",
                "kicking", "pelea", "pelea a puñetazos", "pelea física",
                "pelea callejera", "pelea grupal", "riña", "golpeándose",
                "pateando", "bagarre", "bagarre physique", "bagarre de rue",
                "bagarre de groupe", "rixe", "coups de poing", "coups de pied",
                "打架", "斗殴", "互殴", "群殴", "拳打脚踢", "拳打", "脚踢", "掌掴", "扭打",
            ],
            "conjunction_groups": [],
            "forbidden_terms": [
                "argument", "verbal confrontation", "no fight", "play fight",
                "play fighting", "prank", "staged", "sparring", "training",
                "martial arts practice", "boxing match", "wrestling match",
                "dance", "choreography", "shooting", "gunfight", "stabbing",
                "knife attack", "discusión", "sin pelea", "pelea de juego",
                "broma", "entrenamiento", "combate de boxeo", "apuñalamiento",
                "ataque con cuchillo", "dispute verbale", "sans bagarre",
                "fausse bagarre", "entraînement", "match de boxe", "agression au couteau",
                "口角", "对峙", "没有打架", "假装打架", "玩闹", "对练", "训练",
                "拳击比赛", "摔跤比赛", "枪击", "持刀攻击", "捅刺",
            ],
        }
    }
    policy.update(
        {
            "policy_version": "surveillance_scoring_v1.9.0",
            "status": "frozen",
            "frozen_at": "2026-08-27",
            "frozen_by": "user",
            "revision_from": "surveillance_scoring_v1.8.0",
            "revision_reason": "新增独立fight_positive_v1真实身体打架任务；来源门和既有任务规则不变。",
        }
    )
    policy["content_sha256"] = _canonical_hash(
        policy,
        (
            "status",
            "frozen_at",
            "frozen_by",
            "content_sha256",
            "content_sha256_scope",
        ),
    )
    POLICY_PATH.write_text(
        json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
