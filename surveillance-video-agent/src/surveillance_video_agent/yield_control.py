"""Deterministic batch/partition yield accounting and stop decisions."""

from __future__ import annotations

from dataclasses import dataclass

from surveillance_video_agent.db import CandidateDatabase, utc_now


@dataclass(frozen=True, slots=True)
class YieldEvaluation:
    batch_id: str
    released_count: int
    eligible_count: int
    yield_rate: float
    campaign_consecutive_low_yield_batches: int
    campaign_stopped: bool
    suspended_partitions: tuple[tuple[str, str], ...]


def record_batch_yield(
    database: CandidateDatabase,
    *,
    batch_id: str,
    low_yield_threshold: float,
    low_yield_consecutive_windows: int,
    partition_window_size: int,
) -> YieldEvaluation:
    if not 0 < low_yield_threshold < 1:
        raise ValueError("low yield threshold must be between 0 and 1")
    if low_yield_consecutive_windows <= 0 or partition_window_size <= 0:
        raise ValueError("low yield window settings must be positive")
    batch = database.connection.execute(
        "SELECT * FROM secondary_batches WHERE batch_id = ?", (batch_id,)
    ).fetchone()
    if batch is None:
        raise ValueError("secondary batch not found")
    decisions = database.connection.execute(
        """
        SELECT d.decision, i.candidate_key, i.subtype,
               f.attributed_query_id
        FROM secondary_filter_decisions d
        JOIN secondary_batch_items i
          ON i.batch_id = d.batch_id AND i.candidate_key = d.candidate_key
        JOIN frontier_entries f
          ON f.candidate_key = i.candidate_key
         AND f.campaign_id = i.campaign_id
         AND f.subtype = i.subtype
         AND f.run_id = ?
        WHERE d.batch_id = ?
        ORDER BY i.rank, i.candidate_key
        """,
        (batch["run_id"], batch_id),
    ).fetchall()
    if not decisions:
        raise ValueError("secondary batch has no completed decisions")
    released = len(decisions)
    eligible = sum(row["decision"] == "download_eligible" for row in decisions)
    rate = eligible / released
    low = rate < low_yield_threshold
    now = utc_now()
    with database.transaction() as connection:
        existing = connection.execute(
            "SELECT 1 FROM secondary_batch_yields WHERE batch_id = ?", (batch_id,)
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO secondary_batch_yields(
                    batch_id, run_id, campaign_id, released_count,
                    eligible_count, yield_rate, low_yield, evaluated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    batch["run_id"],
                    batch["campaign_id"],
                    released,
                    eligible,
                    rate,
                    int(low),
                    now,
                ),
            )
        connection.execute(
            """
            INSERT OR IGNORE INTO campaign_run_control(
                run_id, campaign_id, status,
                consecutive_low_yield_batches, updated_at
            ) VALUES (?, ?, 'active', 0, ?)
            """,
            (batch["run_id"], batch["campaign_id"], now),
        )
        yields = connection.execute(
            """
            SELECT low_yield FROM secondary_batch_yields
            WHERE run_id = ? AND campaign_id = ?
            ORDER BY evaluated_at, batch_id
            """,
            (batch["run_id"], batch["campaign_id"]),
        ).fetchall()
        consecutive = 0
        for item in reversed(yields):
            if not item["low_yield"]:
                break
            consecutive += 1
        stopped = consecutive >= low_yield_consecutive_windows
        connection.execute(
            """
            UPDATE campaign_run_control
            SET status = ?, consecutive_low_yield_batches = ?,
                stop_reason = ?, updated_at = ?
            WHERE run_id = ? AND campaign_id = ?
            """,
            (
                "stopped" if stopped else "active",
                consecutive,
                "campaign consecutive low secondary yield"
                if stopped
                else None,
                now,
                batch["run_id"],
                batch["campaign_id"],
            ),
        )
        if stopped:
            connection.execute(
                """
                UPDATE frontier_entries SET status = 'suspended', updated_at = ?
                WHERE run_id = ? AND campaign_id = ? AND status = 'ready'
                """,
                (now, batch["run_id"], batch["campaign_id"]),
            )

    partitions = {
        (row["attributed_query_id"], row["subtype"]) for row in decisions
    }
    suspended: list[tuple[str, str]] = []
    for query_id, subtype in sorted(partitions):
        if _refresh_partition(
            database,
            run_id=batch["run_id"],
            campaign_id=batch["campaign_id"],
            query_id=query_id,
            subtype=subtype,
            threshold=low_yield_threshold,
            required_consecutive=low_yield_consecutive_windows,
            window_size=partition_window_size,
        ):
            suspended.append((query_id, subtype))
    return YieldEvaluation(
        batch_id=batch_id,
        released_count=released,
        eligible_count=eligible,
        yield_rate=rate,
        campaign_consecutive_low_yield_batches=consecutive,
        campaign_stopped=stopped,
        suspended_partitions=tuple(suspended),
    )


