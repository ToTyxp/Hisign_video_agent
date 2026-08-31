"""Freeze uploader-diverse mobile small-sign queries for v1.36."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.35.0.json"
DESTINATION = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.36.0.json"

LOCATIONS = {
    "en": [
        "college gate",
        "school board meeting entrance",
        "city hall steps",
        "roadside intersection",
        "apartment complex entrance",
        "sports arena concourse",
        "workplace parking lot",
        "restaurant sidewalk",
    ],
    "es": [
        "puerta de universidad",
        "entrada de reunión del consejo escolar",
        "escalones del ayuntamiento",
        "cruce al borde de carretera",
        "entrada de complejo de apartamentos",
        "pasillo de estadio deportivo",
        "estacionamiento del lugar de trabajo",
        "acera frente a restaurante",
    ],
    "fr": [
        "portail d'université",
        "entrée de réunion du conseil scolaire",
        "marches de mairie",
        "carrefour au bord de route",
        "entrée de résidence",
        "coursive d'arène sportive",
        "parking du lieu de travail",
        "trottoir devant restaurant",
    ],
}

FORMS = {
    "en": [
        "one person displaying a handmade cardboard sign",
        "solo protester holding a handwritten placard",
        "two neighbors showing paper message boards",
        "small group raising slogan signs",
        "one person presenting a complaint poster",
    ],
    "es": [
        "una persona mostrando un cartel de cartón hecho a mano",
        "manifestante individual sosteniendo pancarta manuscrita",
        "dos vecinos mostrando carteles de papel con mensajes",
        "grupo pequeño levantando carteles con lemas",
        "una persona presentando un cartel de queja",
    ],
    "fr": [
        "une personne montrant une pancarte en carton faite main",
        "manifestant seul tenant une pancarte manuscrite",
        "deux voisins montrant des panneaux message en papier",
        "petit groupe levant des pancartes à slogan",
        "une personne présentant une affiche de plainte",
    ],
}

ANCHORS = {
    "en": [
        "YouTube Shorts",
        "vertical phone footage",
        "mobile clip",
        "witness video",
        "short cellphone video",
    ],
    "es": [
        "YouTube Shorts",
        "grabación vertical de móvil",
        "clip de móvil",
        "video de testigo",
        "video corto de celular",
    ],
    "fr": [
        "YouTube Shorts",
        "séquence verticale téléphone",
        "clip mobile",
        "vidéo de témoin",
        "courte vidéo téléphone",
    ],
}

RATIONALE_ZH = (
    "从冻结的1至5名真实参与者持牌/横幅定义派生新上传者和新现场表达；"
    "每条同时包含移动拍摄来源锚点与持牌动作/场景词，保持来源门、"
    "原始0.440最大相似度门、全局uploader cap及全部硬排除。"
)


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    queries: list[dict[str, object]] = []
    for lang, locations in LOCATIONS.items():
        for location_index, location in enumerate(locations):
            for form_index, form in enumerate(FORMS[lang], start=1):
                ordinal = location_index * len(FORMS[lang]) + form_index
                anchor = ANCHORS[lang][form_index - 1]
                action_or_scene = f"{form} {location}"
                queries.append(
                    {
                        "query_id": f"sav136-uploader-{lang}-{ordinal:02d}",
                        "campaign_id": "sign_action_v1",
                        "subtype": "举牌/横幅",
                        "lang": lang,
                        "query": f"{action_or_scene} {anchor}",
                        "source_anchor": anchor,
                        "source_pool": "mobile_adjacent",
                        "action_or_scene_term": action_or_scene,
                        "rationale_zh": RATIONALE_ZH,
                    }
                )
    assert len(queries) == 120
    canonical = json.dumps(
        queries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n"
    document.update(
        {
            "query_pack_version": "sign_action_v1.qp.v1.36.0",
            "queries": queries,
            "content_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
            "created_at": "2026-08-30T00:00:00Z",
            "frozen_at": "2026-08-30",
            "frozen_by": "derived_from_frozen_user_concepts",
            "revision_from": "sign_action_v1.qp.v1.35.0",
            "revision_reason": (
                "v1.35 Frontier耗尽且目标仍差11条；恢复120条查询并覆盖校园、"
                "市政、路边、住宅、体育、工作场所和商铺等新上传者空间；"
                "保持1至5人、来源门、0.440最大相似度和150 probe上限。"
            ),
        }
    )
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
