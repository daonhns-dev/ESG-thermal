from __future__ import annotations

import json
import struct
from pathlib import Path

import cv2
import jsonschema
import numpy as np

from heatpipe_service.analysis.io import read_att_frame_celsius
from heatpipe_service.jobs.batch import BatchConfig, BatchProgress, BatchRequest, run_batch, run_batch_safely


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
    progress: list[BatchProgress] = []

    artifacts = run_batch(
        BatchRequest(job_id="JOB-20261030-000001", monitoring_data_id="sample", att_path=att_path, atg_path=atg_path, output_dir=output_dir),
        BatchConfig(algorithms=("robust_zscore",), min_track_frames=2, min_area=10, max_results=3),
        progress.append,
    )
    assert [item.status for item in progress[:2]] == ["VALIDATING", "ANALYZING"]
    assert progress[-1].status == "SUCCEEDED"
    assert progress[-1].progress_percent == 100
    assert progress[-1].total_frame_count == 5
    assert [item.progress_percent for item in progress] == sorted(item.progress_percent for item in progress)

    payload = json.loads(artifacts.result_json.read_text(encoding="utf-8"))
    schema_path = Path(__file__).resolve().parents[2] / "docs" / "schemas" / "analysis-result-v0.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(payload)

    assert payload["status"] == "SUCCEEDED"
    assert payload["job_id"] == "JOB-20261030-000001"
    assert payload["monitoring_data_id"] == "sample"
    assert payload["candidate_count"] >= 1
    assert payload["candidate_count"] == len(payload["candidates"])
    assert payload["processing"]["analysis_roi"] == {"x": 32, "y": 32, "width": 32, "height": 32}
    assert payload["warnings"][0]["code"] == "ATT_ATG_COUNT_MISMATCH"
    assert artifacts.candidates_csv.read_text(encoding="utf-8-sig").startswith("candidate_id,")

    candidate = payload["candidates"][0]
    assert candidate["candidate_id"].startswith("JOB-20261030-000001-CAND-")
    if candidate["representative_frame"] < 3:
        assert candidate["captured_at"].endswith("+09:00")
    else:
        assert candidate["captured_at"] is None
        assert candidate["gps"] is None
    assert candidate["temperature_c"]["delta_from_background"] > 4
    representative = read_att_frame_celsius(att_path, candidate["representative_frame"])
    roi_pixels = representative[32:64, 32:64]
    roi_temperature = candidate["roi_temperature_c"]
    assert roi_temperature["minimum"] == round(float(np.min(roi_pixels)), 2)
    assert roi_temperature["mean"] == round(float(np.mean(roi_pixels)), 2)
    assert roi_temperature["maximum"] == round(float(np.max(roi_pixels)), 2)
    assert roi_temperature["max_minus_min"] == round(
        roi_temperature["maximum"] - roi_temperature["minimum"], 2
    )
    assert roi_temperature["max_minus_mean"] == round(
        roi_temperature["maximum"] - roi_temperature["mean"], 2
    )
    image_path = output_dir / candidate["result_image"]["path"]
    image = cv2.imdecode(np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert image.shape == (64, 96, 3)
    assert image[32, 32, 2] > 200  # red analysis ROI border
    assert candidate["candidate_bbox"]["x"] >= 32
    assert candidate["candidate_bbox"]["x"] + candidate["candidate_bbox"]["width"] <= 64
    assert candidate["result_image"]["grid"] == {"rows": 2, "columns": 3}


def test_default_roi_excludes_other_grid_cells_and_rejects_roi_outside_cell(tmp_path: Path) -> None:
    yy, xx = np.ogrid[:64, :96]
    frame = np.full((64, 96), 30, dtype=np.float32)
    frame[(xx - 16) ** 2 + (yy - 48) ** 2 <= 5**2] += 5  # bottom-left cell only
    att_path = tmp_path / "left.att"
    atg_path = tmp_path / "left.atg"
    _write_att(att_path, [frame] * 3)
    _write_atg(atg_path, 3)
    config = BatchConfig(algorithms=("robust_zscore",), min_track_frames=2, min_area=10)

    default = run_batch(
        BatchRequest("JOB-20261102-000010", "left", att_path, atg_path, tmp_path / "default"),
        config,
    )
    default_result = json.loads(default.result_json.read_text(encoding="utf-8"))
    assert default_result["processing"]["analysis_roi"] == {
        "x": 32, "y": 32, "width": 32, "height": 32,
    }
    assert default_result["candidate_count"] == 0

    custom = run_batch_safely(
        BatchRequest(
            "JOB-20261102-000011", "left", att_path, atg_path,
            tmp_path / "custom", analysis_roi=(0, 32, 32, 64),
        ),
        config,
    )
    custom_result = json.loads(custom.result_json.read_text(encoding="utf-8"))
    assert custom.success is False
    assert custom_result["error"]["code"] == "INVALID_ANALYSIS_REQUEST"
    assert "bottom-center" in custom_result["error"]["message"]

    inside = run_batch(
        BatchRequest(
            "JOB-20261102-000012", "left", att_path, atg_path,
            tmp_path / "inside", analysis_roi=(40, 40, 60, 60),
        ),
        config,
    )
    inside_result = json.loads(inside.result_json.read_text(encoding="utf-8"))
    assert inside_result["processing"]["analysis_roi"] == {
        "x": 40, "y": 40, "width": 20, "height": 20,
    }


def test_safe_batch_writes_schema_valid_failure_result(tmp_path: Path) -> None:
    output_dir = tmp_path / "failed-result"
    progress: list[BatchProgress] = []
    artifacts = run_batch_safely(
        BatchRequest(
            job_id="JOB-20261030-000001",
            monitoring_data_id="missing",
            att_path=tmp_path / "missing.att",
            atg_path=tmp_path / "missing.atg",
            output_dir=output_dir,
        ),
        on_progress=progress.append,
    )
    assert progress[-1].status == "FAILED"
    assert progress[-1].error_code == "INPUT_FILE_NOT_FOUND"

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
