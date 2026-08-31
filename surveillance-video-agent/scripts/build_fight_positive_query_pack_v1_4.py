"""Add a final location slice to fill the remaining real-fight target."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/fight_positive_v1/fight_positive_v1.qp.v1.3.0.json"
DESTINATION = ROOT / "query-packs/fight_positive_v1/fight_positive_v1.qp.v1.4.0.json"
QUERIES = (
    ("en", "CCTV fist fight port terminal raw footage", "CCTV", "fist fight port terminal"),
    ("en", "security camera brawl railway station raw video", "security camera", "brawl railway station"),
    ("en", "surveillance camera physical fight hospital emergency entrance uncut", "surveillance camera", "physical fight hospital emergency entrance"),
    ("en", "CCTV group fight stadium entrance caught on camera", "CCTV", "group fight stadium entrance"),
    ("en", "security footage people punching dormitory hallway", "security footage", "people punching dormitory hallway"),
    ("es", "CCTV pelea a puñetazos terminal portuaria grabación original", "CCTV", "pelea a puñetazos terminal portuaria"),
    ("es", "cámara de seguridad pelea estación de tren video sin editar", "cámara de seguridad", "pelea estación de tren"),
    ("es", "cámara de vigilancia pelea física entrada de urgencias grabación completa", "cámara de vigilancia", "pelea física entrada de urgencias"),
    ("es", "CCTV pelea grupal entrada de estadio captada por cámara", "CCTV", "pelea grupal entrada de estadio"),
    ("es", "grabación de vigilancia personas golpeándose pasillo de residencia", "grabación de vigilancia", "personas golpeándose pasillo de residencia"),
    ("fr", "CCTV bagarre à coups de poing terminal portuaire vidéo brute", "CCTV", "bagarre à coups de poing terminal portuaire"),
    ("fr", "caméra de sécurité bagarre gare ferroviaire vidéo brute", "caméra de sécurité", "bagarre gare ferroviaire"),
    ("fr", "caméra de surveillance bagarre physique entrée urgences séquence complète", "caméra de surveillance", "bagarre physique entrée urgences"),
    ("fr", "CCTV bagarre de groupe entrée stade filmée par caméra", "CCTV", "bagarre de groupe entrée stade"),
    ("fr", "enregistrement de surveillance personnes donnant coups de poing couloir résidence", "enregistrement de surveillance", "personnes donnant coups de poing couloir résidence"),
)


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    counters = {"en": 0, "es": 0, "fr": 0}
    queries = []
    for lang, query, source_anchor, action_term in QUERIES:
        counters[lang] += 1
        queries.append(
            {
                "query_id": f"fpv14-fight-{lang}-{counters[lang]:02d}",
                "campaign_id": "fight_positive_v1",
                "subtype": "真实打架/斗殴",
                "lang": lang,
                "query": query,
                "source_anchor": source_anchor,
                "source_pool": "surveillance",
                "action_or_scene_term": action_term,
                "rationale_zh": "从冻结真实打架定义派生最终长尾地点；来源与0.40门不变。",
            }
        )
    canonical = json.dumps(
        queries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n"
    document.update(
        {
            "query_pack_version": "fight_positive_v1.qp.v1.4.0",
            "queries": queries,
            "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "created_at": "2026-08-28T00:30:00Z",
            "frozen_at": "2026-08-28",
            "frozen_by": "derived_from_frozen_user_concepts",
            "revision_from": "fight_positive_v1.qp.v1.3.0",
            "revision_reason": "累计唯一成功57/60；增加最终长尾地点补齐3条，不改变语义边界。",
        }
    )
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
