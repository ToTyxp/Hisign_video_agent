"""Add distinct frozen small-scale sign settings for download-only expansion."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.11.0.json"
DESTINATION = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.12.0.json"
QUERIES = (
    ("en", "one person holding complaint sign outside courthouse phone video", "phone video", "one person holding complaint sign outside courthouse"),
    ("en", "two workers with picket signs at transit depot vertical video", "vertical video", "two workers with picket signs at transit depot"),
    ("en", "small group with protest banner library entrance short video", "short video", "small group with protest banner library entrance"),
    ("en", "tenant holding housing protest sign outside office mobile video", "mobile video", "tenant holding housing protest sign outside office"),
    ("en", "two people displaying petition banner community center phone video", "phone video", "two people displaying petition banner community center"),
    ("es", "una persona con cartel de queja frente al juzgado vídeo móvil", "vídeo móvil", "una persona con cartel de queja frente al juzgado"),
    ("es", "dos trabajadores con carteles de piquete en depósito de transporte vídeo vertical", "vídeo vertical", "dos trabajadores con carteles de piquete en depósito de transporte"),
    ("es", "grupo pequeño con pancarta de protesta en entrada de biblioteca video corto", "video corto", "grupo pequeño con pancarta de protesta en entrada de biblioteca"),
    ("es", "inquilino con cartel de protesta por vivienda fuera de oficina vídeo móvil", "vídeo móvil", "inquilino con cartel de protesta por vivienda fuera de oficina"),
    ("es", "dos personas mostrando pancarta de petición en centro comunitario vídeo móvil", "vídeo móvil", "dos personas mostrando pancarta de petición en centro comunitario"),
    ("fr", "une personne avec pancarte de plainte devant tribunal vidéo mobile", "vidéo mobile", "une personne avec pancarte de plainte devant tribunal"),
    ("fr", "deux travailleurs avec pancartes de piquet au dépôt de transport vidéo verticale", "vidéo verticale", "deux travailleurs avec pancartes de piquet au dépôt de transport"),
    ("fr", "petit groupe avec banderole de protestation entrée bibliothèque vidéo courte", "vidéo courte", "petit groupe avec banderole de protestation entrée bibliothèque"),
    ("fr", "locataire avec pancarte protestation logement devant bureau vidéo mobile", "vidéo mobile", "locataire avec pancarte protestation logement devant bureau"),
    ("fr", "deux personnes montrant banderole de pétition centre communautaire vidéo mobile", "vidéo mobile", "deux personnes montrant banderole de pétition centre communautaire"),
)

def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8")); counters = {"en": 0, "es": 0, "fr": 0}; queries = []
    for lang, query, anchor, action in QUERIES:
        counters[lang] += 1
        queries.append({"query_id": f"sav112-sign-banner-{lang}-{counters[lang]:02d}", "campaign_id": "sign_action_v1", "subtype": "举牌/横幅", "lang": lang, "query": query, "source_anchor": anchor, "source_pool": "mobile_adjacent", "action_or_scene_term": action, "rationale_zh": "从冻结的1至5人直接举牌定义派生法院、交通枢纽、图书馆、办公室和社区中心长尾场景；不改变来源、0.440原始语义门或大规模硬排除。"})
    canonical = json.dumps(queries, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    document.update({"query_pack_version": "sign_action_v1.qp.v1.12.0", "queries": queries, "content_sha256": hashlib.sha256(canonical.encode()).hexdigest(), "created_at": "2026-08-28T00:00:00Z", "frozen_at": "2026-08-28", "frozen_by": "derived_from_frozen_user_concepts", "revision_from": "sign_action_v1.qp.v1.11.0", "revision_reason": "下载目标尚有178条缺口；仅扩展冻结小规模举牌的未覆盖地点，不改变门槛。"})
    DESTINATION.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__": main()
