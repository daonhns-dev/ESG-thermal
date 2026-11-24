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
from heatpipe_service.storage.shared_inputs import SHARED_ROOT_MARKER, SHARED_ROOT_MARKER_CONTENT


def _mark_shared_root(root):
    (root / SHARED_ROOT_MARKER).write_bytes(SHARED_ROOT_MARKER_CONTENT)


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
    validate_source_url("http://portal-minio:9000/data.att", frozenset(), frozenset({"portal-minio:9000"}))
    with pytest.raises(StorageError, match="scheme or host"):
        validate_source_url("http://portal-minio:9001/data.att", frozenset(), frozenset({"portal-minio:9000"}))
    with pytest.raises(StorageError, match="scheme or host"):
        validate_source_url("http://minio:9000/data.att", frozenset({"minio"}))
    with pytest.raises(StorageError, match="scheme or host"):
        validate_source_url("http://external.example/data.att", frozenset(), frozenset({"minio"}))


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel", "retry"])
def test_shared_worker_skips_fetch_and_preserves_originals(tmp_path, monkeypatch, outcome):
    shared = tmp_path / "shared" / "heatpipe-portal" / "raw"
    shared.mkdir(parents=True)
    _mark_shared_root(tmp_path / "shared")
    att, atg = _inputs(shared)
    att.rename(shared / "different-att.att")
    atg.rename(shared / "different-atg.atg")
    att, atg = shared / "different-att.att", shared / "different-atg.atg"
    originals = (att.read_bytes(), atg.read_bytes())
    monkeypatch.setenv("HEATPIPE_SHARED_INPUT_ROOT", str(tmp_path / "shared"))
    monkeypatch.setenv("HEATPIPE_ALLOWED_INTERNAL_HTTP_HOSTS", "portal-minio:9000")
    storage = LocalStorage(tmp_path / "objects")
    def forbidden_fetch(*args, **kwargs):
        pytest.fail("Direct shared-file analysis must not fetch or copy input files")
    monkeypatch.setattr(storage, "fetch", forbidden_fetch)
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create(CreateJobRequest.model_validate({
        "monitoring_data_id": "shared-job",
        "att": {"path": "heatpipe-portal/raw/different-att.att",
                "size_bytes": len(originals[0]), "sha256": hashlib.sha256(originals[0]).hexdigest()},
        "atg": {"path": "heatpipe-portal/raw/different-atg.atg",
                "size_bytes": len(originals[1]), "sha256": hashlib.sha256(originals[1]).hexdigest()},
    }))
    worker = JobWorker(store, storage, tmp_path / "work")
    if outcome != "success":
        import heatpipe_service.jobs.worker as worker_module
        from heatpipe_service.cancellation import JobCancelled
        if outcome != "retry":
            with sqlite3.connect(tmp_path / "jobs.sqlite3") as db:
                db.execute("UPDATE jobs SET attempt=3 WHERE job_id=?", (job.job_id,))
        def interrupted_batch(request, **kwargs):
            assert request.att_path.is_symlink()
            assert request.att_path.read_bytes() == originals[0]
            if outcome == "cancel":
                store.cancel(job.job_id)
                raise JobCancelled()
            if outcome == "retry":
                raise StorageError("UPLOAD_FAILED", "Injected retryable failure")
            raise ValueError("Injected analysis failure")
        monkeypatch.setattr(worker_module, "run_batch_safely", interrupted_batch)
    assert worker.run_once() == job.job_id
    expected = {"success": JobStatus.SUCCEEDED, "failure": JobStatus.FAILED,
                "cancel": JobStatus.CANCELLED, "retry": JobStatus.QUEUED}[outcome]
    assert store.get(job.job_id).status == expected
    assert not (tmp_path / "work" / job.job_id).exists()
    assert (att.read_bytes(), atg.read_bytes()) == originals
    if outcome != "success":
        return
    result = json.loads(storage.read_bytes(store.get_result_key(job.job_id)))
    assert result["processing"]["input"]["att_file_name"] == "shared-job.att"
    assert result["candidates"]
    api = TestClient(create_app(tmp_path / "jobs.sqlite3", storage=storage, api_token="test"))
    api.headers["Authorization"] = "Bearer test"
    assert api.get(f"/api/v1/analysis-jobs/{job.job_id}").json()["status"] == "SUCCEEDED"
    response = api.get(f"/api/v1/analysis-jobs/{job.job_id}/results")
    assert response.status_code == 200
    candidate = response.json()["candidates"][0]
    png = api.get(candidate["result_image"]["path"])
    assert png.status_code == 200 and png.headers["content-type"].startswith("image/png")
    assert hashlib.sha256(png.content).hexdigest() == candidate["result_image"]["sha256"]


def test_shared_input_rejects_invalid_sources(tmp_path):
    from heatpipe_service.storage.shared_inputs import link_shared_input
    root = tmp_path / "shared"
    root.mkdir()
    _mark_shared_root(root)
    destination = tmp_path / "work" / "sample.att"
    for key in ("missing.att", "object.att"):
        if key == "object.att":
            (root / key).mkdir()  # MinIO object directories are not plain input files.
        with pytest.raises(StorageError, match="regular file"):
            link_shared_input(FileReference(path=key, size_bytes=1, sha256="0" * 64), destination, root)
    (root / "sample.att").write_bytes(b"input")
    with pytest.raises(StorageError, match="size"):
        link_shared_input(FileReference(path="sample.att", size_bytes=99, sha256="0" * 64), destination, root)
    with pytest.raises(StorageError):
        link_shared_input(FileReference(url="http://portal-minio:9000/file.att"), destination, root)
    assert not destination.exists()


