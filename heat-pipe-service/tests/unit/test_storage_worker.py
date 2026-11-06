import hashlib
import json
import hashlib
import sqlite3
import struct
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import jsonschema
import numpy as np
import pytest
from fastapi.testclient import TestClient

from heatpipe_service.api.app import create_app
from heatpipe_service.api.models import CreateJobRequest, FileReference, JobStatus
from heatpipe_service.jobs import batch as batch_module
from heatpipe_service.jobs.store import JobStore
from heatpipe_service.jobs.worker import JobWorker
from heatpipe_service.storage.backends import LocalStorage, MinioStorage, StorageError, validate_source_url


def _inputs(root: Path) -> tuple[Path, Path]:
    rng = np.random.default_rng(20261102)
    yy, xx = np.ogrid[:64, :96]
    frames = []
    for index in range(4):
        frame = 30 + rng.normal(0, 0.08, (64, 96))
        frame[(xx - 48) ** 2 + (yy - (46 + index)) ** 2 <= 5**2] += 5
        frames.append(frame)
    att = root / "sample.att"
    atg = root / "sample.atg"
    att.write_bytes(struct.pack("<4H", 1, 0, 96, 64) + b"".join(
        np.rint(frame * 100).astype("<u2").tobytes() for frame in frames
    ))
    atg.write_bytes(b"".join(
        f"2026-08-13 14:02:{index:02d}".encode("ascii")
        + struct.pack("<i", index)
        + struct.pack("<dd", 37.5, 126.9)
        for index in range(4)
    ))
    return att, atg


def test_local_storage_validates_keys_size_and_checksum(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "objects")
    source = tmp_path / "payload.att"
    source.write_bytes(b"thermal-data")
    key = storage.put(source, "inputs/payload.att")
    assert key == "inputs/payload.att"
    downloaded = tmp_path / "downloaded.att"
    storage.fetch(FileReference(
        object_key=key, size_bytes=source.stat().st_size,
        sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
    ), downloaded)
    assert downloaded.read_bytes() == source.read_bytes()
    with pytest.raises(StorageError, match="size"):
        storage.fetch(FileReference(object_key=key, size_bytes=999), tmp_path / "bad.att")
    assert not (tmp_path / "bad.att").exists()
    with pytest.raises(StorageError, match="SHA-256"):
        storage.fetch(FileReference(object_key=key, sha256="0" * 64), tmp_path / "bad-hash.att")
    with pytest.raises(StorageError):
        storage.put(source, "../outside.att")
    with pytest.raises(StorageError):
        storage.fetch(FileReference(url="https://untrusted.example/file"), tmp_path / "url.att")


def test_internal_http_requires_its_own_explicit_host_allowlist() -> None:
    validate_source_url("https://files.example/data.att", frozenset({"files.example"}))
    validate_source_url("http://minio:9000/data.att?signature=redacted", frozenset(), frozenset({"minio"}))
    with pytest.raises(StorageError, match="scheme or host"):
        validate_source_url("http://minio:9000/data.att", frozenset({"minio"}))
    with pytest.raises(StorageError, match="scheme or host"):
        validate_source_url("http://external.example/data.att", frozenset(), frozenset({"minio"}))


