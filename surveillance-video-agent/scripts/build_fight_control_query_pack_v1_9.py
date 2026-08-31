"""Focus the remaining fight-control quota on non-attack verbal conflicts."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.8.0.json"
DESTINATION=ROOT/"query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.9.0.json"
Q=(
("冲突但未攻击","en","CCTV delivery driver customer argument no fight raw footage","CCTV","delivery driver customer argument no fight"),("冲突但未攻击","en","security camera neighbors property dispute without attack","security camera","neighbors property dispute without attack"),("冲突但未攻击","en","surveillance camera queue argument no physical violence","surveillance camera","queue argument no physical violence"),("冲突但未攻击","en","CCTV hotel guest staff verbal confrontation raw footage","CCTV","hotel guest staff verbal confrontation"),("冲突但未攻击","en","security footage shopkeeper customer face off without fight","security footage","shopkeeper customer face off without fight"),
("冲突但未攻击","es","CCTV discusión repartidor cliente sin pelea grabación original","CCTV","discusión repartidor cliente sin pelea"),("冲突但未攻击","es","cámara de seguridad disputa vecinos propiedad sin ataque","cámara de seguridad","disputa vecinos propiedad sin ataque"),("冲突但未攻击","es","cámara de vigilancia discusión en cola sin violencia física","cámara de vigilancia","discusión en cola sin violencia física"),("冲突但未攻击","es","CCTV confrontación verbal huésped personal hotel grabación","CCTV","confrontación verbal huésped personal hotel"),("冲突但未攻击","es","grabación de seguridad comerciante cliente frente a frente sin pelea","grabación de seguridad","comerciante cliente frente a frente sin pelea"),
("冲突但未攻击","fr","CCTV dispute livreur client sans bagarre images brutes","CCTV","dispute livreur client sans bagarre"),("冲突但未攻击","fr","caméra de sécurité conflit voisins propriété sans attaque","caméra de sécurité","conflit voisins propriété sans attaque"),("冲突但未攻击","fr","caméra de surveillance dispute file attente sans violence physique","caméra de surveillance","dispute file attente sans violence physique"),("冲突但未攻击","fr","CCTV confrontation verbale client personnel hôtel images brutes","CCTV","confrontation verbale client personnel hôtel"),("冲突但未攻击","fr","images de sécurité commerçant client face à face sans bagarre","images de sécurité","commerçant client face à face sans bagarre"),
("舞蹈/玩闹/训练","en","CCTV friends wrestling practice raw footage","CCTV","friends wrestling practice"),("舞蹈/玩闹/训练","es","CCTV amigos practicando lucha grabación original","CCTV","amigos practicando lucha"),("舞蹈/玩闹/训练","fr","CCTV amis entraînement lutte images brutes","CCTV","amis entraînement lutte"),
("场景先验","en","CCTV hotel lobby people waiting no fight raw footage","CCTV","hotel lobby people waiting no fight"),("场景先验","es","CCTV vestíbulo hotel personas esperando sin pelea","CCTV","vestíbulo hotel personas esperando sin pelea"),("场景先验","fr","CCTV hall hôtel personnes attendant sans bagarre","CCTV","hall hôtel personnes attendant sans bagarre"),
("非攻击性身体接触","en","CCTV people high five raw footage","CCTV","people high five"),("非攻击性身体接触","es","CCTV personas chocando las manos grabación original","CCTV","personas chocando las manos"),("非攻击性身体接触","fr","CCTV personnes se tapant dans la main images brutes","CCTV","personnes se tapant dans la main"),
)
def main()->None:
 d=json.loads(SOURCE.read_text(encoding="utf-8"));qs=[]
 for i,(subtype,lang,query,anchor,action) in enumerate(Q,1):qs.append({"query_id":f"fcv19-{i:02d}-{lang}","campaign_id":"fight_confounder_v1","subtype":subtype,"lang":lang,"query":query,"source_anchor":anchor,"source_pool":"surveillance","action_or_scene_term":action,"rationale_zh":"重点补冲突未攻击剩余配额；来源与0.40门不变。"})
 canonical=json.dumps(qs,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n"
 d.update({"query_pack_version":"fight_confounder_v1.qp.v1.9.0","queries":qs,"content_sha256":hashlib.sha256(canonical.encode()).hexdigest(),"created_at":"2026-08-28T04:00:00Z","frozen_at":"2026-08-28","frozen_by":"derived_from_frozen_user_concepts","revision_from":"fight_confounder_v1.qp.v1.8.0","revision_reason":"剩余14条，重点补冲突未攻击12条并保留完整三语subtype契约。"})
 DESTINATION.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if __name__=="__main__":main()
