"""Build a UGC-first sign query pack without relaxing a frozen gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.6.0.json"
DESTINATION = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.7.0.json"

QUERIES = (
    ("en", "one person holding protest sign #shorts", "#shorts", "one person holding protest sign"),
    ("en", "woman holding protest placard phone video", "phone video", "woman holding protest placard"),
    ("en", "man holding picket sign vertical video", "vertical video", "man holding picket sign"),
    ("en", "my protest sign short video", "short video", "my protest sign"),
    ("en", "standing with protest banner mobile video", "mobile video", "standing with protest banner"),
    ("es", "una persona sosteniendo cartel de protesta shorts", "shorts", "una persona sosteniendo cartel de protesta"),
    ("es", "mujer con pancarta de protesta vídeo móvil", "vídeo móvil", "mujer con pancarta de protesta"),
    ("es", "hombre con cartel de protesta vídeo vertical", "vídeo vertical", "hombre con cartel de protesta"),
    ("es", "mi cartel de protesta video corto", "video corto", "mi cartel de protesta"),
    ("es", "persona de pie con pancarta de protesta reel", "reel", "persona de pie con pancarta de protesta"),
    ("fr", "une personne tenant une pancarte de protestation shorts", "shorts", "une personne tenant une pancarte de protestation"),
    ("fr", "femme avec pancarte de protestation vidéo mobile", "vidéo mobile", "femme avec pancarte de protestation"),
    ("fr", "homme tenant pancarte de protestation vidéo verticale", "vidéo verticale", "homme tenant pancarte de protestation"),
    ("fr", "ma pancarte de protestation vidéo courte", "vidéo courte", "ma pancarte de protestation"),
    ("fr", "personne debout avec banderole de protestation reel", "reel", "personne debout avec banderole de protestation"),
)


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    counters = {"en": 0, "es": 0, "fr": 0}
    queries = []
    for lang, query, source_anchor, action_term in QUERIES:
        counters[lang] += 1
        queries.append(
            {
                "query_id": f"sav17-sign-banner-{lang}-{counters[lang]:02d}",
                "campaign_id": "sign_action_v1",
                "subtype": "举牌/横幅",
                "lang": lang,
                "query": query,
                "source_anchor": source_anchor,
                "source_pool": "mobile_adjacent",
                "action_or_scene_term": action_term,
                "rationale_zh": (
                    "人工反馈显示新闻包装过多；保持手机来源与举牌动作双锚点，"
                    "用单人、人物和第一人称表达扩展UGC搜索空间，不修改门槛。"
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
            "query_pack_version": "sign_action_v1.qp.v1.7.0",
            "queries": queries,
            "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "created_at": "2026-08-27T13:00:00Z",
            "frozen_at": "2026-08-27",
            "frozen_by": "user_feedback",
            "revision_from": "sign_action_v1.qp.v1.6.0",
            "revision_reason": (
                "本轮18条人工反馈仅6条来源正确；转向UGC/单人自然表达收集更多数据，"
                "保持0.440阈值、1至5人上限和全部硬排除不变。"
            ),
        }
    )
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
