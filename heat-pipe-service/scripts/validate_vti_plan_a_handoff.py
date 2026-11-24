"""Validate the handoff ZIP and run its synthetic path example locally on Linux."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from fastapi.testclient import TestClient
from heatpipe_service.api.app import create_app
from heatpipe_service.api.models import CreateJobRequest, CreateJobResponse, ErrorResponse, AnalysisResultResponse
from heatpipe_service.jobs.store import JobStore
from heatpipe_service.jobs.worker import JobWorker
from heatpipe_service.storage.backends import LocalStorage
from heatpipe_service.storage.shared_inputs import SHARED_ROOT_MARKER, SHARED_ROOT_MARKER_CONTENT

ROOT = Path(__file__).resolve().parents[1]


def main():
    archive = ROOT / "output" / "VTI_API_v1.1_방안A_20261119_송부용.zip"
    with TemporaryDirectory(prefix="heatpipe-handoff-") as directory:
        scratch = Path(directory)
        with ZipFile(archive) as package:
            names = package.namelist()
            assert all(not Path(n).is_absolute() and ".." not in Path(n).parts for n in names)
            assert not any(Path(n).name == ".env" for n in names)
            assert package.testzip() is None
            package.extractall(scratch)
        for name, digest in json.loads((scratch / "SHA256SUMS.json").read_text()).items():
            assert hashlib.sha256((scratch / name).read_bytes()).hexdigest() == digest
        for path in (scratch / "examples").glob("*.request.json"):
            CreateJobRequest.model_validate_json(path.read_text())
        CreateJobResponse.model_validate_json((scratch / "examples/int-003-202.response.json").read_text())
        ErrorResponse.model_validate_json((scratch / "examples/int-003-503.response.json").read_text())
        exported = json.loads((scratch / "openapi-v1.1.json").read_text())
        expected = json.loads((ROOT / "docs/openapi.json").read_text())
        normalized = deepcopy(exported)
        for key in ("x-contract-version", "x-issued-on", "x-deployment-status"):
            normalized["info"].pop(key)
        assert normalized == expected
        body = json.loads((scratch / "examples/int-003-path.request.json").read_text())
        shared = scratch / "samples"
        (shared / SHARED_ROOT_MARKER).write_bytes(SHARED_ROOT_MARKER_CONTENT)
        for name in ("att", "atg"):
            path = shared / body[name]["path"]
            assert path.stat().st_size == body[name]["size_bytes"]
            assert hashlib.sha256(path.read_bytes()).hexdigest() == body[name]["sha256"]
        previous = os.environ.get("HEATPIPE_SHARED_INPUT_ROOT")
        os.environ["HEATPIPE_SHARED_INPUT_ROOT"] = str(shared)
        try:
            db = scratch / "jobs.sqlite3"
            storage = LocalStorage(scratch / "objects")
            def forbidden_fetch(*args, **kwargs):
                raise AssertionError("Path handoff must never fetch original files")
            storage.fetch = forbidden_fetch
            client = TestClient(create_app(db, storage=storage, api_token="synthetic-handoff-only"))
            client.headers["Authorization"] = "Bearer synthetic-handoff-only"
            accepted = client.post("/api/v1/analysis-jobs", json=body)
            assert accepted.status_code == 202, accepted.text
            job_id = accepted.json()["job_id"]
            JobWorker(JobStore(db), storage, scratch / "work").run_once()
            status = client.get(f"/api/v1/analysis-jobs/{job_id}").json()
            assert status["status"] == "SUCCEEDED", status
            result = client.get(f"/api/v1/analysis-jobs/{job_id}/results").json()
            AnalysisResultResponse.model_validate(result)
            assert result["candidates"], "Synthetic handoff should produce a PNG candidate"
            for candidate in result["candidates"]:
                image = client.get(candidate["result_image"]["path"])
                assert image.status_code == 200
                assert hashlib.sha256(image.content).hexdigest() == candidate["result_image"]["sha256"]
            for name in ("att", "atg"):
                assert hashlib.sha256((shared / body[name]["path"]).read_bytes()).hexdigest() == body[name]["sha256"]
            print(f"PASS: archive, examples, OpenAPI, sample integrity, no-fetch workflow, {len(result['candidates'])} PNG(s), original preservation")
        finally:
            if previous is None:
                os.environ.pop("HEATPIPE_SHARED_INPUT_ROOT", None)
            else:
                os.environ["HEATPIPE_SHARED_INPUT_ROOT"] = previous


if __name__ == "__main__":
    main()
