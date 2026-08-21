"""
detect_hotspot_candidates.py가 프레임별로 낸 후보(candidates.csv)를 프레임 간에 이어붙여
"같은 물체가 여러 프레임에 걸쳐 어떻게 움직였는지"를 본다.

동기: 차량 이동 촬영이라 프레임마다 다른 위치를 찍으므로, 진짜 지면(배관) 이상과 "화면에
잠깐 잡힌 다른 차량"을 구분하려면 물체 종류가 아니라 **움직임 패턴**으로 걸러야 한다.
  - 진짜 지면 위 고정된 지점: 차가 다가갈수록 프레임 안에서 아래로 내려가며 커지다가,
    화면 하단 경계에서 사라짐 (차가 그 위를 지나가므로).
  - 앞서가는 다른 차량: 대체로 비슷한 높이/크기를 유지하다가 방향을 틀거나 멀어지며
    사라짐 - 하단 경계로 "빠져나가는" 패턴이 아님.

방법: candidates.csv의 프레임별 후보를 그리디하게 프레임 간 매칭(중심좌표 거리 기준)해서
track(동일 물체로 추정되는 연속 구간)을 만들고, 각 track의 면적 증가율/하단 이탈 여부를
계산해 참고용 flag를 붙인다. 아직 검증 전 휴리스틱이라 track 몇 개를 실제 영상 프레임과
대조해서 사람이 판단해야 한다.

먼저 detect_hotspot_candidates.py로 candidates.csv를 만들어둬야 한다.

사용법 (heat-pipe/ 에서 실행):
    python scripts/experiments/track_hotspot_candidates.py --session 20260813_133232
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from pathlib import Path

import numpy as np

DEFAULT_DATASET_ROOT = r"E:\열수송관 모니터링 데이터\dataset"


def load_candidates(session_dir: Path):
    csv_path = session_dir / "candidates.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"{csv_path} 없음 - detect_hotspot_candidates.py로 먼저 만들어야 함")
    by_frame = defaultdict(list)
    with open(csv_path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["frame_idx"] = int(row["frame_idx"])
            for k in ("bbox_x0", "bbox_y0", "bbox_x1", "bbox_y1", "area_px"):
                row[k] = int(row[k])
            for k in ("edge_sharpness", "circularity", "mean_temp_c", "peak_temp_c"):
                row[k] = float(row[k])
            by_frame[row["frame_idx"]].append(row)
    return by_frame


def centroid(c):
    return ((c["bbox_x0"] + c["bbox_x1"]) / 2, (c["bbox_y0"] + c["bbox_y1"]) / 2)


def build_tracks(by_frame: dict, max_dist: float, max_gap: int):
    """그리디 최근접 매칭. 각 track은 [(frame_idx, candidate_dict), ...]"""
    active = []  # [{"points": [...], "last_frame": int}]
    finished = []
    frames = sorted(by_frame.keys())
    for f in frames:
        cands = list(by_frame[f])
        used = set()
        for track in active:
            last_frame, last_c = track["points"][-1]
            if f - last_frame > max_gap:
                continue  # 아래에서 gap 초과 track은 정리
            lx, ly = centroid(last_c)
            best, best_dist = None, max_dist
            for i, c in enumerate(cands):
                if i in used:
                    continue
                cx, cy = centroid(c)
                d = math.hypot(cx - lx, cy - ly)
                if d < best_dist:
                    best, best_dist = i, d
            if best is not None:
                track["points"].append((f, cands[best]))
                track["last_frame"] = f
                used.add(best)

        still_active = []
        for track in active:
            if f - track["last_frame"] > max_gap:
                finished.append(track)
            else:
                still_active.append(track)
        active = still_active

        for i, c in enumerate(cands):
            if i not in used:
                active.append({"points": [(f, c)], "last_frame": f})

    finished.extend(active)
    return finished


def summarize_track(track: dict, track_id: int, frame_height: int, bottom_margin: int, growth_thresh: float, min_circularity: float):
    points = track["points"]
    frames = [p[0] for p in points]
    first_c, last_c = points[0][1], points[-1][1]
    area0, area1 = first_c["area_px"], last_c["area_px"]
    y0 = centroid(first_c)[1]
    y1 = centroid(last_c)[1]
    exits_bottom = last_c["bbox_y1"] >= frame_height - 1 - bottom_margin
    growth = area1 / area0 if area0 > 0 else float("nan")
    mean_circularity = float(np.mean([p[1]["circularity"] for p in points]))
    # circularity 낮음(=길쭉하게 퍼진 모양) -> 도색 차선(자전거도로/속도표시) 페인트일 가능성이 높음(실측으로 확인).
    # 높음(=뭉친/원형에 가까움) -> 국소적인 지점형 이상일 가능성. min_circularity도 아직 미검증 시작값.
    likely_ground_fixed = (
        len(points) >= 3 and growth >= growth_thresh and exits_bottom and y1 > y0
        and mean_circularity >= min_circularity
    )
    return {
        "track_id": track_id,
        "n_frames": len(points),
        "start_frame": frames[0],
        "end_frame": frames[-1],
        "area_start": area0,
        "area_end": area1,
        "mean_circularity": round(mean_circularity, 3),
        "area_growth_ratio": round(growth, 3) if not math.isnan(growth) else "",
        "y_center_start": round(y0, 1),
        "y_center_end": round(y1, 1),
        "moved_down": y1 > y0,
        "exits_bottom": exits_bottom,
        "mean_peak_temp_c": round(float(np.mean([p[1]["peak_temp_c"] for p in points])), 2),
        "likely_ground_fixed": likely_ground_fixed,
    }


def main():
    parser = argparse.ArgumentParser(description="프레임 간 후보 추적 - 통과하며 사라지는(지면) vs 동행하는(다른 차량) 패턴 구분")
    parser.add_argument("--session", type=str, required=True)
    parser.add_argument("--dataset-root", type=str, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--max-dist", type=float, default=25.0, help="프레임 간 같은 물체로 볼 최대 중심좌표 이동거리(px, 미검증 시작값)")
    parser.add_argument("--max-gap", type=int, default=1, help="이 프레임 수 이상 안 이어지면 track 종료")
    parser.add_argument("--bottom-margin", type=int, default=5, help="이 픽셀 이내로 화면 하단에 닿으면 '하단 이탈'로 간주")
    parser.add_argument("--growth-thresh", type=float, default=1.3, help="likely_ground_fixed 판정용 면적 증가율 기준 (미검증)")
    parser.add_argument("--min-circularity", type=float, default=0.35, help="이보다 낮으면 길쭉한 도색 차선 등으로 보고 제외 (실측 확인: circularity 낮은 track 대부분이 자전거도로 도색/속도표시였음, 미검증 시작값)")
    args = parser.parse_args()

    session_dir = Path(args.dataset_root) / args.session
    by_frame = load_candidates(session_dir)
    total_cands = sum(len(v) for v in by_frame.values())
    print(f"[{args.session}] {len(by_frame)}개 프레임, 후보 {total_cands}개 로드")

    thermal_dir = session_dir / "thermal"
    sample_npy = next(thermal_dir.glob("*.npy"))
    frame_height = np.load(sample_npy).shape[0]

    tracks = build_tracks(by_frame, args.max_dist, args.max_gap)
    rows = [
        summarize_track(t, i, frame_height, args.bottom_margin, args.growth_thresh, args.min_circularity)
        for i, t in enumerate(tracks)
    ]
    rows.sort(key=lambda r: -r["n_frames"])

    out_csv = session_dir / "tracks.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        if rows:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    n_ground = sum(1 for r in rows if r["likely_ground_fixed"])
    n_multi = sum(1 for r in rows if r["n_frames"] >= 3)
    print(f"완료: track {len(rows)}개 (3프레임+ 지속 {n_multi}개, likely_ground_fixed {n_ground}개) -> {out_csv}")
    if n_ground:
        print("likely_ground_fixed track (frame 범위) - candidate_viz에서 이 구간 프레임들 대조해서 확인:")
        for r in rows:
            if r["likely_ground_fixed"]:
                print(f"  track {r['track_id']}: frame {r['start_frame']}~{r['end_frame']} "
                      f"(면적 {r['area_start']}->{r['area_end']}px, circularity {r['mean_circularity']}, peak {r['mean_peak_temp_c']}°C)")


if __name__ == "__main__":
    main()
