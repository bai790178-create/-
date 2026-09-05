import math
import os

import cv2
import numpy as np


MODEL_WIDTH = 384
MODEL_HEIGHT = 288
DEFAULT_THRESHOLD = 0.55
MIN_PERIOD_PX = 130
MAX_PERIOD_PX = 280


class CenterlineSpacingModel(object):
    """Run the trained centerline model and derive a robust fringe spacing."""

    def __init__(self, model_path=None):
        if model_path is None:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            model_path = os.path.join(project_root, "assets", "models", "fringe_centerline_v0.onnx")
        self.model_path = model_path
        self.net = None
        self.load_error = ""

    def _load(self):
        if self.net is not None:
            return True
        try:
            with open(self.model_path, "rb") as handle:
                model_bytes = np.frombuffer(handle.read(), dtype=np.uint8)
            self.net = cv2.dnn.readNetFromONNX(model_bytes)
            return True
        except Exception as exc:
            self.load_error = str(exc)
            return False

    @staticmethod
    def _crop_roi(gray, roi):
        if not roi:
            return gray
        height, width = gray.shape[:2]
        x = max(0, int(roi.get("x", 0)))
        y = max(0, int(roi.get("y", 0)))
        roi_width = max(1, int(roi.get("width", width)))
        roi_height = max(1, int(roi.get("height", height)))
        return gray[y : min(height, y + roi_height), x : min(width, x + roi_width)]

    @staticmethod
    def _normalize(values):
        values = np.nan_to_num(values.astype(np.float64))
        median = float(np.median(values))
        scale = float(np.percentile(np.abs(values - median), 75)) + 1e-6
        return np.clip((values - median) / scale, -5.0, 5.0)

    @staticmethod
    def _gaussian_1d(values, sigma):
        row = np.asarray(values, dtype=np.float64).reshape(1, -1)
        return cv2.GaussianBlur(row, (0, 0), sigmaX=float(sigma), borderType=cv2.BORDER_REFLECT101)[0]

    def _predict_probability(self, gray):
        resized = cv2.resize(gray, (MODEL_WIDTH, MODEL_HEIGHT), interpolation=cv2.INTER_LINEAR)
        array = resized.astype(np.float32) / 255.0
        low, high = np.percentile(array, (1.0, 99.0))
        array = np.clip((array - low) / max(float(high - low), 1e-5), 0.0, 1.0).astype(np.float32)
        self.net.setInput(array[None, None])
        logits = self.net.forward()[0, 0]
        probability = 1.0 / (1.0 + np.exp(-np.clip(logits, -30.0, 30.0)))
        return cv2.resize(probability, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_LINEAR)

    @staticmethod
    def _connect_and_filter(probability, threshold):
        height, width = probability.shape
        vertical = max(5, int(round(height * 0.012)))
        mask = np.uint8(probability >= threshold)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((vertical, 3), dtype=np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((max(3, vertical // 3), 1), dtype=np.uint8))
        count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        cleaned = np.zeros_like(mask)
        minimum_area = max(20, int(height * width * 0.00008))
        minimum_height = max(20, int(height * 0.10))
        for component_id in range(1, count):
            if stats[component_id, cv2.CC_STAT_AREA] >= minimum_area and stats[component_id, cv2.CC_STAT_HEIGHT] >= minimum_height:
                cleaned[labels == component_id] = 1
        return cleaned.astype(bool)

    @staticmethod
    def _autocorrelation_period(profile):
        profile = profile.astype(np.float64)
        profile -= float(np.mean(profile))
        norm = float(np.dot(profile, profile))
        if norm <= 1e-9:
            return None, 0.0
        correlation = np.correlate(profile, profile, mode="full")[len(profile) - 1 :]
        max_lag = min(MAX_PERIOD_PX, len(profile) - 2)
        min_lag = min(MIN_PERIOD_PX, max_lag)
        if max_lag <= min_lag:
            return None, 0.0
        lags = np.arange(min_lag, max_lag + 1)
        normalized = correlation[lags] / max(norm, 1e-9)
        best_index = int(np.argmax(normalized))
        period = float(lags[best_index])
        strength = float(normalized[best_index])
        if 0 < best_index < len(lags) - 1:
            y1, y2, y3 = normalized[best_index - 1 : best_index + 2]
            denominator = y1 - 2.0 * y2 + y3
            if abs(denominator) > 1e-9:
                period += float(np.clip(0.5 * (y1 - y3) / denominator, -0.5, 0.5))
        return period, strength

    def _band_profiles(self, gray, probability, connected):
        height, width = gray.shape
        bands = []
        edges = np.linspace(int(height * 0.08), int(height * 0.88), 9).astype(int)
        for top, bottom in zip(edges[:-1], edges[1:]):
            if bottom <= top:
                continue
            image_profile = np.mean(gray[top:bottom], axis=0)
            background = self._gaussian_1d(image_profile, max(12.0, width / 60.0))
            image_response = self._gaussian_1d(image_profile - background, max(1.5, width / 1300.0))
            model_profile = self._gaussian_1d(np.mean(probability[top:bottom], axis=0), 2.0)
            mask_profile = self._gaussian_1d(np.mean(connected[top:bottom], axis=0), 2.0)
            combined = (
                0.42 * self._normalize(image_response)
                + 0.48 * self._normalize(model_profile)
                + 0.10 * self._normalize(mask_profile)
            )
            period, strength = self._autocorrelation_period(combined)
            if period is not None:
                bands.append(((top + bottom) / 2.0, combined, period, strength))
        return bands

    @staticmethod
    def _find_peaks(values, distance, prominence=None):
        values = np.asarray(values, dtype=np.float64)
        if values.size < 3:
            return np.array([], dtype=int)
        candidates = np.flatnonzero((values[1:-1] > values[:-2]) & (values[1:-1] >= values[2:])) + 1
        if prominence is not None and candidates.size:
            radius = max(2, int(distance // 2))
            accepted = []
            for index in candidates:
                left = values[max(0, index - radius) : index + 1]
                right = values[index : min(values.size, index + radius + 1)]
                base = max(float(np.min(left)), float(np.min(right)))
                if float(values[index] - base) >= float(prominence):
                    accepted.append(index)
            candidates = np.asarray(accepted, dtype=int)
        selected = []
        for index in sorted(candidates.tolist(), key=lambda item: float(values[item]), reverse=True):
            if all(abs(index - previous) >= distance for previous in selected):
                selected.append(index)
        return np.asarray(sorted(selected), dtype=int)

    def _spacing_from_peaks(self, profile, period):
        smoothed = self._gaussian_1d(profile, 2.0)
        prominence = max(0.25, float(np.std(smoothed)) * 0.18)
        distance = max(12, int(period * 0.62))
        peaks = self._find_peaks(smoothed, distance, prominence)
        if len(peaks) < 3:
            peaks = self._find_peaks(smoothed, max(12, int(period * 0.58)))
        spacings = []
        for difference in np.diff(peaks):
            multiple = max(1, int(round(float(difference) / period)))
            normalized = float(difference) / multiple
            if period * 0.72 <= normalized <= period * 1.28:
                spacings.append(normalized)
        return peaks, spacings

    @staticmethod
    def _robust_value(values):
        if not values:
            return None, math.inf, []
        array = np.asarray(values, dtype=np.float64)
        median = float(np.median(array))
        deviation = np.abs(array - median)
        mad = float(np.median(deviation)) + 1e-6
        accepted = array[deviation <= max(2.5, 3.5 * mad)]
        if len(accepted) == 0:
            accepted = array
        return float(np.mean(accepted)), float(np.std(accepted)), [float(value) for value in accepted]

    def _trace_centerlines(self, bands, spacing, height, width):
        if not bands:
            return []
        aggregate = np.mean([band[1] for band in bands], axis=0)
        seeds, _ = self._spacing_from_peaks(aggregate, spacing)
        seeds = seeds[(seeds > spacing * 0.25) & (seeds < width - spacing * 0.25)]
        centerlines = []
        for seed in seeds:
            observations = []
            previous_x = float(seed)
            for y, profile, _, _ in bands:
                radius = max(8, int(spacing * 0.30))
                left = max(0, int(round(previous_x)) - radius)
                right = min(width, int(round(previous_x)) + radius + 1)
                if right - left < 3:
                    continue
                local_x = float(left + int(np.argmax(profile[left:right])))
                observations.append((float(y), local_x))
                previous_x = local_x
            if len(observations) < max(4, len(bands) // 2):
                continue
            ys = np.asarray([point[0] for point in observations])
            xs = np.asarray([point[1] for point in observations])
            degree = 2 if len(observations) >= 5 else 1
            coefficients = np.polyfit(ys, xs, degree)
            fitted = np.polyval(coefficients, ys)
            keep = np.abs(xs - fitted) <= max(8.0, spacing * 0.12)
            if int(keep.sum()) >= 4:
                coefficients = np.polyfit(ys[keep], xs[keep], degree)
            sampled_y = np.linspace(height * 0.06, height * 0.92, 32)
            sampled_x = np.polyval(coefficients, sampled_y)
            valid_x = sampled_x[(sampled_x >= -5) & (sampled_x <= width + 5)]
            if valid_x.size:
                centerlines.append(float(np.median(np.clip(valid_x, 0, width - 1))))
        centerlines.sort()
        merged = []
        for center in centerlines:
            if not merged or center - merged[-1] >= spacing * 0.45:
                merged.append(center)
        return merged

    def measure(self, frame, roi=None, threshold=DEFAULT_THRESHOLD):
        if not self._load():
            return {"status": "model_error", "message": "中心线模型加载失败：{}".format(self.load_error)}
        if frame is None:
            return {"status": "no_image", "message": "没有可分析图像。"}
        if len(frame.shape) == 2:
            gray = frame.copy()
        else:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = self._crop_roi(gray, roi)
        if gray.size == 0 or gray.shape[0] < 16 or gray.shape[1] < MIN_PERIOD_PX + 2:
            return {"status": "need_roi", "message": "有效分析区域过小。"}

        probability = self._predict_probability(gray)
        connected = self._connect_and_filter(probability, float(threshold))
        gray_float = gray.astype(np.float32) / 255.0
        bands = self._band_profiles(gray_float, probability, connected)
        periods = [band[2] for band in bands if band[3] >= 0.08]
        strength = float(np.median([band[3] for band in bands])) if bands else 0.0
        initial_period, _, _ = self._robust_value(periods)
        observations = []
        peak_counts = []
        if initial_period is not None:
            for _, profile, period, _ in bands:
                peaks, values = self._spacing_from_peaks(profile, initial_period)
                peak_counts.append(len(peaks))
                observations.extend(values)
                if initial_period * 0.85 <= period <= initial_period * 1.15:
                    observations.append(period)
        spacing, spacing_std, accepted = self._robust_value(observations)

        model_evidence = float(np.percentile(probability, 97))
        mask_coverage = float(np.mean(connected))
        typical_peak_count = float(np.median(peak_counts)) if peak_counts else 0.0
        periodicity = max(0.0, min(1.0, (strength - 0.10) / 0.34))
        consistency = 0.0 if spacing is None else max(0.0, 1.0 - spacing_std / max(spacing * 0.08, 1.0))
        line_evidence = max(0.0, min(1.0, (model_evidence - 0.42) / 0.35))
        count_evidence = max(0.0, min(1.0, (typical_peak_count - 2.5) / 4.5))
        confidence = float(0.34 * periodicity + 0.30 * consistency + 0.22 * line_evidence + 0.14 * count_evidence)
        reliable = (
            spacing is not None
            and MIN_PERIOD_PX <= spacing <= MAX_PERIOD_PX
            and strength >= 0.16
            and typical_peak_count >= 4
            and confidence >= 0.28
            and mask_coverage >= 0.00005
        )
        centers = self._trace_centerlines(bands, float(spacing), gray.shape[0], gray.shape[1]) if reliable else []
        if reliable and len(centers) < 3:
            reliable = False
            centers = []
        aggregate = np.mean([band[1] for band in bands], axis=0) if bands else np.array([], dtype=float)
        if not reliable:
            spacing = None
            accepted = []
        sem = spacing_std / math.sqrt(len(accepted)) if len(accepted) >= 2 else None
        return {
            "status": "ok" if reliable else "no_stripe",
            "message": "中心线模型已识别条纹并计算相邻中心距。" if reliable else "中心线模型未检测到可靠条纹。",
            "spacing_px": spacing,
            "spacing_std_px": spacing_std if reliable else None,
            "spacing_sem_px": sem,
            "spacing_samples_px": accepted,
            "stripe_centers_px": centers,
            "confidence": confidence,
            "model_evidence": model_evidence,
            "profile": aggregate.tolist(),
            "roi_count": len(bands),
        }
