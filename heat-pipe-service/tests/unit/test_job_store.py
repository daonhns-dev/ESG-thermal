from pathlib import Path

import pytest

from heatpipe_service.api.models import CreateJobRequest, JobStatus, Message
from heatpipe_service.jobs.batch import BatchProgress
from heatpipe_service.jobs.store import InvalidJobUpdate, JobStore


def _request() -> CreateJobRequest:
    return CreateJobRequest.model_validate({
        "monitoring_data_id": "sample",
        "att": {"object_key": "input/sample.att"},
        "atg": {"object_key": "input/sample.atg"},
    })


def test_job_status_persists_and_rejects_invalid_progress(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    store = JobStore(path)
    created = store.create(_request())
    assert created.status == JobStatus.QUEUED
    assert store.get_request(created.job_id) == _request()
    validating = store.update(created.job_id, JobStatus.VALIDATING, 1, 0, None, "Validating")
    assert validating.started_at is not None
    store.update(created.job_id, JobStatus.ANALYZING, 10, 0, 5, "Analyzing")
    store.update(created.job_id, JobStatus.ANALYZING, 40, 2, 5, "Analyzing")
    assert JobStore(path).get(created.job_id).processed_frame_count == 2
    with pytest.raises(InvalidJobUpdate):
        store.update(created.job_id, JobStatus.ANALYZING, 30, 1, 5, "Backwards")
    with pytest.raises(InvalidJobUpdate):
        store.update(created.job_id, JobStatus.SUCCEEDED, 100, 2, 5, "Skipped rendering")
    store.update(created.job_id, JobStatus.RENDERING, 90, 5, 5, "Rendering")
    completed = store.update(created.job_id, JobStatus.SUCCEEDED, 100, 5, 5, "Complete")
    assert completed.completed_at is not None
    assert completed.error is None
    with pytest.raises(InvalidJobUpdate):
        store.update(created.job_id, JobStatus.SUCCEEDED, 100, 5, 5, "Again")


def test_retry_policy_only_allows_retryable_failure_up_to_three_attempts(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create(_request())
    for attempt in range(3):
        store.update(job.job_id, JobStatus.VALIDATING, 1, 0, None, "Validating")
        failed = store.update(
            job.job_id, JobStatus.FAILED, 1, 0, None, "Download failed",
            Message(code="DOWNLOAD_FAILED", message="Download failed"),
        )
        assert failed.error.code == "DOWNLOAD_FAILED"
        if attempt < 2:
            assert store.retry_failed(job.job_id).status == JobStatus.QUEUED
        else:
            with pytest.raises(InvalidJobUpdate):
                store.retry_failed(job.job_id)

    permanent = store.create(_request())
    store.update(permanent.job_id, JobStatus.VALIDATING, 1, 0, None, "Validating")
    store.update(permanent.job_id, JobStatus.FAILED, 1, 0, None, "Invalid input",
                 Message(code="INVALID_ANALYSIS_REQUEST", message="Invalid input"))
    with pytest.raises(InvalidJobUpdate):
        store.retry_failed(permanent.job_id)


def test_batch_callback_and_restart_recovery(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    store = JobStore(path)
    job = store.create(_request())
    callback = store.batch_callback(job.job_id)
    callback(BatchProgress("VALIDATING", 1, 0, None, "Validating"))
    callback(BatchProgress("ANALYZING", 10, 0, 4, "Analyzing"))
    callback(BatchProgress("ANALYZING", 25, 1, 4, "Frame 1"))
    assert store.get(job.job_id).processed_frame_count == 1
    assert JobStore(path).recover_interrupted() == 1
    failed = store.get(job.job_id)
    assert failed.status == JobStatus.FAILED
    assert failed.error.code == "WORKER_INTERRUPTED"
    assert store.retry_failed(job.job_id).status == JobStatus.QUEUED