@pytest.mark.parametrize("mixed", [False, True])
def test_shared_root_does_not_override_url_fallback(tmp_path, monkeypatch, mixed):
    import heatpipe_service.storage.backends as backends
    root = tmp_path / "published"
    root.mkdir()
    _mark_shared_root(root)
    att, atg = _inputs(root)
    monkeypatch.setenv("HEATPIPE_SHARED_INPUT_ROOT", str(root))
    calls = []
    def download(url, destination, *args):
        calls.append(url)
        source = att if url.endswith(".att") else atg
        destination.write_bytes(source.read_bytes())
    monkeypatch.setattr(backends, "_download_url", download)
    request = {
        "monitoring_data_id": "sample",
        "att": {"url": "https://example.test/sample.att"},
        "atg": {"url": "https://example.test/sample.atg"},
    }
    if mixed:
        request["att"] = {"path": "sample.att", "size_bytes": att.stat().st_size,
                          "sha256": hashlib.sha256(att.read_bytes()).hexdigest()}
    else:
        (root / SHARED_ROOT_MARKER).unlink()  # URL fallback does not depend on shared storage.
    storage = LocalStorage(tmp_path / "objects", frozenset({"example.test"}))
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create(CreateJobRequest.model_validate(request))
    JobWorker(store, storage, tmp_path / "work").run_once()
    assert store.get(job.job_id).status == JobStatus.SUCCEEDED
    assert calls == (["https://example.test/sample.atg"] if mixed else
                     ["https://example.test/sample.att", "https://example.test/sample.atg"])
    assert att.is_file() and atg.is_file()


@pytest.mark.parametrize("interrupted_attempt", [1, 2])
@pytest.mark.parametrize("leftover", ["both", "att-only", "dangling"])
def test_shared_inputs_resume_after_worker_interruption(tmp_path, monkeypatch, interrupted_attempt, leftover):
    root = tmp_path / "published"
    root.mkdir()
    _mark_shared_root(root)
    att, atg = _inputs(root)
    original = (att.read_bytes(), atg.read_bytes())
    monkeypatch.setenv("HEATPIPE_SHARED_INPUT_ROOT", str(root))
    db = tmp_path / "jobs.sqlite3"
    store = JobStore(db)
    request = CreateJobRequest.model_validate({
        "monitoring_data_id": "sample",
        "att": {"path": "sample.att", "size_bytes": len(original[0]),
                "sha256": hashlib.sha256(original[0]).hexdigest()},
        "atg": {"path": "sample.atg", "size_bytes": len(original[1]),
                "sha256": hashlib.sha256(original[1]).hexdigest()},
    })
    job = store.create(request)
    assert store.claim_next() == job.job_id
    store.update(job.job_id, JobStatus.DOWNLOADING, 5, 0, None, "Preparing ATT/ATG files")
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE jobs SET attempt=? WHERE job_id=?", (interrupted_attempt, job.job_id))
    old_input = tmp_path / "work" / job.job_id / "input"
    old_input.mkdir(parents=True)
    (old_input / "sample.att").symlink_to(root / "gone.att" if leftover == "dangling" else att)
    if leftover == "both":
        (old_input / "sample.atg").symlink_to(atg)
    # Same recovery calls as the actual worker entry point; finally did not run.
    recovered = JobStore(db)
    assert recovered.recover_interrupted() == 1
    assert recovered.get(job.job_id).error.code == "WORKER_INTERRUPTED"
    assert recovered.requeue_pending_retries() == 1
    storage = LocalStorage(tmp_path / "objects")
    def no_fetch(*args, **kwargs):
        pytest.fail("Recovered path jobs must not download")
    monkeypatch.setattr(storage, "fetch", no_fetch)
    JobWorker(recovered, storage, tmp_path / "work").run_once()
    status = recovered.get(job.job_id)
    assert status.status == JobStatus.SUCCEEDED
    assert status.attempt == interrupted_attempt + 1
    assert not (tmp_path / "work" / job.job_id).exists()
    assert (att.read_bytes(), atg.read_bytes()) == original


def test_shared_storage_lost_after_admission_reports_configuration_error(tmp_path, monkeypatch):
    root = tmp_path / "published"
    root.mkdir()
    _mark_shared_root(root)
    att, atg = _inputs(root)
    monkeypatch.setenv("HEATPIPE_SHARED_INPUT_ROOT", str(root))
    storage = LocalStorage(tmp_path / "objects")
    db = tmp_path / "jobs.sqlite3"
    client = TestClient(create_app(db, storage=storage, api_token="test"))
    client.headers["Authorization"] = "Bearer test"
    body = {"monitoring_data_id": "sample"}
    for name, file in (("att", att), ("atg", atg)):
        body[name] = {"path": file.name, "size_bytes": file.stat().st_size,
                      "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
    response = client.post("/api/v1/analysis-jobs", json=body)
    assert response.status_code == 202
    (root / SHARED_ROOT_MARKER).unlink()
    store = JobStore(db)
    JobWorker(store, storage, tmp_path / "work").run_once()
    failed = store.get(response.json()["job_id"])
    assert failed.status == JobStatus.FAILED
    assert failed.error.code == "SHARED_INPUT_NOT_CONFIGURED"
    assert failed.error.retryable is False
    assert failed.attempt == 1
    assert att.is_file() and atg.is_file()


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
