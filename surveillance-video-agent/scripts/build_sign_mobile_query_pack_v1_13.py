"""Derive additional small-scale sign settings without changing frozen gates."""
from __future__ import annotations
import hashlib, json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.12.0.json"
DESTINATION = ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.13.0.json"
QUERIES = (
    ("en","one person holding protest sign outside clinic phone video","phone video","one person holding protest sign outside clinic"),("en","two workers holding picket signs at delivery depot vertical video","vertical video","two workers holding picket signs at delivery depot"),("en","small group displaying petition banner outside council office short video","short video","small group displaying petition banner outside council office"),("en","tenant holding eviction protest placard apartment entrance mobile video","mobile video","tenant holding eviction protest placard apartment entrance"),("en","two people holding complaint banner outside utility office phone video","phone video","two people holding complaint banner outside utility office"),
    ("es","una persona con cartel de protesta frente a clínica vídeo móvil","vídeo móvil","una persona con cartel de protesta frente a clínica"),("es","dos trabajadores con carteles de piquete en depósito de reparto vídeo vertical","vídeo vertical","dos trabajadores con carteles de piquete en depósito de reparto"),("es","grupo pequeño mostrando pancarta de petición frente a oficina municipal video corto","video corto","grupo pequeño mostrando pancarta de petición frente a oficina municipal"),("es","inquilino con cartel contra desalojo en entrada de apartamento vídeo móvil","vídeo móvil","inquilino con cartel contra desalojo en entrada de apartamento"),("es","dos personas con pancarta de queja frente a oficina de servicios vídeo móvil","vídeo móvil","dos personas con pancarta de queja frente a oficina de servicios"),
    ("fr","une personne avec pancarte de protestation devant clinique vidéo mobile","vidéo mobile","une personne avec pancarte de protestation devant clinique"),("fr","deux travailleurs avec pancartes de piquet dépôt livraison vidéo verticale","vidéo verticale","deux travailleurs avec pancartes de piquet dépôt livraison"),("fr","petit groupe montrant banderole de pétition devant mairie vidéo courte","vidéo courte","petit groupe montrant banderole de pétition devant mairie"),("fr","locataire avec pancarte contre expulsion entrée immeuble vidéo mobile","vidéo mobile","locataire avec pancarte contre expulsion entrée immeuble"),("fr","deux personnes avec banderole de plainte devant bureau services vidéo mobile","vidéo mobile","deux personnes avec banderole de plainte devant bureau services"),
)
def main() -> None:
    d=json.loads(SOURCE.read_text(encoding="utf-8")); count={"en":0,"es":0,"fr":0}; qs=[]
    for lang,q,a,t in QUERIES:
        count[lang]+=1; qs.append({"query_id":f"sav113-sign-banner-{lang}-{count[lang]:02d}","campaign_id":"sign_action_v1","subtype":"举牌/横幅","lang":lang,"query":q,"source_anchor":a,"source_pool":"mobile_adjacent","action_or_scene_term":t,"rationale_zh":"从冻结的1至5人直接举牌定义派生诊所、配送站、市政办公室、住房和公共服务长尾场景；保持来源门、0.440原始语义门和大规模硬排除。"})
    canonical=json.dumps(qs,ensure_ascii=False,sort_keys=True,separators=(",",":"))+"\n"
    d.update({"query_pack_version":"sign_action_v1.qp.v1.13.0","queries":qs,"content_sha256":hashlib.sha256(canonical.encode()).hexdigest(),"created_at":"2026-08-28T01:00:00Z","frozen_at":"2026-08-28","frozen_by":"derived_from_frozen_user_concepts","revision_from":"sign_action_v1.qp.v1.12.0","revision_reason":"v1.12激活后的下一批二筛为0/4；仅扩展未覆盖小规模举牌地点，不降低任一门槛。"})
    DESTINATION.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
if __name__ == "__main__": main()
