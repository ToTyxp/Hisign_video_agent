"""Complete v1.7 with three-language terms for the already-full contact subtype."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.7.0.json"
DESTINATION=ROOT/"query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.8.0.json"
CONTACT=(
("en","CCTV people shaking hands raw footage","CCTV","people shaking hands"),
("es","CCTV personas dándose la mano grabación original","CCTV","personas dándose la mano"),
("fr","CCTV personnes se serrant la main images brutes","CCTV","personnes se serrant la main"),
)
def main()->None:
 d=json.loads(SOURCE.read_text(encoding="utf-8"));qs=[]
 for item in d["queries"]: qs.append(dict(item,query_id=item["query_id"].replace("fcv17-","fcv18-",1)))
 for lang,query,anchor,action in CONTACT: qs.append({"query_id":f"fcv18-contact-{lang}-01","campaign_id":"fight_confounder_v1","subtype":"非攻击性身体接触","lang":lang,"query":query,"source_anchor":anchor,"source_pool":"surveillance","action_or_scene_term":action,"rationale_zh":"补齐查询包三语义词契约；该subtype已达标，不扩大其下载配额。"})
 canonical=json.dumps(qs,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n"
 d.update({"query_pack_version":"fight_confounder_v1.qp.v1.8.0","queries":qs,"content_sha256":hashlib.sha256(canonical.encode()).hexdigest(),"created_at":"2026-08-28T03:30:00Z","frozen_at":"2026-08-28","frozen_by":"derived_from_frozen_user_concepts","revision_from":"fight_confounder_v1.qp.v1.7.0","revision_reason":"补齐已达标非攻击接触subtype的en/es/fr语义词，使校准查询契约完整；配额不变。"})
 DESTINATION.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if __name__=="__main__":main()
