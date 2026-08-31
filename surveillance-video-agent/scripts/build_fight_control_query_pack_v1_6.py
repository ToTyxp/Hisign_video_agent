"""Target remaining fight-control subtype gaps with action-centric queries."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/"query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.5.0.json"
DESTINATION=ROOT/"query-packs/fight_confounder_v1/fight_confounder_v1.qp.v1.6.0.json"
Q=(
("冲突但未攻击","en","CCTV heated argument no fight raw footage office reception","CCTV","heated argument no fight"),("冲突但未攻击","en","security camera verbal confrontation without attack transit platform","security camera","verbal confrontation without attack"),
("冲突但未攻击","es","CCTV discusión acalorada sin pelea grabación original recepción","CCTV","discusión acalorada sin pelea"),("冲突但未攻击","es","cámara de seguridad confrontación verbal sin ataque plataforma","cámara de seguridad","confrontación verbal sin ataque"),
("冲突但未攻击","fr","CCTV dispute animée sans bagarre images brutes réception","CCTV","dispute animée sans bagarre"),("冲突但未攻击","fr","caméra de sécurité confrontation verbale sans attaque quai","caméra de sécurité","confrontation verbale sans attaque"),
("舞蹈/玩闹/训练","en","CCTV boxing training gym raw footage","CCTV","boxing training"),("舞蹈/玩闹/训练","en","security camera friends play fighting backyard raw video","security camera","friends play fighting"),
("舞蹈/玩闹/训练","es","CCTV entrenamiento de boxeo gimnasio grabación original","CCTV","entrenamiento de boxeo"),("舞蹈/玩闹/训练","es","cámara de seguridad amigos jugando a pelear patio video","cámara de seguridad","amigos jugando a pelear"),
("舞蹈/玩闹/训练","fr","CCTV entraînement de boxe salle de sport images brutes","CCTV","entraînement de boxe"),("舞蹈/玩闹/训练","fr","caméra de sécurité fausse bagarre entre amis cour vidéo brute","caméra de sécurité","fausse bagarre entre amis"),
("非攻击性身体接触","en","CCTV helping fallen person stand up raw footage","CCTV","helping fallen person stand up"),("非攻击性身体接触","en","security camera people hugging reunion raw video","security camera","people hugging reunion"),
("非攻击性身体接触","es","CCTV ayudando a persona caída a levantarse grabación original","CCTV","ayudando a persona caída a levantarse"),("非攻击性身体接触","es","cámara de seguridad personas abrazándose reunión video","cámara de seguridad","personas abrazándose reunión"),
("非攻击性身体接触","fr","CCTV aide personne tombée à se relever images brutes","CCTV","aide personne tombée à se relever"),("非攻击性身体接触","fr","caméra de sécurité personnes se prenant dans les bras retrouvailles","caméra de sécurité","personnes se prenant dans les bras retrouvailles"),
("场景先验","en","CCTV hospital waiting room people sitting no fight raw footage","CCTV","hospital waiting room people sitting no fight"),("场景先验","en","security camera station queue people talking no attack raw video","security camera","station queue people talking no attack"),
("场景先验","es","CCTV sala de espera hospital personas sentadas sin pelea grabación original","CCTV","sala de espera hospital personas sentadas sin pelea"),("场景先验","es","cámara de seguridad cola estación personas hablando sin ataque video","cámara de seguridad","cola estación personas hablando sin ataque"),
("场景先验","fr","CCTV salle attente hôpital personnes assises sans bagarre images brutes","CCTV","salle attente hôpital personnes assises sans bagarre"),("场景先验","fr","caméra de sécurité file gare personnes discutant sans attaque vidéo brute","caméra de sécurité","file gare personnes discutant sans attaque"),
)
def main()->None:
 d=json.loads(SOURCE.read_text(encoding="utf-8")); counts={}; qs=[]
 for subtype,lang,query,anchor,action in Q:
  key=(subtype,lang); counts[key]=counts.get(key,0)+1
  qs.append({"query_id":f"fcv16-{len(qs)+1:02d}-{lang}","campaign_id":"fight_confounder_v1","subtype":subtype,"lang":lang,"query":query,"source_anchor":anchor,"source_pool":"surveillance","action_or_scene_term":action,"rationale_zh":"针对剩余subtype配额增加动作型长尾查询；非攻击边界、来源门和0.40门不变。"})
 canonical=json.dumps(qs,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n"
 d.update({"query_pack_version":"fight_confounder_v1.qp.v1.6.0","queries":qs,"content_sha256":hashlib.sha256(canonical.encode()).hexdigest(),"created_at":"2026-08-28T02:00:00Z","frozen_at":"2026-08-28","frozen_by":"derived_from_frozen_user_concepts","revision_from":"fight_confounder_v1.qp.v1.5.0","revision_reason":"按剩余四个subtype缺口改为动作型查询，不降低任何门槛。"})
 DESTINATION.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if __name__=="__main__":main()
