"""Single-process SQLite worker that runs queued ATT/ATG analyses."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path

from heatpipe_service.api.models import AnalysisResultResponse, JobStatus, Message
from heatpipe_service.jobs.batch import BatchProgress, BatchRequest, run_batch_safely
from heatpipe_service.jobs.store import InvalidJobUpdate, JobStore
from heatpipe_service.storage.backends import Storage, StorageError, storage_from_env

logger = logging.getLogger(__name__)


class JobWorker:
    def __init__(self, store: JobStore, storage: Storage, work_root: Path | str) -> None:
        self.store = store
        self.storage = storage
        self.work_root = Path(work_root).resolve()
        self.work_root.mkdir(parents=True, exist_ok=True)

    def run_once(self) -> str | None:
        """Claim and execute one due job, returning its ID or None if idle."""
        job_id = self.store.claim_next()
        if job_id is None:
            return None
        try:
            self._execute(job_id)
        except Exception as error:
            current = self.store.get(job_id)
            if current.status not in {JobStatus.FAILED, JobStatus.SUCCEEDED}:
                code = error.code if isinstance(error, StorageError) else (
                    "INPUT_OUTPUT_ERROR" if isinstance(error, OSError) else "ANALYSIS_FAILED"
                )
                self.store.update(
                    job_id, JobStatus.FAILED, current.progress_percent,
                    current.processed_frame_count, current.total_frame_count,
                    str(error) or error.__class__.__name__,
                    Message(code=code, message=str(error) or error.__class__.__name__),
                )
        finally:
            self._cleanup_workdir(job_id)
        if self.store.get(job_id).status == JobStatus.FAILED:
            self._schedule_retry(job_id)
        return job_id

    def _cleanup_workdir(self, job_id: str) -> None:
        if not re.fullmatch(r"JOB-[0-9]{8}-[0-9]{6}", job_id):
            logger.warning("Skipping cleanup for invalid job ID")
            return
        job_dir = self.work_root / job_id
        if job_dir.is_symlink() or job_dir.resolve().parent != self.work_root:
            logger.warning("Skipping cleanup outside work root for job %s", job_id)
            return
        try:
            shutil.rmtree(job_dir)
        except FileNotFoundError:
            pass
        except OSError:
            logger.exception("Could not remove temporary files for job %s", job_id)

    def _execute(self, job_id: str) -> None:
        request = self.store.get_request(job_id)
        job_dir = self.work_root / job_id
        input_dir = job_dir / "input"
        output_dir = job_dir / "output"
        att = input_dir / f"{request.monitoring_data_id}.att"
        atg = input_dir / f"{request.monitoring_data_id}.atg"

        self.store.update(job_id, JobStatus.DOWNLOADING, 5, 0, None, "Fetching ATT/ATG files")
        self.storage.fetch(request.att, att)
        self.storage.fetch(request.atg, atg)

        def on_progress(progress: BatchProgress) -> None:
            if progress.status in {"VALIDATING", "SUCCEEDED"}:
                return  # The worker already validated inputs and must upload before success.
            if progress.status == "FAILED":
                current = self.store.get(job_id)
                self.store.update(
                    job_id, JobStatus.FAILED, current.progress_percent,
                    current.processed_frame_count, current.total_frame_count,
                    progress.message,
                    Message(code=progress.error_code or "ANALYSIS_FAILED", message=progress.message),
                )
                return
            self.store.batch_callback(job_id)(progress)

        roi = request.analysis_roi
        artifacts = run_batch_safely(
            BatchRequest(
                job_id=job_id, monitoring_data_id=request.monitoring_data_id,
                att_path=att, atg_path=atg, output_dir=output_dir,
                frame_start=request.frame_range.start,
                frame_end=request.frame_range.end_exclusive,
                analysis_roi=(roi.x, roi.y, roi.x + roi.width, roi.y + roi.height) if roi else None,
            ),
            on_progress=on_progress,
        )
        if not artifacts.success:
            return

        current = self.store.get(job_id)
        self.store.update(
            job_id, JobStatus.UPLOADING, 95,
            current.processed_frame_count, current.total_frame_count,
            "Uploading analysis artifacts",
        )
        payload = json.loads(artifacts.result_json.read_text(encoding="utf-8"))
        prefix = f"results/{job_id}"
        for candidate in payload["candidates"]:
            relative = candidate["result_image"]["path"]
            candidate["result_image"]["path"] = self.storage.put(
                output_dir / relative, f"{prefix}/{relative}",
            )
        self.storage.put(artifacts.candidates_csv, f"{prefix}/analysis_candidates.csv")
        AnalysisResultResponse.model_validate(payload)
        artifacts.result_json.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8",
        )
        result_key = self.storage.put(artifacts.result_json, f"{prefix}/analysis_result.json")
        self.store.set_result_key(job_id, result_key)
        self.store.update(
            job_id, JobStatus.SUCCEEDED, 100,
            current.processed_frame_count, current.total_frame_count,
            "Analysis complete",
        )

    def _schedule_retry(self, job_id: str) -> None:
        try:
            attempt = self.store.attempt(job_id)
            self.store.retry_failed(job_id, delay_seconds=5 * 2 ** (attempt - 1))
        except InvalidJobUpdate:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process one due job and exit")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        parser.error("--poll-seconds must be positive")
    store = JobStore(os.environ.get("HEATPIPE_JOB_DB_PATH", "data/jobs.sqlite3"))
    store.recover_interrupted()
    worker = JobWorker(store, storage_from_env(), os.environ.get("HEATPIPE_WORK_ROOT", "data/work"))
    if args.once:
        worker.run_once()
        return
    try:
        while True:
            if worker.run_once() is None:
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
