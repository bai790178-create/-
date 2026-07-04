import math
import os


class AnalysisResult(object):
    def __init__(
        self,
        stripe_spacing_px=None,
        stripe_spacing_um=None,
        spacing_mean_px=None,
        spacing_std_px=None,
        spacing_sem_px=None,
        spacing_uncertainty_px=None,
        bright_spacing_px=None,
        dark_spacing_px=None,
        stripe_centers_px=None,
        spacing_samples_px=None,
        clarity_score=0.0,
        confidence=0.0,
        status="no_stripe",
        profile=None,
        orientation="vertical",
        message="",
        measurement_method="period_fallback",
        raw_spacing_px=None,
        stable_spacing_px=None,
        roi_count=0,
        used_bright_intervals=None,
    ):
        self.stripe_spacing_px = stripe_spacing_px
        self.stripe_spacing_um = stripe_spacing_um
        self.spacing_mean_px = spacing_mean_px
        self.spacing_std_px = spacing_std_px
        self.spacing_sem_px = spacing_sem_px
        self.spacing_uncertainty_px = spacing_uncertainty_px
        self.bright_spacing_px = bright_spacing_px
        self.dark_spacing_px = dark_spacing_px
        self.stripe_centers_px = stripe_centers_px or []
        self.spacing_samples_px = spacing_samples_px or []
        self.clarity_score = clarity_score
        self.confidence = confidence
        self.status = status
        self.profile = profile or []
        self.orientation = orientation
        self.message = message
        self.measurement_method = measurement_method
        self.raw_spacing_px = raw_spacing_px
        self.stable_spacing_px = stable_spacing_px
        self.roi_count = roi_count
        self.used_bright_intervals = used_bright_intervals or []

    def to_dict(self):
        return {
            "stripe_spacing_px": self.stripe_spacing_px,
            "stripe_spacing_um": self.stripe_spacing_um,
            "spacing_mean_px": self.spacing_mean_px,
            "spacing_std_px": self.spacing_std_px,
            "spacing_sem_px": self.spacing_sem_px,
            "spacing_uncertainty_px": self.spacing_uncertainty_px,
            "bright_spacing_px": self.bright_spacing_px,
            "dark_spacing_px": self.dark_spacing_px,
            "stripe_centers_px": self.stripe_centers_px,
            "spacing_samples_px": self.spacing_samples_px,
            "clarity_score": self.clarity_score,
            "confidence": self.confidence,
            "status": self.status,
            "orientation": self.orientation,
            "message": self.message,
            "measurement_method": self.measurement_method,
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

    def enhance_frame(self, frame):
        if not self._ready() or frame is None:
            return None

        np = self.np
        cv2 = self.cv2
        if len(frame.shape) == 2:
            gray = frame.copy()
        elif cv2 is not None:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = (frame[:, :, 0] * 0.299 + frame[:, :, 1] * 0.587 + frame[:, :, 2] * 0.114).astype(np.uint8)

        enhanced = self._prepare_gray(gray)
        if enhanced.dtype != np.uint8:
            enhanced = np.clip(enhanced * 255.0, 0, 255).astype(np.uint8)

        if cv2 is not None:
            blur = cv2.GaussianBlur(enhanced, (0, 0), 1.1)
            enhanced = cv2.addWeighted(enhanced, 1.28, blur, -0.28, 0)

        return np.ascontiguousarray(enhanced)

    def calculate_calibrated_contrast(self, stripe_frame, background_frame, dark_frame):
        if not self._ready():
            return {"status": "missing_dependency", "message": self._dependency_result().message}
        if dark_frame is None:
            return {"status": "missing_dark", "message": "请先拍暗场图。"}
        if background_frame is None:
            return {"status": "missing_background", "message": "请先拍背景图。"}
        if stripe_frame is None:
            return {"status": "missing_stripe", "message": "请先拍条纹图。"}

        try:
            dark = self._gray_float(dark_frame)
            background = self._gray_float(background_frame)
            stripe = self._gray_float(stripe_frame)
            if dark.shape != background.shape or dark.shape != stripe.shape:
                return {"status": "shape_mismatch", "message": "暗场图、背景图和条纹图尺寸不一致。"}

            eps = self.np.float32(1e-6)
            stripe_corr = self.np.maximum(stripe - dark, self.np.float32(0.0))
            bg_corr = background - dark
            if float(self.np.max(bg_corr)) <= eps:
                return {"status": "invalid_background", "message": "背景图扣除暗场后强度过低，无法计算衬比度。"}

            corrected = stripe_corr / self.np.maximum(bg_corr, eps)
            roi = self._center_crop(corrected)
            if roi.size == 0:
                return {"status": "need_roi", "message": "有效分析区域为空。"}

            contrast = self._fringe_contrast(roi)
            return {
                "status": "ok",
                "message": "已完成暗场/背景校正衬比度计算。",
                "gamma": self._round(contrast["gamma"]),
                "i_max": self._round(contrast["i_max"]),
                "i_min": self._round(contrast["i_min"]),
                "roi_height": int(roi.shape[0]),
                "roi_width": int(roi.shape[1]),
                "image_height": int(stripe.shape[0]),
                "image_width": int(stripe.shape[1]),
            }
        except Exception as exc:
            return {"status": "contrast_error", "message": str(exc)}

    def corrected_contrast_image(self, stripe_frame, background_frame, dark_frame):
        if not self._ready() or stripe_frame is None or background_frame is None or dark_frame is None:
            return None
        try:
            dark = self._gray_float(dark_frame)
            background = self._gray_float(background_frame)
            stripe = self._gray_float(stripe_frame)
            if dark.shape != background.shape or dark.shape != stripe.shape:
                return None

            bg_corr = background - dark
            if float(self.np.max(bg_corr)) <= 1e-6:
                return None

            corrected = self.np.maximum(stripe - dark, self.np.float32(0.0)) / self.np.maximum(bg_corr, self.np.float32(1e-6))
            return self._display_gray(corrected)
        except Exception:
            return None

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

            measurement_gray = gray.copy()
            gray = self._prepare_gray(gray)

            vertical = self._analyze_projection(gray, axis=0, orientation="vertical", pixel_scale=pixel_scale, measurement_gray=measurement_gray)
            horizontal = self._analyze_projection(gray, axis=1, orientation="horizontal", pixel_scale=pixel_scale, measurement_gray=measurement_gray)
            result = vertical if vertical.confidence >= horizontal.confidence else horizontal

            if result.confidence < 0.35:
                result.status = "low_confidence"
                result.message = "条纹周期不稳定，建议重新选择 ROI 或调整图像清晰度。"
            elif result.stripe_spacing_px is None:
                result.status = "no_stripe"
                result.message = "未识别到稳定的相邻亮纹或暗纹中心距。"
            elif result.measurement_method == "band_center":
                result.status = "ok"
                result.message = "已按亮条带几何中线测得相邻中心距。"
            else:
                result.status = "period_estimate"
                result.message = "亮条带样本不足，已回退到周期估计。"
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

    def _gray_float(self, frame):
        if len(frame.shape) == 2:
            return frame.astype(self.np.float32)
        if self.cv2 is not None:
            return self.cv2.cvtColor(frame, self.cv2.COLOR_BGR2GRAY).astype(self.np.float32)
        return (frame[:, :, 0] * 0.299 + frame[:, :, 1] * 0.587 + frame[:, :, 2] * 0.114).astype(self.np.float32)

    def _center_crop(self, gray):
        h, w = gray.shape[:2]
        if h < 16 or w < 16:
            return gray
        return gray[int(h * 0.12) : int(h * 0.88), int(w * 0.08) : int(w * 0.92)]

    def _prepare_gray(self, gray):
        np = self.np
        cv2 = self.cv2
        if cv2 is None:
            return self._normalize(gray.astype(np.float32))

        gray8 = gray.astype(np.uint8)
        sigma = max(8.0, min(gray8.shape[:2]) / 36.0)
        background = cv2.GaussianBlur(gray8, (0, 0), sigma)
        flattened = np.clip(gray8.astype(np.float32) - background.astype(np.float32) + np.float32(128.0), 0, 255).astype(np.uint8)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(flattened)
        return cv2.GaussianBlur(enhanced, (3, 3), 0)

    def _analyze_projection(self, gray, axis, orientation, pixel_scale, measurement_gray=None):
        np = self.np
        bands = self._projection_bands(gray, axis)
        stats = []
        for profile, band_center_distance, band_quality in bands:
            norm_profile = self._normalize(self._smooth(profile))
            clarity = self._clarity(norm_profile)
            period, corr = self._estimate_period(norm_profile)
            if period is not None and period >= 8.0 and corr >= 0.12 and clarity >= 0.12:
                local_score = corr * 0.48 + clarity * 0.22 + band_quality * 0.30
                stats.append({
                    "profile": norm_profile,
                    "period": period,
                    "corr": corr,
                    "clarity": clarity,
                    "quality": band_quality,
                    "score": local_score,
                    "band_center_distance": band_center_distance,
                })

        master_period = self._weighted_period(stats)
        selected = []
        if master_period is not None:
            period_matched = [
                item
                for item in stats
                if master_period * 0.65 <= item["period"] <= master_period * 1.35
            ]
            if period_matched:
                best_score = max(item["score"] for item in period_matched)
                score_floor = max(0.14, best_score * 0.58)
                selected = [item for item in period_matched if item["score"] >= score_floor]
                if not selected:
                    selected = sorted(period_matched, key=lambda item: item["score"], reverse=True)[:2]
        if not selected:
            full_profile = gray.mean(axis=axis)
            norm = self._normalize(self._smooth(full_profile))
            selected = [{
                "profile": norm,
                "period": master_period,
                "corr": 0.0,
                "clarity": self._clarity(norm),
                "quality": self._band_quality(gray),
                "score": 0.0,
                "band_center_distance": 0.0,
            }]

        weights = np.array([
            max(0.04, item["score"] * item["score"]) for item in selected
        ], dtype=np.float32)
        profiles = np.array([item["profile"] for item in selected], dtype=np.float32)
        combined = np.average(profiles, axis=0, weights=weights)
        norm = self._normalize(self._smooth(combined))
        clarity = self._clarity(norm)
        period, corr = self._estimate_period(norm)
        if master_period is not None:
            period = master_period if period is None else (master_period * 0.65 + period * 0.35)
            corr = max(corr, max(item["corr"] for item in selected))

        measurement_norm = norm
        if measurement_gray is not None and measurement_gray.size:
            measurement_norm = self._normalize(self._smooth(measurement_gray.mean(axis=axis)))

        bright_info = self._band_center_spacing_details(measurement_norm, kind="bright", expected_period=period)
        dark_info = self._band_center_spacing_details(measurement_norm, kind="dark", expected_period=period)

        chosen_info = None
        measurement_method = "period_fallback"
        if bright_info is not None and bright_info["band_count"] >= 4 and bright_info["sample_count"] >= 3:
            chosen_info = bright_info
            measurement_method = "band_center"
        elif dark_info is not None and dark_info["band_count"] >= 4 and dark_info["sample_count"] >= 3:
            chosen_info = dark_info
            measurement_method = "band_center"

        if chosen_info is not None:
            spacing = chosen_info["spacing_mean"]
            agreement = chosen_info["consistency"]
        elif period is not None:
            spacing = period
            agreement = 0.45
        else:
            spacing = None
            agreement = 0.0

        if spacing is not None and spacing < 8.0:
            spacing = None
            chosen_info = None
            measurement_method = "period_fallback"
            agreement = 0.0

        if spacing is not None and period:
            period_agreement = max(0.0, 1.0 - abs(spacing - period) / max(spacing, period, 1.0))
        else:
            period_agreement = 0.0

        quality = max(item.get("quality", 0.0) for item in selected)
        consistency = self._period_consistency(selected)
        confidence = max(0.0, min(1.0, corr * 0.42 + agreement * 0.33 + period_agreement * 0.15 + quality * 0.10))
        if chosen_info is not None:
            confidence = max(confidence, min(1.0, 0.42 + chosen_info["band_count"] * 0.06 + chosen_info["consistency"] * 0.22))
        confidence = max(0.0, min(1.0, confidence * (0.74 + quality * 0.26) * consistency))

        if chosen_info is not None:
            stripe_centers_px = chosen_info["centers"]
            spacing_samples_px = chosen_info["spacing_samples"]
            spacing_mean_px = chosen_info["spacing_mean"]
            spacing_std_px = chosen_info["spacing_std"]
            spacing_sem_px = chosen_info["spacing_sem"]
            spacing_uncertainty_px = chosen_info["spacing_uncertainty"]
        else:
            stripe_centers_px = []
            spacing_samples_px = []
            spacing_mean_px = spacing
            spacing_std_px = None
            spacing_sem_px = None
            spacing_uncertainty_px = self._round(max(0.2, float(spacing) * 0.05)) if spacing is not None else None

        bright_spacing = bright_info["spacing_mean"] if bright_info is not None else None
        dark_spacing = dark_info["spacing_mean"] if dark_info is not None else None
        return AnalysisResult(
            stripe_spacing_px=self._round(spacing),
            stripe_spacing_um=self._round(spacing * pixel_scale if spacing is not None else None),
            spacing_mean_px=self._round(spacing_mean_px),
            spacing_std_px=self._round(spacing_std_px),
            spacing_sem_px=self._round(spacing_sem_px),
            spacing_uncertainty_px=self._round(spacing_uncertainty_px),
            bright_spacing_px=self._round(bright_spacing),
            dark_spacing_px=self._round(dark_spacing),
            stripe_centers_px=[self._round(v) for v in stripe_centers_px],
            spacing_samples_px=[self._round(v) for v in spacing_samples_px],
            clarity_score=self._round(clarity * 100.0),
            confidence=self._round(confidence),
            status="ok" if spacing is not None else "no_stripe",
            profile=[self._round(float(v)) for v in norm.tolist()],
            orientation=orientation,
            measurement_method=measurement_method,
            roi_count=len(selected),
            used_bright_intervals=[self._round(v) for v in (bright_info["spacing_samples"] if bright_info else [])],
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
                    band = gray[start:end, :]
                    bands.append((band.mean(axis=0), abs(center - length / 2.0) / max(length, 1), self._band_quality(band)))
        else:
            length = w
            width = max(12, int(length * 0.22))
            centers = np.linspace(length * 0.18, length * 0.82, 7)
            for center in centers:
                start = max(0, int(center - width / 2))
                end = min(w, int(center + width / 2))
                if end - start >= 8:
                    band = gray[:, start:end]
                    bands.append((band.mean(axis=1), abs(center - length / 2.0) / max(length, 1), self._band_quality(band)))

        if not bands:
            bands.append((gray.mean(axis=axis), 0.0, self._band_quality(gray)))
        return bands

    def _band_quality(self, band):
        np = self.np
        if band is None or band.size == 0:
            return 0.0
        values = band.astype(np.float32)
        contrast = float(np.percentile(values, 95) - np.percentile(values, 5)) / 96.0
        dx = np.diff(values, axis=1) if values.shape[1] > 1 else np.zeros((1,), dtype=np.float32)
        dy = np.diff(values, axis=0) if values.shape[0] > 1 else np.zeros((1,), dtype=np.float32)
        gradient = (float(np.mean(np.abs(dx))) + float(np.mean(np.abs(dy)))) / 42.0
        return max(0.0, min(1.0, contrast * 0.62 + gradient * 0.38))

    def _period_consistency(self, selected):
        if len(selected) < 2:
            return 1.0
        periods = [item.get("period") for item in selected if item.get("period") is not None]
        if len(periods) < 2:
            return 1.0
        np = self.np
        values = np.array(periods, dtype=np.float32)
        median = float(np.median(values))
        if median <= 1e-6:
            return 1.0
        spread = float(np.median(np.abs(values - median))) / median
        return max(0.45, min(1.0, 1.0 - spread * 2.4))

    def _smooth(self, profile):
        np = self.np
        window = max(7, int(len(profile) / 120))
        if window % 2 == 0:
            window += 1
        return np.convolve(profile.astype(np.float32), np.ones(window, dtype=np.float32) / np.float32(window), mode="same")

    def _normalize(self, profile):
        np = self.np
        lo = float(np.min(profile))
        hi = float(np.max(profile))
        if hi - lo < 1e-6:
            return np.zeros_like(profile, dtype=np.float32)
        return ((profile - lo) / (hi - lo)).astype(np.float32)

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
        clusters = []
        for item in sorted(stats, key=lambda value: value["period"]):
            matched = None
            for cluster in clusters:
                center = cluster["center"]
                if center * 0.72 <= item["period"] <= center * 1.28:
                    matched = cluster
                    break
            if matched is None:
                matched = {"items": [], "center": item["period"]}
                clusters.append(matched)
            matched["items"].append(item)
            matched["center"] = float(np.median(np.array([entry["period"] for entry in matched["items"]], dtype=np.float32)))

        def cluster_score(cluster):
            support = min(1.0, len(cluster["items"]) / 3.0)
            strength = sum(max(0.05, item.get("score", item["corr"] * item["clarity"])) for item in cluster["items"])
            return strength * (0.70 + support * 0.30)

        usable = max(clusters, key=cluster_score)["items"]
        weights = np.array([max(0.05, item.get("score", item["corr"] * item["clarity"])) for item in usable], dtype=np.float32)
        values = np.array([item["period"] for item in usable], dtype=np.float32)
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
        valid = (periods >= 8.0) & (periods <= min(220.0, len(values) / 2.0))
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
        details = self._band_center_spacing_details(profile, kind, expected_period=expected_period)
        if details is None:
            return None
        if sample_count:
            intervals = details["spacing_samples"][:sample_count]
            if not intervals:
                return None
            return {"spacing": float(self.np.mean(self.np.array(intervals, dtype=float))), "intervals": intervals}
        return {"spacing": details["spacing_mean"], "intervals": details["spacing_samples"]}

    def _band_center_spacing_details(self, profile, kind, expected_period=None):
        np = self.np
        if profile is None or len(profile) < 8:
            return None
        values = self._normalize(self._smooth(profile if kind == "bright" else 1.0 - profile))
        if float(np.std(values)) < 1e-6:
            return None
        threshold = self._adaptive_band_threshold(values)
        segments = self._threshold_segments(values, threshold)
        if not segments:
            return None

        min_width = max(3.0, (expected_period or len(values) / 20.0) * 0.15)
        max_width = max(min_width + 1.0, (expected_period or len(values) / 2.0) * 0.75)
        bands = self._bands_from_segments(values, segments, threshold, min_width, max_width)
        if len(bands) < 4 and expected_period is not None:
            bands = self._bands_from_segments(values, segments, threshold, 3.0, max(4.0, len(values) / 3.0))

        if len(bands) < 2:
            return None

        bands = sorted(bands, key=lambda item: item["center"])
        centers = np.array([item["center"] for item in bands], dtype=np.float32)
        spacing_samples = np.diff(centers)
        spacing_samples = self._select_spacing_samples(spacing_samples, expected_period)
        if spacing_samples.size == 0:
            return None

        spacing_samples = self._remove_spacing_outliers(spacing_samples)
        if spacing_samples.size == 0:
            return None

        spacing_mean = float(np.mean(spacing_samples))
        spacing_std = float(np.std(spacing_samples, ddof=1)) if spacing_samples.size >= 2 else 0.0
        spacing_sem = spacing_std / math.sqrt(float(spacing_samples.size)) if spacing_samples.size >= 1 else None
        spacing_uncertainty = max(float(spacing_sem or 0.0), 0.2)
        consistency = 1.0
        if spacing_mean > 1e-6:
            consistency = max(0.0, min(1.0, 1.0 - (spacing_std / spacing_mean) * 2.5))
        if spacing_mean > 1e-6 and spacing_std / spacing_mean > 0.08:
            return None

        return {
            "spacing_mean": spacing_mean,
            "spacing_std": spacing_std,
            "spacing_sem": spacing_sem,
            "spacing_uncertainty": spacing_uncertainty,
            "spacing_samples": [float(v) for v in spacing_samples.tolist()],
            "centers": [float(v) for v in centers.tolist()],
            "band_count": len(bands),
            "sample_count": int(spacing_samples.size),
            "consistency": consistency,
        }

    def _bands_from_segments(self, values, segments, threshold, min_width, max_width):
        np = self.np
        bands = []
        for start, end in segments:
            left = self._threshold_crossing(values, start - 1, start, threshold)
            right = self._threshold_crossing(values, end, end + 1, threshold)
            if left is None or right is None:
                continue
            width = right - left
            if width < min_width or width > max_width:
                continue
            band_values = values[start : end + 1]
            bands.append({
                "left": float(left),
                "right": float(right),
                "center": float((left + right) * 0.5),
                "width": float(width),
                "strength": float(np.mean(band_values)),
            })
        return bands

    def _select_spacing_samples(self, samples, expected_period):
        np = self.np
        values = np.array(samples, dtype=np.float32).ravel()
        if values.size == 0:
            return values
        if expected_period is not None:
            expected = values[(values >= expected_period * 0.65) & (values <= expected_period * 1.35)]
            if expected.size >= 3:
                return expected
        return self._dominant_spacing_cluster(values)

    def _dominant_spacing_cluster(self, samples):
        np = self.np
        values = np.array(samples, dtype=np.float32).ravel()
        if values.size < 3:
            return values
        clusters = []
        for value in sorted(float(v) for v in values.tolist()):
            matched = None
            for cluster in clusters:
                center = cluster["center"]
                if center * 0.72 <= value <= center * 1.28:
                    matched = cluster
                    break
            if matched is None:
                matched = {"center": value, "values": []}
                clusters.append(matched)
            matched["values"].append(value)
            matched["center"] = float(np.median(np.array(matched["values"], dtype=np.float32)))

        def score(cluster):
            vals = np.array(cluster["values"], dtype=np.float32)
            mean = float(np.mean(vals))
            spread = float(np.std(vals)) / max(mean, 1e-6) if vals.size > 1 else 0.0
            return vals.size - spread

        best = max(clusters, key=score)
        return np.array(best["values"], dtype=np.float32)

    def _adaptive_band_threshold(self, values):
        np = self.np
        p35 = float(np.percentile(values, 35))
        p85 = float(np.percentile(values, 85))
        return p35 + 0.45 * max(p85 - p35, 1e-6)

    def _threshold_segments(self, values, threshold):
        segments = []
        start = None
        for idx, value in enumerate(values):
            if value >= threshold:
                if start is None:
                    start = idx
            elif start is not None:
                segments.append((start, idx - 1))
                start = None
        if start is not None:
            segments.append((start, len(values) - 1))
        return segments

    def _threshold_crossing(self, values, left_idx, right_idx, threshold):
        if left_idx < 0 or right_idx >= len(values):
            return None
        left = float(values[left_idx])
        right = float(values[right_idx])
        if abs(right - left) < 1e-9:
            return float(left_idx)
        ratio = (threshold - left) / (right - left)
        ratio = max(0.0, min(1.0, ratio))
        return float(left_idx) + ratio

    def _remove_spacing_outliers(self, samples):
        np = self.np
        values = np.array(samples, dtype=np.float32).ravel()
        if values.size < 3:
            return values
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median)))
        if mad < 1e-6:
            return values
        limit = max(0.2, mad * 3.5 * 1.4826)
        filtered = values[np.abs(values - median) <= limit]
        return filtered if filtered.size >= 2 else values

    def _fringe_contrast(self, gray):
        np = self.np
        values = gray.astype(np.float32).ravel()
        if values.size == 0:
            return {"gamma": None, "i_max": None, "i_min": None}

        i_min = float(np.percentile(values, 5))
        i_max = float(np.percentile(values, 95))
        denom = i_max + i_min
        gamma = None if denom <= 1e-9 else max(0.0, min(1.0, (i_max - i_min) / denom))
        return {"gamma": gamma, "i_max": i_max, "i_min": i_min}

    def _display_gray(self, gray):
        np = self.np
        values = gray.astype(np.float32)
        lo = float(np.percentile(values, 1))
        hi = float(np.percentile(values, 99))
        if hi - lo <= 1e-9:
            return np.zeros(values.shape, dtype=np.uint8)
        return np.clip((values - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)

    def _round(self, value):
        return None if value is None else round(float(value), 3)
