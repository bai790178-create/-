import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_ROOT = os.path.join(PROJECT_ROOT, "src")
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

from analysis.stripe_analyzer import StripeAnalyzer


class CenterlineSpacingTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = StripeAnalyzer()

    def test_packaged_model_loads(self):
        self.assertTrue(self.analyzer.centerline_model._load(), self.analyzer.centerline_model.load_error)

    def test_known_positive_spacing(self):
        path = os.path.join(PROJECT_ROOT, "annotation_dataset", "20260727_110524", "image.png")
        if not os.path.exists(path):
            self.skipTest("local validation sample is unavailable")
        result = self.analyzer.analyze_file(path)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.measurement_method, "centerline_model")
        self.assertAlmostEqual(result.stripe_spacing_px, 198.7986, delta=3.0)
        self.assertGreaterEqual(len(result.stripe_centers_px), 3)

    def test_known_negative_is_rejected(self):
        path = os.path.join(PROJECT_ROOT, "annotation_dataset", "20260728_163743", "image.png")
        if not os.path.exists(path):
            self.skipTest("local validation sample is unavailable")
        result = self.analyzer.analyze_file(path)
        self.assertEqual(result.status, "no_stripe")
        self.assertIsNone(result.stripe_spacing_px)


if __name__ == "__main__":
    unittest.main()
