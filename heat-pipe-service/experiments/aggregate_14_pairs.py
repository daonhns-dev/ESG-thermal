"""Create a factual E0-E3 summary after all 14 pairs have finished."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from statistics import median
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT / "output" / "experiments"
INITIAL = {"20260813_132634", "20260814_233331"}
METHODS = {
    "E0": "baseline",
    "E1": "nonzero_mad_fallback",
    "E2": "gap_one_missing",
}


def directory(stem: str, experiment: str, overnight: Path) -> Path:
    if stem in INITIAL:
        return OUTPUT / f"{experiment.lower()}-{stem}-20261116"
    return overnight / "runs" / stem / METHODS[experiment]


def candidate_key(candidate: dict) -> tuple:
    box = candidate["candidate_bbox"]
    return (
        candidate["representative_frame"], box["x"], box["y"],
        box["width"], box["height"],
    )


def bridged_tracks(path: Path) -> tuple[int, int]:
    tracks = 0
    gaps = 0
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            item = json.loads(line)
            frames = [point[0] for point in item["points"]]
            count = sum(right - left == 2 for left, right in zip(frames, frames[1:]))
            tracks += count > 0
            gaps += count
    return tracks, gaps


def verify_run(path: Path) -> dict:
    summary = json.loads((path / "experiment_summary.json").read_text(encoding="utf-8"))
    result = json.loads((path / "analysis_result.json").read_text(encoding="utf-8"))
    with gzip.open(path / "eligible_tracks.jsonl.gz", "rt", encoding="utf-8") as stream:
        exported = sum(1 for _ in stream)
    with (path / "analysis_candidates.csv").open(newline="", encoding="utf-8-sig") as stream:
        csv_rows = sum(1 for _ in csv.DictReader(stream))
    images = len(list((path / "images").glob("*.png")))
    detected = result["detected_candidate_count"]
    returned = result["candidate_count"]
    if not (
        summary["status"] == result["status"] == "SUCCEEDED"
        and detected == summary["detected_candidate_count"] == summary["exported_track_count"] == exported
        and returned == summary["candidate_count"] == len(result["candidates"]) == csv_rows == images
    ):
        raise ValueError(f"Output counts do not agree: {path}")
    return {"eligible_tracks": exported, "returned_candidates": returned}


def aggregate(overnight: Path) -> list[dict]:
    status = json.loads((overnight / "status.json").read_text(encoding="utf-8"))
    if status["state"] != "COMPLETE" or status["completed"] != 36:
        raise ValueError("Overnight 12-pair run has not completed")
    stems = sorted(INITIAL | {row["stem"] for row in status["rows"]})
    if len(stems) != 14:
        raise ValueError(f"Expected 14 pairs; found {len(stems)}")
    rows = []
    for stem in stems:
        data = {}
        for experiment in METHODS:
            path = directory(stem, experiment, overnight)
            verification = verify_run(path)
            data[experiment] = {
                "path": path,
                "verification": verification,
                "result": json.loads((path / "analysis_result.json").read_text(encoding="utf-8")),
                "diagnostic": json.loads((path / "e0_diagnostics.json").read_text(encoding="utf-8")),
            }
        analyzed_frame_counts = {
            data[experiment]["diagnostic"]["frame_range"]["processed_frame_count"]
            for experiment in METHODS
        }
        att_frame_counts = {
            data[experiment]["diagnostic"]["frame_range"]["total_att_frames"]
            for experiment in METHODS
        }
        if len(analyzed_frame_counts) != 1 or len(att_frame_counts) != 1:
            raise ValueError(f"Frame counts differ across experiments: {stem}")
        selected = {
            experiment: {candidate_key(item) for item in data[experiment]["result"]["candidates"]}
            for experiment in METHODS
        }
        selections = {
            experiment: json.loads(
                (data[experiment]["path"] / "e3_selection_comparison.json").read_text(
                    encoding="utf-8"
                )
            )
            for experiment in ("E0", "E1")
        }
        bridged, gaps = bridged_tracks(data["E2"]["path"] / "eligible_tracks.jsonl.gz")
        row = {
            "stem": stem,
            "att_frames": att_frame_counts.pop(),
            "analyzed_frames": analyzed_frame_counts.pop(),
            "atg_count_mismatch": any(
                warning["code"] == "ATT_ATG_COUNT_MISMATCH"
                for warning in data["E0"]["result"]["warnings"]
            ),
            "e0_tracks": data["E0"]["diagnostic"]["eligible_tracks"],
            "e1_tracks": data["E1"]["diagnostic"]["eligible_tracks"],
            "e2_tracks": data["E2"]["diagnostic"]["eligible_tracks"],
            "e0_p99": data["E0"]["diagnostic"]["persistence_score_quantiles"]["99"],
            "e1_p99": data["E1"]["diagnostic"]["persistence_score_quantiles"]["99"],
            "e0_max": data["E0"]["diagnostic"]["persistence_score_quantiles"]["100"],
            "e1_max": data["E1"]["diagnostic"]["persistence_score_quantiles"]["100"],
            "e0_peak130": data["E0"]["diagnostic"]["returned_candidate_peak_at_least_130_c"],
            "e1_peak130": data["E1"]["diagnostic"]["returned_candidate_peak_at_least_130_c"],
            "e2_bridged_tracks": bridged,
            "e2_one_frame_gaps": gaps,
            "e0_e1_top50_overlap": len(selected["E0"] & selected["E1"]),
            "e0_e2_top50_overlap": len(selected["E0"] & selected["E2"]),
            "e0_global_occupied_intervals": sum(
                count > 0 for count in selections["E0"]["global_top"]["by_tenth"]
            ),
            "e1_global_occupied_intervals": sum(
                count > 0 for count in selections["E1"]["global_top"]["by_tenth"]
            ),
            "e0_global_balanced_overlap": selections["E0"]["shared_track_count"],
            "e1_global_balanced_overlap": selections["E1"]["shared_track_count"],
        }
        rows.append(row)
        row["skipped_frames"] = row["att_frames"] - row["analyzed_frames"]
    return rows


def write_report(rows: list[dict], path: Path) -> None:
    full_footage = [row for row in rows if row["skipped_frames"] == 0]
    lines = [
        "# 14쌍 E0~E3 재현성 시험",
        "",
        "14쌍의 전체 ATT 프레임을 읽고, 0값뿐인 빈 프레임을 건너뛴 뒤 현재 방식(E0), MAD 대체값(E1), 중간 1프레임 누락 허용(E2)으로 분석했다. E3은 E0·E1의 상한 전 트랙으로 선정 정책을 비교했다. 정답 라벨이 없으므로 누수 재현율·정밀도나 후보 품질 우열은 판단하지 않는다.",
        "",
        "| 파일 | ATT/분석 프레임 | E0/E1/E2 적격 트랙 | E0→E1 점수 p99 | E2 건너 연결 트랙 | E0·E1 상위 50 공통 | 점수순 50의 구간 수 E0→E1 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| `{row['stem']}` | {row['att_frames']:,}/{row['analyzed_frames']:,} | "
            f"{row['e0_tracks']:,}/{row['e1_tracks']:,}/{row['e2_tracks']:,} | "
            f"{row['e0_p99']:,.1f}→{row['e1_p99']:,.1f} | "
            f"{row['e2_bridged_tracks']:,} | {row['e0_e1_top50_overlap']} | "
            f"{row['e0_global_occupied_intervals']}→{row['e1_global_occupied_intervals']} |"
        )
    lines.extend([
        "",
        f"ATT 총 프레임: **{sum(row['att_frames'] for row in rows):,}**, "
        f"분석 프레임: **{sum(row['analyzed_frames'] for row in rows):,}**, "
        f"건너뛴 빈 프레임: **{sum(row['skipped_frames'] for row in rows):,}**. "
        f"ATT·ATG 개수 불일치: **{sum(row['atg_count_mismatch'] for row in rows)}쌍**.",
        "",
        "`20260812_203339`, `20260815_095210`, `20260817_094744`는 앞부분의 유효 프레임 뒤가 대부분 0값이다. "
        "이 세 파일의 후보 건수와 분포는 전체 촬영 구간을 대표하지 않는다. 원본 데이터 상태를 별도로 확인해야 한다.",
        "",
        f"전체 프레임이 유효한 **{len(full_footage)}쌍** 중 E0 점수 99백분위가 1,000을 넘는 파일은 "
        f"**{sum(row['e0_p99'] > 1000 for row in full_footage)}쌍**이다. "
        f"E1에서는 0쌍이다. 점수순 50건이 분포한 10구간의 중앙값은 "
        f"E0 **{median(row['e0_global_occupied_intervals'] for row in full_footage):g}구간**, "
        f"E1 **{median(row['e1_global_occupied_intervals'] for row in full_footage):g}구간**이다. "
        "이는 점수와 선정 결과의 변화이며 후보 정확도 향상을 뜻하지 않는다.",
        "",
        f"E2에서 실제 중간 프레임을 건너 연결한 트랙은 **{sum(row['e2_bridged_tracks'] for row in rows):,}건**이다. "
        f"E0와 E1의 상위 50 동일 프레임·사각형 후보는 파일당 중앙값 **{median(row['e0_e1_top50_overlap'] for row in rows):g}건**이다.",
        "",
        f"반환 50건 중 영역 최고 온도가 130℃ 이상인 후보는 E0 **{sum(row['e0_peak130'] for row in rows)}건**, "
        f"E1 **{sum(row['e1_peak130'] for row in rows)}건**이다. "
        "이 값만으로 장비 상한이나 관로 이상 여부를 판단하지 않는다.",
        "",
        "첫 두 쌍은 CPU, 나머지 12쌍은 로컬 CUDA로 실행했다. 같은 쌍의 E0·E1·E2는 같은 장치에서 비교했다. "
        "처리 속도는 장치가 다른 파일끼리 직접 비교하지 않는다.",
        "",
        "42개 실행 모두 적격 트랙 압축 파일, 결과 JSON, CSV, PNG의 건수를 대조했다. "
        "전체 수치는 `aggregate_14_pairs.json`과 각 실행 폴더의 JSON·PNG에서 확인할 수 있다.",
        "",
        "## 검토 순서",
        "",
        "1. 0값 프레임이 많은 세 ATT의 원본 상태와 촬영·저장 과정을 확인한다.",
        "2. `20260813_132634`와 `20260817_225314`의 130℃ 이상 반환 후보 PNG를 보고 열원의 종류를 기록한다. 130℃는 자동 제외 기준으로 쓰지 않는다.",
        "3. E0와 E1의 상위 후보가 크게 바뀐 파일, 특히 `20260816_120017`의 동일 후보 25/50건을 살펴본다.",
        "4. E2에서 연결된 트랙의 실제 노면 위치 일치 여부를 표본 검토한 뒤 추적 정책 변경을 판단한다.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("overnight", type=Path)
    args = parser.parse_args()
    rows = aggregate(args.overnight)
    target = args.overnight / "aggregate_14_pairs.json"
    target.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    report = args.overnight / "REPORT_14_PAIRS.md"
    write_report(rows, report)
    print(f"{len(rows)} pairs, {sum(row['att_frames'] for row in rows)} ATT frames: {report}")


if __name__ == "__main__":
    main()
