import math
import os


class AnalysisResult(object):
    def __init__(
        self,
        stripe_spacing_px=None,
        stripe_spacing_um=None,
        bright_spacing_px=None,
        dark_spacing_px=None,
        clarity_score=0.0,
        confidence=0.0,
        status="no_stripe",
        profile=None,
        orientation="vertical",
        message="",
    ):
        self.stripe_spacing_px = stripe_spacing_px
        self.stripe_spacing_um = stripe_spacing_um
        self.bright_spacing_px = bright_spacing_px
        self.dark_spacing_px = dark_spacing_px
        self.clarity_score = clarity_score
        self.confidence = confidence
        self.status = status
        self.profile = profile or []
        self.orientation = orientation
        self.message = message

    def to_dict(self):
        return {
            "stripe_spacing_px": self.stripe_spacing_px,
            "stripe_spacing_um": self.stripe_spacing_um,
            "bright_spacing_px": self.bright_spacing_px,
            "dark_spacing_px": self.dark_spacing_px,
            "clarity_score": self.clarity_score,
            "confidence": self.confidence,
            "status": self.status,
            "orientation": self.orientation,
            "message": self.message,
            "profile": self.profile,
        }


class StripeAnalyzer(object):
    def __init__(self):
        self.cv2 = None
        self.np = None
        self.Image = None
        self.dependency_error = ""
        try:
            import numpy as np

            self.np = np
        except Exception as exc:
            self.dependency_error = str(exc)
        try:
            import cv2

            self.cv2 = cv2
        except Exception:
            self.cv2 = None
        try:
            from PIL import Image

            self.Image = Image
        except Exception:
            self.Image = None

    def analyze_file(self, path, options=None):
        if not self._ready():
            return self._dependency_result()
        if not path or not os.path.exists(path):
            return AnalysisResult(status="missing_file", message="图像文件不存在。")

        frame = self.read_image(path)
        if frame is None:
            return AnalysisResult(status="invalid_image", message="图像文件无法读取。")
        return self.analyze_frame(frame, options)

    def read_image(self, path):
        if self.cv2 is not None:
            frame = self.cv2.imdecode(self.np.fromfile(path, dtype=self.np.uint8), self.cv2.IMREAD_COLOR)
            if frame is not None:
                return frame
        if self.Image is None:
            return None
        image = self.Image.open(path).convert("RGB")
        return self.np.array(image)

    def analyze_frame(self, frame, options=None):
        if not self._ready():
            return self._dependency_result()
        if frame is None:
            return AnalysisResult(status="no_image", message="没有可分析图像。")

        options = options or {}
        pixel_scale = float(options.get("pixel_scale") or 1.0)
        roi = options.get("roi")
        cv2 = self.cv2
        np = self.np

        try:
            if len(frame.shape) == 2:
                gray = frame.copy()
            elif cv2 is not None:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            else:
                gray = (frame[:, :, 0] * 0.299 + frame[:, :, 1] * 0.587 + frame[:, :, 2] * 0.114).astype(np.uint8)
            if roi:
                gray = self._crop_roi(gray, roi)

            gray = self._center_crop(gray)
            if gray.size == 0:
                return AnalysisResult(status="need_roi", message="有效分析区域为空。")

            if cv2 is not None:
                gray = cv2.GaussianBlur(gray, (5, 5), 0)
                gray = cv2.equalizeHist(gray)

            vertical = self._analyze_projection(gray, axis=0, orientation="vertical", pixel_scale=pixel_scale)
            horizontal = self._analyze_projection(gray, axis=1, orientation="horizontal", pixel_scale=pixel_scale)
            result = vertical if vertical.confidence >= horizontal.confidence else horizontal

            if result.confidence < 0.25:
                result.status = "low_confidence"
                result.message = "条纹周期不稳定，建议重新选择 ROI 或调整图像清晰度。"
            elif result.stripe_spacing_px is None:
                result.status = "no_stripe"
                result.message = "未识别到稳定的相邻亮纹或暗纹中心距。"
            else:
                result.status = "ok"
                result.message = "已识别相邻亮条纹/暗条纹中心距。"
            return result
        except Exception as exc:
            return AnalysisResult(status="analysis_error", message=str(exc))

    def _ready(self):
        return self.np is not None and (self.cv2 is not None or self.Image is not None)

    def _dependency_result(self):
        message = "缺少 NumPy/Pillow，请先运行 setup_env.ps1 初始化项目环境。"
        if self.dependency_error:
            message += " " + self.dependency_error
        return AnalysisResult(status="missing_dependency", message=message)

    def _crop_roi(self, gray, roi):
        h, w = gray.shape[:2]
        x = max(0, int(roi.get("x", 0)))
        y = max(0, int(roi.get("y", 0)))
        rw = max(1, int(roi.get("width", w)))
        rh = max(1, int(roi.get("height", h)))
        return gray[y : min(h, y + rh), x : min(w, x + rw)]

    def _center_crop(self, gray):
        h, w = gray.shape[:2]
        if h < 16 or w < 16:
            return gray
        return gray[int(h * 0.12) : int(h * 0.88), int(w * 0.08) : int(w * 0.92)]

    def _analyze_projection(self, gray, axis, orientation, pixel_scale):
        np = self.np
        profile = gray.mean(axis=axis)
        smooth = self._smooth(profile)
        norm = self._normalize(smooth)
        clarity = self._clarity(norm)
        period, corr = self._autocorrelation_period(norm)
        bright = self._peak_spacing(norm, kind="bright", expected_period=period, sample_count=5)
        dark = self._peak_spacing(norm, kind="dark", expected_period=period)

        spacing = None
        if bright is not None:
            spacing = bright
            if dark is not None:
                agreement = max(0.0, 1.0 - abs(bright - dark) / max(spacing, 1.0))
            else:
                agreement = 0.72
        elif dark is not None:
            spacing = dark
            agreement = 0.65
        elif period:
            spacing = period
            agreement = 0.45
        else:
            agreement = 0.0

        if spacing is not None and period:
            period_agreement = max(0.0, 1.0 - abs(spacing - period) / max(spacing, period, 1.0))
        else:
            period_agreement = 0.0

        confidence = max(0.0, min(1.0, corr * 0.5 + agreement * 0.3 + period_agreement * 0.2))
        return AnalysisResult(
            stripe_spacing_px=self._round(spacing),
            stripe_spacing_um=self._round(spacing * pixel_scale if spacing is not None else None),
            bright_spacing_px=self._round(bright),
            dark_spacing_px=self._round(dark),
            clarity_score=self._round(clarity * 100.0),
            confidence=self._round(confidence),
            status="ok" if spacing is not None else "no_stripe",
            profile=[self._round(float(v)) for v in norm.tolist()],
            orientation=orientation,
        )

    def _smooth(self, profile):
        np = self.np
        window = max(7, int(len(profile) / 120))
        if window % 2 == 0:
            window += 1
        return np.convolve(profile.astype(float), np.ones(window, dtype=float) / float(window), mode="same")

    def _normalize(self, profile):
        np = self.np
        lo = float(np.min(profile))
        hi = float(np.max(profile))
        if hi - lo < 1e-6:
            return np.zeros_like(profile, dtype=float)
        return (profile - lo) / (hi - lo)

    def _clarity(self, profile):
        np = self.np
        if len(profile) < 2:
            return 0.0
        contrast = float(np.max(profile) - np.min(profile))
        gradient = float(np.mean(np.abs(np.diff(profile))))
        return max(0.0, min(1.0, contrast * 0.65 + min(gradient * 8.0, 1.0) * 0.35))

    def _autocorrelation_period(self, profile):
        np = self.np
        values = profile - float(np.mean(profile))
        if len(values) < 24 or float(np.std(values)) < 1e-6:
            return None, 0.0

        best_lag = None
        best_corr = -1.0
        for lag in range(8, min(int(len(values) / 2), 260) + 1):
            left = values[:-lag]
            right = values[lag:]
            denom = math.sqrt(float(np.dot(left, left)) * float(np.dot(right, right)))
            corr = float(np.dot(left, right)) / denom if denom > 1e-9 else 0.0
            if corr > best_corr:
                best_corr = corr
                best_lag = lag
        return best_lag, max(0.0, min(1.0, best_corr))

    def _peak_spacing(self, profile, kind, expected_period=None, sample_count=None):
        np = self.np
        values = profile if kind == "bright" else 1.0 - profile
        threshold = float(np.mean(values) + np.std(values) * 0.25)
        min_distance = int(max(6, (expected_period or len(values) / 20.0) * 0.45))

        peaks = []
        last_peak = -min_distance
        for idx in range(2, len(values) - 2):
            if values[idx] <= threshold:
                continue
            if not (values[idx] >= values[idx - 1] and values[idx] >= values[idx + 1]):
                continue
            if idx - last_peak < min_distance:
                if peaks and values[idx] > values[peaks[-1]]:
                    peaks[-1] = idx
                    last_peak = idx
                continue
            peaks.append(idx)
            last_peak = idx

        if len(peaks) < 2:
            return None
        spacing_items = []
        center = len(values) / 2.0
        for i in range(1, len(peaks)):
            spacing = peaks[i] - peaks[i - 1]
            midpoint = (peaks[i] + peaks[i - 1]) / 2.0
            spacing_items.append((spacing, abs(midpoint - center)))

        if expected_period:
            spacing_items = [
                item for item in spacing_items if expected_period * 0.45 <= item[0] <= expected_period * 1.65
            ]
        if not spacing_items:
            return None
        if sample_count:
            spacing_items = sorted(spacing_items, key=lambda item: item[1])[:sample_count]
            spacings = [item[0] for item in spacing_items]
            return float(np.mean(np.array(spacings, dtype=float)))

        spacings = [item[0] for item in spacing_items]
        return float(np.median(np.array(spacings, dtype=float)))

    def _round(self, value):
        return None if value is None else round(float(value), 3)
