"""
.avi(RGB) + .att(열화상 raw) + .atg(GPS/타임스탬프)를 세션 단위로 매칭해
RGB-Thermal paired 데이터셋을 만든다.

포맷 검증 내용은 datasets/att_atg_io.py 상단 docstring 참고.
.avi와 .att는 프레임 인덱스가 1:1로 대응한다는 것을 실측으로 확인했으므로
별도의 시간축 리샘플링 없이 같은 인덱스끼리 그대로 페어링한다.

출력 구조:
    <output>/<session_name>/
        rgb/000000.jpg, 000001.jpg, ...          (--thermal-only면 생략)
        thermal/000000.npy, 000001.npy, ...      (uint16 raw, 섭씨 = 값 / 100)
        visualization/000000.png, ...            (--viz-stride > 0 인 프레임만)
        metadata.json

사용 예:
    # 전체 스캔만 해보고 실제 추출은 안 함 (세션/프레임 수 확인용)
    python build_rgb_thermal_dataset.py --dry-run

    # 세션 1개만, 앞 20프레임만 빠르게 테스트
    python build_rgb_thermal_dataset.py --sessions 20260813_133232 --limit 20

    # 전체 세션, 실사용 빌드 (기본 stride=1 이라 용량이 크다 - 먼저 --dry-run으로 예상 용량 확인 권장)
    python build_rgb_thermal_dataset.py
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

import cv2
import numpy as np
from tqdm import tqdm

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from datasets.att_atg_io import (
    ATT_HEADER_SIZE,
    TEMP_SCALE,
    AttHeader,
    att_frame_count,
    read_att_frame_raw,
    read_att_header,
    read_atg,
)

DEFAULT_SOURCE = r"E:\열수송관 모니터링 데이터"
DEFAULT_OUTPUT = r"E:\열수송관 모니터링 데이터\dataset"


class Session:
    def __init__(self, att_path: Path):
        self.att_path = att_path
        self.name = att_path.stem
        self.avi_path = att_path.with_suffix(".avi")
        self.atg_path = att_path.with_suffix(".atg")

    @property
    def has_avi(self) -> bool:
        return self.avi_path.exists()

    @property
    def has_atg(self) -> bool:
        return self.atg_path.exists()


def find_sessions(source: Path, only: Optional[List[str]] = None) -> List[Session]:
    sessions = [Session(p) for p in sorted(source.rglob("*.att"))]
    if only:
        sessions = [s for s in sessions if s.name in only]
    return sessions


def imwrite_unicode(path: Path, img: np.ndarray, ext: str, params: Optional[list] = None) -> bool:
    """cv2.imwrite는 Windows에서 비-ASCII(한글 등) 경로를 못 씀 -> imencode + 일반 파일쓰기로 우회"""
    ok, buf = cv2.imencode(ext, img, params or [])
    if not ok:
        return False
    path.write_bytes(buf.tobytes())
    return True


def colorize(frame_c: np.ndarray) -> np.ndarray:
    """섭씨 온도 배열 -> JET 컬러맵 BGR 이미지 (프레임별 min-max 정규화)"""
    lo, hi = frame_c.min(), frame_c.max()
    norm = np.zeros_like(frame_c, dtype=np.uint8) if hi <= lo else (
        ((frame_c - lo) / (hi - lo) * 255).astype(np.uint8)
    )
    return cv2.applyColorMap(norm, cv2.COLORMAP_JET)


def process_session(
    session: Session,
    out_dir: Path,
    stride: int,
    viz_stride: int,
    limit: Optional[int],
    jpg_quality: int,
    thermal_only: bool,
    strict: bool,
) -> dict:
    header = read_att_header(session.att_path)
    n_att = att_frame_count(session.att_path, header)

    cap = None
    n_avi = None
    fps = None
    if session.has_avi:
        cap = cv2.VideoCapture(str(session.avi_path))
        if not cap.isOpened():
            raise RuntimeError(f"{session.avi_path} 열기 실패")
        n_avi = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        if n_avi != n_att:
            msg = f"[{session.name}] avi 프레임 수({n_avi}) != att 프레임 수({n_att})"
            if strict:
                raise RuntimeError(msg)
            print(f"경고: {msg} -> min({n_avi},{n_att})까지만 사용")
    elif not thermal_only:
        raise RuntimeError(f"{session.name}: .avi 없음 (--thermal-only로 열화상만 추출 가능)")

    n_pairs = min(n_avi, n_att) if n_avi is not None else n_att
    if limit is not None:
        n_pairs = min(n_pairs, limit)

    atg_records = read_atg(session.atg_path) if session.has_atg else []
    if session.has_atg and len(atg_records) != n_att:
        print(
            f"경고: [{session.name}] atg 레코드 수({len(atg_records)}) != att 프레임 수({n_att}) "
            "-> 녹화가 비정상 종료된 세션일 가능성 (있는 만큼만 매칭)"
        )

    rgb_dir = out_dir / "rgb"
    thermal_dir = out_dir / "thermal"
    viz_dir = out_dir / "visualization"
    if cap is not None:
        rgb_dir.mkdir(parents=True, exist_ok=True)
    thermal_dir.mkdir(parents=True, exist_ok=True)
    if viz_stride > 0:
        viz_dir.mkdir(parents=True, exist_ok=True)

    frame_meta = []
    indices = range(0, n_pairs, stride)
    next_target = 0
    for idx in tqdm(range(n_pairs), desc=session.name, leave=False):
        take_this = idx == next_target
        if cap is not None:
            ok, rgb = cap.read()
            if not ok:
                print(f"경고: [{session.name}] avi 프레임 {idx}에서 읽기 실패, 세션 조기 종료")
                break
        if not take_this:
            continue
        next_target += stride

        frame_raw = read_att_frame_raw(session.att_path, idx, header)
        fname = f"{idx:06d}"
        np.save(thermal_dir / f"{fname}.npy", frame_raw)

        if cap is not None:
            imwrite_unicode(rgb_dir / f"{fname}.jpg", rgb, ".jpg", [cv2.IMWRITE_JPEG_QUALITY, jpg_quality])

        if viz_stride > 0 and idx % viz_stride == 0:
            frame_c = frame_raw.astype(np.float32) / TEMP_SCALE
            imwrite_unicode(viz_dir / f"{fname}.png", colorize(frame_c), ".png")

        entry = {"idx": idx}
        if idx < len(atg_records):
            entry.update(asdict(atg_records[idx]))
        frame_meta.append(entry)

    if cap is not None:
        cap.release()

    return {
        "session": session.name,
        "source": {
            "att": str(session.att_path),
            "avi": str(session.avi_path) if session.has_avi else None,
            "atg": str(session.atg_path) if session.has_atg else None,
        },
        "width": header.width,
        "height": header.height,
        "att_header_version": header.version,
        "temp_scale": TEMP_SCALE,
        "temp_unit": "celsius = raw_uint16 / temp_scale",
        "fps": fps,
        "n_att_frames": n_att,
        "n_avi_frames": n_avi,
        "n_atg_records": len(atg_records),
        "n_pairs_extracted": len(frame_meta),
        "stride": stride,
        "frames": frame_meta,
    }


def main():
    parser = argparse.ArgumentParser(description="RGB(.avi) + Thermal(.att/.atg) 페어 데이터셋 빌더")
    parser.add_argument("--source", type=str, default=DEFAULT_SOURCE, help="원본 데이터 루트 (하위 폴더까지 재귀 탐색)")
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT, help="데이터셋 출력 경로")
    parser.add_argument("--sessions", type=str, nargs="*", default=None, help="특정 세션 이름만 처리 (예: 20260813_133232)")
    parser.add_argument("--stride", type=int, default=1, help="N프레임마다 1개 추출 (기본 1 = 전부)")
    parser.add_argument("--viz-stride", type=int, default=0, help="N프레임마다 컬러맵 시각화 PNG도 저장 (0=끔)")
    parser.add_argument("--limit", type=int, default=None, help="세션당 최대 프레임 수 (테스트용)")
    parser.add_argument("--jpg-quality", type=int, default=95)
    parser.add_argument("--thermal-only", action="store_true", help=".avi 없는 세션도 열화상만 추출")
    parser.add_argument("--strict", action="store_true", help="avi/att 프레임 수 불일치 시 에러로 중단")
    parser.add_argument("--dry-run", action="store_true", help="세션/프레임 수만 스캔해서 보여주고 종료 (파일 생성 안 함)")
    args = parser.parse_args()

    source = Path(args.source)
    output = Path(args.output)
    sessions = find_sessions(source, args.sessions)

    if not sessions:
        print(f"'{source}'에서 .att 파일을 찾지 못함")
        return

    if args.dry_run:
        total_frames = 0
        total_bytes = 0
        for s in sessions:
            try:
                header = read_att_header(s.att_path)
                n = att_frame_count(s.att_path, header)
            except ValueError as e:
                print(f"[스킵] {s.name}: {e}")
                continue
            status = "avi有" if s.has_avi else "avi無(RGB 불가)"
            print(f"{s.name}: frames={n} ({header.width}x{header.height}) {status}, atg={'有' if s.has_atg else '無'}")
            total_frames += n // max(args.stride, 1)
            total_bytes += (n // max(args.stride, 1)) * header.frame_bytes
        print(f"\n총 세션 {len(sessions)}개, stride={args.stride} 적용 시 예상 thermal 프레임 {total_frames}개, "
              f"thermal 원본 용량 약 {total_bytes / 1e9:.2f} GB (rgb jpg/visualization 별도)")
        return

    output.mkdir(parents=True, exist_ok=True)
    results = []
    for s in sessions:
        if not s.has_avi and not args.thermal_only:
            print(f"[스킵] {s.name}: .avi 없음 (--thermal-only 필요)")
            continue
        try:
            meta = process_session(
                s,
                output / s.name,
                stride=args.stride,
                viz_stride=args.viz_stride,
                limit=args.limit,
                jpg_quality=args.jpg_quality,
                thermal_only=args.thermal_only,
                strict=args.strict,
            )
        except (RuntimeError, ValueError) as e:
            print(f"[실패] {s.name}: {e}")
            continue
        with open(output / s.name / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        print(f"[완료] {s.name}: {meta['n_pairs_extracted']}프레임 -> {output / s.name}")
        results.append(meta)

    print(f"\n총 {len(results)}개 세션 처리 완료 -> {output}")


if __name__ == "__main__":
    main()
