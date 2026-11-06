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

from heatpipe_service.api.models import CreateJobRequest, JobError, JobStatus, JobStatusResponse, Message

if TYPE_CHECKING:
    from heatpipe_service.jobs.batch import BatchProgress

MAX_ATTEMPTS = 3
WORKER_STALE_AFTER_SECONDS = 30
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


class IdempotencyConflict(ValueError):
    """An idempotency key was already used for a different request."""


class InvalidJobUpdate(ValueError):
    pass


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
                    idempotency_key TEXT,
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
                    next_attempt_at TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                )
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "result_key" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN result_key TEXT")
            if "next_attempt_at" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN next_attempt_at TEXT")
            if "idempotency_key" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN idempotency_key TEXT")
            if "cancel_requested" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0")
            db.execute("""CREATE UNIQUE INDEX IF NOT EXISTS jobs_idempotency_key_unique
                          ON jobs (idempotency_key) WHERE idempotency_key IS NOT NULL""")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_queue_order ON jobs (status, id)")
            db.execute("""CREATE TABLE IF NOT EXISTS worker_heartbeats (
                worker_id TEXT PRIMARY KEY,
                last_activity_at TEXT NOT NULL,
                stopped_at TEXT
            )""")

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

    def get(self, job_id: str) -> JobStatusResponse:
        with self._connect() as db:
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            queue_position = (
                db.execute("SELECT COUNT(*) FROM jobs WHERE status=? AND id<?",
                           (JobStatus.QUEUED, row["id"])).fetchone()[0]
                if row is not None and row["status"] == JobStatus.QUEUED else None
            )
        if row is None:
            raise JobNotFound(job_id)
        return self._status(row, queue_position)

    def cancel(self, job_id: str) -> JobStatusResponse:
        """Cancel a queued job immediately or signal an active worker to stop."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise JobNotFound(job_id)
            status = JobStatus(row["status"])
            if status == JobStatus.CANCELLED:
                return self._status(row)
            if status == JobStatus.QUEUED or (
                status == JobStatus.FAILED and row["attempt"] < row["max_attempts"]
                and row["error_code"] in RETRYABLE_ERROR_CODES
            ):
                db.execute("""UPDATE jobs SET status=?, cancel_requested=1,
                    status_message=?, completed_at=?, next_attempt_at=NULL,
                    error_code=NULL, error_message=NULL, result_key=NULL WHERE job_id=?""",
                    (JobStatus.CANCELLED, "Cancelled by request", _now(), job_id))
            elif status in ACTIVE:
                db.execute("""UPDATE jobs SET cancel_requested=1, status_message=?
                    WHERE job_id=?""", ("Cancellation requested", job_id))
            else:
                raise InvalidJobUpdate("completed job cannot be cancelled")
        return self.get(job_id)

    def cancellation_requested(self, job_id: str) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT cancel_requested FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return bool(row["cancel_requested"])

    def finish_cancel(self, job_id: str) -> JobStatusResponse:
        """Complete cancellation after the worker has stopped using the job."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status, cancel_requested FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise JobNotFound(job_id)
            if row["status"] == JobStatus.CANCELLED:
                pass
            elif not row["cancel_requested"] or row["status"] not in ACTIVE | {JobStatus.FAILED}:
                raise InvalidJobUpdate("job has no pending cancellation")
            else:
                db.execute("""UPDATE jobs SET status=?, status_message=?, completed_at=?,
                    next_attempt_at=NULL, result_key=NULL, error_code=NULL, error_message=NULL
                    WHERE job_id=?""", (JobStatus.CANCELLED, "Cancelled by request", _now(), job_id))
        return self.get(job_id)

    def record_worker_activity(self, worker_id: str) -> None:
        now = _now()
        with self._connect() as db:
            db.execute("""INSERT INTO worker_heartbeats (worker_id, last_activity_at, stopped_at)
                          VALUES (?, ?, NULL)
                          ON CONFLICT(worker_id) DO UPDATE SET
                          last_activity_at=excluded.last_activity_at, stopped_at=NULL""",
                       (worker_id, now))

    def stop_worker(self, worker_id: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE worker_heartbeats SET stopped_at=? WHERE worker_id=?", (_now(), worker_id))

    def worker_health(self) -> tuple[bool, datetime | None, int]:
        with self._connect() as db:
            active = db.execute("""SELECT last_activity_at FROM worker_heartbeats
                                   WHERE stopped_at IS NULL ORDER BY last_activity_at DESC LIMIT 1""").fetchone()
            last = db.execute("SELECT MAX(last_activity_at) FROM worker_heartbeats").fetchone()[0]
            queued = db.execute("SELECT COUNT(*) FROM jobs WHERE status=?", (JobStatus.QUEUED,)).fetchone()[0]
        last_activity_at = datetime.fromisoformat(last) if last else None
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=WORKER_STALE_AFTER_SECONDS)
        running = active is not None and datetime.fromisoformat(active["last_activity_at"]) >= cutoff
        return running, last_activity_at, queued

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
            cancelled = db.execute(
                f"""UPDATE jobs SET status=?, completed_at=?, status_message=?,
                    next_attempt_at=NULL, result_key=NULL, error_code=NULL, error_message=NULL
                    WHERE cancel_requested=1 AND status IN ({','.join('?' for _ in ACTIVE)})""",
                (JobStatus.CANCELLED, _now(), "Cancelled by request", *ACTIVE),
            ).rowcount
            cursor = db.execute(
                f"""UPDATE jobs SET status=?, completed_at=?, error_code=?, error_message=?,
                    status_message=? WHERE status IN ({','.join('?' for _ in ACTIVE)})""",
                (JobStatus.FAILED, _now(), "WORKER_INTERRUPTED",
                 "Analysis worker stopped before completion", "Analysis worker interrupted",
                 *ACTIVE),
            )
            return cancelled + cursor.rowcount

    def requeue_pending_retries(self) -> int:
        """Resume retryable failures left behind by an interrupted worker."""
        with self._connect() as db:
            rows = db.execute(
                f"""SELECT job_id FROM jobs WHERE status=? AND attempt<max_attempts
                    AND error_code IN ({','.join('?' for _ in RETRYABLE_ERROR_CODES)})""",
                (JobStatus.FAILED, *sorted(RETRYABLE_ERROR_CODES)),
            ).fetchall()
        resumed = 0
        for row in rows:
            try:
                self.retry_failed(row["job_id"])
            except InvalidJobUpdate:
                continue
            resumed += 1
        return resumed

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
            if previous in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
                raise InvalidJobUpdate("terminal job cannot be updated")
            if row["cancel_requested"] and status == JobStatus.SUCCEEDED:
                raise InvalidJobUpdate("cancellation requested")
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
                    or row["attempt"] >= row["max_attempts"] or row["cancel_requested"]):
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
    def _status(row: sqlite3.Row, queue_position: int | None = None) -> JobStatusResponse:
        error = (JobError(code=row["error_code"], message=row["error_message"],
                          retryable=row["error_code"] in RETRYABLE_ERROR_CODES)
                 if row["error_code"] is not None else None)
        return JobStatusResponse(
            job_id=row["job_id"], monitoring_data_id=row["monitoring_data_id"],
            status=row["status"], attempt=row["attempt"], max_attempts=row["max_attempts"],
            cancel_requested=bool(row["cancel_requested"]),
            queue_position=queue_position,
            progress_percent=row["progress_percent"],
            processed_frame_count=row["processed_frame_count"],
            total_frame_count=row["total_frame_count"], status_message=row["status_message"],
            created_at=row["created_at"], started_at=row["started_at"],
            completed_at=row["completed_at"], error=error,
        )
