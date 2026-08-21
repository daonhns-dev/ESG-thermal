"""
GPS 기반 세션 간 비교 도구 모음 (구 match_gps_passes.py + render_gps_bin_comparisons.py +
export_gps_kml.py 통합). 서브커맨드 3개:

  match   차량이 같은 배관 구간을 여러 날짜/시각에 반복 통과한 데이터를 GPS 기준으로 묶어서
          "이전 통과엔 없던 열원 후보가 이번 통과에 새로 나타났는가"를 검토할 타임라인 생성.
          metadata.json(frames[].timestamp/lat/lon) + candidates.csv(+tracks.csv 있으면
          in_ground_fixed_track도 같이) -> gps_bin_timeline.csv

  review  match로 만든 gps_bin_timeline.csv에서 여러 세션이 겹치는 GPS bin을 찾아
          세션별 프레임을 컬러맵+후보 박스로 그려 이미지 하나로 저장 (육안 검토용).

  kml     세션 GPS 궤적을 Google Earth/My Maps에 바로 올릴 수 있는 KML로 내보내기.
          ESG 배관 지도가 좌표 없는 내부용 도면이라 텍스트로 대조가 안 될 때, 반대로
          우리 GPS를 실제 지도에 띄워 눈으로 비교하는 용도.

GPS(.atg)는 일반 민수용 수신기 실측값이라 정확도 5~30m + 1Hz 갱신(그 사이 직선 보간)
한계가 있음 - bin-size는 이 오차를 감안해서 잡을 것. 자세한 내용은
../../docs/EXPERIMENT_SUMMARY.md 참고.

사용법 (heat-pipe/ 에서 실행):
    python scripts/experiments/gps_tools.py match
    python scripts/experiments/gps_tools.py review --bin-id -116_452
    python scripts/experiments/gps_tools.py kml
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datasets.att_atg_io import TEMP_SCALE  # noqa: E402
from utils.thermal_viz import imwrite_unicode, colorize  # noqa: E402

DEFAULT_DATASET_ROOT = r"E:\열수송관 모니터링 데이터\dataset"
TILE_W, TILE_H = 220, 165


def date_of(session: str) -> str:
    return session.split("_")[0]


# ── 공용: candidates.csv / tracks.csv 로더 ────────────────────────────────────

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


# ── match ─────────────────────────────────────────────────────────────────────

def to_local_meters(lat: float, lon: float, lat0: float, lon0: float) -> tuple[float, float]:
    dy = (lat - lat0) * 110540.0
    dx = (lon - lon0) * 111320.0 * math.cos(math.radians(lat0))
    return dx, dy


def cmd_match(args):
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


# ── review ────────────────────────────────────────────────────────────────────

def load_timeline(dataset_root: Path):
    path = dataset_root / "gps_bin_timeline.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} 없음 - 먼저 'gps_tools.py match' 실행")
    by_bin = defaultdict(list)
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            row["frame_idx"] = int(row["frame_idx"])
            row["n_candidates"] = int(row["n_candidates"])
            row["in_ground_fixed_track"] = row.get("in_ground_fixed_track") == "True"
            by_bin[row["bin_id"]].append(row)
    return by_bin


def pick_sample_frames(rows: list, n: int) -> list:
    rows = sorted(rows, key=lambda r: r["frame_idx"])
    if len(rows) <= n:
        return rows
    idx = np.linspace(0, len(rows) - 1, n).round().astype(int)
    return [rows[i] for i in sorted(set(idx))]


def render_tile(dataset_root: Path, session: str, frame_idx: int, show_candidates: bool, ground_fixed_frames: set):
    npy_path = dataset_root / session / "thermal" / f"{frame_idx:06d}.npy"
    if not npy_path.exists():
        tile = np.zeros((TILE_H, TILE_W, 3), dtype=np.uint8)
        cv2.putText(tile, "missing", (10, TILE_H // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
        return tile
    raw = np.load(npy_path)
    grid_c = raw.astype(np.float32) / TEMP_SCALE
    lo, hi = np.percentile(grid_c, [1, 99])
    img = colorize(grid_c, lo, hi)

    has_raw_candidate = False
    if show_candidates:
        cand_path = dataset_root / session / "candidates.csv"
        if cand_path.exists():
            with open(cand_path, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if int(row["frame_idx"]) != frame_idx:
                        continue
                    has_raw_candidate = True
                    cv2.rectangle(
                        img,
                        (int(row["bbox_x0"]), int(row["bbox_y0"])),
                        (int(row["bbox_x1"]), int(row["bbox_y1"])),
                        (0, 0, 255), 1,
                    )

    img = cv2.resize(img, (TILE_W, TILE_H), interpolation=cv2.INTER_NEAREST)
    is_ground_fixed = frame_idx in ground_fixed_frames
    # 테두리: 초록 = 동행 차량 필터까지 통과한 후보 있음, 빨강 = 원시 후보만 있음, 없으면 테두리 없음
    if is_ground_fixed:
        cv2.rectangle(img, (0, 0), (TILE_W - 1, TILE_H - 1), (0, 255, 0), 3)
    elif has_raw_candidate:
        cv2.rectangle(img, (0, 0), (TILE_W - 1, TILE_H - 1), (0, 0, 200), 2)
    date = date_of(session)
    cv2.rectangle(img, (0, 0), (TILE_W, 18), (0, 0, 0), -1)
    cv2.putText(img, f"{date} {frame_idx}", (3, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    return img


def cmd_review(args):
    dataset_root = Path(args.dataset_root)
    out_dir = Path(args.output_dir) if args.output_dir else dataset_root / "gps_bin_review"
    out_dir.mkdir(exist_ok=True)

    by_bin = load_timeline(dataset_root)

    candidates = []
    for bin_id, rows in by_bin.items():
        sessions = set(r["session"] for r in rows)
        if len(sessions) < 2:
            continue
        if args.cross_day_only and len(set(date_of(s) for s in sessions)) < 2:
            continue
        has_ground_fixed = any(r["in_ground_fixed_track"] for r in rows)
        if args.ground_fixed_only and not has_ground_fixed:
            continue
        per_session_total = defaultdict(int)
        for r in rows:
            per_session_total[r["session"]] += r["n_candidates"]
        score = max(per_session_total.values()) - min(per_session_total.values())
        if has_ground_fixed:
            score += 100000  # 동행 차량 필터를 통과한 후보가 있는 bin을 항상 우선 정렬
        candidates.append((bin_id, rows, score))

    if args.bin_id:
        candidates = [c for c in candidates if c[0] == args.bin_id]
        if not candidates:
            print(f"bin {args.bin_id}: 조건에 맞는 다중 세션 데이터를 못 찾음")
            return
    else:
        candidates.sort(key=lambda c: -c[2])
        candidates = candidates[: args.top_n]

    print(f"{len(candidates)}개 bin 렌더링 -> {out_dir}")
    ground_fixed_cache = {}
    for bin_id, rows, score in candidates:
        by_session = defaultdict(list)
        for r in rows:
            by_session[r["session"]].append(r)

        session_rows = []
        for session in sorted(by_session, key=date_of):
            if session not in ground_fixed_cache:
                ground_fixed_cache[session] = load_ground_fixed_frames(dataset_root / session)
            gf_frames = ground_fixed_cache[session]
            sample = pick_sample_frames(by_session[session], args.frames_per_session)
            tiles = [render_tile(dataset_root, session, r["frame_idx"], args.show_candidates, gf_frames) for r in sample]
            while len(tiles) < args.frames_per_session:
                tiles.append(np.zeros((TILE_H, TILE_W, 3), dtype=np.uint8))
            session_rows.append(np.hstack(tiles))

        max_w = max(r.shape[1] for r in session_rows)
        rows_padded = []
        for r in session_rows:
            if r.shape[1] < max_w:
                pad = np.zeros((TILE_H, max_w - r.shape[1], 3), dtype=np.uint8)
                r = np.hstack([r, pad])
            rows_padded.append(r)
        grid = np.vstack(rows_padded)

        lat, lon = rows[0]["bin_lat"], rows[0]["bin_lon"]
        safe_bin = bin_id.replace("/", "_")
        out_path = out_dir / f"{safe_bin}_diff{score}_lat{lat}_lon{lon}.png"
        imwrite_unicode(out_path, grid, ".png")
        print(f"  {out_path.name} (세션 {len(by_session)}개, 후보차이 {score})")


# ── kml ───────────────────────────────────────────────────────────────────────

KML_COLORS = [  # KML은 AABBGGRR 순서(알파+파랑+초록+빨강)
    "ff0000ff", "ff00a5ff", "ff00ffff", "ff00ff00",
    "ffff0000", "ffff00ff", "ff808080", "ff0080ff",
]

KML_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
<Document>
<name>열수송관 모니터링 GPS 궤적</name>
"""

