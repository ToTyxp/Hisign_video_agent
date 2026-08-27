"""Create v1.10 of the frozen small-scale sign query pack.

The new pack only adds concrete 1--5 participant settings already present in
the frozen sign concept.  It does not change the source gate, scale exclusion,
or the task definition.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.9.0.json"
DESTINATION = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.10.0.json"

QUERIES = (
    ("en", "one person holding sign outside city hall phone video", "phone video", "one person holding sign outside city hall"),
    ("en", "two people picketing outside school vertical video", "vertical video", "two people picketing outside school"),
    ("en", "small group holding banner at apartment entrance short video", "short video", "small group holding banner at apartment entrance"),
    ("en", "one worker with picket sign outside factory mobile video", "mobile video", "one worker with picket sign outside factory"),
    ("en", "two neighbors holding complaint signs outside building video", "phone video", "two neighbors holding complaint signs outside building"),
    ("es", "una persona con cartel frente al ayuntamiento vídeo móvil", "vídeo móvil", "una persona con cartel frente al ayuntamiento"),
    ("es", "dos personas en piquete frente a escuela vídeo vertical", "vídeo vertical", "dos personas en piquete frente a escuela"),
    ("es", "grupo pequeño con pancarta en entrada de apartamento video corto", "video corto", "grupo pequeño con pancarta en entrada de apartamento"),
    ("es", "un trabajador con cartel de piquete fuera de fábrica vídeo móvil", "vídeo móvil", "un trabajador con cartel de piquete fuera de fábrica"),
    ("es", "dos vecinos con carteles de queja fuera de edificio vídeo", "vídeo", "dos vecinos con carteles de queja fuera de edificio"),
    ("fr", "une personne avec pancarte devant mairie vidéo mobile", "vidéo mobile", "une personne avec pancarte devant mairie"),
    ("fr", "deux personnes en piquet devant école vidéo verticale", "vidéo verticale", "deux personnes en piquet devant école"),
    ("fr", "petit groupe avec banderole à entrée immeuble vidéo courte", "vidéo courte", "petit groupe avec banderole à entrée immeuble"),
    ("fr", "un travailleur avec pancarte de piquet devant usine vidéo mobile", "vidéo mobile", "un travailleur avec pancarte de piquet devant usine"),
    ("fr", "deux voisins avec panneaux de plainte devant bâtiment vidéo", "vidéo", "deux voisins avec panneaux de plainte devant bâtiment"),
)


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    counters = {"en": 0, "es": 0, "fr": 0}
    queries = []
    for lang, query, source_anchor, action_term in QUERIES:
        counters[lang] += 1
        queries.append(
            {
                "query_id": f"sav110-sign-banner-{lang}-{counters[lang]:02d}",
                "campaign_id": "sign_action_v1",
                "subtype": "举牌/横幅",
                "lang": lang,
                "query": query,
                "source_anchor": source_anchor,
                "source_pool": "mobile_adjacent",
                "action_or_scene_term": action_term,
                "rationale_zh": "从冻结的1至5名直接举牌者定义派生市政厅、学校、住宅入口和工厂门口场景；只扩充搜索数据，不改变来源门、0.440原始语义门或大规模抗议硬排除。",
            }
        )
    canonical = json.dumps(queries, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    document.update(
        {
            "query_pack_version": "sign_action_v1.qp.v1.10.0",
            "queries": queries,
            "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "created_at": "2026-08-27T16:15:00Z",
            "frozen_at": "2026-08-27",
            "frozen_by": "derived_from_frozen_user_concepts",
            "revision_from": "sign_action_v1.qp.v1.9.0",
            "revision_reason": "v1.9 的首个增量批次仅有1/13通过二次筛选；按冻结的小规模举牌定义增加未覆盖的具体地点查询，不降低任一门槛。",
        }
    )
    DESTINATION.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
