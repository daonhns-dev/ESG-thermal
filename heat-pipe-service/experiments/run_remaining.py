"""Run E0, E1, and E2 on the remaining copied ATT/ATG pairs."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from heatpipe_service.analysis.io import att_frame_count, read_att_header
from compare_selection import compare
from summarize_e0 import summarize


PROJECT = Path(__file__).resolve().parents[1]
INPUT = PROJECT / "data" / "experiments" / "raw"
DONE = {"20260813_132634", "20260814_233331"}
METHODS = ("baseline", "nonzero_mad_fallback", "gap_one_missing")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def save_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def pairs() -> list[tuple[int, Path, Path]]:
    found = []
    for att in INPUT.glob("*/*.att"):
        if att.stem in DONE:
            continue
        atg = att.with_suffix(".atg")
        if not atg.is_file():
            raise FileNotFoundError(f"missing ATG: {atg}")
        frames = att_frame_count(att, read_att_header(att))
        found.append((frames, att, atg))
    if len(found) != 12:
        raise ValueError(f"expected 12 remaining pairs, found {len(found)}")
    return sorted(found, key=lambda row: (row[0], row[1].stem))


def write_report(path: Path, rows: list[dict], total: int, state: str) -> None:
    lines = [
        "# 남은 12쌍 순차 시험 진행 기록",
        "",
        f"상태: **{state}** · 완료 {len(rows)}/{total} 실행. 입력은 각 파일의 전체 ATT 프레임이다.",
        "E0·E1·E2는 모두 로컬 CUDA 점수맵을 사용한다. 현장 확인 라벨이 없으므로 후보 품질이나 누수 재현율은 계산하지 않는다.",
        "",
        "| 파일 | 방법 | 프레임 | 적격 트랙 | 반환 | 점수 99백분위 | 점수 최댓값 | 130℃ 후보 | 초 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['stem']}` | {row['method']} | {row['frames']} | "
            f"{row['eligible_tracks']} | {row['returned_candidates']} | "
            f"{row['score_p99']} | {row['score_max']} | "
            f"{row['candidate_peak_130']} | {row['elapsed_seconds']} |"
        )
    lines.extend([
        "",
        "각 실행 폴더의 `analysis_result.json`, `eligible_tracks.jsonl.gz`, "
        "`e0_diagnostics.json`을 확인할 수 있다. E0·E1에는 "
        "`e3_selection_comparison.json`도 있다. ATT·ATG 개수 차이는 "
        "`e0_diagnostics.json`의 `warnings`에 기록된다.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    planned = pairs()
    if args.dry_run:
        for frames, att, _ in planned:
            print(f"{att.stem}: {frames} frames × {len(METHODS)} methods")
        print(f"total: {sum(row[0] for row in planned)} frames × {len(METHODS)}")
        return 0

    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(exist_ok=True)
    status_path = root / "status.json"
    report_path = root / "REPORT.md"
    rows: list[dict] = []
    total = len(planned) * len(METHODS)
    environment = os.environ.copy()
    environment["CUPY_CACHE_DIR"] = str(PROJECT / "tmp" / "cupy-cache")

    for frames, att, atg in planned:
        for method in METHODS:
            run_dir = root / "runs" / att.stem / method
            log_path = root / "logs" / f"{att.stem}-{method}.log"
            summary_path = run_dir / "experiment_summary.json"
            if summary_path.is_file():
                existing = json.loads(summary_path.read_text(encoding="utf-8"))
                if existing["status"] != "SUCCEEDED":
                    raise RuntimeError(f"existing failed result: {run_dir}")
                print(f"REUSE {att.stem} {method}", flush=True)
            else:
                if run_dir.exists():
                    raise RuntimeError(f"existing incomplete result: {run_dir}")
                save_json(status_path, {
                    "state": "RUNNING", "updated_at": now(), "completed": len(rows),
                    "total": total, "current": {"stem": att.stem, "method": method},
                    "rows": rows,
                })
                print(f"START {len(rows)+1}/{total} {att.stem} {method}", flush=True)
                command = [
                    sys.executable, str(PROJECT / "experiments" / "compare_methods.py"),
                    "--att", str(att), "--atg", str(atg),
                    "--method", method, "--device", "cuda",
                    "--output", str(run_dir),
                ]
                with log_path.open("w", encoding="utf-8") as log:
                    completed = subprocess.run(
                        command, cwd=PROJECT, env=environment, stdout=log,
                        stderr=subprocess.STDOUT, check=False,
                    )
                if completed.returncode:
                    save_json(status_path, {
                        "state": "FAILED", "updated_at": now(),
                        "completed": len(rows), "total": total,
                        "failed": {"stem": att.stem, "method": method, "log": str(log_path)},
                        "rows": rows,
                    })
                    write_report(report_path, rows, total, "FAILED")
                    print(f"FAILED {att.stem} {method}: {log_path}", flush=True)
                    return 1

            try:
                diagnostic = summarize(run_dir)
                if method != "gap_one_missing":
                    compare(run_dir, 50)
                result = json.loads(summary_path.read_text(encoding="utf-8"))
            except Exception as error:
                save_json(status_path, {
                    "state": "FAILED", "updated_at": now(),
                    "completed": len(rows), "total": total,
                    "failed": {"stem": att.stem, "method": method,
                               "postprocess_error": repr(error)},
                    "rows": rows,
                })
                write_report(report_path, rows, total, "FAILED")
                raise
            score_quantiles = diagnostic["persistence_score_quantiles"] or {}
            row = {
                "stem": att.stem,
                "method": method,
                "frames": frames,
                "eligible_tracks": diagnostic["eligible_tracks"],
                "returned_candidates": diagnostic["returned_candidates"],
                "score_p99": score_quantiles.get("99"),
                "score_max": score_quantiles.get("100"),
                "candidate_peak_130": diagnostic["returned_candidate_peak_at_least_130_c"],
                "elapsed_seconds": diagnostic["elapsed_seconds"],
                "fallback_scale_count": result["fallback_scale_count"],
                "output": str(run_dir),
            }
            rows.append(row)
            save_json(status_path, {
                "state": "RUNNING", "updated_at": now(),
                "completed": len(rows), "total": total, "current": None,
                "rows": rows,
            })
            write_report(report_path, rows, total, "RUNNING")
            print(f"DONE {len(rows)}/{total} {att.stem} {method}", flush=True)

    save_json(status_path, {
        "state": "COMPLETE", "updated_at": now(), "completed": len(rows),
        "total": total, "current": None, "rows": rows,
    })
    write_report(report_path, rows, total, "COMPLETE")
    with (root / "summary.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"COMPLETE {len(rows)}/{total}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
