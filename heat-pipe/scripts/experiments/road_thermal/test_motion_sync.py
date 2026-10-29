"""Focused checks for the motion-offset pilot and rendered-frame mapping."""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from scripts.experiments.road_thermal.motion_sync import (
    offset_candidates,
    rendered_to_att_index,
    rgb_activity_suggestions,
    thermal_motion,
)


class MotionSyncTests(unittest.TestCase):
    def test_recovers_known_offset(self) -> None:
        rng = np.random.default_rng(17)
        rgb = rng.normal(size=300).astype(np.float32)
        thermal = np.full(300, np.nan, dtype=np.float32)
        thermal[4:] = rgb[:-4]
        result = offset_candidates(rgb, thermal, 20, 280, 12)
        self.assertEqual(result["top_distinct_candidates"][0]["thermal_minus_rgb_frames"], 4)
        self.assertGreater(result["top_distinct_candidates"][0]["correlation"], 0.99)
        nearby = result["nearby_offset_scores"]
        self.assertEqual([item["thermal_minus_rgb_frames"] for item in nearby], [2, 3, 4, 5, 6])
        self.assertEqual(len(result["all_offset_scores"]), 25)

    def test_rgb_activity_suggestions_are_separated(self) -> None:
        values = np.zeros(300, dtype=np.float32)
        values[[20, 21, 180]] = [8, 10, 7]
        suggestions = rgb_activity_suggestions(values, 0, 300, count=2, min_separation=100)
        self.assertEqual([item["rgb_idx"] for item in suggestions], [21, 180])

    def test_flat_signal_is_unconfirmed(self) -> None:
        flat = np.zeros(120, dtype=np.float32)
        result = offset_candidates(flat, flat, 0, 120, 5)
        self.assertEqual(result["status"], "no_usable_motion_signal")

    def test_zero_frames_keep_raw_indices_and_invalidate_neighbor_difference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.att"
            pattern = np.arange(1, 17, dtype="<u2").reshape(4, 4)
            with path.open("wb") as stream:
                stream.write(struct.pack("<4H", 1, 0, 4, 4))
                for frame in (pattern, np.zeros_like(pattern), pattern * 2, pattern * 3):
                    stream.write(frame.tobytes())
            energy, zeros = thermal_motion(path)
            self.assertEqual(zeros, [1])
            self.assertTrue(np.isnan(energy[:3]).all())
            self.assertTrue(np.isfinite(energy[3]))

    def test_rendered_frame_mapping_skips_multiple_zero_frames(self) -> None:
        mapping = [rendered_to_att_index(i, [0, 2, 5], 7) for i in range(4)]
        self.assertEqual(mapping, [1, 3, 4, 6])
        with self.assertRaises(ValueError):
            rendered_to_att_index(4, [0, 2, 5], 7)


if __name__ == "__main__":
    unittest.main()
