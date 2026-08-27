"""Build action-phrase-locked demand queries from frozen Chinese semantics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/demand_action_v1/demand_action_v1.qp.v1.2.0.json"
DESTINATION = ROOT / "query-packs/demand_action_v1/demand_action_v1.qp.v1.3.0.json"

QUERY_TERMS = {
    ("举牌/横幅", "en"): (
        ("CCTV", "holding a protest sign"),
        ("security camera", "protest banner"),
        ("surveillance footage", "picket sign"),
    ),
    ("举牌/横幅", "es"): (
        ("cámara de vigilancia", "manifestante con cartel"),
        ("cámara de seguridad", "pancarta de protesta"),
        ("videovigilancia", "piquete con cartel"),
    ),
    ("举牌/横幅", "fr"): (
        ("caméra de surveillance", "manifestant avec pancarte"),
        ("caméra de sécurité", "banderole de protestation"),
        ("vidéosurveillance", "piquet avec pancarte"),
    ),
    ("下跪", "en"): (
        ("CCTV", "kneeling protest"),
        ("security camera", "on knees begging"),
        ("surveillance footage", "kneeling plea"),
    ),
    ("下跪", "es"): (
        ("cámara de vigilancia", "protesta de rodillas"),
        ("cámara de seguridad", "de rodillas pidiendo ayuda"),
        ("videovigilancia", "súplica de rodillas"),
    ),
    ("下跪", "fr"): (
        ("caméra de surveillance", "manifestation à genoux"),
        ("caméra de sécurité", "à genoux pour demander de l'aide"),
        ("vidéosurveillance", "supplie à genoux"),
    ),
    ("静坐", "en"): (
        ("CCTV", "sit-in protest"),
        ("security camera", "seated protest"),
        ("surveillance footage", "sitting blockade"),
    ),
    ("静坐", "es"): (
        ("cámara de vigilancia", "sentada de protesta"),
        ("cámara de seguridad", "manifestantes sentados"),
        ("videovigilancia", "bloqueo sentados"),
    ),
    ("静坐", "fr"): (
        ("caméra de surveillance", "sit-in de protestation"),
        ("caméra de sécurité", "manifestants assis"),
        ("vidéosurveillance", "blocage assis"),
    ),
}
SLUGS = {"举牌/横幅": "sign-banner", "下跪": "kneeling", "静坐": "sit-in"}


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    queries = []
    for subtype in ("举牌/横幅", "下跪", "静坐"):
        for lang in ("en", "es", "fr"):
            for ordinal, (anchor, action) in enumerate(
                QUERY_TERMS[(subtype, lang)], 1
            ):
                query = f'{anchor} "{action}"'
                if anchor not in query or action not in query:
                    raise ValueError("action-locked query lost a required term")
                queries.append(
                    {
                        "query_id": f"dav13-{SLUGS[subtype]}-{lang}-{ordinal:02d}",
                        "campaign_id": "demand_action_v1",
                        "subtype": subtype,
                        "lang": lang,
                        "query": query,
                        "source_anchor": anchor,
                        "action_or_scene_term": action,
                        "rationale_zh": (
                            "保持冻结中文动作边界；移除会稀释检索的地点词与泛化原始性词，"
                            "使用带引号的本语言动作短语锁定结果。"
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
            "query_pack_version": "demand_action_v1.qp.v1.3.0",
            "queries": queries,
            "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "content_sha256_status": "verified_frozen",
            "created_at": "2026-08-27T02:00:00Z",
            "created_by": "ai",
            "frozen_at": "2026-08-27",
            "frozen_by": "user",
            "status": "frozen",
            "revision_from": "demand_action_v1.qp.v1.2.0",
            "revision_reason": (
                "两轮可视 Pilot 的 demand 任务可用率为0/6；不改变冻结中文语义、"
                "来源门或评分阈值，仅将多语言查询改为短动作短语锁定，以收集新数据。"
            ),
        }
    )
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
