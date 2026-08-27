"""Register small-scale sign concepts and explicit large-scale negatives."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "query-packs/scoring-policy.v1.3.0.json"
CONCEPT = ROOT / "query-packs/sign-action-concepts.v1.0.0.freeze.json"
DESTINATION = ROOT / "query-packs/scoring-policy.v1.4.0.json"
LARGE_SCALE = (
    "mass protest",
    "massive protest",
    "large protest",
    "large demonstration",
    "huge rally",
    "hundreds of protesters",
    "thousands of protesters",
    "protesta masiva",
    "gran manifestación",
    "cientos de manifestantes",
    "miles de manifestantes",
    "manifestation massive",
    "grande manifestation",
    "des centaines de manifestants",
    "des milliers de manifestants",
    "大型游行",
    "大规模抗议",
    "数百名抗议者",
    "数千名抗议者",
)


def main() -> None:
    document = json.loads(SOURCE.read_text(encoding="utf-8"))
    concept = json.loads(CONCEPT.read_text(encoding="utf-8"))
    compatible = dict(document.get("compatible_concept_packs") or {})
    compatible[concept["concept_pack_version"]] = concept["source_sha256"]
    document["compatible_concept_packs"] = compatible
    rule = document["task_matching"]["sign_action_v1"]["举牌/横幅"]
    for term in LARGE_SCALE:
        if term not in rule["forbidden_terms"]:
            rule["forbidden_terms"].append(term)
    document.update(
        {
            "policy_version": "surveillance_scoring_v1.4.0",
            "status": "frozen",
            "frozen_at": "2026-08-27",
            "frozen_by": "user",
            "revision_from": "surveillance_scoring_v1.3.0",
            "revision_reason": (
                "注册独立小规模举牌中文概念包，并为sign_action_v1增加明确"
                "大型游行/密集集会反例；不修改来源分、任务分或阈值。"
            ),
        }
    )
    content = dict(document)
    for key in (
        "status",
        "frozen_at",
        "frozen_by",
        "content_sha256",
        "content_sha256_scope",
    ):
        content.pop(key, None)
    canonical = json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    document["content_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    DESTINATION.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
