"""Build a fourth sign query family with small-scale real-world settings."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.2.0.json"
DESTINATION = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.3.0.json"
TERMS = {
    "en": (
        ("shorts", "lone worker holding picket sign", "workplace entrance"),
        ("mobile video", "one person holding complaint sign", "outside store"),
        ("vertical video", "individual with protest placard", "city hall"),
        ("phone video", "small group displaying banner", "building entrance"),
        ("reel", "two people holding signs", "roadside"),
    ),
    "es": (
        ("shorts", "trabajador solo con cartel de protesta", "entrada del trabajo"),
        ("vídeo móvil", "una persona con cartel de queja", "fuera de tienda"),
        ("video vertical", "manifestante individual con pancarta", "ayuntamiento"),
        ("video de teléfono", "grupo pequeño mostrando pancarta", "entrada edificio"),
        ("reel", "dos personas sosteniendo carteles", "carretera"),
    ),
    "fr": (
        ("shorts", "travailleur seul avec piquet", "entrée du travail"),
        ("vidéo mobile", "une personne avec panneau de protestation", "devant magasin"),
        ("vidéo verticale", "manifestant individuel avec pancarte", "mairie"),
        ("filmé au téléphone", "petit groupe montrant banderole", "entrée immeuble"),
        ("reel", "deux personnes tenant pancartes", "bord de route"),
    ),
}


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    queries = []
    for lang in ("en", "es", "fr"):
        for ordinal, (anchor, action, location) in enumerate(TERMS[lang], 1):
            queries.append(
                {
                    "query_id": f"sav13-setting-sign-{lang}-{ordinal:02d}",
                    "campaign_id": "sign_action_v1",
                    "subtype": "举牌/横幅",
                    "lang": lang,
                    "query": f"{action} {anchor} {location}",
                    "source_anchor": anchor,
                    "source_pool": "mobile_adjacent",
                    "action_or_scene_term": action,
                    "rationale_zh": (
                        "保持1至5人小规模定义，增加工作场所、店外、市政厅、"
                        "建筑入口和路边等真实举牌场景切片。"
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
            "query_pack_version": "sign_action_v1.qp.v1.3.0",
            "queries": queries,
            "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "created_at": "2026-08-27T06:00:00Z",
            "frozen_at": "2026-08-27",
            "revision_from": "sign_action_v1.qp.v1.2.0",
            "revision_reason": (
                "小规模手机Pilot达到2/5可用；保持所有门槛，新增自然场景切片"
                "以扩大潜在池并向60条目标增量收敛。"
            ),
        }
    )
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
