"""Run one isolated API -> worker -> result smoke test with real ATT/ATG files.

The source files are read-only. Temporary copies and results are removed on exit.
"""

from __future__ import annotations

import argparse
import json
import secrets
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from heatpipe_service.api.app import create_app
from heatpipe_service.api.models import FileReference
from heatpipe_service.jobs.store import JobStore
from heatpipe_service.jobs.worker import JobWorker
from heatpipe_service.storage.backends import LocalStorage


class SplitStorage:
    """Read inputs from the source directory, write results to the test directory."""

    def __init__(self, source_root: Path, result_root: Path) -> None:
        self.sources = LocalStorage(source_root)
        self.results = LocalStorage(result_root)

    def fetch(self, reference: FileReference, destination: Path) -> None:
        self.sources.fetch(reference, destination)

    def put(self, source: Path, object_key: str) -> str:
        return self.results.put(source, object_key)

    def read_bytes(self, object_key: str) -> bytes:
        return self.results.read_bytes(object_key)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--att", type=Path, required=True)
    parser.add_argument("--atg", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=30)
    args = parser.parse_args()
    if args.frames < 1:
        parser.error("--frames must be positive")
    if args.att.stem != args.atg.stem:
        parser.error("ATT and ATG stems must match")
    if args.att.parent.resolve() != args.atg.parent.resolve():
        parser.error("ATT and ATG must be in the same source directory")

    scratch_root = Path("data").resolve()
    scratch_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="smoke-real-", dir=scratch_root) as scratch:
        scratch_path = Path(scratch)
        storage = SplitStorage(args.att.parent, scratch_path / "objects")
        db_path = scratch_path / "jobs.sqlite3"
        token = secrets.token_urlsafe(32)
        client = TestClient(create_app(db_path, storage=storage, api_token=token))
        client.headers["Authorization"] = f"Bearer {token}"
        accepted = client.post("/api/v1/analysis-jobs", json={
            "monitoring_data_id": args.att.stem,
            "att": {"object_key": args.att.name, "size_bytes": args.att.stat().st_size},
            "atg": {"object_key": args.atg.name, "size_bytes": args.atg.stat().st_size},
            "frame_range": {"start": 0, "end_exclusive": args.frames},
        })
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["job_id"]
        assert accepted.json()["status"] == "QUEUED"

        worker = JobWorker(JobStore(db_path), storage, scratch_path / "work")
        assert worker.run_once() == job_id
        status_response = client.get(f"/api/v1/analysis-jobs/{job_id}")
        assert status_response.status_code == 200, status_response.text
        status = status_response.json()
        assert status["status"] == "SUCCEEDED", status

        result_response = client.get(f"/api/v1/analysis-jobs/{job_id}/results")
        assert result_response.status_code == 200, result_response.text
        result = result_response.json()
        assert result["job_id"] == job_id
        assert result["status"] == "SUCCEEDED"
        image_checked = False
        if result["candidates"]:
            image_path = result["candidates"][0]["result_image"]["path"]
            image_response = client.get(image_path)
            assert image_response.status_code == 200, image_response.text
            assert image_response.headers["content-type"] == "image/png"
            assert image_response.content.startswith(b"\x89PNG")
            image_checked = True
        assert not (scratch_path / "work" / job_id).exists()
        print(json.dumps({
            "job_id": job_id,
            "status": status["status"],
            "progress_percent": status["progress_percent"],
            "processed_frame_count": status["processed_frame_count"],
            "candidate_count": result["candidate_count"],
            "png_checked": image_checked,
            "temporary_files_cleaned": True,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
