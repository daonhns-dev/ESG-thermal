from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

import pytest

from heatpipe_service.api.models import CreateJobRequest, JobStatus, Message
from heatpipe_service.jobs.batch import BatchProgress
from heatpipe_service.jobs.store import IdempotencyConflict, InvalidJobUpdate, JobStore


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
    assert created.queue_position == 0
    assert (created.attempt, created.max_attempts) == (1, 3)
    assert store.get_request(created.job_id) == _request()
    validating = store.update(created.job_id, JobStatus.VALIDATING, 1, 0, None, "Validating")
    assert validating.started_at is not None
    assert validating.queue_position is None
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


def test_queue_position_counts_older_waiting_jobs_and_updates_on_cancel(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    first, second, third = (store.create(_request()) for _ in range(3))
    assert [store.get(job.job_id).queue_position for job in (first, second, third)] == [0, 1, 2]
    assert store.cancel(first.job_id).queue_position is None
    assert [store.get(job.job_id).queue_position for job in (second, third)] == [0, 1]
    assert store.claim_next() == second.job_id
    assert store.get(second.job_id).queue_position is None
    assert store.get(third.job_id).queue_position == 0


def test_concurrent_identical_key_creates_one_durable_job(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    stores = [JobStore(path), JobStore(path)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(lambda i: stores[i % 2].create(_request(), "same-request-key"), range(8)))
    assert len({job.job_id for job in jobs}) == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_idempotency_ignores_refreshed_urls_and_checks_both_files(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")

    def request(signature: str, atg_hash: str = "b" * 64) -> CreateJobRequest:
        return CreateJobRequest.model_validate({
            "monitoring_data_id": "round-1",
            "att": {"url": f"https://example.test/round.att?sig={signature}",
                    "size_bytes": 100, "sha256": "A" * 64},
            "atg": {"url": f"https://example.test/round.atg?sig={signature}",
                    "size_bytes": 20, "sha256": atg_hash},
        })

    first = store.create(request("old"), "same-key")
    repeated = store.create(request("new"), "same-key")
    assert repeated.job_id == first.job_id
    assert str(store.get_request(first.job_id).att.url).endswith("sig=new")
    with pytest.raises(IdempotencyConflict):
        store.create(request("again", atg_hash="c" * 64), "same-key")
    assert store.claim_next() == first.job_id
    assert store.create(request("later"), "same-key").job_id == first.job_id
    assert str(store.get_request(first.job_id).att.url).endswith("sig=new")


def test_worker_heartbeat_expires_after_stale_window(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    store = JobStore(path)
    store.record_worker_activity("worker-1")
    assert store.worker_health()[0] is True
    old = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    with sqlite3.connect(path) as db:
        db.execute("UPDATE worker_heartbeats SET last_activity_at=? WHERE worker_id=?", (old, "worker-1"))
    running, last_activity_at, queued = store.worker_health()
    assert running is False
    assert last_activity_at == datetime.fromisoformat(old)
    assert queued == 0


def test_retry_policy_only_allows_retryable_failure_up_to_three_attempts(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create(_request())
    for attempt in range(3):
        current = store.get(job.job_id)
        assert (current.attempt, current.max_attempts) == (attempt + 1, 3)
        store.update(job.job_id, JobStatus.VALIDATING, 1, 0, None, "Validating")
        failed = store.update(
            job.job_id, JobStatus.FAILED, 1, 0, None, "Download failed",
            Message(code="DOWNLOAD_FAILED", message="Download failed"),
        )
        assert failed.error.code == "DOWNLOAD_FAILED"
        assert failed.error.retryable is True
        if attempt < 2:
            retried = store.retry_failed(job.job_id)
            assert retried.status == JobStatus.QUEUED
            assert (retried.attempt, retried.max_attempts) == (attempt + 2, 3)
        else:
            assert failed.attempt == failed.max_attempts == 3
            assert failed.error.retryable is True
            with pytest.raises(InvalidJobUpdate):
                store.retry_failed(job.job_id)

    permanent = store.create(_request())
    store.update(permanent.job_id, JobStatus.VALIDATING, 1, 0, None, "Validating")
    invalid = store.update(permanent.job_id, JobStatus.FAILED, 1, 0, None, "Invalid input",
                           Message(code="INVALID_ANALYSIS_REQUEST", message="Invalid input"))
    assert invalid.error.retryable is False
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
    assert store.requeue_pending_retries() == 1
    assert store.get(job.job_id).status == JobStatus.QUEUED
    assert store.requeue_pending_retries() == 0


def test_cancel_request_survives_worker_restart(tmp_path: Path) -> None:
    path = tmp_path / "jobs.sqlite3"
    store = JobStore(path)
    job = store.create(_request())
    assert store.claim_next() == job.job_id
    requested = store.cancel(job.job_id)
    assert requested.cancel_requested is True
    assert requested.status == JobStatus.VALIDATING
    restarted = JobStore(path)
    assert restarted.recover_interrupted() == 1
    assert restarted.get(job.job_id).status == JobStatus.CANCELLED
    assert restarted.requeue_pending_retries() == 0
    assert restarted.claim_next() is None


def test_cancel_retry_waiting_job_prevents_next_attempt(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create(_request())
    store.update(job.job_id, JobStatus.VALIDATING, 1, 0, None, "Validating")
    store.update(job.job_id, JobStatus.FAILED, 1, 0, None, "Download failed",
                 Message(code="DOWNLOAD_FAILED", message="Download failed"))
    cancelled = store.cancel(job.job_id)
    assert cancelled.status == JobStatus.CANCELLED
    assert store.requeue_pending_retries() == 0
    with pytest.raises(InvalidJobUpdate):
        store.retry_failed(job.job_id)
