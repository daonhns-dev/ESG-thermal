"""
.att 열화상 raw 데이터를 컬러맵 입힌 mp4 영상으로 렌더링한다.

프레임마다 min-max로 정규화하면 화면이 깜빡이므로, 세션 전체(또는 --sample-every로
샘플링한 일부)를 먼저 훑어 percentile 기반 전역 온도 범위를 구한 뒤 그 범위로 고정 정규화한다.

주의: 여기서 만들어지는 mp4는 AI 학습용 원본이 아니라 육안 확인용 시각화다.
원본은 build_rgb_thermal_dataset.py로 뽑는 thermal/*.npy(raw uint16, 섭씨 = 값/100)를 써야 한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datasets.att_atg_io import TEMP_SCALE, att_frame_count, iter_att_frames_raw, read_att_header


def compute_global_range(att_path: Path, sample_every: int, pct_low: float, pct_high: float):
    header = read_att_header(att_path)
    samples = []
    for i, frame in enumerate(iter_att_frames_raw(att_path, header)):
        if i % sample_every == 0:
            samples.append(frame.ravel())
    all_vals = np.concatenate(samples)
    lo, hi = np.percentile(all_vals, [pct_low, pct_high])
    return header, float(lo), float(hi)


def main():
    parser = argparse.ArgumentParser(description=".att -> 컬러맵 열화상 mp4 렌더링")
    parser.add_argument("att", type=str, help=".att 파일 경로")
    parser.add_argument("--output", type=str, default=None, help="출력 mp4 경로 (기본: att와 같은 이름)")
    parser.add_argument("--fps", type=float, default=None, help="출력 fps (기본: 매칭되는 .avi의 fps, 없으면 15)")
    parser.add_argument("--sample-every", type=int, default=10, help="전역 온도 범위 계산 시 N프레임마다 샘플링")
    parser.add_argument("--pct-low", type=float, default=1.0)
    parser.add_argument("--pct-high", type=float, default=99.0)
    parser.add_argument("--upscale", type=int, default=2, help="원본 해상도 배율 (384x288은 작아서 기본 2배)")
    args = parser.parse_args()

    att_path = Path(args.att)
    out_path = Path(args.output) if args.output else att_path.with_suffix(".thermal.mp4")

    fps = args.fps
    if fps is None:
        avi_path = att_path.with_suffix(".avi")
        if avi_path.exists():
            cap = cv2.VideoCapture(str(avi_path))
            fps = cap.get(cv2.CAP_PROP_FPS) or 15.0
            cap.release()
        else:
            fps = 15.0

    print(f"[1/2] 전역 온도 범위 계산 중 (sample-every={args.sample_every})...")
    header, lo, hi = compute_global_range(att_path, args.sample_every, args.pct_low, args.pct_high)
    print(f"  범위: {lo/TEMP_SCALE:.2f}°C ~ {hi/TEMP_SCALE:.2f}°C (raw {lo:.0f}~{hi:.0f})")

    n = att_frame_count(att_path, header)
    w, h = header.width * args.upscale, header.height * args.upscale

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_path), fourcc, fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError(f"VideoWriter를 열 수 없음: {out_path}")

    print(f"[2/2] {n}프레임 렌더링 중 -> {out_path} ({w}x{h} @ {fps}fps)")
    span = max(hi - lo, 1e-6)
    for frame in tqdm(iter_att_frames_raw(att_path, header), total=n):
        norm = np.clip((frame.astype(np.float32) - lo) / span, 0, 1)
        img8 = (norm * 255).astype(np.uint8)
        colored = cv2.applyColorMap(img8, cv2.COLORMAP_JET)
        if args.upscale != 1:
            colored = cv2.resize(colored, (w, h), interpolation=cv2.INTER_NEAREST)
        writer.write(colored)
    writer.release()
    print(f"완료: {out_path}")


if __name__ == "__main__":
    main()
