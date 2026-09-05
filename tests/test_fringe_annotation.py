import math
import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from annotation.fringe_annotation import estimate_spacing


class EstimateSpacingTests(unittest.TestCase):
    def test_vertical_centerlines(self):
        lines = [
            {"order": order, "points": [[100 + order * 120, 20], [100 + order * 120, 500]]}
            for order in range(6)
        ]
        result = estimate_spacing(lines)
        self.assertAlmostEqual(result["spacing_px"], 120.0, places=3)
        self.assertAlmostEqual(result["orientation_deg"], 90.0, places=3)

    def test_skipped_order_keeps_true_spacing(self):
        lines = [
            {"order": 0, "points": [[100, 20], [100, 500]]},
            {"order": 1, "points": [[220, 20], [220, 500]]},
            {"order": 3, "points": [[460, 20], [460, 500]]},
            {"order": 4, "points": [[580, 20], [580, 500]]},
        ]
        result = estimate_spacing(lines)
        self.assertAlmostEqual(result["spacing_px"], 120.0, places=3)

    def test_tilted_parallel_centerlines(self):
        angle = math.radians(20.0)
        tangent = (math.cos(angle), math.sin(angle))
        normal = (-tangent[1], tangent[0])
        lines = []
        for order in range(5):
            center = (
                500.0 + normal[0] * order * 75.0,
                400.0 + normal[1] * order * 75.0,
            )
            points = [
                [center[0] - tangent[0] * 200.0, center[1] - tangent[1] * 200.0],
                [center[0] + tangent[0] * 200.0, center[1] + tangent[1] * 200.0],
            ]
            lines.append({"order": order, "points": points})
        result = estimate_spacing(lines)
        self.assertAlmostEqual(result["spacing_px"], 75.0, places=3)
        self.assertAlmostEqual(result["orientation_deg"], 20.0, places=3)

    def test_requires_two_usable_lines(self):
        self.assertIsNone(estimate_spacing([]))
        self.assertIsNone(estimate_spacing([{"order": 0, "points": [[1, 2]]}]))

    def test_duplicate_orders_are_rejected(self):
        lines = [
            {"order": 0, "points": [[0, 0], [0, 10]]},
            {"order": 0, "points": [[20, 0], [20, 10]]},
        ]
        with self.assertRaises(ValueError):
            estimate_spacing(lines)


if __name__ == "__main__":
    unittest.main()
