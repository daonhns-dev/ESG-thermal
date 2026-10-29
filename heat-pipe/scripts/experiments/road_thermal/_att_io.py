"""Load the existing raw-file parser without importing training dependencies."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PARSER_PATH = PROJECT_ROOT / "datasets" / "att_atg_io.py"
SPEC = importlib.util.spec_from_file_location("_road_thermal_att_atg_io", PARSER_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"Cannot load ATT/ATG parser: {PARSER_PATH}")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

ATG_RECORD_SIZE = MODULE.ATG_RECORD_SIZE
ATT_HEADER_SIZE = MODULE.ATT_HEADER_SIZE
att_frame_count = MODULE.att_frame_count
iter_att_frames_raw = MODULE.iter_att_frames_raw
read_atg = MODULE.read_atg
read_att_frame_raw = MODULE.read_att_frame_raw
read_att_header = MODULE.read_att_header
