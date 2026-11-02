"""FastAPI contract preview with durable job status and a sample result."""

from __future__ import annotations

import json
from importlib.resources import files
import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

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
from heatpipe_service.jobs.store import JobNotFound, JobStore
from heatpipe_service.storage.backends import Storage, StorageError, storage_from_env


class PngResponse(Response):
    media_type = "image/png"


def create_app(
    db_path: Path | str | None = None,
    storage: Storage | None = None,
    api_token: str | None = None,
) -> FastAPI:
    api = FastAPI(
        title="Heat Pipe Service API",
        version=__version__,
        description="Proposed INT-001/003/004 contract. The worker produces results; INT-001 returns JSON and candidate PNG URLs served by this API.",
    )
    store = JobStore(db_path or os.environ.get("HEATPIPE_JOB_DB_PATH", "data/jobs.sqlite3"))
    result_storage = storage if storage is not None else storage_from_env()
    authorization = Depends(require_bearer(api_token if api_token is not None else os.environ.get("HEATPIPE_API_TOKEN")))
    api.state.job_store = store
    sample = json.loads(files("heatpipe_service.api").joinpath("mock_result.json").read_text(encoding="utf-8"))
    sample_result = AnalysisResultResponse.model_validate(sample)
    sample_time = sample_result.completed_at
    sample_status = JobStatusResponse(
        job_id=sample_result.job_id,
        monitoring_data_id=sample_result.monitoring_data_id,
        status=JobStatus.SUCCEEDED,
        created_at=sample_time,
        progress_percent=100,
        processed_frame_count=sample_result.processing.frame_range.processed_frame_count,
        total_frame_count=sample_result.processing.frame_range.total_att_frames,
        status_message="Mock sample completed",
        started_at=sample_time,
        completed_at=sample_time,
    )

    @api.exception_handler(HTTPException)
    async def http_error(_request, error: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=error.status_code, content={"error": error.detail}, headers=error.headers)

    @api.exception_handler(RequestValidationError)
    async def validation_error(_request, error: RequestValidationError) -> JSONResponse:
        first = error.errors()[0]
        field = ".".join(str(part) for part in first["loc"])
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "INVALID_REQUEST", "message": f"{field}: {first['msg']}"}},
        )

    @api.get("/health", response_model=HealthResponse, tags=["Service"])
    def health() -> HealthResponse:
        return HealthResponse()

    @api.get("/version", response_model=VersionResponse, tags=["Service"])
    def version() -> VersionResponse:
        return VersionResponse(version=__version__)

    @api.post(
        "/api/v1/analysis-jobs",
        response_model=CreateJobResponse,
        status_code=202,
        tags=["INT-003"],
        dependencies=[authorization],
        responses={401: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        summary="Request thermal analysis",
    )
    def create_job(request: CreateJobRequest) -> CreateJobResponse:
        status = store.create(request)
        receipt = CreateJobResponse(
            job_id=status.job_id,
            monitoring_data_id=status.monitoring_data_id,
            status=status.status,
            created_at=status.created_at,
        )
        return receipt

    @api.get(
        "/api/v1/analysis-jobs/{job_id}",
        response_model=JobStatusResponse,
        tags=["INT-004"],
        dependencies=[authorization],
        responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
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
        responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        summary="Get completed analysis result",
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
            return AnalysisResultResponse.model_validate_json(result_storage.read_bytes(key))
        except (StorageError, ValueError) as error:
            raise HTTPException(503, detail={"code": "RESULT_STORAGE_ERROR", "message": "Result cannot be read"}) from error

    @api.get(
        "/api/v1/analysis-jobs/{job_id}/images/{candidate_id}",
        response_class=PngResponse,
        tags=["INT-001"],
        dependencies=[authorization],
        responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
        summary="Get a candidate result PNG",
    )
    def get_image(job_id: str, candidate_id: str) -> PngResponse:
        if job_id == sample_result.job_id:
            raise HTTPException(404, detail={"code": "IMAGE_NOT_FOUND", "message": "Sample image is not available"})
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
