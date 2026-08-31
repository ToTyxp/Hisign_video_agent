"""Add fresh frozen 1--5 participant sign settings for the next discovery pass."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"query-packs/sign_action_v1/sign_action_v1.qp.v1.13.0.json"
DESTINATION=ROOT/"query-packs/sign_action_v1/sign_action_v1.qp.v1.14.0.json"
QUERIES=(
 ("en","one person holding protest sign outside post office phone video","phone video","one person holding protest sign outside post office"),("en","two workers with strike signs outside recycling center vertical video","vertical video","two workers with strike signs outside recycling center"),("en","small group displaying petition banner at park entrance short video","short video","small group displaying petition banner at park entrance"),("en","resident holding complaint placard outside landlord office mobile video","mobile video","resident holding complaint placard outside landlord office"),("en","two people holding protest signs outside employment office phone video","phone video","two people holding protest signs outside employment office"),
 ("es","una persona con cartel de protesta frente a oficina de correos vídeo móvil","vídeo móvil","una persona con cartel de protesta frente a oficina de correos"),("es","dos trabajadores con carteles de huelga frente a centro de reciclaje vídeo vertical","vídeo vertical","dos trabajadores con carteles de huelga frente a centro de reciclaje"),("es","grupo pequeño mostrando pancarta de petición en entrada de parque video corto","video corto","grupo pequeño mostrando pancarta de petición en entrada de parque"),("es","residente con cartel de queja fuera de oficina de propietario vídeo móvil","vídeo móvil","residente con cartel de queja fuera de oficina de propietario"),("es","dos personas con carteles de protesta frente a oficina de empleo vídeo móvil","vídeo móvil","dos personas con carteles de protesta frente a oficina de empleo"),
 ("fr","une personne avec pancarte de protestation devant poste vidéo mobile","vidéo mobile","une personne avec pancarte de protestation devant poste"),("fr","deux travailleurs avec pancartes de grève devant centre recyclage vidéo verticale","vidéo verticale","deux travailleurs avec pancartes de grève devant centre recyclage"),("fr","petit groupe montrant banderole de pétition entrée parc vidéo courte","vidéo courte","petit groupe montrant banderole de pétition entrée parc"),("fr","résident avec pancarte de plainte devant bureau propriétaire vidéo mobile","vidéo mobile","résident avec pancarte de plainte devant bureau propriétaire"),("fr","deux personnes avec pancartes protestation devant bureau emploi vidéo mobile","vidéo mobile","deux personnes avec pancartes protestation devant bureau emploi"),
)
def main():
 d=json.loads(SOURCE.read_text(encoding="utf-8")); counts={"en":0,"es":0,"fr":0}; qs=[]
 for lang,q,a,t in QUERIES:
  counts[lang]+=1; qs.append({"query_id":f"sav114-sign-banner-{lang}-{counts[lang]:02d}","campaign_id":"sign_action_v1","subtype":"举牌/横幅","lang":lang,"query":q,"source_anchor":a,"source_pool":"mobile_adjacent","action_or_scene_term":t,"rationale_zh":"从冻结的1至5人直接举牌定义派生邮局、回收中心、公园入口、房东和就业服务长尾场景；不改变来源、0.440原始语义门或大规模硬排除。"})
 canonical=json.dumps(qs,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n"
 d.update({"query_pack_version":"sign_action_v1.qp.v1.14.0","queries":qs,"content_sha256":hashlib.sha256(canonical.encode()).hexdigest(),"created_at":"2026-08-28T02:00:00Z","frozen_at":"2026-08-28","frozen_by":"derived_from_frozen_user_concepts","revision_from":"sign_action_v1.qp.v1.13.0","revision_reason":"v1.13仅形成4条技术成功下载；仅增加冻结小规模举牌的未覆盖地点，不降低任何门槛。"})
 DESTINATION.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if __name__=="__main__": main()
