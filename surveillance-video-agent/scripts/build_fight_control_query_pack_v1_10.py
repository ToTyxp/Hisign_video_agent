"""Fill the final non-attack conflict quota while preserving query-contract terms."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.9.0.json"
DESTINATION=ROOT/"query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.10.0.json"
Q=(
("冲突但未攻击","en","CCTV security guard customer argument no attack raw footage","CCTV","security guard customer argument no attack"),("冲突但未攻击","en","security camera taxi driver passenger dispute without fight","security camera","taxi driver passenger dispute without fight"),("冲突但未攻击","en","surveillance camera restaurant staff customer confrontation no violence","surveillance camera","restaurant staff customer confrontation no violence"),("冲突但未攻击","en","CCTV parent teacher verbal dispute raw footage","CCTV","parent teacher verbal dispute"),("冲突但未攻击","en","security footage homeowner trespasser face off no physical attack","security footage","homeowner trespasser face off no physical attack"),
("冲突但未攻击","es","CCTV discusión guardia cliente sin ataque grabación original","CCTV","discusión guardia cliente sin ataque"),("冲突但未攻击","es","cámara de seguridad disputa taxista pasajero sin pelea","cámara de seguridad","disputa taxista pasajero sin pelea"),("冲突但未攻击","es","cámara de vigilancia confrontación personal restaurante cliente sin violencia","cámara de vigilancia","confrontación personal restaurante cliente sin violencia"),("冲突但未攻击","es","CCTV disputa verbal padre profesor grabación original","CCTV","disputa verbal padre profesor"),("冲突但未攻击","es","grabación de seguridad propietario intruso frente a frente sin ataque físico","grabación de seguridad","propietario intruso frente a frente sin ataque físico"),
("冲突但未攻击","fr","CCTV dispute agent sécurité client sans attaque images brutes","CCTV","dispute agent sécurité client sans attaque"),("冲突但未攻击","fr","caméra de sécurité conflit chauffeur taxi passager sans bagarre","caméra de sécurité","conflit chauffeur taxi passager sans bagarre"),("冲突但未攻击","fr","caméra de surveillance confrontation personnel restaurant client sans violence","caméra de surveillance","confrontation personnel restaurant client sans violence"),("冲突但未攻击","fr","CCTV dispute verbale parent professeur images brutes","CCTV","dispute verbale parent professeur"),("冲突但未攻击","fr","images de sécurité propriétaire intrus face à face sans attaque physique","images de sécurité","propriétaire intrus face à face sans attaque physique"),
("舞蹈/玩闹/训练","en","CCTV dance rehearsal raw footage","CCTV","dance rehearsal"),("舞蹈/玩闹/训练","es","CCTV ensayo de baile grabación original","CCTV","ensayo de baile"),("舞蹈/玩闹/训练","fr","CCTV répétition danse images brutes","CCTV","répétition danse"),
("非攻击性身体接触","en","CCTV people hugging raw footage","CCTV","people hugging"),("非攻击性身体接触","es","CCTV personas abrazándose grabación original","CCTV","personas abrazándose"),("非攻击性身体接触","fr","CCTV personnes se prenant dans les bras images brutes","CCTV","personnes se prenant dans les bras"),
("场景先验","en","CCTV people waiting no fight raw footage","CCTV","people waiting no fight"),("场景先验","es","CCTV personas esperando sin pelea grabación original","CCTV","personas esperando sin pelea"),("场景先验","fr","CCTV personnes attendant sans bagarre images brutes","CCTV","personnes attendant sans bagarre"),
)
def main()->None:
 d=json.loads(SOURCE.read_text(encoding="utf-8"));qs=[]
 for i,(subtype,lang,query,anchor,action) in enumerate(Q,1):qs.append({"query_id":f"fcv110-{i:02d}-{lang}","campaign_id":"fight_confounder_v1","subtype":subtype,"lang":lang,"query":query,"source_anchor":anchor,"source_pool":"surveillance","action_or_scene_term":action,"rationale_zh":"补齐最终冲突未攻击配额；非攻击边界与0.40门不变。"})
 canonical=json.dumps(qs,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n"
 d.update({"query_pack_version":"fight_confounder_v1.qp.v1.10.0","queries":qs,"content_sha256":hashlib.sha256(canonical.encode()).hexdigest(),"created_at":"2026-08-28T05:00:00Z","frozen_at":"2026-08-28","frozen_by":"derived_from_frozen_user_concepts","revision_from":"fight_confounder_v1.qp.v1.9.0","revision_reason":"只剩冲突未攻击6条；重点补该subtype并保留完整三语查询契约。"})
 DESTINATION.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if __name__=="__main__":main()
