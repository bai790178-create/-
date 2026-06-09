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
        raw_spacing_px=None,
        stable_spacing_px=None,
        roi_count=0,
        used_bright_intervals=None,
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
        self.raw_spacing_px = raw_spacing_px
        self.stable_spacing_px = stable_spacing_px
        self.roi_count = roi_count
        self.used_bright_intervals = used_bright_intervals or []

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
            "raw_spacing_px": self.raw_spacing_px,
            "stable_spacing_px": self.stable_spacing_px,
            "roi_count": self.roi_count,
            "used_bright_intervals": self.used_bright_intervals,
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

            gray = self._prepare_gray(gray)

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

    def _prepare_gray(self, gray):
        np = self.np
        cv2 = self.cv2
        if cv2 is None:
            return self._normalize(gray.astype(float))

        gray8 = gray.astype(np.uint8)
        sigma = max(8.0, min(gray8.shape[:2]) / 36.0)
        background = cv2.GaussianBlur(gray8, (0, 0), sigma)
        flattened = np.clip(gray8.astype(float) - background.astype(float) + 128.0, 0, 255).astype(np.uint8)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(flattened)
        return cv2.GaussianBlur(enhanced, (3, 3), 0)

    def _analyze_projection(self, gray, axis, orientation, pixel_scale):
        np = self.np
        bands = self._projection_bands(gray, axis)
        stats = []
        for profile, band_center_distance in bands:
            norm_profile = self._normalize(self._smooth(profile))
            clarity = self._clarity(norm_profile)
            period, corr = self._estimate_period(norm_profile)
            if period is not None and corr >= 0.12 and clarity >= 0.12:
                stats.append({
                    "profile": norm_profile,
                    "period": period,
                    "corr": corr,
                    "clarity": clarity,
                    "band_center_distance": band_center_distance,
                })

        master_period = self._weighted_period(stats)
        selected = []
        if master_period is not None:
            selected = [
                item
                for item in stats
                if master_period * 0.65 <= item["period"] <= master_period * 1.35
            ]
        if not selected:
            full_profile = gray.mean(axis=axis)
            norm = self._normalize(self._smooth(full_profile))
            selected = [{
                "profile": norm,
                "period": master_period,
                "corr": 0.0,
                "clarity": self._clarity(norm),
                "band_center_distance": 0.0,
            }]

        weights = np.array([
            max(0.05, item["corr"] * 0.65 + item["clarity"] * 0.35) for item in selected
        ], dtype=float)
        profiles = np.array([item["profile"] for item in selected], dtype=float)
        combined = np.average(profiles, axis=0, weights=weights)
        norm = self._normalize(self._smooth(combined))
        clarity = self._clarity(norm)
        period, corr = self._estimate_period(norm)
        if master_period is not None:
            period = master_period if period is None else (master_period * 0.65 + period * 0.35)
            corr = max(corr, max(item["corr"] for item in selected))

        bright_info = self._peak_spacing_details(norm, kind="bright", expected_period=period, sample_count=5)
        dark_info = self._peak_spacing_details(norm, kind="dark", expected_period=period)
        bright = bright_info["spacing"] if bright_info else None
        dark = dark_info["spacing"] if dark_info else None

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
            roi_count=len(selected),
            used_bright_intervals=[self._round(v) for v in (bright_info["intervals"] if bright_info else [])],
        )

    def _projection_bands(self, gray, axis):
        np = self.np
        h, w = gray.shape[:2]
        bands = []
        if axis == 0:
            length = h
            width = max(12, int(length * 0.22))
            centers = np.linspace(length * 0.18, length * 0.82, 7)
            for center in centers:
                start = max(0, int(center - width / 2))
                end = min(h, int(center + width / 2))
                if end - start >= 8:
                    bands.append((gray[start:end, :].mean(axis=0), abs(center - length / 2.0) / max(length, 1)))
        else:
            length = w
            width = max(12, int(length * 0.22))
            centers = np.linspace(length * 0.18, length * 0.82, 7)
            for center in centers:
                start = max(0, int(center - width / 2))
                end = min(w, int(center + width / 2))
                if end - start >= 8:
                    bands.append((gray[:, start:end].mean(axis=1), abs(center - length / 2.0) / max(length, 1)))

        if not bands:
            bands.append((gray.mean(axis=axis), 0.0))
        return bands

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

    def _estimate_period(self, profile):
        ac_period, ac_score = self._autocorrelation_period(profile)
        fft_period, fft_score = self._frequency_period(profile)
        if ac_period is not None and fft_period is not None:
            agreement = abs(ac_period - fft_period) / max(ac_period, fft_period, 1.0)
            if agreement <= 0.28:
                period = (ac_period * ac_score + fft_period * fft_score) / max(ac_score + fft_score, 1e-6)
                return period, max(ac_score, fft_score)
        if fft_period is not None and fft_score > ac_score:
            return fft_period, fft_score
        return ac_period, ac_score

    def _weighted_period(self, stats):
        if not stats:
            return None
        np = self.np
        periods = np.array([item["period"] for item in stats], dtype=float)
        median = float(np.median(periods))
        usable = [item for item in stats if median * 0.65 <= item["period"] <= median * 1.35]
        if not usable:
            return median
        weights = np.array([max(0.05, item["corr"] * item["clarity"]) for item in usable], dtype=float)
        values = np.array([item["period"] for item in usable], dtype=float)
        return float(np.average(values, weights=weights))

    def _autocorrelation_period(self, profile):
        np = self.np
        values = profile - float(np.mean(profile))
        if len(values) < 24 or float(np.std(values)) < 1e-6:
            return None, 0.0

        best_lag = None
        best_corr = -1.0
        for lag in range(6, min(int(len(values) / 2), 260) + 1):
            left = values[:-lag]
            right = values[lag:]
            denom = math.sqrt(float(np.dot(left, left)) * float(np.dot(right, right)))
            corr = float(np.dot(left, right)) / denom if denom > 1e-9 else 0.0
            if corr > best_corr:
                best_corr = corr
                best_lag = lag
        return best_lag, max(0.0, min(1.0, best_corr))

    def _frequency_period(self, profile):
        np = self.np
        values = profile - float(np.mean(profile))
        if len(values) < 24 or float(np.std(values)) < 1e-6:
            return None, 0.0
        windowed = values * np.hanning(len(values))
        spectrum = np.abs(np.fft.rfft(windowed))
        if len(spectrum) < 4:
            return None, 0.0
        spectrum[:2] = 0.0
        indexes = np.arange(len(spectrum), dtype=float)
        valid = indexes > 0
        periods = np.zeros_like(indexes)
        periods[valid] = len(values) / indexes[valid]
        valid = (periods >= 6.0) & (periods <= min(220.0, len(values) / 2.0))
        if not np.any(valid):
            return None, 0.0
        valid_indices = np.where(valid)[0]
        peak_index = int(valid_indices[np.argmax(spectrum[valid])])
        total = float(np.sum(spectrum[valid])) + 1e-9
        score = float(spectrum[peak_index]) / total
        return float(len(values) / peak_index), max(0.0, min(1.0, score * 8.0))

    def _peak_spacing(self, profile, kind, expected_period=None, sample_count=None):
        details = self._peak_spacing_details(profile, kind, expected_period, sample_count)
        return None if details is None else details["spacing"]

    def _peak_spacing_details(self, profile, kind, expected_period=None, sample_count=None):
        np = self.np
        values = profile if kind == "bright" else 1.0 - profile
        threshold = float(np.mean(values) + np.std(values) * 0.18)
        min_distance = int(max(5, (expected_period or len(values) / 20.0) * 0.55))

        peaks = []
        last_peak = -min_distance
        for idx in range(2, len(values) - 2):
            if values[idx] <= threshold:
                continue
            if not (values[idx] >= values[idx - 1] and values[idx] >= values[idx + 1]):
                continue
            prominence = values[idx] - max(min(values[idx - 2], values[idx - 1]), min(values[idx + 1], values[idx + 2]))
            if prominence < max(0.015, float(np.std(values)) * 0.04):
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
        refined_peaks = []
        for peak in peaks:
            if 0 < peak < len(values) - 1:
                left = float(values[peak - 1])
                center_value = float(values[peak])
                right = float(values[peak + 1])
                denom = left - 2.0 * center_value + right
                offset = 0.0 if abs(denom) < 1e-9 else 0.5 * (left - right) / denom
                offset = max(-0.5, min(0.5, offset))
                refined_peaks.append(float(peak) + offset)
            else:
                refined_peaks.append(float(peak))

        spacing_items = []
        center = len(values) / 2.0
        for i in range(1, len(refined_peaks)):
            spacing = refined_peaks[i] - refined_peaks[i - 1]
            midpoint = (refined_peaks[i] + refined_peaks[i - 1]) / 2.0
            spacing_items.append((spacing, abs(midpoint - center)))

        if expected_period:
            spacing_items = [
                item for item in spacing_items if expected_period * 0.65 <= item[0] <= expected_period * 1.45
            ]
        if not spacing_items:
            return None
        if sample_count:
            spacing_items = sorted(spacing_items, key=lambda item: item[1])[:sample_count]
            spacings = [item[0] for item in spacing_items]
            return {"spacing": float(np.mean(np.array(spacings, dtype=float))), "intervals": spacings}

        spacings = [item[0] for item in spacing_items]
        return {"spacing": float(np.median(np.array(spacings, dtype=float))), "intervals": spacings}

    def _round(self, value):
        return None if value is None else round(float(value), 3)
