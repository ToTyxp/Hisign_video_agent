"""Build a second UGC query expansion after feedback rerank validation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.7.0.json"
DESTINATION = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.8.0.json"

QUERIES = (
    ("en", "lone protester holding cardboard sign #shorts", "#shorts", "lone protester holding cardboard sign"),
    ("en", "woman standing with handwritten protest sign phone video", "phone video", "woman standing with handwritten protest sign"),
    ("en", "man displaying protest placard vertical video", "vertical video", "man displaying protest placard"),
    ("en", "two people holding protest banner short video", "short video", "two people holding protest banner"),
    ("en", "small picket line mobile video", "mobile video", "small picket line"),
    ("es", "manifestante solo con cartel de cartón shorts", "shorts", "manifestante solo con cartel de cartón"),
    ("es", "mujer de pie con cartel de protesta escrito a mano vídeo móvil", "vídeo móvil", "mujer de pie con cartel de protesta escrito a mano"),
    ("es", "hombre mostrando pancarta de protesta vídeo vertical", "vídeo vertical", "hombre mostrando pancarta de protesta"),
    ("es", "dos personas sosteniendo pancarta de protesta video corto", "video corto", "dos personas sosteniendo pancarta de protesta"),
    ("es", "pequeño piquete video móvil", "video móvil", "pequeño piquete"),
    ("fr", "manifestant seul avec pancarte en carton shorts", "shorts", "manifestant seul avec pancarte en carton"),
    ("fr", "femme debout avec pancarte de protestation manuscrite vidéo mobile", "vidéo mobile", "femme debout avec pancarte de protestation manuscrite"),
    ("fr", "homme montrant pancarte de protestation vidéo verticale", "vidéo verticale", "homme montrant pancarte de protestation"),
    ("fr", "deux personnes tenant banderole de protestation vidéo courte", "vidéo courte", "deux personnes tenant banderole de protestation"),
    ("fr", "petit piquet vidéo mobile", "vidéo mobile", "petit piquet"),
)


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    counters = {"en": 0, "es": 0, "fr": 0}
    queries = []
    for lang, query, source_anchor, action_term in QUERIES:
        counters[lang] += 1
        queries.append(
            {
                "query_id": f"sav18-sign-banner-{lang}-{counters[lang]:02d}",
                "campaign_id": "sign_action_v1",
                "subtype": "举牌/横幅",
                "lang": lang,
                "query": query,
                "source_anchor": source_anchor,
                "source_pool": "mobile_adjacent",
                "action_or_scene_term": action_term,
                "rationale_zh": (
                    "第二批仍缺安全余量；保持手机来源和举牌动作双锚点，"
                    "用纸板牌、手写牌、1至2人和小型纠察表达扩展新数据。"
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
            "query_pack_version": "sign_action_v1.qp.v1.8.0",
            "queries": queries,
            "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "created_at": "2026-08-27T14:30:00Z",
            "frozen_at": "2026-08-27",
            "frozen_by": "user_feedback",
            "revision_from": "sign_action_v1.qp.v1.7.0",
            "revision_reason": (
                "第二批6/19任务可用、8/19来源正确；不降低门槛，新增另一组"
                "小规模UGC表达并配合已验证的人工反馈重排。"
            ),
        }
    )
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
