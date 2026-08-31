"""Add another unused location slice for the frozen fight-control campaign."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.4.0.json"
DESTINATION = ROOT / "query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.5.0.json"
REPLACEMENTS = {
    "en": {"bus terminal":"ferry terminal", "restaurant lobby":"hospital lobby", "school gate":"stadium gate"},
    "es": {"terminal de autobuses":"terminal de ferry", "vestíbulo de restaurante":"vestíbulo de hospital", "entrada de escuela":"entrada de estadio"},
    "fr": {"gare routière":"terminal de ferry", "hall de restaurant":"hall d'hôpital", "entrée d'école":"entrée de stade"},
}
def main() -> None:
    document=json.loads(SOURCE.read_text(encoding="utf-8")); queries=[]
    for item in document["queries"]:
        query=item["query"]; action=item["action_or_scene_term"]; replaced=False
        for old,new in REPLACEMENTS[item["lang"]].items():
            if old in query: query=query.replace(old,new); action=action.replace(old,new); replaced=True; break
        if not replaced: raise ValueError(f"no location replacement: {item['query_id']}")
        queries.append(dict(item,query_id=item["query_id"].replace("fcv14-","fcv15-",1),query=query,action_or_scene_term=action,rationale_zh=item["rationale_zh"]+" v1.5仅改为渡轮码头、医院大厅和体育场门口；非攻击边界不变。"))
    canonical=json.dumps(queries,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n"
    document.update({"query_pack_version":"fight_confounder_v1.qp.v1.5.0","queries":queries,"content_sha256":hashlib.sha256(canonical.encode()).hexdigest(),"created_at":"2026-08-28T01:00:00Z","frozen_at":"2026-08-28","frozen_by":"derived_from_frozen_user_concepts","revision_from":"fight_confounder_v1.qp.v1.4.0","revision_reason":"剩余25条下载缺口；只增加长尾地点，保持四个非攻击subtype、固定监控来源和0.40门。"})
    DESTINATION.write_text(json.dumps(document,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if __name__ == "__main__": main()
