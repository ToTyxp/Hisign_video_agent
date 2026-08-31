"""Freeze geographically and venue-diverse mobile small-sign queries for v1.35."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"query-packs/sign_action_v1/sign_action_v1.qp.v1.34.0.json"
DESTINATION=ROOT/"query-packs/sign_action_v1/sign_action_v1.qp.v1.35.0.json"
ITEMS={
 "en":["my neighborhood one-person picket holding cardboard sign at rural town square","our two-person banner action outside stadium parking lot","solo silent protest with handwritten placard at community church sidewalk","small group holding message boards near cultural festival entrance","my local phone clip of one person raising paper sign at suburban shopping arcade","two residents showing slogan signs outside village council hall","one-person demonstration with information placard at sports center gate","our small community banner action beside regional bus terminal"],
 "es":["mi piquete de una persona con cartel de cartón en plaza de pueblo rural","nuestra acción de dos personas con pancarta fuera de estacionamiento de estadio","protesta silenciosa individual con cartel escrito a mano en acera de iglesia comunitaria","grupo pequeño con carteles de mensaje cerca de entrada de festival cultural","mi video local de una persona levantando cartel de papel en galería comercial suburbana","dos residentes mostrando carteles de lema frente a ayuntamiento de pueblo","manifestación de una persona con cartel informativo en puerta de centro deportivo","nuestra pequeña acción comunitaria con pancarta junto a terminal regional de autobuses"],
 "fr":["mon piquet d'une personne avec pancarte carton place village rural","notre action de deux personnes avec banderole devant parking stade","protestation silencieuse solo avec pancarte manuscrite trottoir église communauté","petit groupe avec panneaux message près entrée festival culturel","ma vidéo locale d'une personne levant pancarte papier galerie commerciale banlieue","deux résidents montrant pancartes slogan devant mairie village","manifestation d'une personne avec pancarte information portail centre sportif","notre petite action communauté avec banderole près terminal bus régional"],
}
ANCH={"en":["phone video","#shorts","reel","vertical video","short video","mobile video","#shorts","phone video"],"es":["vídeo móvil","shorts","reel","vídeo vertical","video corto","vídeo móvil","shorts","vídeo móvil"],"fr":["vidéo téléphone","shorts","reel","vidéo verticale","vidéo courte","vidéo mobile","shorts","vidéo téléphone"]}
def main():
 d=json.loads(SOURCE.read_text(encoding="utf-8"));qs=[]
 for lang,items in ITEMS.items():
  for i,item in enumerate(items,1):
   a=ANCH[lang][i-1];qs.append({"query_id":f"sav135-venue-{lang}-{i:02d}","campaign_id":"sign_action_v1","subtype":"举牌/横幅","lang":lang,"query":f"{item} {a}","source_anchor":a,"source_pool":"mobile_adjacent","action_or_scene_term":item,"rationale_zh":"从冻结的1至5名直接参与者持牌/横幅定义派生与v1.34明显不同的地域、现场类型和移动拍摄语境；每条含来源锚点与动作场景词，保持来源门、原始0.440最大相似度门、uploader cap及全部硬排除。"})
 assert len(qs)==24
 c=json.dumps(qs,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n"
 d.update({"query_pack_version":"sign_action_v1.qp.v1.35.0","queries":qs,"content_sha256":hashlib.sha256(c.encode()).hexdigest(),"created_at":"2026-08-28T21:00:00Z","frozen_at":"2026-08-28","frozen_by":"derived_from_frozen_user_concepts","revision_from":"sign_action_v1.qp.v1.34.0","revision_reason":"v1.34 Frontier耗尽；使用未覆盖的乡镇广场、体育场、宗教/文化场所、郊区商业区及区域交通设施，配合新上传者移动拍摄表达；保持1至5人、来源门、0.440最大相似度和150 probe上限。"})
 DESTINATION.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if __name__=="__main__":main()
