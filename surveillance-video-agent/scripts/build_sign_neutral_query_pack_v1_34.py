"""Freeze fresh neutral real-person small-sign expressions for v1.34."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"query-packs/sign_action_v1/sign_action_v1.qp.v1.33.0.json"
DESTINATION=ROOT/"query-packs/sign_action_v1/sign_action_v1.qp.v1.34.0.json"
LOC={
 "en":["transit platform","farmers market","clinic walkway","library courtyard","neighborhood street","shop entrance","campus lawn","community fair"],
 "es":["andén de transporte","mercado de agricultores","pasillo de clínica","patio de biblioteca","calle vecinal","entrada de tienda","césped de campus","feria comunitaria"],
 "fr":["quai de transport","marché fermier","allée de clinique","cour de bibliothèque","rue de quartier","entrée de magasin","pelouse de campus","foire communautaire"],
}
FORMS={
 "en":["one person showing a homemade cardboard protest sign","two people holding handwritten message boards","small group raising paper placards","solo silent protest holding a slogan sign","one-person picket displaying a banner"],
 "es":["una persona mostrando cartel de cartón hecho a mano","dos personas con carteles de mensajes escritos a mano","grupo pequeño levantando pancartas de papel","protesta silenciosa individual con cartel de lema","piquete de una persona mostrando una pancarta"],
 "fr":["une personne montrant pancarte carton faite main","deux personnes avec panneaux message manuscrits","petit groupe levant pancartes papier","protestation silencieuse solo avec pancarte slogan","piquet d'une personne montrant une banderole"],
}
ANCH={"en":["phone video","#shorts","reel","vertical video","short video"],"es":["vídeo móvil","shorts","reel","vídeo vertical","video corto"],"fr":["vidéo téléphone","shorts","reel","vidéo verticale","vidéo courte"]}
def main():
 d=json.loads(SOURCE.read_text(encoding="utf-8"));qs=[]
 for lang,places in LOC.items():
  for i,place in enumerate(places):
   for j,form in enumerate(FORMS[lang],1):
    n=i*5+j; a=ANCH[lang][j-1]; act=f"{form} {place}"
    qs.append({"query_id":f"sav134-neutral-{lang}-{n:02d}","campaign_id":"sign_action_v1","subtype":"举牌/横幅","lang":lang,"query":f"{act} {a}","source_anchor":a,"source_pool":"mobile_adjacent","action_or_scene_term":act,"rationale_zh":"从冻结的1至5名直接参与者手持、展示、高举牌子或横幅定义派生未覆盖的真实人物持牌表达；每条含手机或短视频锚点，排除教程、广告、新闻、采访、影视和游戏表达，保持来源门、原始0.440最大相似度门与全部硬排除。"})
 assert len(qs)==120
 c=json.dumps(qs,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n"
 d.update({"query_pack_version":"sign_action_v1.qp.v1.34.0","queries":qs,"content_sha256":hashlib.sha256(c.encode()).hexdigest(),"created_at":"2026-08-28T20:00:00Z","frozen_at":"2026-08-28","frozen_by":"derived_from_frozen_user_concepts","revision_from":"sign_action_v1.qp.v1.33.0","revision_reason":"当前Frontier耗尽；用户要求新上传者与未覆盖真实人物持牌/标语/纸板牌/手写牌表达，并利用扩展PeerTube实例；保持1至5人、来源门、原始0.440最大相似度和150 probe上限。"})
 DESTINATION.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if __name__=="__main__":main()
