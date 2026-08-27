"""Build broader short-form sign queries without changing mobile source policy."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.0.0.json"
DESTINATION = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.1.0.json"
TERMS = {
    "en": (
        ("shorts", "protest sign"),
        ("short video", "protesters holding signs"),
        ("vertical video", "protest banner"),
        ("reel", "picket sign"),
        ("mobile video", "holding placard"),
    ),
    "es": (
        ("video corto", "cartel de protesta"),
        ("video vertical", "manifestantes con carteles"),
        ("shorts", "pancarta de protesta"),
        ("reel", "piquete con cartel"),
        ("vídeo móvil", "sosteniendo pancarta"),
    ),
    "fr": (
        ("vidéo courte", "pancarte de protestation"),
        ("vidéo verticale", "manifestants avec pancartes"),
        ("shorts", "banderole de protestation"),
        ("reel", "piquet avec pancarte"),
        ("vidéo mobile", "tenant une pancarte"),
    ),
}


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    queries = []
    for lang in ("en", "es", "fr"):
        for ordinal, (anchor, action) in enumerate(TERMS[lang], 1):
            queries.append(
                {
                    "query_id": f"sav11-sign-banner-{lang}-{ordinal:02d}",
                    "campaign_id": "sign_action_v1",
                    "subtype": "举牌/横幅",
                    "lang": lang,
                    "query": f"{action} {anchor}",
                    "source_anchor": anchor,
                    "source_pool": "mobile_adjacent",
                    "action_or_scene_term": action,
                    "rationale_zh": (
                        "用户观察到短视频平台存在较多举牌样本；使用短视频格式词作为"
                        "手机邻近来源锚点，动作短语不加引号以避免平台搜索过度收窄。"
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
            "query_pack_version": "sign_action_v1.qp.v1.1.0",
            "queries": queries,
            "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "created_at": "2026-08-27T04:00:00Z",
            "frozen_at": "2026-08-27",
            "revision_from": "sign_action_v1.qp.v1.0.0",
            "revision_reason": (
                "手机举牌 v1.0 来源合格池偏小；保留来源门，放宽平台搜索表达为"
                "shorts/reel/短视频格式锚点加举牌动作词。"
            ),
        }
    )
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
