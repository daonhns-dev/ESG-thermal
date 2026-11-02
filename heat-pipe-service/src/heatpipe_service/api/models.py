"""Proposed INT-001/003/004 wire models. Result fields mirror JSON Schema v0."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Message(WireModel):
    code: str
    message: str


class ErrorResponse(WireModel):
    error: Message


class Roi(WireModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class FileReference(WireModel):
    object_key: str | None = Field(default=None, min_length=1)
    url: HttpUrl | None = None
    size_bytes: int | None = Field(default=None, gt=0)
    sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")

    @model_validator(mode="after")
    def require_one_location(self) -> FileReference:
        if (self.object_key is None) == (self.url is None):
            raise ValueError("provide exactly one of object_key or url")
        return self


class RequestedFrameRange(WireModel):
    start: int = Field(default=0, ge=0)
    end_exclusive: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def check_end(self) -> RequestedFrameRange:
        if self.end_exclusive is not None and self.end_exclusive <= self.start:
            raise ValueError("end_exclusive must be greater than start")
        return self


class CreateJobRequest(WireModel):
    monitoring_data_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_-]+$")
    att: FileReference
    atg: FileReference
    frame_range: RequestedFrameRange = Field(default_factory=RequestedFrameRange)
    analysis_roi: Roi | None = Field(
        default=None,
        description=(
            "Optional pixel ROI inside the bottom-center cell of the full-frame "
            "2-row, 3-column grid. If omitted, analyze that entire cell. "
            "For 384x288 input the cell is x=128, y=144, width=128, height=144. "
            "An ROI outside that cell fails during analysis."
        ),
    )


class JobStatus(StrEnum):
    QUEUED = "QUEUED"
    VALIDATING = "VALIDATING"
    DOWNLOADING = "DOWNLOADING"
    ANALYZING = "ANALYZING"
    RENDERING = "RENDERING"
    UPLOADING = "UPLOADING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class CreateJobResponse(WireModel):
    job_id: str
    monitoring_data_id: str
    status: JobStatus
    created_at: datetime


class JobStatusResponse(CreateJobResponse):
    progress_percent: int = Field(ge=0, le=100)
    processed_frame_count: int = Field(ge=0)
    total_frame_count: int | None = Field(default=None, ge=0)
    status_message: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: Message | None = None


class ProcessingInput(WireModel):
    att_file_name: str = Field(min_length=1)
    atg_file_name: str = Field(min_length=1)


class ProcessedFrameRange(WireModel):
    start: int = Field(ge=0)
    end_exclusive: int = Field(ge=1)
    stride: int = Field(ge=1)
    total_att_frames: int = Field(ge=1)
    total_atg_records: int = Field(ge=0)
    processed_frame_count: int = Field(ge=0)


class AnalysisParameters(WireModel):
    score_threshold: float
    min_area_px: int = Field(ge=1)
    min_track_frames: int = Field(ge=1)
    merge_iou_threshold: float = Field(ge=0, le=1)


class Processing(WireModel):
    input: ProcessingInput
    frame_range: ProcessedFrameRange
    analysis_roi: Roi
    algorithms: list[str]
    parameters: AnalysisParameters
    elapsed_seconds: float = Field(ge=0)


class Gps(WireModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class Temperature(WireModel):
    minimum: float
    mean: float
    maximum: float
    background_median: float
    delta_from_background: float


class RoiTemperature(WireModel):
    minimum: float
    mean: float
    maximum: float
    max_minus_min: float
    max_minus_mean: float


class Evidence(WireModel):
    algorithms: list[str] = Field(min_length=1)
    max_anomaly_score: float
    mean_anomaly_score: float
    persistence_score: float
    tracked_frame_count: int = Field(ge=1)
    track_start_frame: int = Field(ge=0)
    track_end_frame: int = Field(ge=0)
    likely_ground_fixed: bool


class Grid(WireModel):
    rows: Literal[2] = 2
    columns: Literal[3] = 3


class ResultImage(WireModel):
    format: Literal["PNG"] = "PNG"
    width: int = Field(ge=1)
    height: int = Field(ge=1)
    path: str = Field(min_length=1, description="Stored object key in batch JSON; relative DaonH&S API image path in INT-001")
    grid: Grid


class Candidate(WireModel):
    candidate_id: str = Field(pattern=r"^JOB-[0-9]{8}-[0-9]{6}-CAND-[0-9]{4}$")
    rank: int = Field(ge=1)
    representative_frame: int = Field(ge=0)
    captured_at: datetime | None
    gps: Gps | None
    analysis_roi: Roi
    candidate_bbox: Roi
    temperature_c: Temperature
    roi_temperature_c: RoiTemperature | None = Field(
        default=None,
        description="Whole analysis ROI on the candidate's representative ATT frame; absent for older results",
    )
    evidence: Evidence
    result_image: ResultImage


class AnalysisResultResponse(WireModel):
    schema_version: Literal["0.1.0"]
    job_id: str = Field(pattern=r"^JOB-[0-9]{8}-[0-9]{6}$")
    monitoring_data_id: str = Field(min_length=1)
    status: Literal["SUCCEEDED", "FAILED"]
    success: bool
    completed_at: datetime
    processing: Processing | None
    candidate_count: int = Field(ge=0)
    candidates: list[Candidate]
    warnings: list[Message]
    error: Message | None

    @model_validator(mode="after")
    def check_result_consistency(self) -> AnalysisResultResponse:
        if self.candidate_count != len(self.candidates):
            raise ValueError("candidate_count must match candidates")
        if self.success:
            if self.status != "SUCCEEDED" or self.processing is None or self.error is not None:
                raise ValueError("successful result requires processing and no error")
        elif self.status != "FAILED" or self.processing is not None or self.candidates or self.error is None:
            raise ValueError("failed result requires error and no processing or candidates")
        return self


class HealthResponse(WireModel):
    status: Literal["ok"] = "ok"


class VersionResponse(WireModel):
    version: str