KML_FOOTER = "</Document>\n</kml>\n"


def session_placemark(session: str, points: list, color: str) -> str:
    coords = " ".join(f"{lon},{lat},0" for lat, lon in points)
    style_id = f"style_{session}"
    return f"""
<Style id="{style_id}">
  <LineStyle><color>{color}</color><width>3</width></LineStyle>
</Style>
<Placemark>
  <name>{session}</name>
  <styleUrl>#{style_id}</styleUrl>
  <LineString>
    <tessellate>1</tessellate>
    <coordinates>{coords}</coordinates>
  </LineString>
</Placemark>
<Placemark>
  <name>{session} 시작</name>
  <styleUrl>#{style_id}</styleUrl>
  <Point><coordinates>{points[0][1]},{points[0][0]},0</coordinates></Point>
</Placemark>
"""


def cmd_kml(args):
    dataset_root = Path(args.dataset_root)
    session_dirs = sorted(p for p in dataset_root.iterdir() if p.is_dir() and (p / "metadata.json").exists())
    if args.sessions:
        session_dirs = [p for p in session_dirs if p.name in args.sessions]

    body = []
    n_written = 0
    for i, sd in enumerate(session_dirs):
        meta = json.loads((sd / "metadata.json").read_text(encoding="utf-8"))
        points = [(f["lat"], f["lon"]) for f in meta.get("frames", []) if "lat" in f and "lon" in f]
        if len(points) < 2:
            continue
        # KML이 너무 무거워지지 않게 최대 300개 점으로 간략화
        if len(points) > 300:
            step = len(points) // 300
            points = points[::step]
        color = KML_COLORS[i % len(KML_COLORS)]
        body.append(session_placemark(sd.name, points, color))
        n_written += 1

    out_path = Path(args.output) if args.output else dataset_root / "gps_tracks.kml"
    out_path.write_text(KML_HEADER + "".join(body) + KML_FOOTER, encoding="utf-8")
    print(f"완료: {n_written}개 세션 궤적 -> {out_path}")
    print("Google Earth로 더블클릭해서 열거나, https://www.google.com/mymaps 에서 새 지도 만들고 '가져오기'로 업로드하면 됨")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GPS 기반 세션 간 비교 도구 (match / review / kml)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_match = sub.add_parser("match", help="세션 간 GPS 기준 반복 통과 매칭 -> gps_bin_timeline.csv")
    p_match.add_argument("--dataset-root", type=str, default=DEFAULT_DATASET_ROOT)
    p_match.add_argument("--bin-size", type=float, default=5.0, help="공간 비닝 격자 크기 (미터)")
    p_match.add_argument("--output", type=str, default=None, help="기본: <dataset-root>/gps_bin_timeline.csv")

    p_review = sub.add_parser("review", help="GPS bin별 세션 간 프레임 비교 이미지 생성")
    p_review.add_argument("--dataset-root", type=str, default=DEFAULT_DATASET_ROOT)
    p_review.add_argument("--bin-id", type=str, default=None, help="특정 bin 하나만 (기본: 상위 --top-n개 자동 선정)")
    p_review.add_argument("--top-n", type=int, default=15, help="세션 간 후보 개수 차이가 큰 순서로 상위 N개 bin")
    p_review.add_argument("--cross-day-only", action="store_true", default=True, help="다른 날짜끼리 겹치는 bin만 (기본 켜짐)")
    p_review.add_argument("--all-bins", dest="cross_day_only", action="store_false", help="같은 날짜끼리 겹치는 bin도 포함")
    p_review.add_argument("--frames-per-session", type=int, default=4)
    p_review.add_argument("--no-candidates", dest="show_candidates", action="store_false", default=True, help="후보 박스 안 그림")
    p_review.add_argument("--ground-fixed-only", action="store_true", help="likely_ground_fixed(동행 차량 아닌 것) 후보가 있는 bin만")
    p_review.add_argument("--output-dir", type=str, default=None, help="기본: <dataset-root>/gps_bin_review")

    p_kml = sub.add_parser("kml", help="세션별 GPS 궤적을 KML로 내보내기 (Google Earth/My Maps용)")
    p_kml.add_argument("--dataset-root", type=str, default=DEFAULT_DATASET_ROOT)
    p_kml.add_argument("--output", type=str, default=None, help="기본: <dataset-root>/gps_tracks.kml")
    p_kml.add_argument("--sessions", type=str, nargs="*", default=None, help="특정 세션만 (기본: 전체)")

    args = parser.parse_args()
    {"match": cmd_match, "review": cmd_review, "kml": cmd_kml}[args.command](args)


if __name__ == "__main__":
    main()