def _refresh_partition(
    database: CandidateDatabase,
    *,
    run_id: str,
    campaign_id: str,
    query_id: str,
    subtype: str,
    threshold: float,
    required_consecutive: int,
    window_size: int,
) -> bool:
    rows = database.connection.execute(
        """
        SELECT d.decision
        FROM secondary_filter_decisions d
        JOIN secondary_batch_items i
          ON i.batch_id = d.batch_id AND i.candidate_key = d.candidate_key
        JOIN secondary_batches b ON b.batch_id = i.batch_id
        JOIN frontier_entries f
          ON f.candidate_key = i.candidate_key
         AND f.campaign_id = i.campaign_id
         AND f.subtype = i.subtype
         AND f.run_id = b.run_id
        WHERE b.run_id = ? AND b.campaign_id = ?
          AND f.attributed_query_id = ? AND i.subtype = ?
        ORDER BY b.created_at, b.batch_id, i.rank
        """,
        (run_id, campaign_id, query_id, subtype),
    ).fetchall()
    released = len(rows)
    eligible = sum(row["decision"] == "download_eligible" for row in rows)
    complete_window_count = released // window_size
    consecutive = 0
    for window_index in reversed(range(complete_window_count)):
        start = window_index * window_size
        window = rows[start : start + window_size]
        window_eligible = sum(
            row["decision"] == "download_eligible" for row in window
        )
        if window_eligible / window_size >= threshold:
            break
        consecutive += 1
    pending = rows[complete_window_count * window_size :]
    pending_eligible = sum(
        row["decision"] == "download_eligible" for row in pending
    )
    suspended = consecutive >= required_consecutive
    now = utc_now()
    with database.transaction() as connection:
        connection.execute(
            """
            INSERT INTO frontier_partition_stats(
                campaign_id, subtype, query_id, released_count,
                eligible_count, consecutive_low_yield_windows,
                pending_window_released, pending_window_eligible,
                status, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id, subtype, query_id) DO UPDATE SET
                released_count = excluded.released_count,
                eligible_count = excluded.eligible_count,
                consecutive_low_yield_windows = excluded.consecutive_low_yield_windows,
                pending_window_released = excluded.pending_window_released,
                pending_window_eligible = excluded.pending_window_eligible,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                campaign_id,
                subtype,
                query_id,
                released,
                eligible,
                consecutive,
                len(pending),
                pending_eligible,
                "suspended" if suspended else "active",
                now,
            ),
        )
        if suspended:
            connection.execute(
                """
                UPDATE frontier_entries SET status = 'suspended', updated_at = ?
                WHERE run_id = ? AND campaign_id = ? AND subtype = ?
                  AND attributed_query_id = ? AND status = 'ready'
                """,
                (now, run_id, campaign_id, subtype, query_id),
            )
    return suspended
