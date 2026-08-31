"""Add long-tail locations to the frozen real-fight campaign."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/fight_positive_v1/fight_positive_v1.qp.v1.0.0.json"
DESTINATION = ROOT / "query-packs/fight_positive_v1/fight_positive_v1.qp.v1.1.0.json"
QUERIES = (
    ("en", "CCTV fist fight convenience store raw footage", "CCTV", "fist fight convenience store"),
    ("en", "security camera brawl elevator lobby raw video", "security camera", "brawl elevator lobby"),
    ("en", "surveillance camera physical fight bar entrance uncut", "surveillance camera", "physical fight bar entrance"),
    ("en", "CCTV group fight gas station caught on camera", "CCTV", "group fight gas station"),
    ("en", "security footage people punching warehouse aisle", "security footage", "people punching warehouse aisle"),
    ("es", "CCTV pelea a puñetazos tienda grabación original", "CCTV", "pelea a puñetazos tienda"),
    ("es", "cámara de seguridad pelea vestíbulo de ascensor video sin editar", "cámara de seguridad", "pelea vestíbulo de ascensor"),
    ("es", "cámara de vigilancia pelea física entrada de bar grabación completa", "cámara de vigilancia", "pelea física entrada de bar"),
    ("es", "CCTV pelea grupal gasolinera captada por cámara", "CCTV", "pelea grupal gasolinera"),
    ("es", "grabación de vigilancia personas golpeándose pasillo de almacén", "grabación de vigilancia", "personas golpeándose pasillo de almacén"),
    ("fr", "CCTV bagarre à coups de poing supérette vidéo brute", "CCTV", "bagarre à coups de poing supérette"),
    ("fr", "caméra de sécurité bagarre hall ascenseur vidéo brute", "caméra de sécurité", "bagarre hall ascenseur"),
    ("fr", "caméra de surveillance bagarre physique entrée de bar séquence complète", "caméra de surveillance", "bagarre physique entrée de bar"),
    ("fr", "CCTV bagarre de groupe station-service filmée par caméra", "CCTV", "bagarre de groupe station-service"),
    ("fr", "enregistrement de surveillance personnes donnant coups de poing allée entrepôt", "enregistrement de surveillance", "personnes donnant coups de poing allée entrepôt"),
)


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    counters = {"en": 0, "es": 0, "fr": 0}
    queries = []
    for lang, query, source_anchor, action_term in QUERIES:
        counters[lang] += 1
        queries.append(
            {
                "query_id": f"fpv11-fight-{lang}-{counters[lang]:02d}",
                "campaign_id": "fight_positive_v1",
                "subtype": "真实打架/斗殴",
                "lang": lang,
                "query": query,
                "source_anchor": source_anchor,
                "source_pool": "surveillance",
                "action_or_scene_term": action_term,
                "rationale_zh": (
                    "从冻结真实打架定义派生便利店、电梯厅、酒吧入口、加油站和仓库"
                    "长尾地点；只扩数据，不改变固定监控来源或0.40门。"
                ),
            }
        )
    canonical = json.dumps(
        queries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n"
    document.update(
        {
            "query_pack_version": "fight_positive_v1.qp.v1.1.0",
            "queries": queries,
            "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "created_at": "2026-08-27T21:00:00Z",
            "frozen_at": "2026-08-27",
            "frozen_by": "derived_from_frozen_user_concepts",
            "revision_from": "fight_positive_v1.qp.v1.0.0",
            "revision_reason": "v1.0下载27条后Frontier耗尽；增加长尾地点，不改变语义边界。",
        }
    )
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
