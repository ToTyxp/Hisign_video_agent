"""Add a second long-tail location slice for the frozen real-fight campaign."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/fight_positive_v1/fight_positive_v1.qp.v1.1.0.json"
DESTINATION = ROOT / "query-packs/fight_positive_v1/fight_positive_v1.qp.v1.2.0.json"
QUERIES = (
    ("en", "CCTV fist fight restaurant raw footage", "CCTV", "fist fight restaurant"),
    ("en", "security camera brawl school entrance raw video", "security camera", "brawl school entrance"),
    ("en", "surveillance camera physical fight nightclub entrance uncut", "surveillance camera", "physical fight nightclub entrance"),
    ("en", "CCTV group fight subway entrance caught on camera", "CCTV", "group fight subway entrance"),
    ("en", "security footage people punching apartment hallway", "security footage", "people punching apartment hallway"),
    ("es", "CCTV pelea a puñetazos restaurante grabación original", "CCTV", "pelea a puñetazos restaurante"),
    ("es", "cámara de seguridad pelea entrada de escuela video sin editar", "cámara de seguridad", "pelea entrada de escuela"),
    ("es", "cámara de vigilancia pelea física entrada de discoteca grabación completa", "cámara de vigilancia", "pelea física entrada de discoteca"),
    ("es", "CCTV pelea grupal entrada de metro captada por cámara", "CCTV", "pelea grupal entrada de metro"),
    ("es", "grabación de vigilancia personas golpeándose pasillo de apartamento", "grabación de vigilancia", "personas golpeándose pasillo de apartamento"),
    ("fr", "CCTV bagarre à coups de poing restaurant vidéo brute", "CCTV", "bagarre à coups de poing restaurant"),
    ("fr", "caméra de sécurité bagarre entrée école vidéo brute", "caméra de sécurité", "bagarre entrée école"),
    ("fr", "caméra de surveillance bagarre physique entrée boîte de nuit séquence complète", "caméra de surveillance", "bagarre physique entrée boîte de nuit"),
    ("fr", "CCTV bagarre de groupe entrée métro filmée par caméra", "CCTV", "bagarre de groupe entrée métro"),
    ("fr", "enregistrement de surveillance personnes donnant coups de poing couloir appartement", "enregistrement de surveillance", "personnes donnant coups de poing couloir appartement"),
)


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    counters = {"en": 0, "es": 0, "fr": 0}
    queries = []
    for lang, query, source_anchor, action_term in QUERIES:
        counters[lang] += 1
        queries.append(
            {
                "query_id": f"fpv12-fight-{lang}-{counters[lang]:02d}",
                "campaign_id": "fight_positive_v1",
                "subtype": "真实打架/斗殴",
                "lang": lang,
                "query": query,
                "source_anchor": source_anchor,
                "source_pool": "surveillance",
                "action_or_scene_term": action_term,
                "rationale_zh": "从冻结真实打架定义派生第二组长尾地点；来源与0.40门不变。",
            }
        )
    canonical = json.dumps(
        queries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n"
    document.update(
        {
            "query_pack_version": "fight_positive_v1.qp.v1.2.0",
            "queries": queries,
            "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "created_at": "2026-08-27T21:30:00Z",
            "frozen_at": "2026-08-27",
            "frozen_by": "derived_from_frozen_user_concepts",
            "revision_from": "fight_positive_v1.qp.v1.1.0",
            "revision_reason": "累计唯一成功37/60；增加第二组长尾地点，不改变语义边界。",
        }
    )
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
