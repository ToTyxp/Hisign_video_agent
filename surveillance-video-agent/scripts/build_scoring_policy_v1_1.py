"""Build frozen scoring v1.1 multilingual alias/conjunction rules."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/scoring-policy.v1.0.0.draft.json"
DESTINATION = ROOT / "query-packs/scoring-policy.v1.1.0.json"


PHYSICAL_ATTACK = [
    "punching", "throws a punch", "kicking", "stabbing", "knife attack",
    "physical assault", "street brawl", "bar brawl", "violent attack",
    "dando puñetazos", "dando patadas", "apuñalamiento", "agresión física",
    "pelea callejera", "coups de poing", "coups de pied", "agression physique",
    "bagarre de rue", "拳打", "脚踢", "捅刺", "持刀攻击", "身体攻击", "街头斗殴",
]


RULES = {
    "demand_action_v1": {
        "举牌/横幅": {
            "direct_aliases": [
                "holding protest sign", "protesters with signs", "protest placard",
                "protest banner", "picket line", "manifestante con cartel",
                "protesta con pancartas", "pancarta protesta", "piquete de protesta",
                "manifestant avec pancarte", "pancarte manifestation",
                "banderole de protestation", "piquet de grève",
            ],
            "conjunction_groups": [
                {
                    "left": ["sign", "placard", "banner", "picket", "cartel", "pancarta", "banderole", "pancarte", "标语牌", "横幅", "牌子"],
                    "right": ["protest", "protester", "protesters", "demonstration", "petition", "strike", "protesta", "manifestante", "manifestantes", "manifestación", "petición", "huelga", "manifestant", "manifestants", "manifestation", "grève", "抗议", "请愿", "示威", "罢工"],
                    "marker": "sign/banner + demand action",
                }
            ],
            "forbidden_terms": [
                "road sign", "traffic sign", "store sign", "sign language",
                "señal de tráfico", "letrero de tienda", "lengua de signos",
                "panneau routier", "enseigne de magasin", "langue des signes",
                "交通标志", "商店招牌", "手语",
            ],
        },
        "下跪": {
            "direct_aliases": [
                "kneeling protest", "kneeling plea", "protesters kneel",
                "on knees begging", "on his knees pleading", "on her knees pleading",
                "protesta arrodillada", "de rodillas pidiendo", "súplica de rodillas",
                "manifestation à genoux", "à genoux pour demander", "supplie à genoux",
            ],
            "conjunction_groups": [
                {
                    "left": ["kneel", "kneeling", "kneels", "on knees", "on his knees", "on her knees", "arrodillado", "arrodillada", "de rodillas", "à genoux", "agenouillé", "agenouillée", "下跪", "跪地", "跪着"],
                    "right": ["protest", "petition", "plea", "pleading", "begging", "request", "protesta", "petición", "súplica", "pidiendo", "manifestation", "demande", "supplie", "protestation", "抗议", "请愿", "请求", "诉求", "跪求"],
                    "marker": "kneeling + demand/request",
                }
            ],
            "forbidden_terms": [
                "prayer", "praying", "wedding proposal", "yoga pose", "kneeling exercise",
                "oración", "rezando", "propuesta de matrimonio", "ejercicio de rodillas",
                "prière", "en train de prier", "demande en mariage", "exercice à genoux",
                "祈祷", "礼拜", "求婚", "瑜伽", "跪姿训练",
            ],
        },
        "静坐": {
            "direct_aliases": [
                "sit-in", "seated protest", "protesters sitting", "sitting blockade",
                "occupying entrance", "sentada de protesta", "manifestantes sentados",
                "bloqueo sentados", "sit-in de protestation", "manifestants assis",
                "blocage assis",
            ],
            "conjunction_groups": [
                {
                    "left": ["sit-in", "sitting", "seated", "sit down", "sentada", "sentados", "sentadas", "assis", "assise", "assises", "静坐", "坐地"],
                    "right": ["protest", "petition", "blockade", "blocking", "occupy", "occupation", "protesta", "petición", "bloqueo", "ocupación", "manifestation", "protestation", "blocage", "occupation", "抗议", "请愿", "阻挡", "占据", "诉求"],
                    "marker": "sitting + protest/blocking",
                }
            ],
            "forbidden_terms": ["sitting room", "seated exercise", "sala de estar", "ejercicio sentado", "salon", "exercice assis", "客厅", "坐姿训练"],
        },
    },
    "fight_confounder_v1": {
        "冲突但未攻击": {
            "direct_aliases": [
                "argument", "arguing", "verbal dispute", "verbal confrontation",
                "heated dispute", "standoff", "face off", "discusión", "discutiendo",
                "disputa verbal", "confrontación verbal", "enfrentamiento verbal",
                "dispute", "se disputent", "confrontation verbale", "altercation verbale",
                "争吵", "口角", "争论", "言语冲突", "对峙", "僵持",
            ],
            "conjunction_groups": [],
            "forbidden_terms": PHYSICAL_ATTACK,
        },
        "舞蹈/玩闹/训练": {
            "direct_aliases": [
                "dancing", "dance", "play fighting", "play fight", "sparring",
                "martial arts practice", "boxing practice", "combat training",
                "bailando", "baile", "pelea de juego", "entrenamiento de boxeo",
                "práctica de artes marciales", "danse", "dansant", "fausse bagarre",
                "entraînement de boxe", "entraînement martial", "跳舞", "玩闹", "打闹",
                "嬉闹", "假装打架", "对练", "拳击训练", "武术训练",
            ],
            "conjunction_groups": [],
            "forbidden_terms": [],
        },
        "非攻击性身体接触": {
            "direct_aliases": [
                "hug", "hugging", "handshake", "high five", "helping up",
                "helped to his feet", "helped to her feet", "assisting person",
                "comforting", "accidental bump", "accidental collision", "abrazo",
                "abrazando", "apretón de manos", "ayudando a levantarse",
                "choque accidental", "câlin", "se prennent dans les bras",
                "poignée de main", "aide à se relever", "collision accidentelle",
                "拥抱", "握手", "击掌", "搀扶", "扶起", "安慰", "意外碰撞",
            ],
            "conjunction_groups": [],
            "forbidden_terms": PHYSICAL_ATTACK,
        },
        "场景先验": {
            "direct_aliases": [],
            "conjunction_groups": [
                {
                    "left": ["convenience store", "store counter", "parking lot", "car park", "apartment entrance", "building entrance", "hallway", "elevator", "gas station", "bar entrance", "tienda", "estacionamiento", "entrada del edificio", "pasillo", "ascensor", "gasolinera", "supérette", "parking", "entrée d'immeuble", "couloir", "ascenseur", "station-service", "便利店", "收银台", "停车场", "公寓入口", "楼道", "电梯", "加油站"],
                    "right": ["waiting", "talking", "standing", "sitting", "walking", "queue", "gathering", "no fight", "esperando", "hablando", "de pie", "sentados", "caminando", "cola", "sin pelea", "attendant", "discutant", "debout", "assis", "marchant", "file d'attente", "sans bagarre", "等待", "交谈", "站立", "坐着", "经过", "排队", "无冲突", "没有打架"],
                    "marker": "risk scene + ordinary/non-attack behavior",
                }
            ],
            "forbidden_terms": PHYSICAL_ATTACK,
        },
    },
}


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    document["policy_version"] = "surveillance_scoring_v1.1.0"
    document["status"] = "frozen"
    document["frozen_at"] = "2026-08-26"
    document["frozen_by"] = "user"
    document["revision_from"] = "surveillance_scoring_v1.0.0"
    document["revision_reason"] = (
        "保持分值和阈值不变；从冻结中文定义派生 en/es/fr aliases、"
        "同字段组合条件和明确反例词。"
    )
    document["task_matching"] = RULES
    content = dict(document)
    for key in (
        "status", "frozen_at", "frozen_by", "content_sha256",
        "content_sha256_scope",
    ):
        content.pop(key, None)
    canonical = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    document["content_sha256"] = hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
