"""Checks for independent event-review sheet selection."""

from __future__ import annotations

import unittest

import numpy as np

from scripts.experiments.road_thermal.event_review import (
    frame_groups,
    thermal_display_range,
    validate_crop,
)


class EventReviewTests(unittest.TestCase):
    def test_frame_groups_include_every_frame_once(self) -> None:
        self.assertEqual(frame_groups(10, 17), [[10, 11, 12, 13, 14, 15], [16, 17]])

    def test_crop_bounds(self) -> None:
        self.assertEqual(validate_crop([20, 10, 40, 30], 80, 60), (20, 10, 40, 30))
        with self.assertRaises(ValueError):
            validate_crop([20, 10, 81, 30], 80, 60)

    def test_thermal_range_uses_selected_region_across_frames(self) -> None:
        first = np.full((8, 8), 1000, dtype=np.uint16)
        second = np.full((8, 8), 5000, dtype=np.uint16)
        first[2:6, 2:6] = 3000
        second[2:6, 2:6] = 3200
        lo, hi = thermal_display_range([first, second], (2, 2, 6, 6))
        self.assertEqual((lo, hi), (3000, 3200))


if __name__ == "__main__":
    unittest.main()
