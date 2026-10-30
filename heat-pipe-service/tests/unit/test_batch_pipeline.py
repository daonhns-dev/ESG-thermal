from __future__ import annotations

import json
import struct
from pathlib import Path

import cv2
import jsonschema
import numpy as np

from heatpipe_service.jobs.batch import BatchConfig, BatchRequest, run_batch, run_batch_safely


def _write_att(path: Path, frames_c: list[np.ndarray]) -> None:
    height, width = frames_c[0].shape
    payload = struct.pack("<4H", 1, 0, width, height)
    payload += b"".join(
        np.rint(frame * 100).astype("<u2").tobytes() for frame in frames_c
    )
    path.write_bytes(payload)


def _write_atg(path: Path, count: int) -> None:
    records = []
    for index in range(count):
        timestamp = f"2026-08-13 14:02:{index:02d}".encode("ascii")
        records.append(
            timestamp
            + struct.pack("<i", index)
            + struct.pack("<dd", 37.5 + index * 0.0001, 126.9 + index * 0.0001)
        )
    path.write_bytes(b"".join(records))


def test_batch_writes_schema_valid_json_csv_and_png(tmp_path: Path) -> None:
    rng = np.random.default_rng(20261029)
    frames = []
    yy, xx = np.ogrid[:64, :96]
    for index in range(5):
        frame = 30 + rng.normal(0, 0.08, (64, 96))
        frame[(xx - 48) ** 2 + (yy - (46 + index)) ** 2 <= 5**2] += 5
        frames.append(frame.astype(np.float32))

    att_path = tmp_path / "sample.att"
    atg_path = tmp_path / "sample.atg"
    _write_att(att_path, frames)
    _write_atg(atg_path, 3)
    output_dir = tmp_path / "result"

    artifacts = run_batch(
        BatchRequest(job_id="JOB-20261030-000001", monitoring_data_id="sample", att_path=att_path, atg_path=atg_path, output_dir=output_dir),
        BatchConfig(algorithms=("robust_zscore",), min_track_frames=2, min_area=10, max_results=3),
    )

    payload = json.loads(artifacts.result_json.read_text(encoding="utf-8"))
    schema_path = Path(__file__).resolve().parents[2] / "docs" / "schemas" / "analysis-result-v0.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(payload)

    assert payload["status"] == "SUCCEEDED"
    assert payload["job_id"] == "JOB-20261030-000001"
    assert payload["monitoring_data_id"] == "sample"
    assert payload["candidate_count"] >= 1
    assert payload["candidate_count"] == len(payload["candidates"])
    assert payload["warnings"][0]["code"] == "ATT_ATG_COUNT_MISMATCH"
    assert artifacts.candidates_csv.read_text(encoding="utf-8-sig").startswith("candidate_id,")

    candidate = payload["candidates"][0]
    assert candidate["candidate_id"].startswith("JOB-20261030-000001-CAND-")
    assert candidate["captured_at"] is not None
    assert candidate["captured_at"].endswith("+09:00")
    assert candidate["temperature_c"]["delta_from_background"] > 4
    image_path = output_dir / candidate["result_image"]["path"]
    image = cv2.imdecode(np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image.shape == (64, 96, 3)
    assert candidate["result_image"]["grid"] == {"rows": 2, "columns": 3}


def test_safe_batch_writes_schema_valid_failure_result(tmp_path: Path) -> None:
    output_dir = tmp_path / "failed-result"
    artifacts = run_batch_safely(
        BatchRequest(
            job_id="JOB-20261030-000001",
            monitoring_data_id="missing",
            att_path=tmp_path / "missing.att",
            atg_path=tmp_path / "missing.atg",
            output_dir=output_dir,
        )
    )

    payload = json.loads(artifacts.result_json.read_text(encoding="utf-8"))
    schema_path = Path(__file__).resolve().parents[2] / "docs" / "schemas" / "analysis-result-v0.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(payload)

    assert artifacts.success is False
    assert payload["job_id"] == "JOB-20261030-000001"
    assert payload["status"] == "FAILED"
    assert payload["processing"] is None
    assert payload["candidate_count"] == 0
    assert payload["error"]["code"] == "INPUT_FILE_NOT_FOUND"


def test_published_example_matches_schema() -> None:
    docs_dir = Path(__file__).resolve().parents[2] / "docs"
    schema = json.loads(
        (docs_dir / "schemas" / "analysis-result-v0.schema.json").read_text(encoding="utf-8")
    )
    example = json.loads(
        (docs_dir / "examples" / "analysis-result-v0.example.json").read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(example)
