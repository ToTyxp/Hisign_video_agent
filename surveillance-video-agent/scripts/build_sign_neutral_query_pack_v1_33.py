"""Freeze neutral, task-related real-person small-sign queries for v1.33."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"query-packs/sign_action_v1/sign_action_v1.qp.v1.32.0.json"
DESTINATION=ROOT/"query-packs/sign_action_v1/sign_action_v1.qp.v1.33.0.json"
LOCATIONS={
 "en":["sidewalk","storefront","campus gate","hospital entrance","community center","town square","station entrance","factory gate","courthouse steps","apartment entrance"],
 "es":["acera","frente de tienda","puerta universitaria","entrada de hospital","centro comunitario","plaza municipal","entrada de estación","puerta de fábrica","escaleras del juzgado","entrada de apartamento"],
 "fr":["trottoir","devant magasin","portail université","entrée hôpital","centre communautaire","place mairie","entrée gare","portail usine","marches tribunal","entrée immeuble"],
}
FORMS={
 "en":["one person holding a handwritten protest sign","two people displaying cardboard message signs","small group holding a banner with a message","one person raising a placard","two people holding information signs"],
 "es":["una persona con cartel de protesta escrito a mano","dos personas mostrando carteles de cartón con mensaje","grupo pequeño con pancarta de mensaje","una persona levantando una pancarta","dos personas con carteles informativos"],
 "fr":["une personne avec pancarte de protestation manuscrite","deux personnes montrant pancartes carton avec message","petit groupe avec banderole de message","une personne levant une pancarte","deux personnes avec pancartes informatives"],
}
ANCHORS={"en":["phone video","#shorts","reel","vertical video","short video"],"es":["vídeo móvil","shorts","reel","vídeo vertical","video corto"],"fr":["vidéo téléphone","shorts","reel","vidéo verticale","vidéo courte"]}
def main():
 d=json.loads(SOURCE.read_text(encoding="utf-8"));qs=[]
 for lang,locations in LOCATIONS.items():
  for idx,location in enumerate(locations[:8]):
   for form_index,form in enumerate(FORMS[lang]):
    number=idx*len(FORMS[lang])+form_index+1; anchor=ANCHORS[lang][form_index]
    action=f"{form} {location}";qs.append({"query_id":f"sav133-neutral-{lang}-{number:02d}","campaign_id":"sign_action_v1","subtype":"举牌/横幅","lang":lang,"query":f"{action} {anchor}","source_anchor":anchor,"source_pool":"mobile_adjacent","action_or_scene_term":action,"rationale_zh":"从冻结的1至5名直接参与者手持、展示、高举牌子或横幅定义派生中性真实人物场景；每条含手机或短视频来源锚点，避免教程、广告、新闻、采访、影视和游戏表达，保持来源门、原始0.440最大相似度门与全部硬排除。"})
 assert len(qs)==120
 c=json.dumps(qs,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n"
 d.update({"query_pack_version":"sign_action_v1.qp.v1.33.0","queries":qs,"content_sha256":hashlib.sha256(c.encode()).hexdigest(),"created_at":"2026-08-28T19:00:00Z","frozen_at":"2026-08-28","frozen_by":"derived_from_frozen_user_concepts","revision_from":"sign_action_v1.qp.v1.32.0","revision_reason":"用户要求中性但任务相关的真实人物小规模举牌查询；以单人、两人和小组手写牌、纸板牌、信息牌、标语牌与横幅覆盖10个具体地点，保持1至5人、手机来源和全部硬排除。"})
 DESTINATION.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if __name__=="__main__":main()
