"""Add a third long-tail location slice for the frozen real-fight campaign."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/fight_positive_v1/fight_positive_v1.qp.v1.2.0.json"
DESTINATION = ROOT / "query-packs/fight_positive_v1/fight_positive_v1.qp.v1.3.0.json"
QUERIES = (
    ("en", "CCTV fist fight airport terminal raw footage", "CCTV", "fist fight airport terminal"),
    ("en", "security camera brawl hotel lobby raw video", "security camera", "brawl hotel lobby"),
    ("en", "surveillance camera physical fight shopping mall uncut", "surveillance camera", "physical fight shopping mall"),
    ("en", "CCTV group fight bus station caught on camera", "CCTV", "group fight bus station"),
    ("en", "security footage people punching office corridor", "security footage", "people punching office corridor"),
    ("es", "CCTV pelea a puñetazos terminal de aeropuerto grabación original", "CCTV", "pelea a puñetazos terminal de aeropuerto"),
    ("es", "cámara de seguridad pelea vestíbulo de hotel video sin editar", "cámara de seguridad", "pelea vestíbulo de hotel"),
    ("es", "cámara de vigilancia pelea física centro comercial grabación completa", "cámara de vigilancia", "pelea física centro comercial"),
    ("es", "CCTV pelea grupal estación de autobuses captada por cámara", "CCTV", "pelea grupal estación de autobuses"),
    ("es", "grabación de vigilancia personas golpeándose pasillo de oficina", "grabación de vigilancia", "personas golpeándose pasillo de oficina"),
    ("fr", "CCTV bagarre à coups de poing terminal aéroport vidéo brute", "CCTV", "bagarre à coups de poing terminal aéroport"),
    ("fr", "caméra de sécurité bagarre hall hôtel vidéo brute", "caméra de sécurité", "bagarre hall hôtel"),
    ("fr", "caméra de surveillance bagarre physique centre commercial séquence complète", "caméra de surveillance", "bagarre physique centre commercial"),
    ("fr", "CCTV bagarre de groupe gare routière filmée par caméra", "CCTV", "bagarre de groupe gare routière"),
    ("fr", "enregistrement de surveillance personnes donnant coups de poing couloir bureau", "enregistrement de surveillance", "personnes donnant coups de poing couloir bureau"),
)


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    counters = {"en": 0, "es": 0, "fr": 0}
    queries = []
    for lang, query, source_anchor, action_term in QUERIES:
        counters[lang] += 1
        queries.append(
            {
                "query_id": f"fpv13-fight-{lang}-{counters[lang]:02d}",
                "campaign_id": "fight_positive_v1",
                "subtype": "真实打架/斗殴",
                "lang": lang,
                "query": query,
                "source_anchor": source_anchor,
                "source_pool": "surveillance",
                "action_or_scene_term": action_term,
                "rationale_zh": "从冻结真实打架定义派生第三组长尾地点；来源与0.40门不变。",
            }
        )
    canonical = json.dumps(
        queries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n"
    document.update(
        {
            "query_pack_version": "fight_positive_v1.qp.v1.3.0",
            "queries": queries,
            "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "created_at": "2026-08-27T22:00:00Z",
            "frozen_at": "2026-08-27",
            "frozen_by": "derived_from_frozen_user_concepts",
            "revision_from": "fight_positive_v1.qp.v1.2.0",
            "revision_reason": "累计唯一成功约48/60；增加第三组长尾地点，不改变语义边界。",
        }
    )
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
