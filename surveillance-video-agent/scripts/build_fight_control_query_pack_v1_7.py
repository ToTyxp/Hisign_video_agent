"""Target only fight-control subtypes that remain below quota."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.6.0.json"
DESTINATION=ROOT/"query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.7.0.json"
Q=(
("冲突但未攻击","en","CCTV neighbors shouting no fight raw footage driveway","CCTV","neighbors shouting no fight"),("冲突但未攻击","en","security camera customer argument without attack store counter","security camera","customer argument without attack"),("冲突但未攻击","en","surveillance camera road rage argument no physical fight","surveillance camera","road rage argument no physical fight"),("冲突但未攻击","en","CCTV tenant landlord verbal confrontation raw footage","CCTV","tenant landlord verbal confrontation"),("冲突但未攻击","en","security footage coworkers face off without violence","security footage","coworkers face off without violence"),
("冲突但未攻击","es","CCTV vecinos gritando sin pelea grabación original entrada","CCTV","vecinos gritando sin pelea"),("冲突但未攻击","es","cámara de seguridad discusión cliente sin ataque mostrador","cámara de seguridad","discusión cliente sin ataque"),("冲突但未攻击","es","cámara de vigilancia discusión tráfico sin pelea física","cámara de vigilancia","discusión tráfico sin pelea física"),("冲突但未攻击","es","CCTV confrontación verbal inquilino propietario grabación","CCTV","confrontación verbal inquilino propietario"),("冲突但未攻击","es","grabación de seguridad compañeros frente a frente sin violencia","grabación de seguridad","compañeros frente a frente sin violencia"),
("冲突但未攻击","fr","CCTV voisins criant sans bagarre images brutes allée","CCTV","voisins criant sans bagarre"),("冲突但未攻击","fr","caméra de sécurité dispute client sans attaque comptoir","caméra de sécurité","dispute client sans attaque"),("冲突但未攻击","fr","caméra de surveillance dispute routière sans bagarre physique","caméra de surveillance","dispute routière sans bagarre physique"),("冲突但未攻击","fr","CCTV confrontation verbale locataire propriétaire images brutes","CCTV","confrontation verbale locataire propriétaire"),("冲突但未攻击","fr","images de sécurité collègues face à face sans violence","images de sécurité","collègues face à face sans violence"),
("舞蹈/玩闹/训练","en","CCTV friends sparring practice raw footage","CCTV","friends sparring practice"),("舞蹈/玩闹/训练","es","CCTV amigos practicando combate grabación original","CCTV","amigos practicando combate"),("舞蹈/玩闹/训练","fr","CCTV amis entraînement combat images brutes","CCTV","amis entraînement combat"),
("场景先验","en","CCTV parking entrance people waiting no fight raw footage","CCTV","parking entrance people waiting no fight"),("场景先验","en","security camera convenience store queue talking no attack","security camera","convenience store queue talking no attack"),
("场景先验","es","CCTV entrada estacionamiento personas esperando sin pelea","CCTV","entrada estacionamiento personas esperando sin pelea"),("场景先验","es","cámara de seguridad cola tienda hablando sin ataque","cámara de seguridad","cola tienda hablando sin ataque"),
("场景先验","fr","CCTV entrée parking personnes attendant sans bagarre","CCTV","entrée parking personnes attendant sans bagarre"),("场景先验","fr","caméra de sécurité file supérette discutant sans attaque","caméra de sécurité","file supérette discutant sans attaque"),
)
def main()->None:
 d=json.loads(SOURCE.read_text(encoding="utf-8"));qs=[]
 for i,(subtype,lang,query,anchor,action) in enumerate(Q,1): qs.append({"query_id":f"fcv17-{i:02d}-{lang}","campaign_id":"fight_confounder_v1","subtype":subtype,"lang":lang,"query":query,"source_anchor":anchor,"source_pool":"surveillance","action_or_scene_term":action,"rationale_zh":"只针对未达标subtype扩展动作查询；非攻击边界与0.40门不变。"})
 canonical=json.dumps(qs,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n"
 d.update({"query_pack_version":"fight_confounder_v1.qp.v1.7.0","queries":qs,"content_sha256":hashlib.sha256(canonical.encode()).hexdigest(),"created_at":"2026-08-28T03:00:00Z","frozen_at":"2026-08-28","frozen_by":"derived_from_frozen_user_concepts","revision_from":"fight_confounder_v1.qp.v1.6.0","revision_reason":"仅补冲突未攻击、玩闹训练和场景先验剩余配额；非攻击接触已达标。"})
 DESTINATION.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if __name__=="__main__":main()
