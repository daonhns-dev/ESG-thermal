"""Durable, single-host SQLite state for analysis jobs.

This stores job metadata, progress and result keys. It does not store
ATT/ATG files or result artifacts.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator

from heatpipe_service.api.models import CreateJobRequest, JobStatus, JobStatusResponse, Message

if TYPE_CHECKING:
    from heatpipe_service.jobs.batch import BatchProgress

MAX_ATTEMPTS = 3
RETRYABLE_ERROR_CODES = frozenset({
    "INPUT_OUTPUT_ERROR", "DOWNLOAD_FAILED", "UPLOAD_FAILED", "ANALYSIS_TIMEOUT",
    "WORKER_INTERRUPTED",
})
ACTIVE = frozenset({
    JobStatus.VALIDATING, JobStatus.DOWNLOADING, JobStatus.ANALYZING,
    JobStatus.RENDERING, JobStatus.UPLOADING,
})
NEXT_STATES = {
    JobStatus.QUEUED: {JobStatus.VALIDATING},
    JobStatus.VALIDATING: {JobStatus.DOWNLOADING, JobStatus.ANALYZING},
    JobStatus.DOWNLOADING: {JobStatus.ANALYZING},
    JobStatus.ANALYZING: {JobStatus.RENDERING},
    JobStatus.RENDERING: {JobStatus.UPLOADING, JobStatus.SUCCEEDED},
    JobStatus.UPLOADING: {JobStatus.SUCCEEDED},
}


class JobNotFound(LookupError):
    pass


class InvalidJobUpdate(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, db_path: Path | str) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT UNIQUE,
                    monitoring_data_id TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress_percent INTEGER NOT NULL,
                    processed_frame_count INTEGER NOT NULL,
                    total_frame_count INTEGER,
                    status_message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    result_key TEXT,
                    next_attempt_at TEXT
                )
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "result_key" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN result_key TEXT")
            if "next_attempt_at" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN next_attempt_at TEXT")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=5000")
            with db:
                yield db
        finally:
            db.close()

    def create(self, request: CreateJobRequest) -> JobStatusResponse:
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """INSERT INTO jobs (
                    monitoring_data_id, request_json, status, progress_percent,
                    processed_frame_count, status_message, created_at, max_attempts
                ) VALUES (?, ?, ?, 0, 0, ?, ?, ?)""",
                (request.monitoring_data_id, request.model_dump_json(), JobStatus.QUEUED,
                 "Accepted; waiting for analysis worker", now, MAX_ATTEMPTS),
            )
            job_id = f"JOB-{datetime.fromisoformat(now):%Y%m%d}-{cursor.lastrowid:06d}"
            db.execute("UPDATE jobs SET job_id = ? WHERE id = ?", (job_id, cursor.lastrowid))
        return self.get(job_id)

    def get(self, job_id: str) -> JobStatusResponse:
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return self._status(row)

    def get_request(self, job_id: str) -> CreateJobRequest:
        with self._connect() as db:
            row = db.execute("SELECT request_json FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return CreateJobRequest.model_validate_json(row["request_json"])

    def claim_next(self) -> str | None:
        """Atomically claim the oldest due job for one worker."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT job_id FROM jobs WHERE status=?
                   AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
                   ORDER BY id LIMIT 1""",
                (JobStatus.QUEUED, _now()),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                """UPDATE jobs SET status=?, progress_percent=1, status_message=?,
                   started_at=?, next_attempt_at=NULL WHERE job_id=?""",
                (JobStatus.VALIDATING, "Validating job request", _now(), row["job_id"]),
            )
            return row["job_id"]

    def set_result_key(self, job_id: str, key: str) -> None:
        with self._connect() as db:
            cursor = db.execute("UPDATE jobs SET result_key=? WHERE job_id=?", (key, job_id))
        if cursor.rowcount != 1:
            raise JobNotFound(job_id)

    def get_result_key(self, job_id: str) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT result_key FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return row["result_key"]

    def recover_interrupted(self) -> int:
        """Mark abandoned in-progress jobs failed after a single-process server restart."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                f"""UPDATE jobs SET status=?, completed_at=?, error_code=?, error_message=?,
                    status_message=? WHERE status IN ({','.join('?' for _ in ACTIVE)})""",
                (JobStatus.FAILED, _now(), "WORKER_INTERRUPTED",
                 "Analysis worker stopped before completion", "Analysis worker interrupted",
                 *ACTIVE),
            )
            return cursor.rowcount

    def update(
        self,
        job_id: str,
        status: JobStatus,
        progress_percent: int,
        processed_frame_count: int,
        total_frame_count: int | None,
        status_message: str,
        error: Message | None = None,
    ) -> JobStatusResponse:
        if not 0 <= progress_percent <= 100 or processed_frame_count < 0:
            raise InvalidJobUpdate("invalid progress value")
        if total_frame_count is not None and (total_frame_count < 0 or processed_frame_count > total_frame_count):
            raise InvalidJobUpdate("processed frames exceed total frames")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise JobNotFound(job_id)
            previous = JobStatus(row["status"])
            allowed = NEXT_STATES.get(previous, set()) | ({JobStatus.FAILED} if previous in ACTIVE else set())
            if status == previous and status not in ACTIVE:
                raise InvalidJobUpdate("only active jobs can report repeated progress")
            if status != previous and status not in allowed:
                raise InvalidJobUpdate(f"invalid transition: {previous} -> {status}")
            if previous in {JobStatus.SUCCEEDED, JobStatus.FAILED}:
                raise InvalidJobUpdate("terminal job cannot be updated")
            if progress_percent < row["progress_percent"] or processed_frame_count < row["processed_frame_count"]:
                raise InvalidJobUpdate("progress cannot decrease")
            if row["total_frame_count"] is not None and total_frame_count != row["total_frame_count"]:
                raise InvalidJobUpdate("total frame count cannot change")
            if status == JobStatus.SUCCEEDED and progress_percent != 100:
                raise InvalidJobUpdate("successful job must have 100% progress")
            if (status == JobStatus.FAILED) != (error is not None):
                raise InvalidJobUpdate("failure requires an error; other states cannot have one")
            started_at = row["started_at"] or (_now() if status in ACTIVE else None)
            completed_at = _now() if status in {JobStatus.SUCCEEDED, JobStatus.FAILED} else None
            db.execute("""UPDATE jobs SET status=?, progress_percent=?, processed_frame_count=?,
                total_frame_count=?, status_message=?, started_at=?, completed_at=?,
                error_code=?, error_message=? WHERE job_id=?""",
                (status, progress_percent, processed_frame_count, total_frame_count,
                 status_message, started_at, completed_at,
                 error.code if error else None, error.message if error else None, job_id),
            )
        return self.get(job_id)

    def retry_failed(self, job_id: str, delay_seconds: int = 0) -> JobStatusResponse:
        """Requeue a retryable failure after an optional delay."""
        if delay_seconds < 0:
            raise ValueError("retry delay must be non-negative")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                raise JobNotFound(job_id)
            if (row["status"] != JobStatus.FAILED or row["error_code"] not in RETRYABLE_ERROR_CODES
                    or row["attempt"] >= row["max_attempts"]):
                raise InvalidJobUpdate("job is not eligible for retry")
            db.execute("""UPDATE jobs SET status=?, progress_percent=0, processed_frame_count=0,
                total_frame_count=NULL, status_message=?, started_at=NULL, completed_at=NULL,
                error_code=NULL, error_message=NULL, attempt=attempt+1,
                next_attempt_at=?, result_key=NULL WHERE job_id=?""",
                (JobStatus.QUEUED, "Queued for retry",
                 (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)).isoformat(), job_id),
            )
        return self.get(job_id)

    def attempt(self, job_id: str) -> int:
        with self._connect() as db:
            row = db.execute("SELECT attempt FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return row["attempt"]

    def batch_callback(self, job_id: str) -> Callable[[BatchProgress], None]:
        """Adapt optional batch progress events to durable job state updates."""
        def record(progress: BatchProgress) -> None:
            self.update(
                job_id, JobStatus(progress.status), progress.progress_percent,
                progress.processed_frame_count, progress.total_frame_count,
                progress.message,
                Message(code=progress.error_code, message=progress.message)
                if progress.error_code else None,
            )
        return record

    @staticmethod
    def _status(row: sqlite3.Row) -> JobStatusResponse:
        error = (Message(code=row["error_code"], message=row["error_message"])
                 if row["error_code"] is not None else None)
        return JobStatusResponse(
            job_id=row["job_id"], monitoring_data_id=row["monitoring_data_id"],
            status=row["status"], progress_percent=row["progress_percent"],
            processed_frame_count=row["processed_frame_count"],
            total_frame_count=row["total_frame_count"], status_message=row["status_message"],
            created_at=row["created_at"], started_at=row["started_at"],
            completed_at=row["completed_at"], error=error,
        )
