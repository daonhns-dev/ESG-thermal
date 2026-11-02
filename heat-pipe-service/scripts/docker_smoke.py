"""Prepare a tiny ATT/ATG pair and verify the deployed HTTP job flow."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np


MONITORING_ID = "docker-smoke"
INPUT_DIR = Path("tmp/docker-smoke")


def prepare() -> None:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(20261102)
    yy, xx = np.ogrid[:64, :96]
    frames = []
    for index in range(4):
        frame = 30 + rng.normal(0, 0.08, (64, 96))
        frame[(xx - 48) ** 2 + (yy - (46 + index)) ** 2 <= 5**2] += 5
        frames.append(np.rint(frame * 100).astype("<u2").tobytes())
    att = INPUT_DIR / f"{MONITORING_ID}.att"
    atg = INPUT_DIR / f"{MONITORING_ID}.atg"
    att.write_bytes(struct.pack("<4H", 1, 0, 96, 64) + b"".join(frames))
    atg.write_bytes(b"".join(
        f"2026-08-13 14:02:{index:02d}".encode("ascii")
        + struct.pack("<i", index)
        + struct.pack("<dd", 37.5, 126.9)
        for index in range(4)
    ))
    print(json.dumps({"att": str(att), "atg": str(atg)}, ensure_ascii=False))


def _token() -> str:
    for line in Path(".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("HEATPIPE_API_TOKEN="):
            token = line.partition("=")[2].strip()
            if token:
                return token
    raise RuntimeError("HEATPIPE_API_TOKEN is missing from .env")


def verify(
    base_url: str, att: Path, atg: Path, monitoring_id: str, frames: int | None,
    timeout: int, source_url: str | None, url_ttl: int, att_size_adjust: int,
    expect_error: str | None,
) -> None:
    token = _token()
    base_url = base_url.rstrip("/")

    def request(method: str, path: str, payload: dict | None = None) -> tuple[int, bytes, str]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            base_url + path,
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return response.status, response.read(), response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"{method} {path}: HTTP {exc.code} {exc.read().decode()}") from exc

    def reference(file: Path, size_adjust: int = 0) -> dict:
        item = {"size_bytes": file.stat().st_size + size_adjust}
        if source_url is None:
            item["object_key"] = f"inputs/{file.name}"
        else:
            path = f"/files/{file.name}"
            expires = int(time.time()) + url_ttl
            signature = hmac.new(
                token.encode(), f"{path}\n{expires}".encode(), hashlib.sha256,
            ).hexdigest()
            item["url"] = f"{source_url.rstrip('/')}{path}?expires={expires}&signature={signature}"
        return item

    payload = {
        "monitoring_data_id": monitoring_id,
        "att": reference(att, att_size_adjust),
        "atg": reference(atg),
    }
    if frames is not None:
        payload["frame_range"] = {"start": 0, "end_exclusive": frames}
    accepted_code, accepted_body, _ = request("POST", "/api/v1/analysis-jobs", payload)
    assert accepted_code == 202, accepted_code
    job_id = json.loads(accepted_body)["job_id"]
    print(json.dumps({"accepted_job_id": job_id}, ensure_ascii=False), flush=True)
    deadline = time.monotonic() + timeout
    last_stage = None
    while True:
        status_code, status_body, _ = request("GET", f"/api/v1/analysis-jobs/{job_id}")
        assert status_code == 200
        status = json.loads(status_body)
        if status["status"] != last_stage:
            print(json.dumps({"status": status["status"], "progress_percent": status["progress_percent"]}), flush=True)
            last_stage = status["status"]
        if status["status"] in {"SUCCEEDED", "FAILED"}:
            break
        if time.monotonic() > deadline:
            raise TimeoutError(f"Job {job_id} remained {status['status']}")
        time.sleep(1)
    if expect_error:
        assert status["status"] == "FAILED", status
        assert status["error"]["code"] == expect_error, status
        print(json.dumps({"job_id": job_id, "status": "FAILED", "error_code": expect_error}))
        return
    assert status["status"] == "SUCCEEDED", status
    result_code, result_body, _ = request("GET", f"/api/v1/analysis-jobs/{job_id}/results")
    assert result_code == 200
    result = json.loads(result_body)
    assert result["candidate_count"] >= 1, result
    candidate = result["candidates"][0]
    assert candidate["roi_temperature_c"]["maximum"] >= candidate["roi_temperature_c"]["mean"]
    image_code, image_body, image_type = request("GET", candidate["result_image"]["path"])
    assert image_code == 200 and image_type.startswith("image/png") and image_body.startswith(b"\x89PNG")
    print(json.dumps({
        "job_id": job_id,
        "status": status["status"],
        "progress_percent": status["progress_percent"],
        "processed_frame_count": status["processed_frame_count"],
        "candidate_count": result["candidate_count"],
        "roi_temperature_c": candidate["roi_temperature_c"],
        "png_checked": True,
    }, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("--base-url", default="http://127.0.0.1:18001")
    parser.add_argument("--att", type=Path, default=INPUT_DIR / f"{MONITORING_ID}.att")
    parser.add_argument("--atg", type=Path, default=INPUT_DIR / f"{MONITORING_ID}.atg")
    parser.add_argument("--monitoring-id", default=MONITORING_ID)
    parser.add_argument("--frames", type=int)
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--source-url", help="HTTPS source base URL for signed ATT/ATG download")
    parser.add_argument("--url-ttl", type=int, default=3600)
    parser.add_argument("--att-size-adjust", type=int, default=0)
    parser.add_argument("--expect-error")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    else:
        frames = args.frames
        if frames is None and args.monitoring_id == MONITORING_ID:
            frames = 4
        verify(
            args.base_url, args.att, args.atg, args.monitoring_id, frames, args.timeout,
            args.source_url, args.url_ttl, args.att_size_adjust, args.expect_error,
        )
