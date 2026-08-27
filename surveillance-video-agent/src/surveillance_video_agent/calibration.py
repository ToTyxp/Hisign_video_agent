"""Uploader-grouped relevance threshold calibration with hard sufficiency gates."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Mapping, Sequence

from surveillance_video_agent.db import CandidateDatabase, utc_now
from surveillance_video_agent.embedding import EmbeddingSchema


CALIBRATION_ALGORITHM_VERSION = "grouped-threshold-calibration-v1.0.0"
_CALIBRATION_NAMESPACE = uuid.UUID("ed908835-0a1e-43e0-8f64-6b2609492404")


@dataclass(frozen=True, slots=True)
class CalibrationExample:
    candidate_key: str
    campaign_id: str
    subtype: str
    uploader_identity: str
    platform: str
    lang: str
    similarity: float
    usable: bool

    def __post_init__(self) -> None:
        if not all(
            isinstance(value, str) and value
            for value in (
                self.candidate_key,
                self.campaign_id,
                self.subtype,
                self.uploader_identity,
                self.platform,
                self.lang,
            )
        ):
            raise ValueError("calibration example identity fields are required")
        if not math.isfinite(self.similarity) or not 0 <= self.similarity <= 1:
            raise ValueError("calibration similarity must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class ThresholdMetrics:
    sample_count: int
    positive_count: int
    negative_count: int
    predicted_positive_count: int
    true_positive_count: int
    precision: float
    recall: float


@dataclass(frozen=True, slots=True)
class SubtypeCalibrationResult:
    subtype: str
    status: str
    threshold: float | None
    reason: str | None
    uploader_group_count: int
    training: ThresholdMetrics | None
    evaluation: ThresholdMetrics | None
    platforms: tuple[str, ...]
    languages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    calibration_id: str
    algorithm_version: str
    campaign_id: str
    embedding_schema_version: str
    label_source_sha256: str
    status: str
    thresholds: Mapping[str, float]
    subtypes: tuple[SubtypeCalibrationResult, ...]


def calibrate_relevance_thresholds(
    examples: Sequence[CalibrationExample],
    *,
    campaign_id: str,
    embedding_schema_version: str,
    expected_subtypes: Sequence[str],
    min_examples_per_subtype: int = 30,
    min_positive_per_subtype: int = 10,
    min_negative_per_subtype: int = 10,
    min_eval_positive: int = 2,
    min_eval_negative: int = 2,
    recall_floor: float = 0.90,
    precision_floor: float = 0.20,
) -> CalibrationResult:
    if not campaign_id or not embedding_schema_version or not expected_subtypes:
        raise ValueError("campaign, schema, and expected subtypes are required")
    if len(set(expected_subtypes)) != len(expected_subtypes):
        raise ValueError("expected subtypes must be unique")
    if min_examples_per_subtype <= 0 or min_positive_per_subtype <= 0:
        raise ValueError("calibration sample minimums must be positive")
    if min_negative_per_subtype <= 0 or min_eval_positive <= 0 or min_eval_negative <= 0:
        raise ValueError("calibration class minimums must be positive")
    if not 0 < recall_floor <= 1 or not 0 < precision_floor <= 1:
        raise ValueError("calibration metric floors must be between 0 and 1")
    values = tuple(examples)
    if any(item.campaign_id != campaign_id for item in values):
        raise ValueError("calibration examples span multiple campaigns")
    unexpected = {item.subtype for item in values} - set(expected_subtypes)
    if unexpected:
        raise ValueError(f"unexpected calibration subtypes: {sorted(unexpected)}")
    duplicate_keys = [
        key
        for key, count in _counts(
            f"{item.subtype}\0{item.candidate_key}" for item in values
        ).items()
        if count > 1
    ]
    if duplicate_keys:
        raise ValueError("calibration candidate keys must be unique")

    source_hash = _label_source_hash(values)
    calibration_id = str(
        uuid.uuid5(
            _CALIBRATION_NAMESPACE,
            f"{CALIBRATION_ALGORITHM_VERSION}:{campaign_id}:"
            f"{embedding_schema_version}:{source_hash}",
        )
    )
    subtype_results: list[SubtypeCalibrationResult] = []
    thresholds: dict[str, float] = {}
    for subtype in expected_subtypes:
        subset = tuple(item for item in values if item.subtype == subtype)
        result = _calibrate_subtype(
            subtype,
            subset,
            min_examples=min_examples_per_subtype,
            min_positive=min_positive_per_subtype,
            min_negative=min_negative_per_subtype,
            min_eval_positive=min_eval_positive,
            min_eval_negative=min_eval_negative,
            recall_floor=recall_floor,
            precision_floor=precision_floor,
        )
        subtype_results.append(result)
        if result.status == "passed" and result.threshold is not None:
            thresholds[subtype] = result.threshold
    status = "passed" if len(thresholds) == len(expected_subtypes) else "insufficient"
    if status != "passed":
        thresholds = {}
    return CalibrationResult(
        calibration_id=calibration_id,
        algorithm_version=CALIBRATION_ALGORITHM_VERSION,
        campaign_id=campaign_id,
        embedding_schema_version=embedding_schema_version,
        label_source_sha256=source_hash,
        status=status,
        thresholds=MappingProxyType(thresholds),
        subtypes=tuple(subtype_results),
    )


def store_calibration_result(
    database: CandidateDatabase,
    schema: EmbeddingSchema,
    result: CalibrationResult,
    *,
    run_id: str,
) -> None:
    if result.embedding_schema_version != schema.version:
        raise ValueError("calibration result belongs to a different embedding schema")
    run = database.connection.execute(
        "SELECT status FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if run is None or run["status"] != "running":
        raise ValueError("an existing running run_id is required")
    database.register_embedding_schema(schema)
    report = {
        "algorithm_version": result.algorithm_version,
        "campaign_id": result.campaign_id,
        "embedding_schema_version": result.embedding_schema_version,
        "label_source_sha256": result.label_source_sha256,
        "status": result.status,
        "subtypes": [
            {
                **asdict(item),
                "training": asdict(item.training) if item.training else None,
                "evaluation": asdict(item.evaluation) if item.evaluation else None,
            }
            for item in result.subtypes
        ],
    }
    thresholds_json = json.dumps(
        dict(result.thresholds),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    report_json = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with database.transaction() as connection:
        existing = connection.execute(
            "SELECT * FROM threshold_calibrations WHERE calibration_id = ?",
            (result.calibration_id,),
        ).fetchone()
        if existing is not None:
            if (
                existing["status"] != result.status
                or existing["thresholds_json"] != thresholds_json
                or existing["report_json"] != report_json
            ):
                raise ValueError("calibration identity already has different results")
            return
        connection.execute(
            """
            INSERT INTO threshold_calibrations(
                calibration_id, run_id, campaign_id,
                embedding_schema_version, label_source_sha256,
                status, thresholds_json, report_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result.calibration_id,
                run_id,
                result.campaign_id,
                result.embedding_schema_version,
                result.label_source_sha256,
                result.status,
                thresholds_json,
                report_json,
                utc_now(),
            ),
        )


