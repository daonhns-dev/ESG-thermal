"""
차량이 같은 배관 구간을 여러 날짜/시각에 반복 통과한 데이터를 GPS 기준으로 묶어서,
"이전 통과엔 없던 열원 후보가 이번 통과에 새로 나타났는가"를 사람이 검토할 수 있는
타임라인으로 만든다.

각 세션의 metadata.json(frames[].timestamp/lat/lon, build_rgb_thermal_dataset.py가 생성)과
detect_hotspot_candidates.py가 만든 candidates.csv를 join한다. GPS는 위경도를 로컬
평면(미터) 좌표로 근사 변환(equirectangular, 이 위도 범위에서는 오차가 작아 충분)한 뒤
고정 크기 격자로 비닝한다. GPS 자체의 오차·차로 편차 때문에 같은 실제 위치도 통과마다
격자 경계에 걸려 다른 bin으로 갈리는 경우가 있을 수 있음 - --bin-size로 조정.

track_hotspot_candidates.py가 만든 tracks.csv가 있으면 같이 읽어서, likely_ground_fixed로
판정된 track이 덮는 프레임 구간에 in_ground_fixed_track=True를 붙인다 (동행 차량 등 프레임
간 움직임으로 걸러지는 후보는 제외되고 남은 것). 이게 있는 세션은 candidates.csv의 원시
후보 전체보다 이 플래그로 우선 검토하는 게 노이즈가 훨씬 적다.

출력은 long-format CSV라 pandas/Excel에서 bin_id로 group-by 해서 보는 걸 전제로 한다.

사용법 (heat-pipe/ 에서 실행):
    python scripts/experiments/match_gps_passes.py
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Optional

DEFAULT_DATASET_ROOT = r"E:\열수송관 모니터링 데이터\dataset"


def to_local_meters(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    dy = (lat - lat0) * 110540.0
    dx = (lon - lon0) * 111320.0 * math.cos(math.radians(lat0))
    return dx, dy


def load_candidates(session_dir: Path) -> dict:
    """frame_idx -> {n_candidates, max_edge_sharpness, max_circularity, has_sharp}"""
    csv_path = session_dir / "candidates.csv"
    per_frame = defaultdict(lambda: {"n_candidates": 0, "max_edge_sharpness": 0.0, "max_circularity": 0.0, "has_sharp": False})
    if not csv_path.exists():
        return per_frame
    with open(csv_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            idx = int(row["frame_idx"])
            entry = per_frame[idx]
            entry["n_candidates"] += 1
            entry["max_edge_sharpness"] = max(entry["max_edge_sharpness"], float(row["edge_sharpness"]))
            entry["max_circularity"] = max(entry["max_circularity"], float(row["circularity"]))
            if row["shape_label"] == "sharp":
                entry["has_sharp"] = True
    return per_frame


def load_ground_fixed_frames(session_dir: Path) -> set:
    """likely_ground_fixed track이 덮는 프레임 idx 집합 (start_frame~end_frame 구간 전체)"""
    csv_path = session_dir / "tracks.csv"
    frames = set()
    if not csv_path.exists():
        return frames
    with open(csv_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["likely_ground_fixed"] != "True":
                continue
            frames.update(range(int(row["start_frame"]), int(row["end_frame"]) + 1))
    return frames


def main():
    parser = argparse.ArgumentParser(description="세션 간 GPS 기준 반복 통과 매칭")
    parser.add_argument("--dataset-root", type=str, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--bin-size", type=float, default=5.0, help="공간 비닝 격자 크기 (미터)")
    parser.add_argument("--output", type=str, default=None, help="기본: <dataset-root>/gps_bin_timeline.csv")
    args = parser.parse_args()

    dataset_root = Path(args.dataset_root)
    session_dirs = sorted(p for p in dataset_root.iterdir() if p.is_dir() and (p / "metadata.json").exists())
    if not session_dirs:
        print(f"{dataset_root}에서 metadata.json 있는 세션을 못 찾음")
        return

    all_points = []  # (session, frame_idx, timestamp, lat, lon)
    for sd in session_dirs:
        meta = json.loads((sd / "metadata.json").read_text(encoding="utf-8"))
        for fr in meta.get("frames", []):
            if "lat" not in fr or "lon" not in fr:
                continue
            all_points.append((sd.name, fr["idx"], fr.get("timestamp"), fr["lat"], fr["lon"]))

    if not all_points:
        print("GPS 좌표가 있는 프레임이 하나도 없음 (.atg 없거나 매칭 안 됨)")
        return

    lat0 = sum(p[3] for p in all_points) / len(all_points)
    lon0 = sum(p[4] for p in all_points) / len(all_points)
    print(f"기준점(전체 평균): lat={lat0:.6f}, lon={lon0:.6f}, 총 {len(all_points)}개 GPS 프레임, {len(session_dirs)}개 세션")

    candidates_cache = {sd.name: load_candidates(sd) for sd in session_dirs}
    ground_fixed_cache = {sd.name: load_ground_fixed_frames(sd) for sd in session_dirs}
    n_with_tracks = sum(1 for v in ground_fixed_cache.values() if v)
    print(f"tracks.csv 있는 세션 중 likely_ground_fixed 프레임 있는 세션: {n_with_tracks}개")

    bins = defaultdict(list)
    for session, idx, ts, lat, lon in all_points:
        dx, dy = to_local_meters(lat, lon, lat0, lon0)
        bin_id = f"{int(dx // args.bin_size)}_{int(dy // args.bin_size)}"
        bins[bin_id].append((session, idx, ts, lat, lon))

    rows = []
    for bin_id, pts in bins.items():
        lat_c = sum(p[3] for p in pts) / len(pts)
        lon_c = sum(p[4] for p in pts) / len(pts)
        n_sessions = len(set(p[0] for p in pts))
        for session, idx, ts, lat, lon in sorted(pts, key=lambda p: (p[2] or "")):
            cand = candidates_cache[session].get(idx, {"n_candidates": 0, "max_edge_sharpness": 0.0, "max_circularity": 0.0, "has_sharp": False})
            in_ground_fixed = idx in ground_fixed_cache[session]
            rows.append({
                "bin_id": bin_id, "bin_lat": round(lat_c, 6), "bin_lon": round(lon_c, 6), "n_sessions_in_bin": n_sessions,
                "session": session, "frame_idx": idx, "timestamp": ts,
                "n_candidates": cand["n_candidates"], "has_sharp": cand["has_sharp"],
                "max_edge_sharpness": round(cand["max_edge_sharpness"], 3), "max_circularity": round(cand["max_circularity"], 3),
                "in_ground_fixed_track": in_ground_fixed,
            })

    out_path = Path(args.output) if args.output else dataset_root / "gps_bin_timeline.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    multi_pass_bins = {b for b, pts in bins.items() if len(set(p[0] for p in pts)) >= 2}
    ground_fixed_bin_ids = {r["bin_id"] for r in rows if r["in_ground_fixed_track"]}
    multi_pass_ground_fixed = multi_pass_bins & ground_fixed_bin_ids
    print(f"완료: {len(bins)}개 bin, 그중 2개 이상 세션이 겹치는 bin {len(multi_pass_bins)}개 -> {out_path}")
    print(f"  그중 in_ground_fixed_track(동행 차량 필터 통과) 후보가 있는 bin: {len(multi_pass_ground_fixed)}개")


if __name__ == "__main__":
    main()
