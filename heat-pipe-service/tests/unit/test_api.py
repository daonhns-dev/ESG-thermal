"""Mock HTTP contract checks; the batch engine remains independent."""

import json
import hashlib
from pathlib import Path

import jsonschema
from fastapi.testclient import TestClient
from pydantic import ValidationError

from heatpipe_service.api.app import create_app
from heatpipe_service.api.models import AnalysisResultResponse, JobStatus, Message
from heatpipe_service.jobs.store import JobStore
from heatpipe_service.jobs.worker import running_worker_heartbeat
from heatpipe_service.storage.backends import LocalStorage

TEST_TOKEN = "integration-test-token"


def _client(db_path: Path) -> TestClient:
    storage = LocalStorage(db_path.parent / "objects", frozenset({"example.test"}))
    client = TestClient(create_app(db_path, storage=storage, api_token=TEST_TOKEN))
    client.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
    return client


def test_openapi_exposes_only_scoped_endpoints_and_models(tmp_path: Path) -> None:
    client = _client(tmp_path / "jobs.sqlite3")
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert set(paths) == {
        "/health", "/version", "/api/v1/analysis-jobs",
        "/api/v1/analysis-jobs/{job_id}/cancel",
        "/api/v1/analysis-jobs/{job_id}",
        "/api/v1/analysis-jobs/{job_id}/results",
        "/api/v1/analysis-jobs/{job_id}/images/{candidate_id}",
    }
    schemas = response.json()["components"]["schemas"]
    assert schemas["CreateJobRequest"]["properties"]["monitoring_data_id"]["maxLength"] == 128
    image_get = paths["/api/v1/analysis-jobs/{job_id}/images/{candidate_id}"]["get"]
    assert "image/png" in image_get["responses"]["200"]["content"]
    for path, methods in paths.items():
        if not path.startswith("/api/"):
            continue
        for endpoint in methods.values():
            for status, documented in endpoint["responses"].items():
                if int(status) >= 400:
                    assert list(documented["content"]) == ["application/json"]
                    assert documented["content"]["application/json"]["schema"]["$ref"] == "#/components/schemas/ErrorResponse"
    assert image_get["security"] == [{"VtiBearerApiKey": []}]
    assert "security" not in paths["/health"]["get"]
    assert "VtiBearerApiKey" in response.json()["components"]["securitySchemes"]
    assert TEST_TOKEN not in response.text
    exported = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
    assert response.json() == json.loads(exported.read_text(encoding="utf-8"))
    for name in ("CreateJobRequest", "CreateJobResponse", "JobStatusResponse", "AnalysisResultResponse", "ErrorResponse"):
        assert name in schemas
    assert client.get("/docs").status_code == 200


def test_http_errors_use_json_envelope_including_image_route(tmp_path: Path) -> None:
    client = _client(tmp_path / "jobs.sqlite3")
    mock_id = "JOB-20261030-000001"
    unauthenticated = TestClient(create_app(tmp_path / "unauth.sqlite3", api_token=TEST_TOKEN))
    unconfigured = TestClient(create_app(tmp_path / "unconfigured.sqlite3", api_token=""))
    responses = [
        client.post("/api/v1/analysis-jobs", json={}),
        client.get("/api/v1/analysis-jobs/no-such-job"),
        client.get(f"/api/v1/analysis-jobs/{mock_id}/images/no-such-candidate"),
        client.post(f"/api/v1/analysis-jobs/{mock_id}/cancel"),
        client.get("/no-such-route"),
        unauthenticated.get(f"/api/v1/analysis-jobs/{mock_id}/images/no-such-candidate"),
        unconfigured.get(f"/api/v1/analysis-jobs/{mock_id}/images/no-such-candidate"),
    ]
    assert [response.status_code for response in responses] == [422, 404, 404, 409, 404, 401, 503]
    for response in responses:
        assert response.headers["content-type"].startswith("application/json")
        assert set(response.json()) == {"error"}
        assert set(response.json()["error"]) == {"code", "message"}


