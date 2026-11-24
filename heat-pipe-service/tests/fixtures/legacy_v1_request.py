# Frozen request contract from git 7e6137 (pre-path), for rollback regression tests.
from __future__ import annotations

from datetime import datetime

from enum import StrEnum

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

class Roi(WireModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    width: int = Field(ge=1)
    height: int = Field(ge=1)

class FileReference(WireModel):
    object_key: str | None = Field(default=None, min_length=1)
    url: HttpUrl | None = Field(
        default=None,
        description=("Presigned GET URL. HTTPS or explicitly allowed internal HTTP and the source host are checked "
                     "before the job is queued; connectivity and expiry are checked during download."),
    )
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
    monitoring_data_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
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


from heatpipe_service.api.models import JobStatus
from datetime import timezone
from heatpipe_service.jobs.store import JobStore as CurrentJobStore, MAX_ATTEMPTS, IdempotencyConflict

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _idempotency_identity(request: CreateJobRequest) -> tuple[object, ...]:
    """Identify input content, ignoring short-lived source locations."""
    return (
        request.monitoring_data_id,
        request.att.size_bytes,
        request.att.sha256.lower() if request.att.sha256 else None,
        request.atg.size_bytes,
        request.atg.sha256.lower() if request.atg.sha256 else None,
    )

class LegacyJobStore(CurrentJobStore):
    def create(self, request: CreateJobRequest, idempotency_key: str | None = None) -> JobStatusResponse:
            now = _now()
            request_json = request.model_dump_json()
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if idempotency_key is not None:
                    existing = db.execute(
                        "SELECT job_id, request_json, status FROM jobs WHERE idempotency_key = ?",
                        (idempotency_key,),
                    ).fetchone()
                else:
                    existing = None
                if existing is not None:
                    original = CreateJobRequest.model_validate_json(existing["request_json"])
                    if _idempotency_identity(original) != _idempotency_identity(request):
                        raise IdempotencyConflict("Idempotency-Key was already used for a different request")
                    job_id = existing["job_id"]
                    if existing["status"] == JobStatus.QUEUED:
                        if original.att.url is not None and request.att.url is not None:
                            original.att.url = request.att.url
                        if original.atg.url is not None and request.atg.url is not None:
                            original.atg.url = request.atg.url
                        db.execute("UPDATE jobs SET request_json=? WHERE job_id=?",
                                   (original.model_dump_json(), job_id))
                else:
                    cursor = db.execute(
                        """INSERT INTO jobs (
                            idempotency_key, monitoring_data_id, request_json, status, progress_percent,
                            processed_frame_count, status_message, created_at, max_attempts
                        ) VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?)""",
                        (idempotency_key, request.monitoring_data_id, request_json, JobStatus.QUEUED,
                         "Accepted; waiting for analysis worker", now, MAX_ATTEMPTS),
                    )
                    job_id = f"JOB-{datetime.fromisoformat(now):%Y%m%d}-{cursor.lastrowid:06d}"
                    db.execute("UPDATE jobs SET job_id = ? WHERE id = ?", (job_id, cursor.lastrowid))
            return self.get(job_id)
