"""Checks for numbered frame-review sheets."""

from __future__ import annotations

import unittest

from scripts.experiments.road_thermal.frame_review import review_indices


class FrameReviewTests(unittest.TestCase):
    def test_default_segment_centers_include_anchor(self) -> None:
        windows = review_indices(120, None, radius=4, step=2)
        self.assertEqual(windows, [[16, 18, 20, 22, 24],
                                   [56, 58, 60, 62, 64],
                                   [96, 98, 100, 102, 104]])

    def test_window_clamps_at_boundaries(self) -> None:
        self.assertEqual(review_indices(10, [0, 9], radius=4, step=2),
                         [[0, 2, 4], [5, 7, 9]])

    def test_rejects_out_of_range_anchor(self) -> None:
        with self.assertRaises(ValueError):
            review_indices(10, [10], radius=4, step=2)


if __name__ == "__main__":
    unittest.main()
