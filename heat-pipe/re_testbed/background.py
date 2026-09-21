"""Background estimation and robust z-score helpers for TESTBED/ROI review."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.thermal_anomaly import compute_background, robust_z_score_map  # noqa: E402


__all__ = ["compute_background", "robust_z_score_map"]
