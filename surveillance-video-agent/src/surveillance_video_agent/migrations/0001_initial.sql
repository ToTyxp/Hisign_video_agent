PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    schema_version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_meta(schema_version, applied_at)
VALUES (2, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed', 'stopped')),
    config_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(config_json)),
    code_version TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json))
);

CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id TEXT PRIMARY KEY,
    active_policy_version TEXT,
    active_frontier_policy_version TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaign_policy_versions (
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
    policy_version TEXT NOT NULL,
    subtype_limits_json TEXT NOT NULL CHECK(json_valid(subtype_limits_json)),
    max_candidates INTEGER NOT NULL CHECK(max_candidates >= 0),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY(campaign_id, policy_version)
);

CREATE TABLE IF NOT EXISTS frontier_policy_versions (
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
    frontier_policy_version TEXT NOT NULL,
    policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY(campaign_id, frontier_policy_version)
);

CREATE TABLE IF NOT EXISTS embedding_schema_versions (
    embedding_schema_version TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL CHECK(dimensions > 0),
    distance TEXT NOT NULL CHECK(distance IN ('cosine', 'dot', 'euclid')),
    text_template_version TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS semantic_query_templates (
    template_version TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK(status = 'frozen'),
    content_sha256 TEXT NOT NULL,
    content_json TEXT NOT NULL CHECK(json_valid(content_json)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS query_packs (
    query_pack_version TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    concept_pack_version TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    network_config TEXT NOT NULL CHECK(network_config = 'default'),
    status TEXT NOT NULL CHECK(status = 'frozen'),
    frozen_at TEXT NOT NULL,
    frozen_by TEXT NOT NULL,
    content_json TEXT NOT NULL CHECK(json_valid(content_json))
);

CREATE TABLE IF NOT EXISTS queries (
    query_id TEXT PRIMARY KEY,
    query_pack_version TEXT NOT NULL REFERENCES query_packs(query_pack_version),
    campaign_id TEXT NOT NULL,
    subtype TEXT NOT NULL,
    lang TEXT NOT NULL CHECK(lang IN ('en', 'es', 'fr')),
    query_text TEXT NOT NULL,
    source_anchor TEXT NOT NULL,
    action_or_scene_term TEXT NOT NULL,
    UNIQUE(query_pack_version, query_text, lang)
);

CREATE TABLE IF NOT EXISTS search_cache (
    platform TEXT NOT NULL CHECK(platform IN ('youtube', 'dailymotion', 'peertube')),
    query TEXT NOT NULL,
    lang TEXT NOT NULL CHECK(lang IN ('en', 'es', 'fr')),
    query_pack_version TEXT NOT NULL REFERENCES query_packs(query_pack_version),
    network_config TEXT NOT NULL CHECK(network_config = 'default'),
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    PRIMARY KEY(platform, query, lang, query_pack_version, network_config)
);

CREATE TABLE IF NOT EXISTS probe_cache (
    platform TEXT NOT NULL CHECK(platform IN ('youtube', 'dailymotion', 'peertube')),
    source_id TEXT NOT NULL,
    network_config TEXT NOT NULL CHECK(network_config = 'default'),
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    normalized_json TEXT NOT NULL CHECK(json_valid(normalized_json)),
    raw_json TEXT NOT NULL CHECK(json_valid(raw_json)),
    PRIMARY KEY(platform, source_id, network_config)
);

CREATE TABLE IF NOT EXISTS adapter_calls (
    request_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    platform TEXT NOT NULL CHECK(platform IN ('youtube', 'dailymotion', 'peertube')),
    operation TEXT NOT NULL CHECK(operation IN ('search', 'probe', 'download')),
    query_id TEXT REFERENCES queries(query_id),
    candidate_key TEXT,
    cache_hit INTEGER NOT NULL CHECK(cache_hit IN (0, 1)),
    status TEXT NOT NULL CHECK(status IN ('succeeded', 'failed')),
    error_kind TEXT,
    error_message TEXT,
    attempts INTEGER NOT NULL DEFAULT 1 CHECK(attempts >= 1),
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    CHECK(
        (operation = 'search' AND query_id IS NOT NULL AND candidate_key IS NULL) OR
        (operation IN ('probe', 'download') AND query_id IS NULL AND candidate_key IS NOT NULL)
    ),
    CHECK(
        (status = 'succeeded' AND error_kind IS NULL AND error_message IS NULL) OR
        (status = 'failed' AND error_kind IS NOT NULL AND error_message IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS embedding_calls (
    call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    embedding_schema_version TEXT NOT NULL
        REFERENCES embedding_schema_versions(embedding_schema_version),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    operation TEXT NOT NULL CHECK(operation IN ('candidate_documents', 'subtype_queries')),
    subject_count INTEGER NOT NULL CHECK(subject_count > 0),
    input_hashes_json TEXT NOT NULL CHECK(json_valid(input_hashes_json)),
    status TEXT NOT NULL CHECK(status IN ('succeeded', 'failed')),
    error_kind TEXT,
    status_code INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    CHECK(
        (status = 'succeeded' AND error_kind IS NULL) OR
        (status = 'failed' AND error_kind IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS candidates (
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

CREATE TABLE IF NOT EXISTS candidate_discoveries (
    discovery_id TEXT PRIMARY KEY,
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    query_id TEXT NOT NULL REFERENCES queries(query_id),
    platform_position INTEGER NOT NULL CHECK(platform_position >= 1),
    discovered_at TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    UNIQUE(candidate_key, query_id, platform_position)
);

CREATE TABLE IF NOT EXISTS probe_selections (
    campaign_id TEXT NOT NULL,
    query_pack_version TEXT NOT NULL REFERENCES query_packs(query_pack_version),
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    selection_rank INTEGER NOT NULL CHECK(selection_rank >= 1),
    status TEXT NOT NULL CHECK(status IN ('selected', 'probed', 'failed', 'blocked')),
    selected_run_id TEXT NOT NULL REFERENCES runs(run_id),
    completed_run_id TEXT REFERENCES runs(run_id),
    selected_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY(campaign_id, query_pack_version, candidate_key),
    UNIQUE(campaign_id, query_pack_version, selection_rank),
    CHECK(
        (status = 'selected' AND completed_run_id IS NULL AND completed_at IS NULL) OR
        (status <> 'selected' AND completed_run_id IS NOT NULL AND completed_at IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS score_evidence (
    evidence_id TEXT PRIMARY KEY,
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    score_kind TEXT NOT NULL CHECK(score_kind IN ('source', 'task', 'hard_exclusion')),
    campaign_id TEXT,
    subtype TEXT,
    rule_code TEXT NOT NULL,
    points INTEGER NOT NULL,
    matched_fields_json TEXT NOT NULL CHECK(json_valid(matched_fields_json)),
    matched_terms_json TEXT NOT NULL CHECK(json_valid(matched_terms_json)),
    reason TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidate_task_scores (
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    campaign_id TEXT NOT NULL,
    subtype TEXT NOT NULL,
    score INTEGER NOT NULL CHECK(score BETWEEN 0 AND 6),
    qualified INTEGER NOT NULL CHECK(qualified IN (0, 1)),
    blocked_by_source_gate INTEGER NOT NULL CHECK(blocked_by_source_gate IN (0, 1)),
    policy_version TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    calculated_at TEXT NOT NULL,
    PRIMARY KEY(candidate_key, campaign_id, subtype)
);

CREATE TABLE IF NOT EXISTS vector_index_outbox (
    event_id TEXT PRIMARY KEY,
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    embedding_schema_version TEXT NOT NULL REFERENCES embedding_schema_versions(embedding_schema_version),
    projection_revision INTEGER NOT NULL CHECK(projection_revision >= 1),
    input_hash TEXT NOT NULL,
    event_kind TEXT NOT NULL CHECK(event_kind IN ('upsert', 'rebuild', 'delete')),
    status TEXT NOT NULL CHECK(status IN ('pending', 'processing', 'completed', 'failed', 'superseded')),
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(candidate_key, embedding_schema_version, projection_revision, event_kind)
);

CREATE TABLE IF NOT EXISTS candidate_embeddings (
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    embedding_schema_version TEXT NOT NULL REFERENCES embedding_schema_versions(embedding_schema_version),
    vector_name TEXT NOT NULL CHECK(vector_name IN ('relevance', 'duplicate')),
    projection_revision INTEGER NOT NULL CHECK(projection_revision >= 1),
    current_input_hash TEXT NOT NULL,
    indexed_input_hash TEXT,
    qdrant_point_id TEXT NOT NULL,
    index_status TEXT NOT NULL CHECK(index_status IN ('pending', 'ready', 'failed', 'superseded')),
    indexed_at TEXT,
    PRIMARY KEY(candidate_key, embedding_schema_version, vector_name)
);

CREATE TABLE IF NOT EXISTS subtype_semantic_queries (
    query_key TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    subtype TEXT NOT NULL,
    query_pack_version TEXT NOT NULL REFERENCES query_packs(query_pack_version),
    template_version TEXT NOT NULL REFERENCES semantic_query_templates(template_version),
    embedding_schema_version TEXT NOT NULL
        REFERENCES embedding_schema_versions(embedding_schema_version),
    instruction_version TEXT NOT NULL,
    instruction_text TEXT NOT NULL,
    query_text TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    qdrant_point_id TEXT NOT NULL,
    index_status TEXT NOT NULL CHECK(index_status IN ('pending', 'ready', 'failed')),
    created_run_id TEXT NOT NULL REFERENCES runs(run_id),
    updated_run_id TEXT NOT NULL REFERENCES runs(run_id),
    created_at TEXT NOT NULL,
    indexed_at TEXT,
    error_kind TEXT,
    UNIQUE(
        campaign_id, subtype, query_pack_version,
        template_version, embedding_schema_version
    ),
    CHECK(
        (index_status = 'ready' AND indexed_at IS NOT NULL AND error_kind IS NULL) OR
        (index_status = 'pending' AND indexed_at IS NULL AND error_kind IS NULL) OR
        (index_status = 'failed' AND indexed_at IS NULL AND error_kind IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS threshold_calibrations (
    calibration_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    campaign_id TEXT NOT NULL,
    embedding_schema_version TEXT NOT NULL
        REFERENCES embedding_schema_versions(embedding_schema_version),
    label_source_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('passed', 'insufficient', 'failed')),
    thresholds_json TEXT NOT NULL CHECK(json_valid(thresholds_json)),
    report_json TEXT NOT NULL CHECK(json_valid(report_json)),
    created_at TEXT NOT NULL,
    CHECK(
        (status = 'passed' AND thresholds_json <> '{}') OR
        (status <> 'passed' AND thresholds_json = '{}')
    )
);

CREATE TABLE IF NOT EXISTS calibration_exports (
    export_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    campaign_id TEXT NOT NULL,
    query_pack_version TEXT NOT NULL REFERENCES query_packs(query_pack_version),
    embedding_schema_version TEXT NOT NULL
        REFERENCES embedding_schema_versions(embedding_schema_version),
    output_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    record_count INTEGER NOT NULL CHECK(record_count >= 0),
    subtype_counts_json TEXT NOT NULL CHECK(json_valid(subtype_counts_json)),
    created_at TEXT NOT NULL,
    UNIQUE(
        run_id, campaign_id, query_pack_version,
        embedding_schema_version, output_path
    )
);

CREATE TABLE IF NOT EXISTS calibration_candidate_embeddings (
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    embedding_schema_version TEXT NOT NULL
        REFERENCES embedding_schema_versions(embedding_schema_version),
    input_hash TEXT NOT NULL,
    qdrant_point_id TEXT NOT NULL,
    index_status TEXT NOT NULL CHECK(index_status IN ('pending', 'ready', 'failed')),
    indexed_at TEXT,
    error_kind TEXT,
    updated_run_id TEXT NOT NULL REFERENCES runs(run_id),
    PRIMARY KEY(candidate_key, embedding_schema_version),
    CHECK(
        (index_status = 'ready' AND indexed_at IS NOT NULL AND error_kind IS NULL) OR
        (index_status = 'pending' AND indexed_at IS NULL AND error_kind IS NULL) OR
        (index_status = 'failed' AND indexed_at IS NULL AND error_kind IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS calibration_embedding_calls (
    call_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    embedding_schema_version TEXT NOT NULL
        REFERENCES embedding_schema_versions(embedding_schema_version),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    subject_count INTEGER NOT NULL CHECK(subject_count > 0),
    input_hashes_json TEXT NOT NULL CHECK(json_valid(input_hashes_json)),
    status TEXT NOT NULL CHECK(status IN ('succeeded', 'failed')),
    error_kind TEXT,
    status_code INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    CHECK(
        (status = 'succeeded' AND error_kind IS NULL) OR
        (status = 'failed' AND error_kind IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS semantic_recall_exports (
    export_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    campaign_id TEXT NOT NULL,
    query_pack_version TEXT NOT NULL REFERENCES query_packs(query_pack_version),
    embedding_schema_version TEXT NOT NULL
        REFERENCES embedding_schema_versions(embedding_schema_version),
    output_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    record_count INTEGER NOT NULL CHECK(record_count >= 0),
    subtype_counts_json TEXT NOT NULL CHECK(json_valid(subtype_counts_json)),
    created_at TEXT NOT NULL,
    UNIQUE(run_id, campaign_id, output_path)
);

CREATE TABLE IF NOT EXISTS semantic_task_eligibility (
    eligibility_id TEXT PRIMARY KEY,
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    campaign_id TEXT NOT NULL,
    subtype TEXT NOT NULL,
    query_pack_version TEXT NOT NULL REFERENCES query_packs(query_pack_version),
    embedding_schema_version TEXT NOT NULL
        REFERENCES embedding_schema_versions(embedding_schema_version),
    policy_version TEXT NOT NULL,
    similarity REAL NOT NULL CHECK(similarity >= -1 AND similarity <= 1),
    threshold REAL NOT NULL CHECK(threshold >= -1 AND threshold <= 1),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    created_at TEXT NOT NULL,
    UNIQUE(
        candidate_key, campaign_id, subtype, query_pack_version,
        embedding_schema_version, policy_version
    )
);

CREATE INDEX IF NOT EXISTS semantic_task_eligibility_lookup_idx
ON semantic_task_eligibility(
    campaign_id, subtype, query_pack_version,
    embedding_schema_version, similarity DESC
);

CREATE TABLE IF NOT EXISTS pilot_feedback_imports (
    import_id TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL,
    source_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL UNIQUE,
    exported_at TEXT,
    label_count INTEGER NOT NULL CHECK(label_count > 0),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pilot_feedback_labels (
    import_id TEXT NOT NULL REFERENCES pilot_feedback_imports(import_id),
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    campaign_id TEXT NOT NULL,
    shown_subtype TEXT NOT NULL,
    source_correct INTEGER CHECK(source_correct IN (0, 1) OR source_correct IS NULL),
    task_usable INTEGER CHECK(task_usable IN (0, 1) OR task_usable IS NULL),
    corrected_subtype TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(import_id, candidate_key, campaign_id, shown_subtype)
);

CREATE TABLE IF NOT EXISTS semantic_gate_decisions (
    decision_id TEXT PRIMARY KEY,
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    campaign_id TEXT NOT NULL,
    subtype TEXT NOT NULL,
    base_policy_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    accepted INTEGER NOT NULL CHECK(accepted IN (0, 1)),
    similarity REAL NOT NULL CHECK(similarity >= -1 AND similarity <= 1),
    threshold REAL NOT NULL CHECK(threshold >= -1 AND threshold <= 1),
    source_score INTEGER NOT NULL,
    reasons_json TEXT NOT NULL CHECK(json_valid(reasons_json)),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    created_at TEXT NOT NULL,
    UNIQUE(candidate_key, campaign_id, subtype, policy_version)
);

CREATE INDEX IF NOT EXISTS semantic_gate_decisions_lookup_idx
ON semantic_gate_decisions(policy_version, campaign_id, subtype, accepted);

CREATE TABLE IF NOT EXISTS dedupe_policy_versions (
    dedupe_policy_version TEXT PRIMARY KEY,
    embedding_schema_version TEXT NOT NULL REFERENCES embedding_schema_versions(embedding_schema_version),
    policy_json TEXT NOT NULL CHECK(json_valid(policy_json)),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS duplicate_edges (
    edge_id TEXT PRIMARY KEY,
    left_candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    right_candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    kind TEXT NOT NULL CHECK(kind IN ('exact', 'fingerprint', 'vector_suspect', 'sha256')),
    evidence_version TEXT NOT NULL,
    similarity REAL,
    evidence_json TEXT NOT NULL CHECK(json_valid(evidence_json)),
    created_at TEXT NOT NULL,
    CHECK(left_candidate_key < right_candidate_key),
    UNIQUE(left_candidate_key, right_candidate_key, kind, evidence_version)
);

CREATE TABLE IF NOT EXISTS duplicate_clusters (
    duplicate_cluster_id TEXT PRIMARY KEY,
    dedupe_policy_version TEXT NOT NULL REFERENCES dedupe_policy_versions(dedupe_policy_version),
    cluster_kind TEXT NOT NULL CHECK(cluster_kind IN ('exact', 'fingerprint', 'vector_suspect', 'sha256')),
    status TEXT NOT NULL CHECK(status IN ('active', 'dismissed', 'confirmed')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS duplicate_cluster_members (
    duplicate_cluster_id TEXT NOT NULL REFERENCES duplicate_clusters(duplicate_cluster_id),
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    member_status TEXT NOT NULL CHECK(member_status IN ('ready', 'leased', 'suspended')),
    run_id TEXT REFERENCES runs(run_id),
    PRIMARY KEY(duplicate_cluster_id, candidate_key)
);

CREATE TABLE IF NOT EXISTS dedupe_refreshes (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    campaign_id TEXT NOT NULL,
    embedding_schema_version TEXT NOT NULL REFERENCES embedding_schema_versions(embedding_schema_version),
    dedupe_policy_version TEXT NOT NULL REFERENCES dedupe_policy_versions(dedupe_policy_version),
    status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'failed')),
    edge_count INTEGER NOT NULL DEFAULT 0,
    cluster_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY(run_id, campaign_id, embedding_schema_version, dedupe_policy_version)
);

CREATE TABLE IF NOT EXISTS frontier_entries (
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    campaign_id TEXT NOT NULL,
    subtype TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    status TEXT NOT NULL CHECK(status IN ('ready', 'leased', 'consumed', 'suspended')),
    task_score INTEGER NOT NULL CHECK(task_score BETWEEN 4 AND 6),
    source_score INTEGER NOT NULL CHECK(source_score >= 4),
    platform TEXT NOT NULL,
    lang TEXT NOT NULL CHECK(lang IN ('en', 'es', 'fr')),
    attributed_query_id TEXT NOT NULL REFERENCES queries(query_id),
    frontier_policy_version TEXT NOT NULL,
    embedding_schema_version TEXT NOT NULL,
    dedupe_policy_version TEXT NOT NULL,
    lease_id TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(candidate_key, campaign_id, subtype, run_id)
);

CREATE TABLE IF NOT EXISTS secondary_batches (
    batch_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    campaign_id TEXT NOT NULL,
    campaign_policy_version TEXT NOT NULL,
    frontier_policy_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('open', 'reviewed', 'queued', 'completed', 'failed')),
    requested_size INTEGER NOT NULL CHECK(requested_size > 0),
    actual_size INTEGER NOT NULL CHECK(actual_size >= 0),
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS one_open_batch_per_campaign_run
ON secondary_batches(run_id, campaign_id)
WHERE status IN ('open', 'reviewed', 'queued');

CREATE TABLE IF NOT EXISTS secondary_batch_items (
    batch_id TEXT NOT NULL REFERENCES secondary_batches(batch_id),
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    campaign_id TEXT NOT NULL,
    subtype TEXT NOT NULL,
    rank INTEGER NOT NULL CHECK(rank >= 1),
    vector_similarity REAL,
    rrf_score REAL,
    lease_id TEXT NOT NULL,
    PRIMARY KEY(batch_id, candidate_key),
    UNIQUE(batch_id, rank)
);

CREATE TABLE IF NOT EXISTS secondary_filter_decisions (
    batch_id TEXT NOT NULL,
    candidate_key TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN (
        'download_eligible', 'below_semantic_threshold',
        'duplicate_suspect', 'reclassified', 'deferred'
    )),
    decided_campaign_id TEXT,
    decided_subtype TEXT,
    vector_similarity REAL,
    threshold REAL,
    reasons_json TEXT NOT NULL CHECK(json_valid(reasons_json)),
    decided_at TEXT NOT NULL,
    PRIMARY KEY(batch_id, candidate_key),
    FOREIGN KEY(batch_id, candidate_key)
        REFERENCES secondary_batch_items(batch_id, candidate_key)
);

CREATE TABLE IF NOT EXISTS frontier_partition_stats (
    campaign_id TEXT NOT NULL,
    subtype TEXT NOT NULL,
    query_id TEXT NOT NULL REFERENCES queries(query_id),
    released_count INTEGER NOT NULL DEFAULT 0,
    eligible_count INTEGER NOT NULL DEFAULT 0,
    consecutive_low_yield_windows INTEGER NOT NULL DEFAULT 0,
    pending_window_released INTEGER NOT NULL DEFAULT 0,
    pending_window_eligible INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK(status IN ('active', 'suspended', 'exhausted')),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(campaign_id, subtype, query_id)
);

CREATE TABLE IF NOT EXISTS secondary_batch_yields (
    batch_id TEXT PRIMARY KEY REFERENCES secondary_batches(batch_id),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    campaign_id TEXT NOT NULL,
    released_count INTEGER NOT NULL CHECK(released_count > 0),
    eligible_count INTEGER NOT NULL CHECK(eligible_count >= 0),
    yield_rate REAL NOT NULL CHECK(yield_rate >= 0 AND yield_rate <= 1),
    low_yield INTEGER NOT NULL CHECK(low_yield IN (0, 1)),
    evaluated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaign_run_control (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    campaign_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'completed', 'stopped')),
    consecutive_low_yield_batches INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id, campaign_id),
    CHECK(
        (status = 'stopped' AND stop_reason IS NOT NULL) OR
        (status <> 'stopped' AND stop_reason IS NULL)
    )
);

CREATE TABLE IF NOT EXISTS campaign_hold_events (
    event_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
    action TEXT NOT NULL CHECK(action IN ('hold', 'release')),
    reason TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS campaign_hold_events_latest_idx
ON campaign_hold_events(campaign_id, created_at DESC, event_id DESC);

CREATE TABLE IF NOT EXISTS campaign_human_targets (
    target_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(campaign_id),
    target_kind TEXT NOT NULL CHECK(target_kind = 'task_usable'),
    target_count INTEGER NOT NULL CHECK(target_count > 0),
    candidate_budget INTEGER NOT NULL CHECK(candidate_budget >= target_count),
    policy_version TEXT NOT NULL,
    reason TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    created_at TEXT NOT NULL,
    UNIQUE(campaign_id, policy_version, target_kind)
);

CREATE TABLE IF NOT EXISTS candidate_suppressions (
    suppression_id TEXT PRIMARY KEY,
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    suppression_kind TEXT NOT NULL CHECK(suppression_kind IN ('source_hard_exclusion')),
    policy_version TEXT NOT NULL,
    reasons_json TEXT NOT NULL CHECK(json_valid(reasons_json)),
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    created_at TEXT NOT NULL,
    UNIQUE(candidate_key, suppression_kind, policy_version)
);

CREATE INDEX IF NOT EXISTS candidate_suppressions_lookup_idx
ON candidate_suppressions(candidate_key, suppression_kind, policy_version);

CREATE TABLE IF NOT EXISTS candidate_suppression_releases (
    release_id TEXT PRIMARY KEY,
    suppression_id TEXT NOT NULL UNIQUE REFERENCES candidate_suppressions(suppression_id),
    reason TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS queue_assignments (
    candidate_key TEXT PRIMARY KEY REFERENCES candidates(candidate_key),
    batch_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    subtype TEXT NOT NULL,
    rank INTEGER NOT NULL CHECK(rank >= 1),
    origin_discovery_id TEXT REFERENCES candidate_discoveries(discovery_id),
    queued_at TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    FOREIGN KEY(batch_id, candidate_key)
        REFERENCES secondary_batch_items(batch_id, candidate_key)
);

CREATE TABLE IF NOT EXISTS download_attempts (
    attempt_id TEXT PRIMARY KEY,
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    platform TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    campaign_id TEXT NOT NULL,
    subtype TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'running', 'succeeded', 'failed', 'resource_limit')),
    adapter_version TEXT NOT NULL,
    network_config TEXT NOT NULL CHECK(network_config = 'default'),
    cooldown_seconds REAL NOT NULL DEFAULT 0 CHECK(cooldown_seconds >= 0),
    error_kind TEXT,
    error_message TEXT,
    temp_path TEXT,
    final_path TEXT,
    bytes_downloaded INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS download_retry_events (
    retry_event_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL REFERENCES download_attempts(attempt_id),
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    retry_ordinal INTEGER NOT NULL CHECK(retry_ordinal >= 1),
    error_kind TEXT NOT NULL CHECK(error_kind IN ('network', 'rate_limited', 'timeout')),
    delay_seconds REAL NOT NULL CHECK(delay_seconds >= 0),
    error_message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(attempt_id, retry_ordinal)
);

CREATE TABLE IF NOT EXISTS technical_checks (
    attempt_id TEXT PRIMARY KEY REFERENCES download_attempts(attempt_id),
    ffprobe_passed INTEGER NOT NULL CHECK(ffprobe_passed IN (0, 1)),
    video_stream_present INTEGER NOT NULL CHECK(video_stream_present IN (0, 1)),
    decode_first_passed INTEGER NOT NULL CHECK(decode_first_passed IN (0, 1)),
    decode_middle_passed INTEGER NOT NULL CHECK(decode_middle_passed IN (0, 1)),
    decode_last_passed INTEGER NOT NULL CHECK(decode_last_passed IN (0, 1)),
    duration_seconds REAL,
    width INTEGER,
    height INTEGER,
    sha256 TEXT,
    details_json TEXT NOT NULL CHECK(json_valid(details_json)),
    checked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS media_objects (
    sha256 TEXT PRIMARY KEY,
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    publish_status TEXT NOT NULL CHECK(publish_status IN ('pending', 'published', 'quarantined', 'failed')),
    final_path TEXT,
    bytes INTEGER NOT NULL CHECK(bytes >= 0),
    created_at TEXT NOT NULL,
    published_at TEXT
);

CREATE TABLE IF NOT EXISTS media_publish_intents (
    attempt_id TEXT PRIMARY KEY REFERENCES download_attempts(attempt_id),
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    sha256 TEXT NOT NULL REFERENCES media_objects(sha256),
    kind TEXT NOT NULL CHECK(kind IN ('publish', 'quarantine')),
    status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'failed')),
    temp_path TEXT NOT NULL,
    target_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS state_transitions (
    transition_id TEXT PRIMARY KEY,
    candidate_key TEXT NOT NULL REFERENCES candidates(candidate_key),
    old_status TEXT NOT NULL,
    new_status TEXT NOT NULL,
    reason TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    transitioned_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS legacy_downloads (
    candidate_key TEXT PRIMARY KEY,
    youtube_id TEXT NOT NULL UNIQUE,
    legacy_status TEXT NOT NULL,
    source_path TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    CHECK(candidate_key = 'youtube:' || youtube_id)
);

CREATE TABLE IF NOT EXISTS legacy_imports (
    import_id TEXT PRIMARY KEY,
    history_path TEXT NOT NULL,
    history_sha256 TEXT NOT NULL,
    archive_path TEXT,
    imported_download_count INTEGER NOT NULL CHECK(imported_download_count >= 0),
    imported_uploader_prior_count INTEGER NOT NULL CHECK(imported_uploader_prior_count >= 0),
    missing_metadata_count INTEGER NOT NULL CHECK(missing_metadata_count >= 0),
    created_at TEXT NOT NULL,
    UNIQUE(history_sha256, archive_path)
);

CREATE TABLE IF NOT EXISTS uploader_priors (
    platform TEXT NOT NULL CHECK(platform IN ('youtube', 'dailymotion', 'peertube')),
    uploader_id TEXT NOT NULL,
    completed_count INTEGER NOT NULL CHECK(completed_count >= 1),
    prior_points INTEGER NOT NULL CHECK(prior_points BETWEEN 0 AND 2),
    provenance_json TEXT NOT NULL CHECK(json_valid(provenance_json)),
    calculated_at TEXT NOT NULL,
    PRIMARY KEY(platform, uploader_id)
);

CREATE INDEX IF NOT EXISTS candidates_status_idx ON candidates(status);
CREATE INDEX IF NOT EXISTS candidates_uploader_idx ON candidates(platform, uploader_id);
CREATE INDEX IF NOT EXISTS task_scores_queue_idx
ON candidate_task_scores(campaign_id, subtype, qualified, score DESC);
CREATE INDEX IF NOT EXISTS frontier_ready_idx
ON frontier_entries(run_id, campaign_id, subtype, status, task_score DESC, source_score DESC);
CREATE INDEX IF NOT EXISTS score_evidence_candidate_idx
ON score_evidence(candidate_key, score_kind, created_at);
CREATE INDEX IF NOT EXISTS adapter_calls_run_operation_idx
ON adapter_calls(run_id, operation, platform, started_at);
CREATE INDEX IF NOT EXISTS probe_selections_status_idx
ON probe_selections(campaign_id, query_pack_version, status, selection_rank);
CREATE INDEX IF NOT EXISTS subtype_semantic_queries_ready_idx
ON subtype_semantic_queries(
    campaign_id, query_pack_version, embedding_schema_version, index_status
);
CREATE INDEX IF NOT EXISTS embedding_calls_run_idx
ON embedding_calls(run_id, operation, started_at);
CREATE INDEX IF NOT EXISTS threshold_calibrations_campaign_idx
ON threshold_calibrations(campaign_id, embedding_schema_version, status, created_at);

CREATE TRIGGER IF NOT EXISTS prevent_frozen_query_pack_update
BEFORE UPDATE ON query_packs
BEGIN
    SELECT RAISE(ABORT, 'frozen query packs are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_frozen_query_pack_delete
BEFORE DELETE ON query_packs
BEGIN
    SELECT RAISE(ABORT, 'frozen query packs are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_frozen_query_update
BEFORE UPDATE ON queries
WHEN EXISTS (
    SELECT 1 FROM query_packs
    WHERE query_pack_version = OLD.query_pack_version AND status = 'frozen'
)
BEGIN
    SELECT RAISE(ABORT, 'queries in a frozen pack are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_frozen_query_delete
BEFORE DELETE ON queries
WHEN EXISTS (
    SELECT 1 FROM query_packs
    WHERE query_pack_version = OLD.query_pack_version AND status = 'frozen'
)
BEGIN
    SELECT RAISE(ABORT, 'queries in a frozen pack are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_semantic_query_template_update
BEFORE UPDATE ON semantic_query_templates
BEGIN
    SELECT RAISE(ABORT, 'semantic query templates are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_semantic_query_template_delete
BEFORE DELETE ON semantic_query_templates
BEGIN
    SELECT RAISE(ABORT, 'semantic query templates are immutable');
END;

CREATE TRIGGER IF NOT EXISTS validate_candidate_state_edge
BEFORE UPDATE OF status ON candidates
WHEN OLD.status <> NEW.status AND NOT (
    (OLD.status = 'discovered' AND NEW.status = 'source_qualified') OR
    (OLD.status = 'source_qualified' AND NEW.status = 'task_queued') OR
    (OLD.status = 'task_queued' AND NEW.status IN ('downloaded', 'technical_failed', 'duplicate_suppressed'))
)
BEGIN
    SELECT RAISE(ABORT, 'illegal candidate state transition');
END;

DROP TRIGGER IF EXISTS validate_task_queue_prerequisites;
CREATE TRIGGER validate_task_queue_prerequisites
BEFORE UPDATE OF status ON candidates
WHEN NEW.status = 'task_queued' AND (
    NOT EXISTS (
        SELECT 1 FROM queue_assignments q
        WHERE q.candidate_key = NEW.candidate_key
          AND (
              EXISTS (
                  SELECT 1 FROM candidate_task_scores t
                  WHERE t.candidate_key = q.candidate_key
                    AND t.campaign_id = q.campaign_id
                    AND t.subtype = q.subtype
                    AND t.qualified = 1 AND t.score >= 4
              )
              OR EXISTS (
                  SELECT 1 FROM semantic_task_eligibility s
                  WHERE s.candidate_key = q.candidate_key
                    AND s.campaign_id = q.campaign_id
                    AND s.subtype = q.subtype
              )
          )
    ) OR
    NOT EXISTS (
        SELECT 1
        FROM secondary_filter_decisions d
        JOIN queue_assignments q
          ON q.batch_id = d.batch_id AND q.candidate_key = d.candidate_key
        WHERE d.candidate_key = NEW.candidate_key AND d.decision = 'download_eligible'
    )
)
BEGIN
    SELECT RAISE(ABORT, 'task_queued prerequisites are missing');
END;

CREATE TRIGGER IF NOT EXISTS validate_downloaded_prerequisites
BEFORE UPDATE OF status ON candidates
WHEN NEW.status = 'downloaded' AND NOT EXISTS (
    SELECT 1 FROM media_objects
    WHERE candidate_key = NEW.candidate_key AND publish_status = 'published'
)
BEGIN
    SELECT RAISE(ABORT, 'downloaded requires a published media object');
END;

CREATE TRIGGER IF NOT EXISTS validate_technical_failure_prerequisites
BEFORE UPDATE OF status ON candidates
WHEN NEW.status = 'technical_failed' AND NOT EXISTS (
    SELECT 1 FROM download_attempts
    WHERE candidate_key = NEW.candidate_key AND status IN ('failed', 'resource_limit')
)
BEGIN
    SELECT RAISE(ABORT, 'technical_failed requires a failed download attempt');
END;

CREATE TRIGGER IF NOT EXISTS validate_duplicate_prerequisites
BEFORE UPDATE OF status ON candidates
WHEN NEW.status = 'duplicate_suppressed' AND NOT EXISTS (
    SELECT 1 FROM duplicate_edges
    WHERE kind = 'sha256'
      AND (left_candidate_key = NEW.candidate_key OR right_candidate_key = NEW.candidate_key)
)
BEGIN
    SELECT RAISE(ABORT, 'duplicate_suppressed requires SHA-256 evidence');
END;

CREATE TRIGGER IF NOT EXISTS prevent_state_transition_update
BEFORE UPDATE ON state_transitions
BEGIN
    SELECT RAISE(ABORT, 'state transitions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_state_transition_delete
BEFORE DELETE ON state_transitions
BEGIN
    SELECT RAISE(ABORT, 'state transitions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_score_evidence_update
BEFORE UPDATE ON score_evidence
BEGIN
    SELECT RAISE(ABORT, 'score evidence is append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_score_evidence_delete
BEFORE DELETE ON score_evidence
BEGIN
    SELECT RAISE(ABORT, 'score evidence is append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_adapter_call_update
BEFORE UPDATE ON adapter_calls
BEGIN
    SELECT RAISE(ABORT, 'adapter calls are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_adapter_call_delete
BEFORE DELETE ON adapter_calls
BEGIN
    SELECT RAISE(ABORT, 'adapter calls are append-only');
END;

CREATE TRIGGER IF NOT EXISTS validate_probe_selection_transition
BEFORE UPDATE OF status ON probe_selections
WHEN OLD.status <> NEW.status AND NOT (
    OLD.status = 'selected' AND NEW.status IN ('probed', 'failed', 'blocked')
)
BEGIN
    SELECT RAISE(ABORT, 'illegal probe selection transition');
END;

CREATE TRIGGER IF NOT EXISTS prevent_embedding_call_update
BEFORE UPDATE ON embedding_calls
BEGIN
    SELECT RAISE(ABORT, 'embedding calls are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_embedding_call_delete
BEFORE DELETE ON embedding_calls
BEGIN
    SELECT RAISE(ABORT, 'embedding calls are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_threshold_calibration_update
BEFORE UPDATE ON threshold_calibrations
BEGIN
    SELECT RAISE(ABORT, 'threshold calibrations are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_threshold_calibration_delete
BEFORE DELETE ON threshold_calibrations
BEGIN
    SELECT RAISE(ABORT, 'threshold calibrations are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_calibration_export_update
BEFORE UPDATE ON calibration_exports
BEGIN
    SELECT RAISE(ABORT, 'calibration exports are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_calibration_export_delete
BEFORE DELETE ON calibration_exports
BEGIN
    SELECT RAISE(ABORT, 'calibration exports are immutable');
END;

CREATE TRIGGER IF NOT EXISTS prevent_legacy_import_update
BEFORE UPDATE ON legacy_imports
BEGIN
    SELECT RAISE(ABORT, 'legacy imports are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_legacy_import_delete
BEFORE DELETE ON legacy_imports
BEGIN
    SELECT RAISE(ABORT, 'legacy imports are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_calibration_embedding_call_update
BEFORE UPDATE ON calibration_embedding_calls
BEGIN
    SELECT RAISE(ABORT, 'calibration embedding calls are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_calibration_embedding_call_delete
BEFORE DELETE ON calibration_embedding_calls
BEGIN
    SELECT RAISE(ABORT, 'calibration embedding calls are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_semantic_recall_export_update
BEFORE UPDATE ON semantic_recall_exports
BEGIN
    SELECT RAISE(ABORT, 'semantic recall exports are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_semantic_recall_export_delete
BEFORE DELETE ON semantic_recall_exports
BEGIN
    SELECT RAISE(ABORT, 'semantic recall exports are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_semantic_task_eligibility_update
BEFORE UPDATE ON semantic_task_eligibility
BEGIN
    SELECT RAISE(ABORT, 'semantic task eligibility is append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_semantic_task_eligibility_delete
BEFORE DELETE ON semantic_task_eligibility
BEGIN
    SELECT RAISE(ABORT, 'semantic task eligibility is append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_pilot_feedback_import_update
BEFORE UPDATE ON pilot_feedback_imports
BEGIN
    SELECT RAISE(ABORT, 'pilot feedback imports are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_pilot_feedback_import_delete
BEFORE DELETE ON pilot_feedback_imports
BEGIN
    SELECT RAISE(ABORT, 'pilot feedback imports are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_pilot_feedback_label_update
BEFORE UPDATE ON pilot_feedback_labels
BEGIN
    SELECT RAISE(ABORT, 'pilot feedback labels are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_pilot_feedback_label_delete
BEFORE DELETE ON pilot_feedback_labels
BEGIN
    SELECT RAISE(ABORT, 'pilot feedback labels are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_semantic_gate_decision_update
BEFORE UPDATE ON semantic_gate_decisions
BEGIN
    SELECT RAISE(ABORT, 'semantic gate decisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_semantic_gate_decision_delete
BEFORE DELETE ON semantic_gate_decisions
BEGIN
    SELECT RAISE(ABORT, 'semantic gate decisions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_download_retry_event_update
BEFORE UPDATE ON download_retry_events
BEGIN
    SELECT RAISE(ABORT, 'download retry events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_download_retry_event_delete
BEFORE DELETE ON download_retry_events
BEGIN
    SELECT RAISE(ABORT, 'download retry events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_campaign_hold_event_update
BEFORE UPDATE ON campaign_hold_events
BEGIN
    SELECT RAISE(ABORT, 'campaign hold events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_campaign_hold_event_delete
BEFORE DELETE ON campaign_hold_events
BEGIN
    SELECT RAISE(ABORT, 'campaign hold events are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_campaign_human_target_update
BEFORE UPDATE ON campaign_human_targets
BEGIN
    SELECT RAISE(ABORT, 'campaign human targets are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_campaign_human_target_delete
BEFORE DELETE ON campaign_human_targets
BEGIN
    SELECT RAISE(ABORT, 'campaign human targets are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_candidate_suppression_update
BEFORE UPDATE ON candidate_suppressions
BEGIN
    SELECT RAISE(ABORT, 'candidate suppressions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_candidate_suppression_delete
BEFORE DELETE ON candidate_suppressions
BEGIN
    SELECT RAISE(ABORT, 'candidate suppressions are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_candidate_suppression_release_update
BEFORE UPDATE ON candidate_suppression_releases
BEGIN
    SELECT RAISE(ABORT, 'candidate suppression releases are append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_candidate_suppression_release_delete
BEFORE DELETE ON candidate_suppression_releases
BEGIN
    SELECT RAISE(ABORT, 'candidate suppression releases are append-only');
END;