def _calibrate_subtype(
    subtype: str,
    examples: tuple[CalibrationExample, ...],
    *,
    min_examples: int,
    min_positive: int,
    min_negative: int,
    min_eval_positive: int,
    min_eval_negative: int,
    recall_floor: float,
    precision_floor: float,
) -> SubtypeCalibrationResult:
    positives = sum(item.usable for item in examples)
    negatives = len(examples) - positives
    groups = sorted(
        {item.uploader_identity for item in examples},
        key=lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest(),
    )
    base = {
        "subtype": subtype,
        "uploader_group_count": len(groups),
        "platforms": tuple(sorted({item.platform for item in examples})),
        "languages": tuple(sorted({item.lang for item in examples})),
    }
    if len(examples) < min_examples or positives < min_positive or negatives < min_negative:
        return SubtypeCalibrationResult(
            status="insufficient",
            threshold=None,
            reason="minimum total/positive/negative labels not met",
            training=None,
            evaluation=None,
            **base,
        )
    if len(groups) < 3:
        return SubtypeCalibrationResult(
            status="insufficient",
            threshold=None,
            reason="at least three uploader groups are required",
            training=None,
            evaluation=None,
            **base,
        )
    eval_groups = set(groups[::3])
    evaluation = tuple(item for item in examples if item.uploader_identity in eval_groups)
    training = tuple(item for item in examples if item.uploader_identity not in eval_groups)
    eval_positives = sum(item.usable for item in evaluation)
    eval_negatives = len(evaluation) - eval_positives
    if eval_positives < min_eval_positive or eval_negatives < min_eval_negative:
        return SubtypeCalibrationResult(
            status="insufficient",
            threshold=None,
            reason="uploader-grouped evaluation split lacks both classes",
            training=None,
            evaluation=None,
            **base,
        )
    candidates = sorted(
        {0.0, 1.0, *(item.similarity for item in training)}
    )
    options: list[tuple[float, ThresholdMetrics]] = []
    for threshold in candidates:
        metrics = _metrics(training, threshold)
        if metrics.recall >= recall_floor:
            options.append((threshold, metrics))
    if not options:
        return SubtypeCalibrationResult(
            status="insufficient",
            threshold=None,
            reason="no threshold met training recall floor",
            training=None,
            evaluation=None,
            **base,
        )
    threshold, training_metrics = max(
        options,
        key=lambda item: (item[1].precision, item[0], item[1].recall),
    )
    evaluation_metrics = _metrics(evaluation, threshold)
    passed = (
        evaluation_metrics.recall >= recall_floor
        and evaluation_metrics.precision > precision_floor
    )
    return SubtypeCalibrationResult(
        status="passed" if passed else "insufficient",
        threshold=threshold if passed else None,
        reason=None if passed else "independent evaluation metric gate failed",
        training=training_metrics,
        evaluation=evaluation_metrics,
        **base,
    )


def _metrics(
    examples: Sequence[CalibrationExample], threshold: float
) -> ThresholdMetrics:
    positives = sum(item.usable for item in examples)
    predicted = [item for item in examples if item.similarity >= threshold]
    true_positive = sum(item.usable for item in predicted)
    precision = true_positive / len(predicted) if predicted else 0.0
    recall = true_positive / positives if positives else 0.0
    return ThresholdMetrics(
        sample_count=len(examples),
        positive_count=positives,
        negative_count=len(examples) - positives,
        predicted_positive_count=len(predicted),
        true_positive_count=true_positive,
        precision=precision,
        recall=recall,
    )


def _label_source_hash(examples: Sequence[CalibrationExample]) -> str:
    rows = [
        {
            "candidate_key": item.candidate_key,
            "campaign_id": item.campaign_id,
            "lang": item.lang,
            "platform": item.platform,
            "similarity": item.similarity,
            "subtype": item.subtype,
            "uploader_identity_sha256": hashlib.sha256(
                item.uploader_identity.encode("utf-8")
            ).hexdigest(),
            "usable": item.usable,
        }
        for item in sorted(examples, key=lambda value: value.candidate_key)
    ]
    content = json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _counts(values) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return result
