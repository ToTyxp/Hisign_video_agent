"""Add long-tail small-scale sign settings without changing frozen gates."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.10.0.json"
DESTINATION = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.11.0.json"
QUERIES = (
    ("en", "single picketer outside hospital phone video", "phone video", "single picketer outside hospital"),
    ("en", "worker holding union sign at warehouse gate vertical video", "vertical video", "worker holding union sign at warehouse gate"),
    ("en", "two tenants holding rent protest signs sidewalk short video", "short video", "two tenants holding rent protest signs sidewalk"),
    ("en", "person holding boycott placard supermarket entrance mobile video", "mobile video", "person holding boycott placard supermarket entrance"),
    ("en", "small group holding protest banner university gate phone video", "phone video", "small group holding protest banner university gate"),
    ("es", "una persona en piquete frente a hospital vídeo móvil", "vídeo móvil", "una persona en piquete frente a hospital"),
    ("es", "trabajador con cartel sindical en puerta de almacén vídeo vertical", "vídeo vertical", "trabajador con cartel sindical en puerta de almacén"),
    ("es", "dos inquilinos con carteles de protesta por alquiler video corto", "video corto", "dos inquilinos con carteles de protesta por alquiler"),
    ("es", "persona con pancarta de boicot en entrada de supermercado vídeo móvil", "vídeo móvil", "persona con pancarta de boicot en entrada de supermercado"),
    ("es", "grupo pequeño con pancarta de protesta en puerta universitaria vídeo móvil", "vídeo móvil", "grupo pequeño con pancarta de protesta en puerta universitaria"),
    ("fr", "une personne en piquet devant hôpital vidéo mobile", "vidéo mobile", "une personne en piquet devant hôpital"),
    ("fr", "travailleur avec pancarte syndicale au portail entrepôt vidéo verticale", "vidéo verticale", "travailleur avec pancarte syndicale au portail entrepôt"),
    ("fr", "deux locataires avec pancartes de protestation loyer vidéo courte", "vidéo courte", "deux locataires avec pancartes de protestation loyer"),
    ("fr", "personne avec pancarte boycott entrée supermarché vidéo mobile", "vidéo mobile", "personne avec pancarte boycott entrée supermarché"),
    ("fr", "petit groupe avec banderole de protestation portail université vidéo mobile", "vidéo mobile", "petit groupe avec banderole de protestation portail université"),
)


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    counters = {"en": 0, "es": 0, "fr": 0}
    queries = []
    for lang, query, source_anchor, action_term in QUERIES:
        counters[lang] += 1
        queries.append(
            {
                "query_id": f"sav111-sign-banner-{lang}-{counters[lang]:02d}",
                "campaign_id": "sign_action_v1",
                "subtype": "举牌/横幅",
                "lang": lang,
                "query": query,
                "source_anchor": source_anchor,
                "source_pool": "mobile_adjacent",
                "action_or_scene_term": action_term,
                "rationale_zh": (
                    "从冻结的1至5人举牌定义派生医院、仓库、租户、超市和大学门口"
                    "长尾场景；只增加搜索数据，不改变0.440门或大规模硬排除。"
                ),
            }
        )
    canonical = json.dumps(
        queries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n"
    document.update(
        {
            "query_pack_version": "sign_action_v1.qp.v1.11.0",
            "queries": queries,
            "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            "created_at": "2026-08-27T20:00:00Z",
            "frozen_at": "2026-08-27",
            "frozen_by": "derived_from_frozen_user_concepts",
            "revision_from": "sign_action_v1.qp.v1.10.0",
            "revision_reason": (
                "用户将技术成功目标提高到300；旧激活池耗尽，增加未覆盖长尾地点，"
                "保持任务定义、来源门、反馈排序和语义阈值不变。"
            ),
        }
    )
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
