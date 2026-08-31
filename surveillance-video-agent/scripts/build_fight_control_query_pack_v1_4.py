"""Add unused surveillance locations while preserving non-attack task terms."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.3.0.json"
DESTINATION = ROOT / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.4.0.json"
REPLACEMENTS = {"en": {"warehouse aisle": "bus terminal", "elevator lobby": "restaurant lobby", "hotel entrance": "school gate"}, "es": {"pasillo de almacén": "terminal de autobuses", "vestíbulo de ascensor": "vestíbulo de restaurante", "entrada de hotel": "entrada de escuela"}, "fr": {"allée d'entrepôt": "gare routière", "hall d'ascenseur": "hall de restaurant", "entrée d'hôtel": "entrée d'école"}}
def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8")); queries=[]
    for item in document["queries"]:
        query=item["query"]; action=item["action_or_scene_term"]; replaced=False
        for old,new in REPLACEMENTS[item["lang"]].items():
            if old in query: query=query.replace(old,new); action=action.replace(old,new); replaced=True; break
        if not replaced: raise ValueError(f"no location replacement: {item['query_id']}")
        revised=dict(item, query_id=item["query_id"].replace("fcv13-", "fcv14-", 1), query=query, action_or_scene_term=action, rationale_zh=item["rationale_zh"]+" v1.4仅改为公交枢纽、餐厅大厅和学校门口；非攻击边界不变。")
        queries.append(revised)
    canonical=json.dumps(queries,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n"
    document.update({"query_pack_version":"fight_confounder_v1.qp.v1.4.0","queries":queries,"content_sha256":hashlib.sha256(canonical.encode()).hexdigest(),"created_at":"2026-08-28T00:00:00Z","frozen_at":"2026-08-28","frozen_by":"derived_from_frozen_user_concepts","revision_from":"fight_confounder_v1.qp.v1.3.0","revision_reason":"剩余31条下载缺口；仅替换未覆盖地点，保持四个非攻击subtype、固定监控来源和0.40门。"})
    DESTINATION.write_text(json.dumps(document,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if __name__ == "__main__": main()
