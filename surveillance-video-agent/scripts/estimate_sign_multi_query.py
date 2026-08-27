#!/usr/bin/env python3
"""Audit max-score sign recall across all frozen sign query versions."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from surveillance_video_agent.cli import (
    PROJECT_ROOT,
    QDRANT_PATH,
    STATE_DB,
    _scoring_policy_version,
)
from surveillance_video_agent.db import CandidateDatabase
from surveillance_video_agent.qwen_embedding import (
    QWEN_SCHEMA,
    DashScopeQwenEmbeddingProvider,
)
from surveillance_video_agent.semantic_queries import SemanticQueryVectorService
from surveillance_video_agent.vector_index import QdrantVectorIndex


PACKS = (
    PROJECT_ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.0.0.json",
    PROJECT_ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.1.0.json",
    PROJECT_ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.2.0.json",
    PROJECT_ROOT / "query-packs/sign_action_v1/sign_action_v1.qp.v1.4.0.json",
)


def main() -> None:
    run_id = "sign-multi-query-estimate-" + str(uuid.uuid4())
    with CandidateDatabase(STATE_DB) as database, QdrantVectorIndex(
        QDRANT_PATH
    ) as index:
        database.initialize()
        database.create_run(
            run_id,
            "sign-multi-query-estimate",
            config={"query_packs": [str(path) for path in PACKS], "threshold": 0.44},
        )
        provider = DashScopeQwenEmbeddingProvider()
        semantic = SemanticQueryVectorService(
            database, index, provider, QWEN_SCHEMA
        )
        rows = database.connection.execute(
            """
            SELECT c.candidate_key, c.title, c.source_score, c.camera_pool,
                   c.duration_seconds
            FROM candidates c
            WHERE c.status = 'source_qualified'
              AND c.resource_eligible = 1 AND c.hard_excluded = 0
              AND c.camera_pool IN ('surveillance', 'mobile_adjacent')
              AND EXISTS (
                  SELECT 1 FROM candidate_discoveries d
                  JOIN queries q ON q.query_id = d.query_id
                  WHERE d.candidate_key = c.candidate_key
                    AND q.campaign_id = 'sign_action_v1'
              )
              AND NOT EXISTS (
                  SELECT 1 FROM candidate_suppressions s
                  WHERE s.candidate_key = c.candidate_key
                    AND NOT EXISTS (
                        SELECT 1 FROM candidate_suppression_releases r
                        WHERE r.suppression_id = s.suppression_id
                  )
              )
              AND NOT EXISTS (
                  SELECT 1 FROM score_evidence e
                  WHERE e.candidate_key = c.candidate_key
                    AND e.score_kind = 'task'
                    AND e.campaign_id = 'sign_action_v1'
                    AND e.subtype = '举牌/横幅'
                    AND e.rule_code = 'task.forbidden_semantics'
                    AND e.policy_version = ?
              )
            ORDER BY c.candidate_key
            """,
            (_scoring_policy_version(),),
        ).fetchall()
        by_key = {row["candidate_key"]: dict(row) for row in rows}
        scores: dict[str, tuple[float, str]] = {}
        for path in PACKS:
            prepared = semantic.prepare(run_id=run_id, query_pack_path=path)
            matches = index.query_calibration_relevance(
                QWEN_SCHEMA,
                prepared.vectors["举牌/横幅"],
                candidate_keys=tuple(by_key),
                limit=len(by_key),
            )
            for match in matches:
                previous = scores.get(match.candidate_key)
                if previous is None or match.score > previous[0]:
                    scores[match.candidate_key] = (
                        match.score,
                        prepared.query_pack_version,
                    )
        selected = []
        for candidate_key, (score, version) in scores.items():
            if score <= 0.44:
                continue
            selected.append(
                {
                    **by_key[candidate_key],
                    "max_similarity": score,
                    "best_query_pack_version": version,
                }
            )
        selected.sort(key=lambda item: (-item["max_similarity"], item["candidate_key"]))
        output = {
            "run_id": run_id,
            "candidate_count": len(by_key),
            "threshold": 0.44,
            "selected_count": len(selected),
            "selected": selected,
        }
        destination = PROJECT_ROOT / ".surveillance-pool/runs" / run_id / "result.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(output, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        database.finish_run(run_id, status="completed", result=output)
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
