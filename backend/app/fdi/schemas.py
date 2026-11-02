"""Pydantic contracts for the FDI API.

The four schema decisions applied here:
  1. XaiShapTwin.target — literal widened to "twin_sm5" | "stage1_m1"
     so the string "twin_sm5" keeps working; the new panel can use
     "stage1_m1" when it's ready. (Backward compatible.)
  2. XaiMeta keeps `score` (value is v3 proba in [0,1]); adds an optional
     `proba` alias so new frontends can use the precise name.
  3. AttackClassificationDetected.type stays `str | None`; adds `type_conf`.
  4. DemoResponse unchanged; the shipped demo is a real parquet.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Level = Literal["ATTAQUE", "SUSPECT", "normal", "historique insuffisant"]


class ModelInfo(BaseModel):
    ready: bool
    framework: str
    repo_id: str | None = None
    filename: str | None = None
    stations: list[str]
    twin_features: list[str]
    inputs: list[str]
    threshold: float
    suspect_threshold: float | None = None
    min_history_days: int


class DatasetInfo(BaseModel):
    rows: int
    scored: int
    insufficient_history: int
    stations: list[str]
    start: str | None = None
    end: str | None = None
    renamed_columns: dict[str, str] = Field(default_factory=dict)
    source: str
    filename: str | None = None


class Summary(BaseModel):
    hours: int
    attaque: int
    suspect: int
    normal: int
    insufficient_history: int
    alerts: int
    max_score: float | None = None
    mean_score: float | None = None
    threshold: float


class Point(BaseModel):
    i: int
    t: str
    station: str
    score: float | None = None
    level: Level
    alert: int
    sm5: float | None = None


class Candidate(BaseModel):
    i: int
    t: str
    station: str
    score: float | None = None
    level: Level
    rule: str | None = None


class PredictResponse(BaseModel):
    run_id: str
    created_at: str
    model: ModelInfo
    dataset: DatasetInfo
    summary: Summary
    points: list[Point]
    candidates: list[Candidate]


class DemoResponse(PredictResponse):
    injected_attacks: dict[str, list[str | None]] = Field(default_factory=dict)


class TargetModel(BaseModel):
    actual: float | None = None
    expected: float | None = None
    error: float | None = None
    score: float | None = None
    level: Level
    alert: int
    rule: str | None = None
    reason: str | None = None


class XaiBase(BaseModel):
    run_id: str
    row: int
    t: str
    station: str
    target: TargetModel


class ShapResponse(XaiBase):
    method: str
    base: float
    pred: float
    names: list[str]
    values: list[float]
    note: str | None = None
    elapsed_ms: float


class TemporalPoint(BaseModel):
    i: int
    t: str
    actual: float | None = None
    expected: float | None = None
    error: float | None = None
    score: float | None = None
    level: Level
    band_low: float | None = None
    band_high: float | None = None


class TemporalTarget(BaseModel):
    sensors: dict[str, float | None] = Field(default_factory=dict)


class TemporalResponse(BaseModel):
    run_id: str
    row: int
    t: str
    station: str
    span: int
    envelope: float
    score_threshold: float
    target: TemporalTarget
    points: list[TemporalPoint]
    note: str | None = None


class StatusResponse(BaseModel):
    available: bool
    repo_id: str | None = None
    filename: str | None = None
    stations: list[str] = Field(default_factory=list)
    threshold: float | None = None
    suspect_threshold: float | None = None
    min_history_days: int | None = None
    detail: str | None = None


class ErrorResponse(BaseModel):
    detail: str


# --- /api/xai panel contracts -----------------------------------------

class XaiMeta(BaseModel):
    station: str
    hour_iso: str
    niveau: Level
    score: float | None = None            # v3 proba (0-1)
    proba: float | None = None            # alias — new clients
    alert: bool
    model_version: str
    feature_version: str
    threshold: float
    ground_truth: Literal["attack", "normal"] | None = None


class XaiSummary(BaseModel):
    expected_sm5: float | None = None
    observed_sm5: float | None = None
    deviation_pct: float | None = None
    twin_residual_z: float | None = None
    verdict: Literal["ALERT", "NORMAL"]


class AttackClassificationDetected(BaseModel):
    detected: bool
    confidence: float
    type: str | None = None
    type_conf: float | None = None


class XaiRuleDetailsMarches(BaseModel):
    step_up: float
    step_down: float
    gap_hours: int
    unexplained_10cm: bool
    offset_vs_twin: float


class XaiRuleDetailsReplay(BaseModel):
    replay_distance_hours: int
    rounded_match_score: float = 1.0


class XaiRuleDetailsEcod(BaseModel):
    ecod_score: float
    ecod_threshold: float = 1.0


XaiRuleDetails = (
    XaiRuleDetailsMarches | XaiRuleDetailsReplay | XaiRuleDetailsEcod | dict[str, Any]
)


class XaiRules(BaseModel):
    fired: Literal["marches", "replay", "ecod", "aucune", "no_hist"]
    reason_text: str | None
    details: XaiRuleDetails
    recommended_action: str
    attack_classification: AttackClassificationDetected | None = None


class XaiEcodFeature(BaseModel):
    key: str
    label: str
    family: str
    value: float | None
    tail: Literal["upper", "lower"]
    p: float
    contribution: float
    signed_contribution: float
    rank: int


class XaiEcodFamily(BaseModel):
    family: str
    signed_contribution: float
    contribution_sum: float
    share_pct: float


class XaiEcod(BaseModel):
    score: float | None
    threshold: float
    suspect: bool
    features: list[XaiEcodFeature]
    families: list[XaiEcodFamily]


class XaiShapFeature(BaseModel):
    key: str
    label: str
    value: float | None
    shap: float
    contribution_sign_ok: bool
    rank: int


class XaiShapTwin(BaseModel):
    target: Literal["twin_sm5", "stage1_m1"]
    base_value: float
    features: list[XaiShapFeature]


class XaiTemporalPoint(BaseModel):
    t: str
    expected: float | None
    observed: float | None


class XaiTemporal(BaseModel):
    window_hours: int = 24
    points: list[XaiTemporalPoint]


class XaiSeriesPoint(BaseModel):
    t: str
    niveau: Level
    alert: bool
    ecod_score: float | None
    twin_residual_z: float | None
    top_ecod_family: str | None


class XaiSeries(BaseModel):
    station: str
    hours: list[XaiSeriesPoint]


class XaiResponse(BaseModel):
    meta: XaiMeta
    summary: XaiSummary
    rules: XaiRules
    ecod: XaiEcod
    shap_twin: XaiShapTwin
    temporal: XaiTemporal


class AttackClassificationResponse(BaseModel):
    attack_classification: AttackClassificationDetected | None = None


# --- AI copilot contracts ---------------------------------------------

class InterpretXaiRequest(BaseModel):
    payload: dict[str, Any]

    def validated_payload(self) -> dict:
        return XaiResponse.model_validate(self.payload).model_dump()


class InterpretXaiResponse(BaseModel):
    resume: str
    verdict: Literal["ALERT", "NORMAL"]
    what_happened: str
    key_factors: list[str]
    what_it_means: str
    recommended_action: str
    confidence: Literal["low", "medium", "high"]
    attempt: int = 1
    request_id: str