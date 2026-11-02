"""Mock HTTP contract checks; the batch engine remains independent."""

import json
from pathlib import Path

import jsonschema
from fastapi.testclient import TestClient
from pydantic import ValidationError

from heatpipe_service.api.app import create_app
from heatpipe_service.api.models import AnalysisResultResponse, JobStatus

TEST_TOKEN = "integration-test-token"


def _client(db_path: Path) -> TestClient:
    client = TestClient(create_app(db_path, api_token=TEST_TOKEN))
    client.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
    return client


def test_openapi_exposes_only_scoped_endpoints_and_models(tmp_path: Path) -> None:
    client = _client(tmp_path / "jobs.sqlite3")
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert set(paths) == {
        "/health", "/version", "/api/v1/analysis-jobs",
        "/api/v1/analysis-jobs/{job_id}",
        "/api/v1/analysis-jobs/{job_id}/results",
        "/api/v1/analysis-jobs/{job_id}/images/{candidate_id}",
    }
    schemas = response.json()["components"]["schemas"]
    image_get = paths["/api/v1/analysis-jobs/{job_id}/images/{candidate_id}"]["get"]
    assert "image/png" in image_get["responses"]["200"]["content"]
    assert image_get["security"] == [{"VtiBearerApiKey": []}]
    assert "security" not in paths["/health"]["get"]
    assert "VtiBearerApiKey" in response.json()["components"]["securitySchemes"]
    assert TEST_TOKEN not in response.text
    exported = Path(__file__).resolve().parents[2] / "docs" / "openapi.json"
    assert response.json() == json.loads(exported.read_text(encoding="utf-8"))
    for name in ("CreateJobRequest", "CreateJobResponse", "JobStatusResponse", "AnalysisResultResponse", "ErrorResponse"):
        assert name in schemas
    assert client.get("/docs").status_code == 200


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
    assert status["progress_percent"] == 0
    restarted = _client(db_path)
    assert restarted.get(f"/api/v1/analysis-jobs/{job_id}").json() == status
    restarted.app.state.job_store.update(job_id, JobStatus.VALIDATING, 1, 0, None, "Validating")
    assert restarted.get(f"/api/v1/analysis-jobs/{job_id}").json()["progress_percent"] == 1
    assert client.get(f"/api/v1/analysis-jobs/{job_id}/results").json()["error"]["code"] == "RESULT_NOT_READY"
    assert client.get(f"/api/v1/analysis-jobs/{job_id}/results").status_code == 409


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
    failure = {**success, "status": "FAILED", "success": False, "processing": None,
               "candidate_count": 0, "candidates": [], "error": {"code": "ANALYSIS_FAILED", "message": "mock"}}
    AnalysisResultResponse.model_validate(failure)
    failure["candidate_count"] = 1
    try:
        AnalysisResultResponse.model_validate(failure)
    except ValidationError:
        pass
    else:
        raise AssertionError("inconsistent candidate count accepted")
