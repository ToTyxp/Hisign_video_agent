"""Expand frozen sign concepts into concrete small-scale street settings."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.8.0.json"
DESTINATION = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.9.0.json"

QUERIES = (
    ("en", "lone protester holding sign on sidewalk #shorts", "#shorts", "lone protester holding sign on sidewalk"),
    ("en", "person holding complaint sign outside store phone video", "phone video", "person holding complaint sign outside store"),
    ("en", "worker holding picket sign at gate vertical video", "vertical video", "worker holding picket sign at gate"),
    ("en", "two people displaying banner outside building short video", "short video", "two people displaying banner outside building"),
    ("en", "small roadside protest with signs mobile video", "mobile video", "small roadside protest with signs"),
    ("es", "manifestante solo con cartel en la acera shorts", "shorts", "manifestante solo con cartel en la acera"),
    ("es", "persona con cartel de queja fuera de tienda vídeo móvil", "vídeo móvil", "persona con cartel de queja fuera de tienda"),
    ("es", "trabajador con cartel de piquete en la puerta vídeo vertical", "vídeo vertical", "trabajador con cartel de piquete en la puerta"),
    ("es", "dos personas mostrando pancarta fuera de edificio video corto", "video corto", "dos personas mostrando pancarta fuera de edificio"),
    ("es", "pequeña protesta al borde de carretera con carteles vídeo móvil", "vídeo móvil", "pequeña protesta al borde de carretera con carteles"),
    ("fr", "manifestant seul avec pancarte sur le trottoir shorts", "shorts", "manifestant seul avec pancarte sur le trottoir"),
    ("fr", "personne avec panneau de plainte devant magasin vidéo mobile", "vidéo mobile", "personne avec panneau de plainte devant magasin"),
    ("fr", "travailleur avec pancarte de piquet au portail vidéo verticale", "vidéo verticale", "travailleur avec pancarte de piquet au portail"),
    ("fr", "deux personnes montrant banderole devant bâtiment vidéo courte", "vidéo courte", "deux personnes montrant banderole devant bâtiment"),
    ("fr", "petite protestation au bord de route avec pancartes vidéo mobile", "vidéo mobile", "petite protestation au bord de route avec pancartes"),
)


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    counters = {"en": 0, "es": 0, "fr": 0}
    queries = []
    for lang, query, source_anchor, action_term in QUERIES:
        counters[lang] += 1
        queries.append(
            {
                "query_id": f"sav19-sign-banner-{lang}-{counters[lang]:02d}",
                "campaign_id": "sign_action_v1",
                "subtype": "举牌/横幅",
                "lang": lang,
                "query": query,
                "source_anchor": source_anchor,
                "source_pool": "mobile_adjacent",
                "action_or_scene_term": action_term,
                "rationale_zh": (
                    "收敛策略已通过，现有Frontier耗尽；仅从冻结中文概念派生街边、"
                    "店外、门口和路边场景以补充数据，不改变任务定义和准入门槛。"
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
            "query_pack_version": "sign_action_v1.qp.v1.9.0",
            "queries": queries,
            "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "created_at": "2026-08-27T15:30:00Z",
            "frozen_at": "2026-08-27",
            "frozen_by": "derived_from_frozen_user_concepts",
            "revision_from": "sign_action_v1.qp.v1.8.0",
            "revision_reason": (
                "第三批通过收敛双门后，旧Frontier仅剩1条下载资格；扩充具体场景"
                "搜索数据，保持v1.8评分、0.440门和反馈重排权重不变。"
            ),
        }
    )
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
