"""Import visual pilot labels and derive an auditable refined semantic gate."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from surveillance_video_agent.db import CandidateDatabase, utc_now


CONFLICT_ATTACK_NEGATIVE_TERMS = (
    "violent",
    "brawl",
    "fight",
    "fighting",
    "punch",
    "punching",
    "kick",
    "kicking",
    "hit",
    "battered",
    "assault",
    "attack",
    "stabbing",
    "knife",
    "gun",
    "rifle",
    "weapon",
    "shooting",
    "shot",
    "pelea",
    "puñetazo",
    "patada",
    "ataque",
    "agresión",
    "apuñalamiento",
    "cuchillo",
    "pistola",
    "arma",
    "disparo",
    "violento",
    "bagarre",
    "coup de poing",
    "coup de pied",
    "attaque",
    "agression",
    "poignard",
    "couteau",
    "pistolet",
    "fusil",
    "arme",
    "tir",
    "斗殴",
    "打架",
    "拳打",
    "脚踢",
    "袭击",
    "攻击",
    "暴力",
    "持刀",
    "开枪",
    "殴打",
    "打人",
)


@dataclass(frozen=True, slots=True)
class FeedbackImportResult:
    import_id: str
    content_sha256: str
    label_count: int
    source_correct_count: int
    source_determinate_count: int
    task_usable_count: int
    task_determinate_count: int


@dataclass(frozen=True, slots=True)
class SemanticGateRefinementResult:
    policy_version: str
    base_policy_version: str
    accepted_pair_count: int
    rejected_pair_count: int
    unique_candidate_count: int
    campaign_subtype_counts: Mapping[str, Mapping[str, int]]


def import_pilot_feedback(
    database: CandidateDatabase,
    path: Path,
    *,
    run_id: str,
) -> FeedbackImportResult:
    source = Path(path).resolve()
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != "pilot_feedback_v1":
        raise ValueError("unsupported pilot feedback schema")
    labels = document.get("labels")
    if not isinstance(labels, list) or not 1 <= len(labels) <= 20:
        raise ValueError("pilot feedback must contain between 1 and 20 labels")
    normalized = [_validate_label(database, item) for item in labels]
    identities = {
        (item["candidate_key"], item["campaign_id"], item["shown_subtype"])
        for item in normalized
    }
    if len(identities) != len(normalized):
        raise ValueError("pilot feedback contains duplicate labels")
    import_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"pilot-feedback:{digest}"))
    now = utc_now()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO pilot_feedback_imports(
                import_id, schema_version, source_path, content_sha256,
                exported_at, label_count, run_id, imported_at
            ) VALUES (?, 'pilot_feedback_v1', ?, ?, ?, ?, ?, ?)
            """,
            (
                import_id,
                str(source),
                digest,
                document.get("exported_at"),
                len(normalized),
                run_id,
                now,
            ),
        )
        for item in normalized:
            connection.execute(
                """
                INSERT OR IGNORE INTO pilot_feedback_labels(
                    import_id, candidate_key, campaign_id, shown_subtype,
                    source_correct, task_usable, corrected_subtype, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    import_id,
                    item["candidate_key"],
                    item["campaign_id"],
                    item["shown_subtype"],
                    _sqlite_bool(item["source_correct"]),
                    _sqlite_bool(item["task_usable"]),
                    item["corrected_subtype"],
                    item["notes"],
                ),
            )
    source_values = [item["source_correct"] for item in normalized if item["source_correct"] is not None]
    task_values = [item["task_usable"] for item in normalized if item["task_usable"] is not None]
    return FeedbackImportResult(
        import_id,
        digest,
        len(normalized),
        sum(value is True for value in source_values),
        len(source_values),
        sum(value is True for value in task_values),
        len(task_values),
    )


def refine_semantic_gate(
    database: CandidateDatabase,
    *,
    run_id: str,
    base_policy_version: str,
    policy_version: str,
    campaign_thresholds: Mapping[str, float],
    minimum_source_score: int,
    negative_terms: Mapping[tuple[str, str], Sequence[str]],
) -> SemanticGateRefinementResult:
    if not policy_version or policy_version == base_policy_version:
        raise ValueError("refined semantic policy needs a new version")
    if minimum_source_score < 4:
        raise ValueError("refinement cannot lower the source gate")
    if not campaign_thresholds or any(not 0 <= value <= 1 for value in campaign_thresholds.values()):
        raise ValueError("campaign thresholds must be bounded")
    rows = database.connection.execute(
        """
        SELECT s.*, c.title, c.video_description, c.tags_json, c.source_score
        FROM semantic_task_eligibility s
        JOIN candidates c ON c.candidate_key = s.candidate_key
        WHERE s.policy_version = ?
        ORDER BY s.campaign_id, s.subtype, s.candidate_key
        """,
        (base_policy_version,),
    ).fetchall()
    accepted_keys: set[str] = set()
    accepted = 0
    rejected = 0
    counts: dict[str, dict[str, int]] = {}
    now = utc_now()
    with database.transaction() as connection:
        for row in rows:
            if row["campaign_id"] not in campaign_thresholds:
                continue
            threshold = float(campaign_thresholds[row["campaign_id"]])
            reasons = []
            if int(row["source_score"]) < minimum_source_score:
                reasons.append("source_score_below_refined_minimum")
            if float(row["similarity"]) <= threshold:
                reasons.append("similarity_not_above_refined_threshold")
            matched = _matched_terms(
                row,
                negative_terms.get((row["campaign_id"], row["subtype"]), ()),
            )
            if matched:
                reasons.append("metadata_contains_task_negative:" + ",".join(matched))
            is_accepted = not reasons
            decision_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{policy_version}:{row['candidate_key']}:"
                    f"{row['campaign_id']}:{row['subtype']}",
                )
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO semantic_gate_decisions(
                    decision_id, candidate_key, campaign_id, subtype,
                    base_policy_version, policy_version, accepted, similarity,
                    threshold, source_score, reasons_json, run_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    row["candidate_key"],
                    row["campaign_id"],
                    row["subtype"],
                    base_policy_version,
                    policy_version,
                    int(is_accepted),
                    row["similarity"],
                    threshold,
                    row["source_score"],
                    json.dumps(reasons, ensure_ascii=False, separators=(",", ":")),
                    run_id,
                    now,
                ),
            )
            if not is_accepted:
                rejected += 1
                continue
            eligibility_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{policy_version}:{row['embedding_schema_version']}:"
                    f"{row['query_pack_version']}:{row['campaign_id']}:"
                    f"{row['subtype']}:{row['candidate_key']}",
                )
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO semantic_task_eligibility(
                    eligibility_id, candidate_key, campaign_id, subtype,
                    query_pack_version, embedding_schema_version,
                    policy_version, similarity, threshold, run_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    eligibility_id,
                    row["candidate_key"],
                    row["campaign_id"],
                    row["subtype"],
                    row["query_pack_version"],
                    row["embedding_schema_version"],
                    policy_version,
                    row["similarity"],
                    threshold,
                    run_id,
                    now,
                ),
            )
            accepted += 1
            accepted_keys.add(row["candidate_key"])
            counts.setdefault(row["campaign_id"], {}).setdefault(row["subtype"], 0)
            counts[row["campaign_id"]][row["subtype"]] += 1
    return SemanticGateRefinementResult(
        policy_version,
        base_policy_version,
        accepted,
        rejected,
        len(accepted_keys),
        {campaign: dict(subtypes) for campaign, subtypes in counts.items()},
    )


def _validate_label(database: CandidateDatabase, item) -> dict:
    if not isinstance(item, dict):
        raise ValueError("pilot feedback label must be an object")
    required = ("candidate_key", "campaign_id", "shown_subtype")
    if any(not isinstance(item.get(key), str) or not item[key] for key in required):
        raise ValueError("pilot feedback label identity is incomplete")
    for key in ("source_correct", "task_usable"):
        if item.get(key) not in (True, False, None):
            raise ValueError(f"pilot feedback {key} must be true, false, or null")
    assignment = database.connection.execute(
        """
        SELECT 1 FROM queue_assignments
        WHERE candidate_key = ? AND campaign_id = ? AND subtype = ?
        """,
        (item["candidate_key"], item["campaign_id"], item["shown_subtype"]),
    ).fetchone()
    if assignment is None:
        raise ValueError("pilot feedback does not match an audited queue assignment")
    corrected = item.get("corrected_subtype") or ""
    notes = item.get("notes") or ""
    if not isinstance(corrected, str) or not isinstance(notes, str):
        raise ValueError("pilot feedback text fields must be strings")
    return {
        "candidate_key": item["candidate_key"],
        "campaign_id": item["campaign_id"],
        "shown_subtype": item["shown_subtype"],
        "source_correct": item.get("source_correct"),
        "task_usable": item.get("task_usable"),
        "corrected_subtype": corrected[:200],
        "notes": notes[:2000],
    }


def _sqlite_bool(value):
    return None if value is None else int(value)


def _matched_terms(row, terms: Sequence[str]) -> list[str]:
    if not terms:
        return []
    text = " ".join(
        (
            row["title"] or "",
            row["video_description"] or "",
            " ".join(json.loads(row["tags_json"] or "[]")),
        )
    ).casefold()
    matches = []
    for term in terms:
        normalized = term.casefold()
        if re.search(r"[a-z0-9]", normalized):
            found = re.search(
                rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])",
                text,
            )
        else:
            found = normalized in text
        if found:
            matches.append(term)
    return sorted(set(matches))
