"""FastAPI contract preview with durable job status and a sample result."""

from __future__ import annotations

import json
import hashlib
from importlib.resources import files
import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from heatpipe_service import __version__
from heatpipe_service.api.auth import require_bearer
from heatpipe_service.api.models import (
    AnalysisResultResponse,
    CreateJobRequest,
    CreateJobResponse,
    ErrorResponse,
    HealthResponse,
    JobStatus,
    JobStatusResponse,
    VersionResponse,
)
from heatpipe_service.jobs.store import IdempotencyConflict, InvalidJobUpdate, JobNotFound, JobStore
from heatpipe_service.security import redact_url_query
from heatpipe_service.storage.backends import Storage, StorageError, storage_from_env, validate_source_url


class PngResponse(Response):
    media_type = "image/png"


logger = logging.getLogger(__name__)


def _error_responses(*statuses: int) -> dict[int, dict[str, object]]:
    return {
        status: {"model": ErrorResponse, "content": {"application/json": {}}}
        for status in statuses
    }


def create_app(
    db_path: Path | str | None = None,
    storage: Storage | None = None,
    api_token: str | None = None,
) -> FastAPI:
    production = os.environ.get("HEATPIPE_ENV", "development").strip().lower() == "production"
    api = FastAPI(
        title="Heat Pipe Service API",
        version=__version__,
        description="Q15 INT-001/003/004 analysis API. The analysis worker produces results; this API provides job status, cancellation, result JSON and candidate PNG files.",
        openapi_url=None if production else "/openapi.json",
        docs_url=None if production else "/docs",
        redoc_url=None if production else "/redoc",
    )
    store = JobStore(db_path or os.environ.get("HEATPIPE_JOB_DB_PATH", "data/jobs.sqlite3"))
    result_storage = storage if storage is not None else storage_from_env()
    authorization = Depends(require_bearer(api_token if api_token is not None else os.environ.get("HEATPIPE_API_TOKEN")))
    api.state.job_store = store
    sample = json.loads(files("heatpipe_service.api").joinpath("mock_result.json").read_text(encoding="utf-8"))
    sample_result = AnalysisResultResponse.model_validate(sample)
    sample_image = files("heatpipe_service.api").joinpath("mock_image.png").read_bytes()
    sample_time = sample_result.completed_at
    sample_status = JobStatusResponse(
        job_id=sample_result.job_id,
        monitoring_data_id=sample_result.monitoring_data_id,
        status=JobStatus.SUCCEEDED,
        attempt=1,
        max_attempts=3,
        cancel_requested=False,
        queue_position=None,
        created_at=sample_time,
        progress_percent=100,
        processed_frame_count=sample_result.processing.frame_range.processed_frame_count,
        total_frame_count=sample_result.processing.frame_range.total_att_frames,
        status_message="Mock sample completed",
        started_at=sample_time,
        completed_at=sample_time,
    )

    @api.exception_handler(StarletteHTTPException)
    async def http_error(_request, error: StarletteHTTPException) -> JSONResponse:
        detail = error.detail
        if not (isinstance(detail, dict) and isinstance(detail.get("code"), str)
                and isinstance(detail.get("message"), str)):
            code = {401: "UNAUTHORIZED", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED",
                    409: "CONFLICT", 422: "INVALID_REQUEST", 503: "SERVICE_UNAVAILABLE"}.get(
                        error.status_code, "HTTP_ERROR"
                    )
            detail = {"code": code, "message": str(detail)}
        detail = {**detail, "message": redact_url_query(detail["message"])}
        return JSONResponse(status_code=error.status_code, content={"error": detail}, headers=error.headers)

    @api.exception_handler(RequestValidationError)
    async def validation_error(_request, error: RequestValidationError) -> JSONResponse:
        first = error.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "INVALID_REQUEST", "message": redact_url_query(f"{field}: {first['msg']}")}},
        )

    @api.exception_handler(Exception)
    async def unexpected_error(_request, error: Exception) -> JSONResponse:
        logger.error("Unhandled API error type=%s", type(error).__name__)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_SERVER_ERROR", "message": "Internal server error"}},
        )

    @api.get("/health", response_model=HealthResponse, tags=["Service"])
    def health() -> HealthResponse:
        running, last_activity_at, queued_job_count = store.worker_health()
        return HealthResponse(
            worker_running=running,
            worker_last_activity_at=last_activity_at,
            queued_job_count=queued_job_count,
        )

    @api.get("/version", response_model=VersionResponse, tags=["Service"])
    def version() -> VersionResponse:
        return VersionResponse(version=__version__)

    @api.post(
        "/api/v1/analysis-jobs",
        response_model=CreateJobResponse,
        status_code=202,
        tags=["INT-003"],
        dependencies=[authorization],
        responses=_error_responses(401, 409, 422, 503),
        summary="Request thermal analysis",
    )
    def create_job(
        request: CreateJobRequest,
        idempotency_key: str | None = Header(
            default=None,
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
            pattern=r"^[!-~]+$",
            description=("Optional request key. The same key, monitoring_data_id, and ATT/ATG "
                         "size_bytes and sha256 return the existing job even when Presigned URLs change. "
                         "A different comparison value returns 409."),
        ),
    ) -> CreateJobResponse:
        for reference in (request.att, request.atg):
            if reference.url is not None:
                try:
                    validate_source_url(
                        str(reference.url), getattr(result_storage, "allowed_url_hosts", frozenset()),
                        getattr(result_storage, "allowed_http_hosts", frozenset()),
                    )
                except StorageError as error:
                    raise HTTPException(422, detail={"code": error.code, "message": str(error)}) from error
        try:
            status = store.create(request, idempotency_key=idempotency_key)
        except IdempotencyConflict as error:
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_KEY_CONFLICT", "message": str(error)}) from error
        receipt = CreateJobResponse(
            job_id=status.job_id,
            monitoring_data_id=status.monitoring_data_id,
            status=status.status,
            created_at=status.created_at,
        )
        return receipt

    @api.post(
        "/api/v1/analysis-jobs/{job_id}/cancel",
        response_model=JobStatusResponse,
        status_code=202,
        tags=["INT-004"],
        dependencies=[authorization],
        responses=_error_responses(401, 404, 409, 422, 503),
        summary="Cancel a queued or running analysis job",
        description=("A queued job becomes CANCELLED immediately. A running job reports "
                     "cancel_requested=true until the worker reaches a cancellation checkpoint."),
    )
    def cancel_job(job_id: str) -> JobStatusResponse:
        if job_id == sample_result.job_id:
            raise HTTPException(409, detail={"code": "CANCEL_NOT_ALLOWED", "message": "Completed job cannot be cancelled"})
        try:
            return store.cancel(job_id)
        except JobNotFound as error:
            raise HTTPException(404, detail={"code": "JOB_NOT_FOUND", "message": "Job not found"}) from error
        except InvalidJobUpdate as error:
            raise HTTPException(409, detail={"code": "CANCEL_NOT_ALLOWED", "message": str(error)}) from error

    @api.get(
        "/api/v1/analysis-jobs/{job_id}",
        response_model=JobStatusResponse,
        tags=["INT-004"],
        dependencies=[authorization],
        responses=_error_responses(401, 404, 422, 503),
        summary="Get analysis job status",
    )
    def get_job(job_id: str) -> JobStatusResponse:
        if job_id == sample_result.job_id:
            return sample_status
        try:
            return store.get(job_id)
        except JobNotFound:
            raise HTTPException(404, detail={"code": "JOB_NOT_FOUND", "message": "Job not found"})

    @api.get(
        "/api/v1/analysis-jobs/{job_id}/results",
        response_model=AnalysisResultResponse,
        response_model_exclude_unset=True,
        tags=["INT-001"],
        dependencies=[authorization],
        responses=_error_responses(401, 404, 409, 422, 503),
        summary="Get completed analysis result",
        description="A job becomes SUCCEEDED only after every candidate PNG is uploaded and verified.",
    )
    def get_results(job_id: str) -> AnalysisResultResponse:
        if job_id == sample_result.job_id:
            return sample_result
        status = get_job(job_id)
        if status.status != JobStatus.SUCCEEDED:
            raise HTTPException(409, detail={"code": "RESULT_NOT_READY", "message": "Analysis result is not ready"})
        result = _stored_result(job_id)
        for candidate in result.candidates:
            candidate.result_image.path = f"/api/v1/analysis-jobs/{job_id}/images/{candidate.candidate_id}"
        return result

    def _stored_result(job_id: str) -> AnalysisResultResponse:
        key = store.get_result_key(job_id)
        if key is None:
            raise HTTPException(503, detail={"code": "RESULT_STORAGE_ERROR", "message": "Result key is missing"})
        try:
            payload = json.loads(result_storage.read_bytes(key))
            for candidate in payload.get("candidates", []):
                if payload.get("detected_candidate_count") is None:
                    candidate.setdefault("roi_temperature_c", None)
                image = candidate["result_image"]
                if "sha256" not in image or "size_bytes" not in image:
                    png = result_storage.read_bytes(image["path"])
                    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                        raise ValueError("Stored candidate image is not PNG")
                    image["sha256"] = hashlib.sha256(png).hexdigest()
                    image["size_bytes"] = len(png)
            payload.setdefault("detected_candidate_count", None)
            if payload.get("processing") is not None:
                payload["processing"]["parameters"].setdefault("max_results", None)
            payload["schema_version"] = "0.3.0"
            return AnalysisResultResponse.model_validate(payload)
        except (StorageError, ValueError, KeyError, TypeError) as error:
            raise HTTPException(503, detail={"code": "RESULT_STORAGE_ERROR", "message": "Result cannot be read"}) from error

    @api.get(
        "/api/v1/analysis-jobs/{job_id}/images/{candidate_id}",
        response_class=Response,
        tags=["INT-001"],
        dependencies=[authorization],
        responses={200: {"content": {"image/png": {"schema": {"type": "string", "format": "binary"}}}},
                   **_error_responses(401, 404, 409, 422, 503)},
        summary="Get a candidate result PNG",
        description="All candidates in a SUCCEEDED result have a PNG verified at job completion. Later storage failures return 503.",
    )
    def get_image(job_id: str, candidate_id: str) -> PngResponse:
        if job_id == sample_result.job_id:
            if any(candidate.candidate_id == candidate_id for candidate in sample_result.candidates):
                return PngResponse(sample_image, headers={"Cache-Control": "private, no-store"})
            raise HTTPException(404, detail={"code": "IMAGE_NOT_FOUND", "message": "Candidate image not found"})
        status = get_job(job_id)
        if status.status != JobStatus.SUCCEEDED:
            raise HTTPException(409, detail={"code": "RESULT_NOT_READY", "message": "Analysis result is not ready"})
        result = _stored_result(job_id)
        candidate = next((item for item in result.candidates if item.candidate_id == candidate_id), None)
        if candidate is None:
            raise HTTPException(404, detail={"code": "IMAGE_NOT_FOUND", "message": "Candidate image not found"})
        try:
            image = result_storage.read_bytes(candidate.result_image.path)
        except StorageError as error:
            raise HTTPException(503, detail={"code": "RESULT_STORAGE_ERROR", "message": "Image cannot be read"}) from error
        return PngResponse(image, headers={"Cache-Control": "private, no-store"})

    return api


app = create_app()
