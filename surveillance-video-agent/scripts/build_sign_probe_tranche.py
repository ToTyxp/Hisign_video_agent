"""Version the broad sign query space to consume the next unprobed URL tranche."""
from __future__ import annotations
import argparse,hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def main()->None:
 p=argparse.ArgumentParser();p.add_argument("--source-minor",type=int,default=22);p.add_argument("--target-minor",type=int,required=True);a=p.parse_args()
 source=ROOT/f"query-packs/sign_action_v1/sign_action_v1.qp.v1.{a.source_minor}.0.json"
 destination=ROOT/f"query-packs/sign_action_v1/sign_action_v1.qp.v1.{a.target_minor}.0.json"
 d=json.loads(source.read_text(encoding="utf-8"));qs=[]
 for index,item in enumerate(d["queries"],1):
  revised=dict(item);revised["query_id"]=f"sav1{a.target_minor}0-tranche-{index:03d}-{item['lang']}";revised["rationale_zh"]="冻结宽查询空间的下一probe tranche；跳过已probe候选，不改变任务、来源门或0.440门。";qs.append(revised)
 canonical=json.dumps(qs,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n"
 d.update({"query_pack_version":f"sign_action_v1.qp.v1.{a.target_minor}.0","queries":qs,"content_sha256":hashlib.sha256(canonical.encode()).hexdigest(),"created_at":"2026-08-28T07:00:00Z","frozen_at":"2026-08-28","frozen_by":"derived_from_frozen_user_concepts","revision_from":d["query_pack_version"],"revision_reason":"复用冻结宽查询文本，消费该搜索空间中尚未probe的下一批候选；所有门槛不变。"})
 destination.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 print(destination)
if __name__=="__main__":main()
