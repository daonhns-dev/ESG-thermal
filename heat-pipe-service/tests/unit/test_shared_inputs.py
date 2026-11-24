"""Contract and filesystem boundaries for the plan-A input path."""
import hashlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from heatpipe_service.api.app import create_app
from heatpipe_service.api.models import CreateJobRequest, FileReference
from heatpipe_service.jobs.store import JobStore
from heatpipe_service.storage.backends import LocalStorage, StorageError
from heatpipe_service.storage.shared_inputs import (
    link_shared_input, validate_shared_root, SHARED_ROOT_MARKER, SHARED_ROOT_MARKER_CONTENT,
)


def ref(path="2026/11/input.att", **kwargs):
    return {"path": path, "size_bytes": 5, "sha256": hashlib.sha256(b"input").hexdigest(), **kwargs}


@pytest.mark.parametrize("path", ["/etc/passwd", "../file.att", "a/../file.att", "./file.att",
                                  "a//b.att", "a/", "C:/file.att", "a\\b.att", "a\x00.att"])
def test_model_rejects_unsafe_relative_paths(path):
    with pytest.raises(ValidationError):
        FileReference.model_validate(ref(path))


@pytest.mark.parametrize("extra", [{"url": "https://example.test/a"}, {"object_key": "a"},
                                   {"size_bytes": None}, {"sha256": None}])
def test_path_has_exactly_one_location_and_required_integrity(extra):
    with pytest.raises(ValidationError):
        FileReference.model_validate(ref(**extra))


def test_admission_and_idempotency_of_paths(tmp_path, monkeypatch):
    root = tmp_path / "shared"
    root.mkdir()
    (root / SHARED_ROOT_MARKER).write_bytes(SHARED_ROOT_MARKER_CONTENT)
    monkeypatch.setenv("HEATPIPE_SHARED_INPUT_ROOT", str(tmp_path / "shared"))
    storage = LocalStorage(tmp_path / "objects", frozenset({"example.test"}))
    db = tmp_path / "jobs.sqlite3"
    client = TestClient(create_app(db, storage=storage, api_token="test"))
    client.headers["Authorization"] = "Bearer test"
    body = {"monitoring_data_id": "sample", "att": ref(), "atg": ref("2026/11/input.atg")}
    headers = {"Idempotency-Key": "shared-key"}
    first = client.post("/api/v1/analysis-jobs", json=body, headers=headers)
    assert first.status_code == 202
    job_id = first.json()["job_id"]
    body["att"]["path"] = "2026/11/new.att"
    again = client.post("/api/v1/analysis-jobs", json=body, headers=headers)
    assert again.json()["job_id"] == job_id
    assert JobStore(db).get_request(job_id).att.path == "2026/11/new.att"
    body["att"] = {"url": "https://example.test/new.att", "size_bytes": 5,
                   "sha256": ref()["sha256"]}
    assert client.post("/api/v1/analysis-jobs", json=body, headers=headers).json()["job_id"] == job_id
    stored = JobStore(db).get_request(job_id)
    assert stored.att.path is None and stored.att.url is not None
    body["att"]["size_bytes"] = 6
    assert client.post("/api/v1/analysis-jobs", json=body, headers=headers).status_code == 409


def test_path_admission_requires_configuration_but_url_does_not(tmp_path, monkeypatch):
    monkeypatch.delenv("HEATPIPE_SHARED_INPUT_ROOT", raising=False)
    client = TestClient(create_app(tmp_path / "jobs.sqlite3",
                                  storage=LocalStorage(tmp_path / "objects", frozenset({"example.test"})),
                                  api_token="test"))
    client.headers["Authorization"] = "Bearer test"
    body = {"monitoring_data_id": "sample", "att": ref(), "atg": ref("sample.atg")}
    response = client.post("/api/v1/analysis-jobs", json=body)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SHARED_INPUT_NOT_CONFIGURED"
    body["att"]["path"] = "../bad.att"
    assert client.post("/api/v1/analysis-jobs", json=body).status_code == 422
    body["att"] = {"url": "https://example.test/file.att"}
    body["atg"] = {"url": "https://example.test/file.atg"}
    assert client.post("/api/v1/analysis-jobs", json=body).status_code == 202


def test_symlink_escape_and_checksum_are_rejected(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    (root / SHARED_ROOT_MARKER).write_bytes(SHARED_ROOT_MARKER_CONTENT)
    outside = tmp_path / "outside.att"
    outside.write_bytes(b"input")
    (root / "escape.att").symlink_to(outside)
    destination = tmp_path / "work" / "input.att"
    with pytest.raises(StorageError) as error:
        link_shared_input(FileReference.model_validate(ref("escape.att")), destination, root)
    assert error.value.code == "INVALID_SHARED_PATH"
    (root / "file.att").write_bytes(b"wrong")
    with pytest.raises(StorageError) as error:
        link_shared_input(FileReference.model_validate(ref("file.att")), destination, root)
    assert error.value.code == "FILE_INTEGRITY_ERROR"
    assert outside.read_bytes() == b"input" and not destination.exists()


@pytest.mark.parametrize("marker", [None, b"", b"wrong\n"])
def test_missing_or_invalid_storage_marker_blocks_api_and_direct_read(tmp_path, monkeypatch, marker):
    root = tmp_path / "shared"
    root.mkdir()
    (root / "file.att").write_bytes(b"input")
    if marker is not None:
        (root / SHARED_ROOT_MARKER).write_bytes(marker)
    monkeypatch.setenv("HEATPIPE_SHARED_INPUT_ROOT", str(root))
    db = tmp_path / "jobs.sqlite3"
    client = TestClient(create_app(db, storage=LocalStorage(tmp_path / "objects"), api_token="test"))
    client.headers["Authorization"] = "Bearer test"
    body = {"monitoring_data_id": "sample", "att": ref("file.att"), "atg": ref("file.atg")}
    response = client.post("/api/v1/analysis-jobs", json=body)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SHARED_INPUT_NOT_CONFIGURED"
    assert JobStore(db).worker_health()[2] == 0
    with pytest.raises(StorageError) as error:
        link_shared_input(FileReference.model_validate(ref("file.att")), tmp_path / "work" / "file.att", root)
    assert error.value.code == "SHARED_INPUT_NOT_CONFIGURED"


def test_marker_removal_is_checked_on_each_request(tmp_path, monkeypatch):
    root = tmp_path / "shared"
    root.mkdir()
    marker = root / SHARED_ROOT_MARKER
    marker.write_bytes(SHARED_ROOT_MARKER_CONTENT)
    monkeypatch.setenv("HEATPIPE_SHARED_INPUT_ROOT", str(root))
    client = TestClient(create_app(tmp_path / "jobs.sqlite3", api_token="test"))
    client.headers["Authorization"] = "Bearer test"
    body = {"monitoring_data_id": "sample", "att": ref(), "atg": ref("file.atg")}
    assert client.post("/api/v1/analysis-jobs", json=body).status_code == 202
    marker.unlink()
    assert client.post("/api/v1/analysis-jobs", json=body).status_code == 503


def test_marker_must_not_be_a_symlink(tmp_path):
    root = tmp_path / "shared"
    root.mkdir()
    marker = tmp_path / "marker"
    marker.write_bytes(SHARED_ROOT_MARKER_CONTENT)
    (root / SHARED_ROOT_MARKER).symlink_to(marker)
    with pytest.raises(StorageError) as error:
        validate_shared_root(root)
    assert error.value.code == "SHARED_INPUT_NOT_CONFIGURED"
