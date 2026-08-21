"""
열화상 프레임 컬러맵 변환 + 유니코드 경로 이미지 저장.

scripts/build_rgb_thermal_dataset.py, scripts/render_thermal_video.py,
scripts/experiments/detect_hotspot_candidates.py, render_gps_bin_comparisons.py에서
각자 따로 정의돼 있던 걸 하나로 합침.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import cv2
import numpy as np


def imwrite_unicode(path: Path, img: np.ndarray, ext: str, params: Optional[list] = None) -> bool:
    """cv2.imwrite는 Windows에서 비-ASCII(한글 등) 경로를 못 씀 -> imencode + 일반 파일쓰기로 우회"""
    ok, buf = cv2.imencode(ext, img, params or [])
    if not ok:
        return False
    path.write_bytes(buf.tobytes())
    return True


def colorize(frame_c: np.ndarray, lo: Optional[float] = None, hi: Optional[float] = None) -> np.ndarray:
    """섭씨 온도 배열 -> JET 컬러맵 BGR 이미지. lo/hi 안 주면 프레임 자체 min/max로 정규화."""
    if lo is None:
        lo = float(frame_c.min())
    if hi is None:
        hi = float(frame_c.max())
    norm = np.clip((frame_c - lo) / max(hi - lo, 1e-6), 0, 1)
    return cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_JET)
