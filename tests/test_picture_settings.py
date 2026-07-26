import os
import sys
import unittest

import numpy as np


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from analysis.stripe_analyzer import StripeAnalyzer
from calibration import PIXEL_SCALE_UM_PER_PX
from camera.picture_settings import (
    apply_picture_settings,
    default_picture_settings,
    normalize_picture_settings,
)


def stripe_frame(height=96, width=128, period=16):
    x = np.arange(width)
    profile = (127.5 + 100.0 * np.cos(2.0 * np.pi * x / period)).astype(np.uint8)
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = profile[None, :, None]
    return frame


class PictureSettingsTests(unittest.TestCase):
    def test_neutral_settings_preserve_frame_exactly(self):
        frame = stripe_frame()
        adjusted = apply_picture_settings(frame, default_picture_settings())
        self.assertTrue(np.array_equal(frame, adjusted))
        self.assertIsNot(frame, adjusted)

    def test_rgb_balance_uses_bgr_frame_order(self):
        frame = np.array([[[10, 20, 30]]], dtype=np.uint8)
        settings = default_picture_settings()
        settings.update({"balance_b": 2.0, "balance_g": 1.5, "balance_r": 0.5})
        adjusted = apply_picture_settings(frame, settings)
        self.assertEqual(adjusted.tolist(), [[[20, 30, 15]]])

    def test_settings_are_normalized_to_safe_ranges(self):
        settings = normalize_picture_settings(
            {"brightness": 999, "gamma": 0, "balance_r": -1, "digital_shift": -999}
        )
        self.assertEqual(settings["brightness"], 100)
        self.assertEqual(settings["gamma"], 0.1)
        self.assertEqual(settings["balance_r"], 0.1)
        self.assertEqual(settings["digital_shift"], -100)

    def test_neutral_pipeline_keeps_stripe_measurement(self):
        analyzer = StripeAnalyzer()
        frame = stripe_frame()
        adjusted = apply_picture_settings(frame, default_picture_settings())
        before = analyzer.analyze_frame(frame, {"pixel_scale": 1.0})
        after = analyzer.analyze_frame(adjusted, {"pixel_scale": 1.0})
        self.assertEqual(vars(before), vars(after))

    def test_stripe_measurement_uses_fixed_pixel_scale(self):
        analyzer = StripeAnalyzer()
        result = analyzer.analyze_frame(stripe_frame(), {"pixel_scale": 0.65})
        self.assertIsNotNone(result.stripe_spacing_px)
        self.assertAlmostEqual(
            result.stripe_spacing_um,
            result.stripe_spacing_px * PIXEL_SCALE_UM_PER_PX,
            delta=0.002,
        )

    def test_neutral_pipeline_keeps_contrast_measurement(self):
        analyzer = StripeAnalyzer()
        frame = stripe_frame(64, 64, 12)
        adjusted = apply_picture_settings(frame, default_picture_settings())
        dark = np.zeros_like(frame)
        background = np.full_like(frame, 180)
        before = analyzer.calculate_calibrated_contrast(frame, background, dark, {})
        after = analyzer.calculate_calibrated_contrast(adjusted, background, dark, {})
        self.assertEqual(before, after)
        self.assertEqual(after["status"], "ok")


if __name__ == "__main__":
    unittest.main()
