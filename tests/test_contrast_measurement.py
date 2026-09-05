import os
import sys
import unittest

import cv2
import numpy as np


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_ROOT = os.path.join(PROJECT_ROOT, "src")
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

from analysis.stripe_analyzer import StripeAnalyzer
from ui.main_window import MainWindow


def translated(image, dx, dy):
    matrix = np.float32([[1.0, 0.0, float(dx)], [0.0, 1.0, float(dy)]])
    return cv2.warpAffine(
        image.astype(np.float32),
        matrix,
        (image.shape[1], image.shape[0]),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REFLECT,
    )


def calibrated_frames(
    visibility,
    period,
    width=640,
    height=480,
    background_shift=(0.0, 0.0),
    extra_period=None,
    extra_visibility=0.0,
    noise_std=0.0,
    seed=1234,
):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:height, :width]
    texture = cv2.GaussianBlur(
        rng.normal(0.0, 1.0, size=(height, width)).astype(np.float32),
        (0, 0),
        3.0,
    )
    texture /= max(float(np.std(texture)), 1e-6)
    illumination = 95.0 * (
        1.0
        + 0.12 * xx / width
        + 0.08 * yy / height
        + 0.10 * texture
    )
    stripe_illumination = translated(
        illumination, background_shift[0], background_shift[1]
    )
    modulation = 1.0 + visibility * np.cos(2.0 * np.pi * xx / period)
    if extra_period is not None:
        modulation += extra_visibility * np.cos(2.0 * np.pi * xx / extra_period)
    dark_level = np.full((height, width), 12.0, dtype=np.float32)
    stripe = dark_level + stripe_illumination * modulation
    if noise_std:
        stripe += rng.normal(0.0, noise_std, size=stripe.shape)
    background = dark_level + illumination

    def frame(values):
        return np.clip(np.rint(values), 0, 255).astype(np.uint8)

    return frame(stripe), frame(background), frame(dark_level)


class ContrastMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = StripeAnalyzer()

    def test_expected_carrier_rejects_stronger_wrong_frequency(self):
        stripe, background, dark = calibrated_frames(
            0.20,
            40.0,
            extra_period=10.0,
            extra_visibility=0.32,
        )
        result = self.analyzer.calculate_calibrated_contrast(
            stripe,
            background,
            dark,
            {
                "contrast_expected_period_px": 40.0,
                "contrast_period_tolerance": 0.20,
                "contrast_register_background": False,
            },
        )

        self.assertLess(abs(result["estimated_period_px"] - 40.0), 1.5)
        self.assertLess(abs(result["gamma"] - 0.20), 0.03)
        self.assertTrue(result["reportable"])

    def test_background_translation_is_registered_before_flat_fielding(self):
        stripe, background, dark = calibrated_frames(
            0.24,
            36.0,
            background_shift=(3.0, -2.0),
            noise_std=0.6,
        )
        result = self.analyzer.calculate_calibrated_contrast(
            stripe,
            background,
            dark,
            {
                "contrast_expected_period_px": 36.0,
                "contrast_period_tolerance": 0.20,
                "contrast_max_registration_shift_px": 8.0,
            },
        )

        self.assertLess(abs(result["registration_dx_px"] - 3.0), 0.8)
        self.assertLess(abs(result["registration_dy_px"] + 2.0), 0.8)
        self.assertGreater(result["registration_response"], 0.1)
        self.assertLess(abs(result["gamma"] - 0.24), 0.035)
        self.assertTrue(result["reportable"])

    def test_roi_with_fewer_than_five_cycles_is_not_reportable(self):
        stripe, background, dark = calibrated_frames(
            0.30,
            110.0,
            width=440,
            height=320,
        )
        result = self.analyzer.calculate_calibrated_contrast(
            stripe,
            background,
            dark,
            {
                "contrast_expected_period_px": 110.0,
                "contrast_period_tolerance": 0.15,
                "contrast_register_background": False,
            },
        )

        self.assertFalse(result["reportable"])
        self.assertLess(result["cycles_across_roi"], 5.0)

    def test_no_fringe_is_not_reportable(self):
        stripe, background, dark = calibrated_frames(0.0, 40.0)
        result = self.analyzer.calculate_calibrated_contrast(
            stripe,
            background,
            dark,
            {
                "contrast_expected_period_px": 40.0,
                "contrast_period_tolerance": 0.20,
                "contrast_register_background": False,
            },
        )

        self.assertFalse(result["reportable"])
        self.assertLess(result["gamma"], 1e-4)

    def test_repeat_summary_reports_frame_to_frame_uncertainty(self):
        summary = self.analyzer.summarize_contrast_repeats(
            [0.198, 0.204, 0.201, 0.196, 0.203, 0.80]
        )

        self.assertEqual(summary["repeat_total_count"], 6)
        self.assertEqual(summary["repeat_used_count"], 5)
        self.assertLess(abs(summary["gamma_repeat_mean"] - 0.2004), 0.002)
        self.assertGreater(summary["gamma_repeat_std"], 0.0)
        self.assertLess(summary["gamma_ci95_low"], summary["gamma_repeat_mean"])
        self.assertGreater(summary["gamma_ci95_high"], summary["gamma_repeat_mean"])

    def test_camera_contrast_capture_prefers_raw_frame(self):
        class FakeWindow(object):
            def __init__(self):
                self.current_frame = np.full((8, 8), 200, dtype=np.uint8)
                self.current_camera_raw_frame = np.full((8, 8), 73, dtype=np.uint8)

            def _log(self, _message):
                pass

        captured = MainWindow._copy_current_frame_for_contrast(FakeWindow(), "条纹图")

        self.assertTrue(np.array_equal(captured, np.full((8, 8), 73, dtype=np.uint8)))


if __name__ == "__main__":
    unittest.main()