def test_worker_runs_batch_and_uploads_result(tmp_path: Path) -> None:
    att, atg = _inputs(tmp_path)
    storage = LocalStorage(tmp_path / "objects")
    storage.put(att, "raw/2026/11/upload-att.att")
    storage.put(atg, "raw/2026/11/upload-atg.atg")
    store = JobStore(tmp_path / "jobs.sqlite3")
    request = CreateJobRequest.model_validate({
        "monitoring_data_id": "sample",
        "att": {"object_key": "raw/2026/11/upload-att.att", "size_bytes": att.stat().st_size},
        "atg": {"object_key": "raw/2026/11/upload-atg.atg"},
        "frame_range": {"start": 0, "end_exclusive": 4},
    })
    job = store.create(request)
    worker = JobWorker(store, storage, tmp_path / "work")
    assert worker.run_once() == job.job_id
    assert worker.run_once() is None
    assert not (tmp_path / "work" / job.job_id).exists()
    status = JobStore(tmp_path / "jobs.sqlite3").get(job.job_id)
    assert status.status == JobStatus.SUCCEEDED
    assert status.progress_percent == 100
    assert status.processed_frame_count == 4
    key = store.get_result_key(job.job_id)
    assert key == f"results/{job.job_id}/analysis_result.json"
    result = json.loads(storage.read_bytes(key))
    schema = json.loads((Path(__file__).resolve().parents[2] / "docs/schemas/analysis-result-v0.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(result)
    assert result["job_id"] == job.job_id
    assert result["status"] == "SUCCEEDED"
    assert result["processing"]["input"] == {"att_file_name": "sample.att", "atg_file_name": "sample.atg"}
    assert result["candidate_count"] >= 1
    assert result["candidates"][0]["roi_temperature_c"]["maximum"] >= result["candidates"][0]["roi_temperature_c"]["mean"]
    assert storage.read_bytes(f"results/{job.job_id}/analysis_candidates.csv")
    for candidate in result["candidates"]:
        png = storage.read_bytes(candidate["result_image"]["path"])
        assert png.startswith(b"\x89PNG")
        assert candidate["result_image"]["size_bytes"] == len(png)
        assert candidate["result_image"]["sha256"] == hashlib.sha256(png).hexdigest()

    api = TestClient(create_app(tmp_path / "jobs.sqlite3", storage=storage, api_token="worker-test-token"))
    api.headers["Authorization"] = "Bearer worker-test-token"
    api_result = api.get(f"/api/v1/analysis-jobs/{job.job_id}/results")
    assert api_result.status_code == 200
    jsonschema.Draft202012Validator(schema).validate(api_result.json())
    assert api_result.json()["candidates"][0]["roi_temperature_c"] == result["candidates"][0]["roi_temperature_c"]
    image_path = api_result.json()["candidates"][0]["result_image"]["path"]
    assert image_path.startswith(f"/api/v1/analysis-jobs/{job.job_id}/images/")
    image = api.get(image_path)
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG")
    assert api_result.json()["candidates"][0]["result_image"]["size_bytes"] == len(image.content)
    assert api_result.json()["candidates"][0]["result_image"]["sha256"] == hashlib.sha256(image.content).hexdigest()
    assert api.get(f"/api/v1/analysis-jobs/{job.job_id}/images/no-such-candidate").status_code == 404


def test_int001_adds_png_integrity_to_older_stored_result(tmp_path: Path) -> None:
    att, atg = _inputs(tmp_path)
    storage = LocalStorage(tmp_path / "objects")
    storage.put(att, "inputs/sample.att")
    storage.put(atg, "inputs/sample.atg")
    db_path = tmp_path / "jobs.sqlite3"
    store = JobStore(db_path)
    job = store.create(CreateJobRequest.model_validate({
        "monitoring_data_id": "sample",
        "att": {"object_key": "inputs/sample.att"},
        "atg": {"object_key": "inputs/sample.atg"},
    }))
    assert JobWorker(store, storage, tmp_path / "work").run_once() == job.job_id
    result_path = storage._path(store.get_result_key(job.job_id))
    stored = json.loads(result_path.read_text(encoding="utf-8"))
    for candidate in stored["candidates"]:
        candidate["result_image"].pop("sha256")
        candidate["result_image"].pop("size_bytes")
        candidate.pop("roi_temperature_c")
    stored.pop("detected_candidate_count")
    stored["processing"]["parameters"].pop("max_results")
    stored["schema_version"] = "0.1.0"
    result_path.write_text(json.dumps(stored), encoding="utf-8")
    api = TestClient(create_app(db_path, storage=storage, api_token="worker-test-token"))
    api.headers["Authorization"] = "Bearer worker-test-token"
    response = api.get(f"/api/v1/analysis-jobs/{job.job_id}/results")
    assert response.status_code == 200
    assert response.json()["schema_version"] == "0.3.0"
    assert response.json()["detected_candidate_count"] is None
    assert response.json()["processing"]["parameters"]["max_results"] is None
    assert response.json()["candidates"][0]["roi_temperature_c"] is None
    for candidate in response.json()["candidates"]:
        png = api.get(candidate["result_image"]["path"]).content
        assert candidate["result_image"]["size_bytes"] == len(png)
        assert candidate["result_image"]["sha256"] == hashlib.sha256(png).hexdigest()


def test_worker_schedules_retry_on_transient_download_error(tmp_path: Path) -> None:
    class FailingStorage:
        def fetch(self, _reference, destination, should_cancel=None):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"partial input")
            raise StorageError("DOWNLOAD_FAILED", "Source temporarily unavailable")

    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create(CreateJobRequest.model_validate({
        "monitoring_data_id": "sample",
        "att": {"object_key": "inputs/sample.att"},
        "atg": {"object_key": "inputs/sample.atg"},
    }))
    worker = JobWorker(store, FailingStorage(), tmp_path / "work")
    assert worker.run_once() == job.job_id
    assert not (tmp_path / "work" / job.job_id).exists()
    assert store.get(job.job_id).status == JobStatus.QUEUED
    assert store.attempt(job.job_id) == 2
    assert worker.run_once() is None  # Delayed retry is not claimed immediately.


def test_running_job_cancels_during_input_copy_without_retry(tmp_path: Path) -> None:
    entered = Event()
    release = Event()

    class BlockingStorage(LocalStorage):
        def fetch(self, reference, destination, should_cancel=None):
            entered.set()
            assert release.wait(timeout=5)
            return super().fetch(reference, destination, should_cancel=should_cancel)

    att, atg = _inputs(tmp_path)
    storage = BlockingStorage(tmp_path / "objects")
    storage.put(att, "inputs/sample.att")
    storage.put(atg, "inputs/sample.atg")
    db_path = tmp_path / "jobs.sqlite3"
    store = JobStore(db_path)
    job = store.create(CreateJobRequest.model_validate({
        "monitoring_data_id": "sample",
        "att": {"object_key": "inputs/sample.att"},
        "atg": {"object_key": "inputs/sample.atg"},
    }))
    worker = JobWorker(store, storage, tmp_path / "work")
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(worker.run_once)
            assert entered.wait(timeout=5)
            api = TestClient(create_app(db_path, storage=storage, api_token="worker-test-token"))
            cancelled = api.post(f"/api/v1/analysis-jobs/{job.job_id}/cancel",
                                 headers={"Authorization": "Bearer worker-test-token"})
            assert cancelled.status_code == 202
            assert cancelled.json()["cancel_requested"] is True
            assert cancelled.json()["status"] == "DOWNLOADING"
            release.set()
            assert running.result(timeout=10) == job.job_id
    finally:
        release.set()
    status = store.get(job.job_id)
    assert status.status == JobStatus.CANCELLED
    assert status.error is None
    assert status.attempt == 1
    assert store.requeue_pending_retries() == 0
    assert store.claim_next() is None
    assert not (tmp_path / "work" / job.job_id).exists()
    assert not (tmp_path / "objects" / "results" / job.job_id).exists()


def test_running_job_cancels_between_analysis_frames(tmp_path: Path, monkeypatch) -> None:
    entered = Event()
    release = Event()
    original = batch_module._frame_candidates

    def pause_first_frame(*args, **kwargs):
        if not entered.is_set():
            entered.set()
            assert release.wait(timeout=5)
        return original(*args, **kwargs)

    monkeypatch.setattr(batch_module, "_frame_candidates", pause_first_frame)
    att, atg = _inputs(tmp_path)
    storage = LocalStorage(tmp_path / "objects")
    storage.put(att, "inputs/sample.att")
    storage.put(atg, "inputs/sample.atg")
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create(CreateJobRequest.model_validate({
        "monitoring_data_id": "sample",
        "att": {"object_key": "inputs/sample.att"},
        "atg": {"object_key": "inputs/sample.atg"},
    }))
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(JobWorker(store, storage, tmp_path / "work").run_once)
            assert entered.wait(timeout=5)
            assert store.cancel(job.job_id).cancel_requested is True
            release.set()
            assert running.result(timeout=10) == job.job_id
    finally:
        release.set()
    assert store.get(job.job_id).status == JobStatus.CANCELLED
    assert store.get_result_key(job.job_id) is None


def test_cancellation_during_result_upload_deletes_uploaded_png(tmp_path: Path) -> None:
    entered = Event()
    release = Event()

    class BlockingUploadStorage(LocalStorage):
        def put(self, source, object_key):
            if object_key.endswith(".png") and not entered.is_set():
                entered.set()
                assert release.wait(timeout=5)
            return super().put(source, object_key)

    att, atg = _inputs(tmp_path)
    storage = BlockingUploadStorage(tmp_path / "objects")
    storage.put(att, "inputs/sample.att")
    storage.put(atg, "inputs/sample.atg")
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create(CreateJobRequest.model_validate({
        "monitoring_data_id": "sample",
        "att": {"object_key": "inputs/sample.att"},
        "atg": {"object_key": "inputs/sample.atg"},
    }))
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(JobWorker(store, storage, tmp_path / "work").run_once)
            assert entered.wait(timeout=5)
            assert store.cancel(job.job_id).cancel_requested is True
            release.set()
            assert running.result(timeout=10) == job.job_id
    finally:
        release.set()
    assert store.get(job.job_id).status == JobStatus.CANCELLED
    assert store.get_result_key(job.job_id) is None
    assert not list((tmp_path / "objects" / "results" / job.job_id).rglob("*.*"))


def test_missing_uploaded_png_prevents_success(tmp_path: Path) -> None:
    class MissingPngStorage(LocalStorage):
        def read_bytes(self, object_key: str) -> bytes:
            if object_key.endswith(".png"):
                raise StorageError("RESULT_NOT_FOUND", "PNG is not available")
            return super().read_bytes(object_key)

    att, atg = _inputs(tmp_path)
    storage = MissingPngStorage(tmp_path / "objects")
    storage.put(att, "inputs/sample.att")
    storage.put(atg, "inputs/sample.atg")
    db_path = tmp_path / "jobs.sqlite3"
    store = JobStore(db_path)
    job = store.create(CreateJobRequest.model_validate({
        "monitoring_data_id": "sample",
        "att": {"object_key": "inputs/sample.att"},
        "atg": {"object_key": "inputs/sample.atg"},
    }))
    with sqlite3.connect(db_path) as db:
        db.execute("UPDATE jobs SET attempt=3 WHERE job_id=?", (job.job_id,))
    worker = JobWorker(store, storage, tmp_path / "work")
    assert worker.run_once() == job.job_id
    status = store.get(job.job_id)
    assert status.status == JobStatus.FAILED
    assert status.error.code == "UPLOAD_FAILED"
    assert store.get_result_key(job.job_id) is None
    api = TestClient(create_app(db_path, storage=storage, api_token="worker-test-token"))
    response = api.get(f"/api/v1/analysis-jobs/{job.job_id}/results",
                       headers={"Authorization": "Bearer worker-test-token"})
    assert response.status_code == 409


def test_minio_adapter_uses_object_keys(tmp_path: Path) -> None:
    objects: dict[str, bytes] = {"input/sample.att": b"thermal-data"}

    class Response:
        def __init__(self, data: bytes):
            self.data = data
            self.offset = 0
        def read(self, size=-1):
            if size < 0:
                size = len(self.data) - self.offset
            chunk = self.data[self.offset:self.offset + size]
            self.offset += len(chunk)
            return chunk
        def close(self):
            pass
        def release_conn(self):
            pass

    class Client:
        def fget_object(self, *, bucket_name, object_name, file_path):
            assert bucket_name == "thermal"
            Path(file_path).write_bytes(objects[object_name])
        def fput_object(self, *, bucket_name, object_name, file_path):
            assert bucket_name == "thermal"
            objects[object_name] = Path(file_path).read_bytes()
        def get_object(self, *, bucket_name, object_name):
            assert bucket_name == "thermal"
            return Response(objects[object_name])
        def remove_object(self, *, bucket_name, object_name):
            assert bucket_name == "thermal"
            del objects[object_name]

    storage = MinioStorage(bucket="thermal", client=Client())
    local = tmp_path / "sample.att"
    storage.fetch(FileReference(object_key="input/sample.att"), local)
    assert local.read_bytes() == b"thermal-data"
    assert storage.put(local, "results/sample.att") == "results/sample.att"
    assert storage.read_bytes("results/sample.att") == b"thermal-data"
    storage.delete("results/sample.att")
    assert "results/sample.att" not in objects
