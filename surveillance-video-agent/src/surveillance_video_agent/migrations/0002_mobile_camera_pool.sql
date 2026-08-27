PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

CREATE TABLE candidates_v2 (
    candidate_key TEXT PRIMARY KEY,
    platform TEXT NOT NULL CHECK(platform IN ('youtube', 'dailymotion', 'peertube')),
    source_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    title TEXT,
    video_description TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(tags_json)),
    uploader TEXT,
    uploader_id TEXT,
    channel TEXT,
    playlist TEXT,
    duration_seconds REAL,
    estimated_bytes INTEGER,
    width INTEGER,
    height INTEGER,
    availability TEXT,
    is_live INTEGER CHECK(is_live IS NULL OR is_live IN (0, 1)),
    live_status TEXT,
    resource_eligible INTEGER CHECK(resource_eligible IS NULL OR resource_eligible IN (0, 1)),
    resource_reasons_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(resource_reasons_json)),
    resource_policy_version TEXT,
    hard_excluded INTEGER NOT NULL DEFAULT 0 CHECK(hard_excluded IN (0, 1)),
    hard_exclusion_reasons_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(hard_exclusion_reasons_json)),
    camera_pool TEXT CHECK(camera_pool IS NULL OR camera_pool IN ('surveillance', 'mobile_adjacent')),
    source_score INTEGER NOT NULL DEFAULT 0 CHECK(source_score BETWEEN -3 AND 9),
    source_policy_version TEXT,
    status TEXT NOT NULL DEFAULT 'discovered' CHECK(status IN (
        'discovered', 'source_qualified', 'task_queued',
        'downloaded', 'technical_failed', 'duplicate_suppressed'
    )),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    created_run_id TEXT NOT NULL REFERENCES runs(run_id),
    updated_run_id TEXT NOT NULL REFERENCES runs(run_id),
    UNIQUE(platform, source_id),
    CHECK(candidate_key = platform || ':' || source_id),
    CHECK(duration_seconds IS NULL OR duration_seconds >= 0),
    CHECK(estimated_bytes IS NULL OR estimated_bytes >= 0),
    CHECK(width IS NULL OR width > 0),
    CHECK(height IS NULL OR height > 0),
    CHECK(
        status NOT IN ('source_qualified', 'task_queued', 'downloaded', 'technical_failed', 'duplicate_suppressed')
        OR (hard_excluded = 0 AND source_score >= 4 AND camera_pool IN ('surveillance', 'mobile_adjacent'))
    )
);

INSERT INTO candidates_v2 SELECT * FROM candidates;
DROP TABLE candidates;
ALTER TABLE candidates_v2 RENAME TO candidates;

COMMIT;
PRAGMA foreign_keys = ON;