def test_post_accepts_only_explicitly_allowed_internal_http_source(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "objects", allowed_http_hosts=frozenset({"minio"}))
    client = TestClient(create_app(tmp_path / "jobs.sqlite3", storage=storage, api_token=TEST_TOKEN))
    client.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
    request = {
        "monitoring_data_id": "sample",
        "att": {"url": "http://minio:9000/raw/att?sig=secret"},
        "atg": {"url": "http://minio:9000/raw/atg?sig=secret"},
    }
    assert client.post("/api/v1/analysis-jobs", json=request).status_code == 202
    request["atg"]["url"] = "http://external.example/raw/atg?sig=secret"
    denied = client.post("/api/v1/analysis-jobs", json=request)
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "SOURCE_URL_NOT_ALLOWED"
    assert "secret" not in denied.text


def test_production_hides_docs_but_keeps_health_and_version_public(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HEATPIPE_ENV", "production")
    client = TestClient(create_app(tmp_path / "jobs.sqlite3", api_token=TEST_TOKEN))
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404
    assert client.get("/health").status_code == 200
    assert client.get("/version").status_code == 200
    assert client.get("/api/v1/analysis-jobs/no-such-job").status_code == 401


def test_mock_request_status_and_pending_result(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite3"
    client = _client(db_path)
    request = {
        "monitoring_data_id": "sample",
        "att": {"object_key": "input/sample.att", "size_bytes": 770000000},
        "atg": {"url": "https://example.test/input/sample.atg"},
        "frame_range": {"start": 2, "end_exclusive": 5},
        "analysis_roi": {"x": 0, "y": 158, "width": 384, "height": 130},
    }
    accepted = client.post("/api/v1/analysis-jobs", json=request)
    assert accepted.status_code == 202
    receipt = accepted.json()
    assert receipt["status"] == "QUEUED"
    assert receipt["monitoring_data_id"] == "sample"
    job_id = receipt["job_id"]
    status = client.get(f"/api/v1/analysis-jobs/{job_id}").json()
    assert status["status"] == "QUEUED"
    assert status["queue_position"] == 0
    assert status["attempt"] == 1
    assert status["max_attempts"] == 3
    assert status["progress_percent"] == 0
    restarted = _client(db_path)
    assert restarted.get(f"/api/v1/analysis-jobs/{job_id}").json() == status
    restarted.app.state.job_store.update(job_id, JobStatus.VALIDATING, 1, 0, None, "Validating")
    assert restarted.get(f"/api/v1/analysis-jobs/{job_id}").json()["progress_percent"] == 1
    assert restarted.get(f"/api/v1/analysis-jobs/{job_id}").json()["queue_position"] is None
    assert client.get(f"/api/v1/analysis-jobs/{job_id}/results").json()["error"]["code"] == "RESULT_NOT_READY"
    assert client.get(f"/api/v1/analysis-jobs/{job_id}/results").status_code == 409

    restarted.app.state.job_store.update(
        job_id, JobStatus.FAILED, 1, 0, None, "Download failed",
        Message(code="DOWNLOAD_FAILED", message="Download failed"),
    )
    failed = client.get(f"/api/v1/analysis-jobs/{job_id}")
    assert failed.status_code == 200
    assert failed.json()["error"] == {
        "code": "DOWNLOAD_FAILED", "message": "Download failed", "retryable": True,
    }


def test_idempotency_key_recovers_lost_receipt_without_duplicate_job(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite3"
    client = _client(db_path)
    request = {
        "monitoring_data_id": "round-1",
        "att": {"url": "https://example.test/round-1.att?sig=old", "size_bytes": 100, "sha256": "a" * 64},
        "atg": {"url": "https://example.test/round-1.atg?sig=old", "size_bytes": 20, "sha256": "b" * 64},
    }
    headers = {"Idempotency-Key": "vti-round-1-request-1"}
    first = client.post("/api/v1/analysis-jobs", json=request, headers=headers)
    assert first.status_code == 202
    restarted = _client(db_path)
    refreshed = {
        **request,
        "att": {**request["att"], "url": "https://example.test/round-1.att?sig=new"},
        "atg": {**request["atg"], "url": "https://example.test/round-1.atg?sig=new"},
    }
    repeated = restarted.post("/api/v1/analysis-jobs", json=refreshed, headers=headers)
    assert repeated.status_code == 202
    assert repeated.json() == first.json()
    assert str(restarted.app.state.job_store.get_request(first.json()["job_id"]).att.url).endswith("sig=new")
    assert restarted.app.state.job_store.claim_next() == first.json()["job_id"]
    assert restarted.app.state.job_store.claim_next() is None
    in_progress = restarted.post("/api/v1/analysis-jobs", json=refreshed, headers=headers)
    assert in_progress.status_code == 202
    assert in_progress.json()["job_id"] == first.json()["job_id"]
    assert in_progress.json()["created_at"] == first.json()["created_at"]
    assert in_progress.json()["status"] == "VALIDATING"

    changed = {**request, "monitoring_data_id": "round-2"}
    conflict = restarted.post("/api/v1/analysis-jobs", json=changed, headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_CONFLICT"
    assert restarted.app.state.job_store.claim_next() is None

    new_key = restarted.post("/api/v1/analysis-jobs", json=changed,
                             headers={"Idempotency-Key": "vti-round-2-request-1"})
    assert new_key.status_code == 202
    assert new_key.json()["job_id"] != first.json()["job_id"]


def test_health_reports_worker_presence_and_queued_jobs(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite3"
    client = _client(db_path)
    idle = client.get("/health")
    assert idle.status_code == 200
    assert idle.json() == {
        "status": "ok", "worker_running": False,
        "worker_last_activity_at": None, "queued_job_count": 0,
    }
    accepted = client.post("/api/v1/analysis-jobs", json={
        "monitoring_data_id": "round-1",
        "att": {"url": "https://example.test/round-1.att"},
        "atg": {"url": "https://example.test/round-1.atg"},
    })
    assert accepted.status_code == 202
    assert client.get("/health").json()["queued_job_count"] == 1
    with running_worker_heartbeat(JobStore(db_path)):
        active = client.get("/health").json()
        assert active["worker_running"] is True
        assert active["worker_last_activity_at"] is not None
        assert active["queued_job_count"] == 1
    stopped = client.get("/health").json()
    assert stopped["worker_running"] is False
    assert stopped["worker_last_activity_at"] == active["worker_last_activity_at"]


def test_mock_sample_result_matches_existing_schema(tmp_path: Path) -> None:
    client = _client(tmp_path / "jobs.sqlite3")
    job_id = "JOB-20261030-000001"
    assert client.get(f"/api/v1/analysis-jobs/{job_id}").json()["status"] == "SUCCEEDED"
    response = client.get(f"/api/v1/analysis-jobs/{job_id}/results")
    assert response.status_code == 200
    docs = Path(__file__).resolve().parents[2] / "docs"
    schema = json.loads((docs / "schemas/analysis-result-v0.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(response.json())
    documented = json.loads((docs / "examples/analysis-result-v0.example.json").read_text(encoding="utf-8"))
    assert response.json() == documented
    assert response.json()["candidate_count"] == 1
    for candidate in response.json()["candidates"]:
        image = client.get(candidate["result_image"]["path"])
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"
        assert image.content.startswith(b"\x89PNG\r\n\x1a\n")
        assert candidate["result_image"]["size_bytes"] == len(image.content)
        assert candidate["result_image"]["sha256"] == hashlib.sha256(image.content).hexdigest()


def test_invalid_request_and_missing_job_use_common_error(tmp_path: Path) -> None:
    client = _client(tmp_path / "jobs.sqlite3")
    invalid = client.post("/api/v1/analysis-jobs", json={
        "monitoring_data_id": "sample", "att": {"object_key": "a", "url": "https://example.test/a"},
        "atg": {"object_key": "b"}, "frame_range": {"start": 3, "end_exclusive": 2},
    })
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_REQUEST"
    for path in ("/api/v1/analysis-jobs/no-such-job", "/api/v1/analysis-jobs/no-such-job/results"):
        missing = client.get(path)
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_monitoring_data_id_length_boundary(tmp_path: Path) -> None:
    client = _client(tmp_path / "jobs.sqlite3")
    request = {"monitoring_data_id": "a" * 128,
               "att": {"object_key": "input/a.att"}, "atg": {"object_key": "input/a.atg"}}
    assert client.post("/api/v1/analysis-jobs", json=request).status_code == 202
    request["monitoring_data_id"] += "a"
    invalid = client.post("/api/v1/analysis-jobs", json=request)
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_REQUEST"


def test_source_url_scheme_and_host_are_rejected_before_queueing(tmp_path: Path) -> None:
    client = _client(tmp_path / "jobs.sqlite3")
    request = {
        "monitoring_data_id": "sample",
        "att": {"url": "https://example.test/sample.att"},
        "atg": {"url": "https://example.test/sample.atg"},
    }
    for field, bad_url in (("att", "http://example.test/sample.att"),
                           ("atg", "https://untrusted.example/sample.atg")):
        invalid = {**request, field: {"url": bad_url}}
        response = client.post("/api/v1/analysis-jobs", json=invalid)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SOURCE_URL_NOT_ALLOWED"
        assert client.get("/health").json()["queued_job_count"] == 0
    assert client.post("/api/v1/analysis-jobs", json=request).status_code == 202


def test_queued_job_cancellation_is_immediate_and_idempotent(tmp_path: Path) -> None:
    client = _client(tmp_path / "jobs.sqlite3")
    receipt = client.post("/api/v1/analysis-jobs", json={
        "monitoring_data_id": "sample",
        "att": {"object_key": "inputs/sample.att"},
        "atg": {"object_key": "inputs/sample.atg"},
    }).json()
    path = f"/api/v1/analysis-jobs/{receipt['job_id']}"
    cancelled = client.post(path + "/cancel")
    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["cancel_requested"] is True
    assert cancelled.json()["completed_at"] is not None
    assert client.post(path + "/cancel").json() == cancelled.json()
    assert client.app.state.job_store.claim_next() is None
    assert client.get(path + "/results").status_code == 409
    assert client.get("/health").json()["queued_job_count"] == 0
    assert client.post("/api/v1/analysis-jobs/no-such-job/cancel").status_code == 404
    completed_mock = client.post("/api/v1/analysis-jobs/JOB-20261030-000001/cancel")
    assert completed_mock.status_code == 409
    assert completed_mock.json()["error"]["code"] == "CANCEL_NOT_ALLOWED"


def test_bearer_auth_covers_job_and_png_routes(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "jobs.sqlite3", api_token=TEST_TOKEN))
    for path in (
        "/api/v1/analysis-jobs/no-such-job",
        "/api/v1/analysis-jobs/no-such-job/results",
        "/api/v1/analysis-jobs/no-such-job/images/no-such-candidate",
    ):
        denied = client.get(path)
        assert denied.status_code == 401
        assert denied.headers["www-authenticate"] == "Bearer"
        assert denied.json()["error"]["code"] == "UNAUTHORIZED"
    assert client.post("/api/v1/analysis-jobs", json={}).status_code == 401
    denied_cancel = client.post("/api/v1/analysis-jobs/no-such-job/cancel")
    assert denied_cancel.status_code == 401
    denied = client.get("/api/v1/analysis-jobs/no-such-job", headers={"Authorization": "Bearer wrong"})
    assert denied.status_code == 401
    assert "wrong" not in denied.text
    assert client.get("/health").status_code == 200
    assert client.get("/docs").status_code == 200

    unconfigured = TestClient(create_app(tmp_path / "other.sqlite3", api_token=""))
    response = unconfigured.get("/api/v1/analysis-jobs/no-such-job")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_NOT_CONFIGURED"


def test_result_model_accepts_documented_success_and_batch_failure_shape() -> None:
    docs = Path(__file__).resolve().parents[2] / "docs"
    success = json.loads((docs / "examples/analysis-result-v0.example.json").read_text(encoding="utf-8"))
    AnalysisResultResponse.model_validate(success)
    assert success["processing"]["parameters"]["max_results"] == 50
    assert success["detected_candidate_count"] >= success["candidate_count"]
    for invalid_roi in (None, "missing"):
        invalid = json.loads(json.dumps(success))
        if invalid_roi == "missing":
            invalid["candidates"][0].pop("roi_temperature_c")
        else:
            invalid["candidates"][0]["roi_temperature_c"] = None
        try:
            AnalysisResultResponse.model_validate(invalid)
        except ValidationError:
            pass
        else:
            raise AssertionError("new result without ROI temperature accepted")
    failure = {**success, "status": "FAILED", "success": False, "processing": None,
               "detected_candidate_count": None, "candidate_count": 0, "candidates": [],
               "error": {"code": "ANALYSIS_FAILED", "message": "mock"}}
    AnalysisResultResponse.model_validate(failure)
    failure["candidate_count"] = 1
    try:
        AnalysisResultResponse.model_validate(failure)
    except ValidationError:
        pass
    else:
        raise AssertionError("inconsistent candidate count accepted")
