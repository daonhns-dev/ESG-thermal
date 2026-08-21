"""
match_gps_passes.py가 만든 gps_bin_timeline.csv에서, 여러 세션(=여러 날짜 통과분)이
겹치는 GPS bin을 찾아 각 세션의 프레임들을 컬러맵+후보 박스로 그려 한 이미지로 저장한다.
지금까지 대화창에서 즉석으로 짜서 확인하던 걸 재사용 가능하게 스크립트로 뺀 것.

세션당 여러 프레임을 균등 간격으로 샘플링해서 한 행으로, bin마다 세션별로 한 행씩 쌓아
이미지 하나로 만든다. bin은 세션 간 후보 개수 차이가 큰 순서로 정렬해서 우선 저장한다
(차이가 클수록 "한쪽 날엔 있는데 다른 쪽엔 없는" 흥미로운 케이스일 가능성이 높음 - 그렇다고
반드시 진짜 이상이라는 뜻은 아니고, 다른 차량이 우연히 오래 잡힌 경우도 있었으니 직접 봐야 함).

먼저 match_gps_passes.py로 gps_bin_timeline.csv를 만들어둬야 한다.
candidates.csv가 있는 세션은 후보 박스도 같이 그려준다(없으면 컬러맵만).

사용법 (heat-pipe/ 에서 실행):
    python scripts/experiments/render_gps_bin_comparisons.py
    python scripts/experiments/render_gps_bin_comparisons.py --bin-id -116_452
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detect_hotspot_candidates import colorize, imwrite_unicode  # noqa: E402
from match_gps_passes import load_ground_fixed_frames  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from datasets.att_atg_io import TEMP_SCALE  # noqa: E402

DEFAULT_DATASET_ROOT = r"E:\열수송관 모니터링 데이터\dataset"
TILE_W, TILE_H = 220, 165


def date_of(session: str) -> str:
    return session.split("_")[0]


def load_timeline(dataset_root: Path):
    path = dataset_root / "gps_bin_timeline.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} 없음 - match_gps_passes.py 먼저 실행")
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


def render_tile(dataset_root: Path, session: str, frame_idx: int, timestamp: Optional[str], show_candidates: bool, ground_fixed_frames: set):
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
    label = f"{date} {frame_idx}"
    cv2.rectangle(img, (0, 0), (TILE_W, 18), (0, 0, 0), -1)
    cv2.putText(img, label, (3, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    return img


def main():
    parser = argparse.ArgumentParser(description="GPS bin별 세션 간 프레임 비교 이미지 생성")
    parser.add_argument("--dataset-root", type=str, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--bin-id", type=str, default=None, help="특정 bin 하나만 (기본: 상위 --top-n개 자동 선정)")
    parser.add_argument("--top-n", type=int, default=15, help="세션 간 후보 개수 차이가 큰 순서로 상위 N개 bin")
    parser.add_argument("--cross-day-only", action="store_true", default=True, help="다른 날짜끼리 겹치는 bin만 (기본 켜짐)")
    parser.add_argument("--all-bins", dest="cross_day_only", action="store_false", help="같은 날짜끼리 겹치는 bin도 포함")
    parser.add_argument("--frames-per-session", type=int, default=4)
    parser.add_argument("--no-candidates", dest="show_candidates", action="store_false", default=True, help="후보 박스 안 그림")
    parser.add_argument("--ground-fixed-only", action="store_true", help="track_hotspot_candidates.py의 likely_ground_fixed(동행 차량 아닌 것) 후보가 있는 bin만")
    parser.add_argument("--output-dir", type=str, default=None, help="기본: <dataset-root>/gps_bin_review")
    args = parser.parse_args()

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
            tiles = [render_tile(dataset_root, session, r["frame_idx"], r["timestamp"], args.show_candidates, gf_frames) for r in sample]
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


if __name__ == "__main__":
    main()
