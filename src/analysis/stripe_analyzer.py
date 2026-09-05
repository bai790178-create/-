import math
import os

from calibration import PIXEL_SCALE_UM_PER_PX
from analysis.centerline_spacing import CenterlineSpacingModel


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
    def __init__(self, centerline_model_path=None):
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
        self.centerline_model = CenterlineSpacingModel(centerline_model_path)

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

    def calculate_calibrated_contrast(self, stripe_frame, background_frame, dark_frame, options=None):
        if not self._ready():
            return {"status": "missing_dependency", "message": self._dependency_result().message}
        if dark_frame is None:
            return {"status": "missing_dark", "message": "请先拍暗场图。"}
        if stripe_frame is None:
            return {"status": "missing_stripe", "message": "请先拍条纹图。"}

        try:
            dark = self._gray_float(dark_frame)
            background = self._gray_float(background_frame) if background_frame is not None else None
            stripe = self._gray_float(stripe_frame)
            if dark.shape != stripe.shape or (background is not None and dark.shape != background.shape):
                return {"status": "shape_mismatch", "message": "暗场图和条纹图尺寸不一致。"}
            options = options or {}
            calibration = self._calibrate_contrast_frames(stripe, background, dark, options)
            if calibration.get("error"):
                return calibration["error"]
            corrected = calibration["corrected"]
            roi = self._contrast_roi(corrected, options.get("roi"))
            valid_roi = self._contrast_roi(calibration["valid_mask"], options.get("roi"))
            if roi.size == 0:
                return {"status": "need_roi", "message": "有效分析区域为空。"}

            expected_period, expected_source = self._expected_contrast_period(options)
            analysis_roi, analysis_mask, analysis_scale = self._contrast_analysis_image(
                roi, valid_roi
            )
            result = self._fft_demodulated_contrast(
                analysis_roi,
                valid_mask=analysis_mask,
                expected_period_px=(
                    expected_period * analysis_scale if expected_period is not None else None
                ),
                period_tolerance=float(options.get("contrast_period_tolerance", 0.35)),
            )
            if result.get("estimated_period_px") is not None:
                result["estimated_period_px"] = round(
                    float(result["estimated_period_px"]) / analysis_scale, 3
                )
            if result.get("carrier_fx") is not None:
                result["carrier_fx"] = float(result["carrier_fx"]) * analysis_scale
            if result.get("carrier_fy") is not None:
                result["carrier_fy"] = float(result["carrier_fy"]) * analysis_scale
            result["analysis_scale"] = analysis_scale
            result.update(calibration["diagnostics"])
            result["expected_period_px"] = expected_period
            result["expected_period_source"] = expected_source
            reportable = bool(result.get("reportable", False))
            warnings = list(result.get("warnings", []))
            external_caution = False
            registration_response = result.get("registration_response")
            registration_exceeded = bool(result.get("registration_exceeded", False))
            if registration_exceeded:
                warnings.append("背景图与条纹图位移超过允许范围。")
                reportable = False
            if registration_response is not None and registration_response < 0.05:
                warnings.append("背景配准响应过低，平场校正可能不可靠。")
                reportable = False
            elif registration_response is not None and registration_response < 0.10:
                warnings.append("背景配准响应偏低，建议检查光路稳定性。")
                external_caution = True
            if result.get("saturated_percent", 0.0) > 0.1:
                warnings.append("存在近饱和像素，已从定量统计中屏蔽。")
                external_caution = True
            result["warnings"] = warnings
            result["reportable"] = reportable
            if reportable and external_caution and result.get("quality_status") == "可报告":
                result["quality_status"] = "谨慎使用"
            result.update({
                "status": "ok" if reportable else "quality_rejected",
                "message": (
                    warnings[0]
                    if warnings
                    else ("衬比度质量检查通过。" if reportable else "衬比度质量检查未通过。")
                ),
                "method": "constrained_fft_demodulation",
                "schema_version": 2,
                "calibration_mode": "flat_reference_normalization" if background is not None else "dark_only",
            })
            result.update({
                "gamma": self._round_digits(result.get("gamma"), 5),
                "gamma_std": self._round_digits(result.get("gamma_std"), 5),
                "gamma_spatial_std": self._round_digits(result.get("gamma_spatial_std"), 5),
                "i_max": self._round_digits(result.get("i_max"), 4),
                "i_min": self._round_digits(result.get("i_min"), 4),
                "roi_height": int(roi.shape[0]),
                "roi_width": int(roi.shape[1]),
                "image_height": int(stripe.shape[0]),
                "image_width": int(stripe.shape[1]),
            })
            return result
        except Exception as exc:
            return {"status": "contrast_error", "message": str(exc)}

    def dark_subtracted_contrast_image(self, stripe_frame, dark_frame):
        if not self._ready() or stripe_frame is None or dark_frame is None:
            return None
        try:
            dark = self._gray_float(dark_frame)
            stripe = self._gray_float(stripe_frame)
            if dark.shape != stripe.shape:
                return None
            return self._display_gray(self.np.maximum(stripe - dark, self.np.float32(0.0)))
        except Exception:
            return None

    def corrected_contrast_image(self, stripe_frame, background_frame, dark_frame, options=None):
        if not self._ready() or stripe_frame is None or background_frame is None or dark_frame is None:
            return None
        try:
            dark = self._gray_float(dark_frame)
            background = self._gray_float(background_frame)
            stripe = self._gray_float(stripe_frame)
            if dark.shape != background.shape or dark.shape != stripe.shape:
                return None

            calibration = self._calibrate_contrast_frames(stripe, background, dark, options or {})
            if calibration.get("error"):
                return None
            return self._display_gray(calibration["corrected"])
        except Exception:
            return None

    def analyze_frame(self, frame, options=None):
        if not self._ready():
            return self._dependency_result()
        if frame is None:
            return AnalysisResult(status="no_image", message="没有可分析图像。")

        options = options or {}
        pixel_scale = PIXEL_SCALE_UM_PER_PX

        try:
            measured = self.centerline_model.measure(frame, roi=options.get("roi"))
            spacing = measured.get("spacing_px")
            spacing_std = measured.get("spacing_std_px")
            spacing_sem = measured.get("spacing_sem_px")
            uncertainty = None
            if spacing is not None:
                uncertainty = max(0.2, float(spacing_sem if spacing_sem is not None else spacing * 0.05))
            return AnalysisResult(
                stripe_spacing_px=self._round(spacing),
                stripe_spacing_um=self._round(spacing * pixel_scale if spacing is not None else None),
                spacing_mean_px=self._round(spacing),
                spacing_std_px=self._round(spacing_std),
                spacing_sem_px=self._round(spacing_sem),
                spacing_uncertainty_px=self._round(uncertainty),
                stripe_centers_px=[self._round(value) for value in measured.get("stripe_centers_px", [])],
                spacing_samples_px=[self._round(value) for value in measured.get("spacing_samples_px", [])],
                clarity_score=self._round(float(measured.get("model_evidence", 0.0)) * 100.0),
                confidence=self._round(measured.get("confidence", 0.0)),
                status=measured.get("status", "model_error"),
                profile=[self._round(float(value)) for value in measured.get("profile", [])],
                orientation="vertical",
                message=measured.get("message", "中心线模型未返回测量结果。"),
                measurement_method="centerline_model",
                roi_count=int(measured.get("roi_count", 0)),
                used_bright_intervals=[self._round(value) for value in measured.get("spacing_samples_px", [])],
            )
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

    def _contrast_roi(self, gray, roi):
        if roi:
            return self._crop_roi(gray, roi)
        return self._center_crop(gray)

    def _calibrate_contrast_frames(self, stripe, background, dark, options):
        np = self.np
        eps = np.float32(1e-6)
        stripe_corr = np.maximum(stripe - dark, np.float32(0.0))
        diagnostics = {
            "registration_dx_px": 0.0,
            "registration_dy_px": 0.0,
            "registration_response": None,
            "registration_applied": False,
            "registration_exceeded": False,
        }

        if background is None:
            corrected = stripe_corr
            valid_mask = stripe_corr > 0.0
        else:
            bg_corr = np.maximum(background - dark, np.float32(0.0))
            if float(np.max(bg_corr)) <= eps:
                return {
                    "error": {
                        "status": "invalid_background",
                        "message": "背景图扣除暗场后强度过低，无法计算衬比度。",
                    }
                }
            coverage = np.ones(bg_corr.shape, dtype=bool)
            if bool(options.get("contrast_register_background", True)):
                registration = self._register_background_translation(
                    bg_corr,
                    stripe_corr,
                    float(options.get("contrast_max_registration_shift_px", 12.0)),
                )
                bg_corr = registration.pop("aligned")
                coverage = registration.pop("coverage")
                diagnostics.update(registration)

            positive = bg_corr[bg_corr > 0.0]
            if positive.size == 0:
                return {
                    "error": {
                        "status": "invalid_background",
                        "message": "背景图中没有可用于平场校正的有效光强。",
                    }
                }
            denominator_floor = max(1.0, 0.05 * float(np.percentile(positive, 99)))
            valid_mask = coverage & (bg_corr > denominator_floor)
            corrected = stripe_corr / np.maximum(bg_corr, np.float32(denominator_floor))

        full_scale = float(options.get("contrast_full_scale", 255.0))
        saturation_limit = 0.995 * full_scale
        saturated = stripe >= saturation_limit
        if background is not None:
            saturated |= background >= saturation_limit
        diagnostics["saturated_percent"] = float(100.0 * np.mean(saturated))
        valid_mask &= ~saturated
        diagnostics["calibration_valid_fraction"] = float(np.mean(valid_mask))
        if not np.any(valid_mask):
            return {
                "error": {
                    "status": "invalid_calibration",
                    "message": "平场校正后没有有效像素。",
                }
            }

        fill_value = float(np.median(corrected[valid_mask]))
        corrected = np.where(valid_mask, corrected, fill_value).astype(np.float32)
        return {
            "corrected": corrected,
            "valid_mask": valid_mask,
            "diagnostics": diagnostics,
        }

    def _register_background_translation(self, background, stripe, max_shift_px):
        np = self.np
        cv2 = self.cv2
        diagnostics = {
            "registration_dx_px": 0.0,
            "registration_dy_px": 0.0,
            "registration_response": None,
            "registration_applied": False,
            "registration_exceeded": False,
        }
        if cv2 is None or min(background.shape) < 32:
            return dict(
                diagnostics,
                aligned=background,
                coverage=np.ones(background.shape, dtype=bool),
            )

        height, width = background.shape
        registration_scale = min(1.0, 1024.0 / float(max(height, width)))
        if registration_scale < 1.0:
            small_size = (
                max(32, int(round(width * registration_scale))),
                max(32, int(round(height * registration_scale))),
            )
            background_search = cv2.resize(background, small_size, interpolation=cv2.INTER_AREA)
            stripe_search = cv2.resize(stripe, small_size, interpolation=cv2.INTER_AREA)
        else:
            background_search = background
            stripe_search = stripe

        def registration_texture(image):
            values = np.asarray(image, dtype=np.float32)
            sigma = max(2.0, min(values.shape) / 80.0)
            low = cv2.GaussianBlur(values, (0, 0), sigma)
            texture = values - low
            scale = float(np.std(texture))
            return texture / max(scale, 1e-6), scale

        bg_texture, bg_scale = registration_texture(background_search)
        stripe_texture, stripe_scale = registration_texture(stripe_search)
        if bg_scale < 1e-3 or stripe_scale < 1e-3:
            return dict(
                diagnostics,
                aligned=background,
                coverage=np.ones(background.shape, dtype=bool),
            )
        search_height, search_width = background_search.shape
        window = cv2.createHanningWindow((search_width, search_height), cv2.CV_32F)
        shift, response = cv2.phaseCorrelate(bg_texture, stripe_texture, window)
        dx = float(shift[0]) / registration_scale
        dy = float(shift[1]) / registration_scale
        exceeded = abs(dx) > max_shift_px or abs(dy) > max_shift_px
        diagnostics.update({
            "registration_dx_px": dx,
            "registration_dy_px": dy,
            "registration_response": float(response),
            "registration_applied": not exceeded,
            "registration_exceeded": exceeded,
        })
        if exceeded:
            return dict(
                diagnostics,
                aligned=background,
                coverage=np.ones(background.shape, dtype=bool),
            )

        matrix = np.float32([[1.0, 0.0, dx], [0.0, 1.0, dy]])
        aligned = cv2.warpAffine(
            background,
            matrix,
            (width, height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT,
        )
        coverage = cv2.warpAffine(
            np.ones(background.shape, dtype=np.uint8),
            matrix,
            (width, height),
            flags=cv2.INTER_NEAREST,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=0,
        ) > 0
        return dict(diagnostics, aligned=aligned, coverage=coverage)

    def _expected_contrast_period(self, options):
        explicit = options.get("contrast_expected_period_px")
        if explicit is not None:
            try:
                value = float(explicit)
                if value > 2.0:
                    return value, "explicit"
            except (TypeError, ValueError):
                pass
        try:
            frequency_mhz = float(options.get("frequency_mhz", 0.0))
            pixel_scale = float(options.get("pixel_scale", PIXEL_SCALE_UM_PER_PX))
            sound_speed = float(options.get("contrast_sound_speed_m_s", 1500.0))
        except (TypeError, ValueError):
            return None, "unconstrained"
        if frequency_mhz > 0.0 and pixel_scale > 0.0 and sound_speed > 0.0:
            return sound_speed / (2.0 * frequency_mhz * pixel_scale), "frequency_model"
        return None, "unconstrained"

    def _contrast_analysis_image(self, image, valid_mask):
        cv2 = self.cv2
        height, width = image.shape
        scale = min(1.0, 1024.0 / float(max(height, width)))
        if cv2 is None or scale >= 1.0:
            return image, valid_mask, 1.0
        size = (
            max(32, int(round(width * scale))),
            max(32, int(round(height * scale))),
        )
        resized = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        resized_mask = cv2.resize(
            valid_mask.astype(self.np.uint8), size, interpolation=cv2.INTER_NEAREST
        ) > 0
        actual_scale = float(size[0]) / float(width)
        return resized, resized_mask, actual_scale

    def _fft_demodulated_contrast(
        self,
        roi,
        valid_mask=None,
        expected_period_px=None,
        period_tolerance=0.35,
    ):
        np = self.np
        image = np.asarray(roi, dtype=np.float32)
        height, width = image.shape
        if height < 16 or width < 16:
            return {"gamma": None, "gamma_std": None, "i_max": None, "i_min": None,
                    "valid_pair_count": 0, "total_pair_count": 0, "quality_status": "不可用",
                    "orientation": "--", "profile": [], "reportable": False,
                    "warnings": ["ROI 尺寸不足。"]}

        if valid_mask is None:
            valid_mask = np.ones(image.shape, dtype=bool)
        else:
            valid_mask = np.asarray(valid_mask, dtype=bool)
            if valid_mask.shape != image.shape:
                raise ValueError("衬比度有效像素掩膜尺寸不一致。")

        window = np.outer(np.hanning(height), np.hanning(width))
        spectrum = np.fft.fftshift(np.fft.fft2((image - float(np.mean(image))) * window))
        power = np.abs(spectrum) ** 2
        cy, cx = height // 2, width // 2
        fy_axis = np.fft.fftshift(np.fft.fftfreq(height))
        fx_axis = np.fft.fftshift(np.fft.fftfreq(width))
        radius = np.hypot(fx_axis[None, :], fy_axis[:, None])
        tolerance = max(0.05, min(0.80, float(period_tolerance)))
        if expected_period_px is not None and float(expected_period_px) > 2.0:
            min_period = float(expected_period_px) * (1.0 - tolerance)
            max_period = float(expected_period_px) * (1.0 + tolerance)
            f_min = 1.0 / max_period
            f_max = min(0.45, 1.0 / max(min_period, 2.01))
        else:
            f_min = 5.0 / float(max(height, width))
            f_max = 0.25
        candidate = (radius >= f_min) & (radius <= f_max)
        candidate &= (fx_axis[None, :] > 0.0) | (
            (np.abs(fx_axis[None, :]) < 0.5 / width) & (fy_axis[:, None] > 0.0)
        )
        if not np.any(candidate):
            return {"gamma": None, "gamma_std": None, "i_max": None, "i_min": None,
                    "valid_pair_count": 0, "total_pair_count": int(image.size),
                    "quality_status": "不可报告", "orientation": "--", "profile": [],
                    "reportable": False, "warnings": ["允许载频范围内没有可搜索频点。"]}

        masked_power = np.where(candidate, power, -np.inf)
        py, px = np.unravel_index(int(np.argmax(masked_power)), power.shape)
        py_sub, px_sub = self._quadratic_spectrum_peak(np.log(power + 1e-12), py, px)
        fy = (py_sub - cy) / float(height)
        fx = (px_sub - cx) / float(width)
        if fx < 0 or (abs(fx) < 0.5 / width and fy < 0):
            fx, fy = -fx, -fy
        carrier = float(np.hypot(fx, fy))
        if carrier <= 1.0 / max(height, width):
            return {"gamma": None, "gamma_std": None, "i_max": None, "i_min": None,
                    "valid_pair_count": 0, "total_pair_count": 0, "quality_status": "不可用",
                    "orientation": "--", "profile": [], "reportable": False,
                    "warnings": ["未检测到有效载频。"]}

        distance = np.hypot(fx_axis[None, :] - fx, fy_axis[:, None] - fy)
        exclusion = max(2.5 / min(height, width), carrier * 0.15)
        noise_mask = candidate & (distance > exclusion)
        noise_values = power[noise_mask]
        noise_reference = (
            float(np.percentile(noise_values, 95))
            if noise_values.size >= 20
            else float(np.median(power[candidate]))
        )
        peak_power = float(power[py, px])
        carrier_snr_db = 10.0 * math.log10(max(peak_power, 1e-12) / max(noise_reference, 1e-12))
        theta = math.atan2(fy, fx)
        projected_length = abs(math.cos(theta)) * width + abs(math.sin(theta)) * height
        cycles = carrier * projected_length

        fy_grid = np.fft.fftfreq(height)[:, None]
        fx_grid = np.fft.fftfreq(width)[None, :]
        sigma = max(carrier / 3.5, 1.0 / max(height, width))
        gaussian = lambda ax, ay: np.exp(-((fx_grid - ax) ** 2 + (fy_grid - ay) ** 2) / (2.0 * sigma ** 2))
        raw_spectrum = np.fft.fft2(image)
        carrier_component = np.fft.ifft2(raw_spectrum * gaussian(fx, fy))
        base = np.real(np.fft.ifft2(raw_spectrum * gaussian(0.0, 0.0)))
        noise_powers = []
        for angle_offset in (60.0, 90.0, 120.0):
            angle = theta + math.radians(angle_offset)
            component = np.fft.ifft2(
                raw_spectrum * gaussian(carrier * math.cos(angle), carrier * math.sin(angle))
            )
            noise_powers.append((2.0 * np.abs(component)) ** 2)
        noise_power = np.median(np.stack(noise_powers, axis=0), axis=0)
        envelope = np.sqrt(np.maximum((2.0 * np.abs(carrier_component)) ** 2 - noise_power, 0.0))
        contrast_map = envelope / np.maximum(base, 1e-6)
        threshold = 0.15 * float(np.percentile(base, 99))
        mask = valid_mask & np.isfinite(contrast_map) & np.isfinite(base) & (base > threshold)
        values = np.clip(contrast_map[mask], 0.0, 1.0)
        if values.size == 0:
            return {"gamma": None, "gamma_std": None, "i_max": None, "i_min": None,
                    "valid_pair_count": 0, "total_pair_count": int(contrast_map.size),
                    "quality_status": "不可报告", "orientation": "--", "profile": [],
                    "reportable": False, "warnings": ["质量筛选后没有有效像素。"]}

        gamma = float(np.median(values))
        q1, q3 = [float(v) for v in np.percentile(values, [25, 75])]
        gamma_std = (q3 - q1) / 1.349
        base_level = float(np.median(base[mask]))
        i_max = base_level * (1.0 + gamma)
        i_min = base_level * (1.0 - gamma)
        valid_fraction = float(values.size) / float(contrast_map.size)
        warnings = []
        reportable = True
        caution = False
        if gamma < 1e-4:
            warnings.append("衬比度低于当前计算分辨下限，未确认存在有效条纹。")
            reportable = False
        if carrier_snr_db < 8.0:
            warnings.append("载频峰信噪比低于 8 dB，未确认存在可靠声学条纹。")
            reportable = False
        elif carrier_snr_db < 12.0:
            warnings.append("载频峰信噪比低于 12 dB，结果需谨慎使用。")
            caution = True
        if cycles < 5.0:
            warnings.append("ROI 内有效条纹周期少于 5 个。")
            reportable = False
        elif cycles < 8.0:
            warnings.append("ROI 内有效条纹周期少于 8 个，建议扩大 ROI。")
            caution = True
        if valid_fraction < 0.25:
            warnings.append("有效像素不足 ROI 的 25%。")
            reportable = False
        quality = "可报告" if reportable and not caution else "谨慎使用" if reportable else "不可报告"
        return {
            "gamma": gamma,
            "gamma_std": gamma_std,
            "gamma_spatial_std": gamma_std,
            "i_max": i_max,
            "i_min": i_min,
            "valid_pair_count": int(values.size),
            "valid_pixel_count": int(values.size),
            "total_pair_count": int(contrast_map.size),
            "total_pixel_count": int(contrast_map.size),
            "valid_fraction": valid_fraction,
            "quality_status": quality,
            "reportable": reportable,
            "warnings": warnings,
            "carrier_snr_db": carrier_snr_db,
            "cycles_across_roi": cycles,
            "carrier_fx": fx,
            "carrier_fy": fy,
            "orientation": round(float(np.degrees(theta)), 2),
            "estimated_period_px": round(1.0 / carrier, 3),
            "profile": [round(float(v), 5) for v in np.percentile(values, np.linspace(0, 100, 21)).tolist()],
        }

    def _quadratic_spectrum_peak(self, log_power, py, px):
        height, width = log_power.shape
        if not (1 <= py < height - 1 and 1 <= px < width - 1):
            return float(py), float(px)

        def offset(left, center, right):
            denominator = left - 2.0 * center + right
            if abs(denominator) < 1e-12:
                return 0.0
            return float(self.np.clip(0.5 * (left - right) / denominator, -0.75, 0.75))

        dy = offset(log_power[py - 1, px], log_power[py, px], log_power[py + 1, px])
        dx = offset(log_power[py, px - 1], log_power[py, px], log_power[py, px + 1])
        return py + dy, px + dx

    def summarize_contrast_repeats(self, gamma_values):
        np = self.np
        values = np.asarray(gamma_values, dtype=np.float64).ravel()
        values = values[np.isfinite(values) & (values >= 0.0) & (values <= 1.0)]
        total_count = int(values.size)
        if total_count == 0:
            return {
                "repeat_total_count": 0,
                "repeat_used_count": 0,
                "gamma_repeat_mean": None,
                "gamma_repeat_std": None,
                "gamma_repeat_sem": None,
                "gamma_ci95_low": None,
                "gamma_ci95_high": None,
            }

        if values.size >= 5:
            median = float(np.median(values))
            mad = float(1.4826 * np.median(np.abs(values - median)))
            if mad > 1e-9:
                filtered = values[np.abs(values - median) <= 3.5 * mad]
                if filtered.size >= 3:
                    values = filtered
        used_count = int(values.size)
        mean = float(np.mean(values))
        if used_count >= 2:
            std = float(np.std(values, ddof=1))
            sem = std / math.sqrt(used_count)
            critical = {
                2: 12.706, 3: 4.303, 4: 3.182, 5: 2.776,
                6: 2.571, 7: 2.447, 8: 2.365, 9: 2.306, 10: 2.262,
            }.get(used_count, 1.96)
            ci_low = max(0.0, mean - critical * sem)
            ci_high = min(1.0, mean + critical * sem)
        else:
            std = None
            sem = None
            ci_low = None
            ci_high = None
        return {
            "repeat_total_count": total_count,
            "repeat_used_count": used_count,
            "gamma_repeat_mean": mean,
            "gamma_repeat_std": std,
            "gamma_repeat_sem": sem,
            "gamma_ci95_low": ci_low,
            "gamma_ci95_high": ci_high,
        }

    def _profile_fringe_contrast(self, roi, stripe_roi):
        candidates = [
            self._profile_contrast_candidate(roi, stripe_roi, axis=0, orientation="vertical"),
            self._profile_contrast_candidate(roi, stripe_roi, axis=1, orientation="horizontal"),
        ]
        candidates = [item for item in candidates if item is not None]
        if not candidates:
            return {
                "gamma": None,
                "gamma_std": None,
                "i_max": None,
                "i_min": None,
                "valid_pair_count": 0,
                "total_pair_count": 0,
                "quality_status": "低可信",
                "orientation": "--",
                "profile": [],
            }
        return max(candidates, key=lambda item: (item["valid_pair_count"], item["score"]))

    def _profile_contrast_candidate(self, roi, stripe_roi, axis, orientation):
        np = self.np
        if roi is None or roi.size == 0:
            return None
        bands = self._contrast_bands(roi, stripe_roi, axis)
        band_results = []
        for band, stripe_band in bands:
            result = self._single_profile_contrast(band, stripe_band, axis)
            if result is not None:
                band_results.append(result)
        if not band_results:
            return None

        usable = [item for item in band_results if item["valid_pair_count"] >= 3]
        if usable:
            gamma_values = np.array([item["gamma"] for item in usable], dtype=np.float32)
            gamma_std_values = [item["gamma_std"] for item in usable if item["gamma_std"] is not None]
            i_max_values = np.array([item["i_max"] for item in usable], dtype=np.float32)
            i_min_values = np.array([item["i_min"] for item in usable], dtype=np.float32)
            gamma = float(np.median(gamma_values))
            between_band_std = float(np.std(gamma_values, ddof=1)) if gamma_values.size >= 2 else 0.0
            within_band_std = float(np.median(np.array(gamma_std_values, dtype=np.float32))) if gamma_std_values else 0.0
            gamma_std = max(between_band_std, within_band_std)
            i_max = float(np.median(i_max_values))
            i_min = float(np.median(i_min_values))
        else:
            gamma = None
            gamma_std = None
            i_max = None
            i_min = None

        valid_count = sum(item["valid_pair_count"] for item in band_results)
        total_count = sum(item["total_pair_count"] for item in band_results)
        usable_count = len(usable)
        total_bands = len(band_results)
        if usable_count >= 3 and valid_count >= 8:
            quality_status = "良好：{}/{} 条带".format(usable_count, total_bands)
        elif usable_count >= 2 and valid_count >= 5:
            quality_status = "可用：{}/{} 条带".format(usable_count, total_bands)
        elif usable_count >= 1:
            quality_status = "低可信：{}/{} 条带".format(usable_count, total_bands)
        else:
            quality_status = "不可用：0/{} 条带".format(total_bands)

        best_profile = max(band_results, key=lambda item: item["score"])
        period_values = [item["estimated_period_px"] for item in band_results if item.get("estimated_period_px") is not None]
        score = valid_count + usable_count * 2.0 + sum(max(0.0, item.get("score", 0.0)) for item in band_results) / max(total_bands, 1)
        return {
            "gamma": gamma,
            "gamma_std": gamma_std,
            "i_max": i_max,
            "i_min": i_min,
            "valid_pair_count": valid_count,
            "total_pair_count": total_count,
            "quality_status": quality_status,
            "orientation": orientation,
            "usable_band_count": usable_count,
            "total_band_count": total_bands,
            "estimated_period_px": self._round(float(np.median(np.array(period_values, dtype=np.float32))) if period_values else None),
            "smoothing_window": best_profile.get("smoothing_window"),
            "profile": best_profile.get("profile", []),
            "score": score,
        }

    def _single_profile_contrast(self, band, stripe_band, axis):
        np = self.np
        raw_profile = self._robust_projection(band, axis)
        if raw_profile is None or raw_profile.size < 12:
            return None

        normalized = self._normalize(raw_profile)
        period, corr = self._estimate_period(self._smooth(normalized))
        window = self._contrast_smoothing_window(period)
        smooth_profile = self._moving_average(raw_profile, window)
        peaks = self._local_extrema(smooth_profile, "peak")
        valleys = self._local_extrema(smooth_profile, "valley")
        saturation_profile = stripe_band.max(axis=axis).astype(np.float32) if stripe_band is not None and stripe_band.size else None
        pair_info = self._contrast_peak_valley_pairs(raw_profile, peaks, valleys, period, saturation_profile)
        gammas = pair_info["gammas"]

        if gammas:
            gamma_values = np.array(gammas, dtype=np.float32)
            i_max_values = np.array(pair_info["i_max_values"], dtype=np.float32)
            i_min_values = np.array(pair_info["i_min_values"], dtype=np.float32)
            gamma = float(np.median(gamma_values))
            gamma_std = float(np.std(gamma_values, ddof=1)) if gamma_values.size >= 2 else 0.0
            i_max = float(np.median(i_max_values))
            i_min = float(np.median(i_min_values))
        else:
            gamma = None
            gamma_std = None
            i_max = None
            i_min = None

        score = len(gammas) + max(0.0, corr) + self._clarity(normalized)
        return {
            "gamma": gamma,
            "gamma_std": gamma_std,
            "i_max": i_max,
            "i_min": i_min,
            "valid_pair_count": len(gammas),
            "total_pair_count": pair_info["total_pair_count"],
            "estimated_period_px": self._round(period),
            "smoothing_window": window,
            "profile": [self._round(float(v)) for v in self._normalize(smooth_profile).tolist()],
            "score": score,
        }

    def _contrast_bands(self, roi, stripe_roi, axis):
        h, w = roi.shape[:2]
        split_axis = 0 if axis == 0 else 1
        length = h if split_axis == 0 else w
        band_count = 5 if length >= 60 else 3 if length >= 30 else 1
        bands = []
        for start, end in self._even_slices(length, band_count):
            if end - start < 5:
                continue
            if split_axis == 0:
                band = roi[start:end, :]
                stripe_band = stripe_roi[start:end, :] if stripe_roi is not None and stripe_roi.size else None
            else:
                band = roi[:, start:end]
                stripe_band = stripe_roi[:, start:end] if stripe_roi is not None and stripe_roi.size else None
            bands.append((band, stripe_band))
        return bands

    def _even_slices(self, length, count):
        if count <= 1:
            return [(0, length)]
        slices = []
        for idx in range(count):
            start = int(round(idx * length / float(count)))
            end = int(round((idx + 1) * length / float(count)))
            slices.append((start, end))
        return slices

    def _robust_projection(self, band, axis):
        np = self.np
        values = band.astype(np.float32)
        thickness = values.shape[0] if axis == 0 else values.shape[1]
        if thickness < 5:
            return None
        if thickness < 12:
            return np.median(values, axis=axis).astype(np.float32)
        trim = int(thickness * 0.10)
        if trim < 1:
            return values.mean(axis=axis).astype(np.float32)
        sorted_values = np.sort(values, axis=axis)
        if axis == 0:
            trimmed = sorted_values[trim:thickness - trim, :]
        else:
            trimmed = sorted_values[:, trim:thickness - trim]
        if trimmed.size == 0:
            return np.median(values, axis=axis).astype(np.float32)
        return trimmed.mean(axis=axis).astype(np.float32)

    def _contrast_peak_valley_pairs(self, raw_profile, peaks, valleys, expected_period, saturation_profile):
        np = self.np
        peaks = sorted(int(v) for v in peaks)
        valleys = sorted(int(v) for v in valleys)
        profile_range = self._robust_profile_range(raw_profile)
        noise_floor = self._profile_noise_floor(raw_profile)
        min_diff = max(profile_range * 0.06, noise_floor * 2.5, 1e-6)
        radius = self._peak_value_radius(expected_period)
        total_pair_count = 0
        gammas = []
        i_max_values = []
        i_min_values = []

        for peak in peaks:
            left_candidates = [valley for valley in valleys if valley < peak]
            right_candidates = [valley for valley in valleys if valley > peak]
            if not left_candidates or not right_candidates:
                continue
            left_valley = left_candidates[-1]
            right_valley = right_candidates[0]
            total_pair_count += 1

            if expected_period is not None:
                left_dist = peak - left_valley
                right_dist = right_valley - peak
                min_dist = max(2.0, expected_period * 0.20)
                max_dist = max(min_dist + 1.0, expected_period * 0.85)
                if left_dist < min_dist or left_dist > max_dist or right_dist < min_dist or right_dist > max_dist:
                    continue

            if saturation_profile is not None and self._local_profile_value(saturation_profile, peak, radius, "max") >= 254.0:
                continue

            i_max = self._local_profile_value(raw_profile, peak, radius, "max")
            i_min_left = self._local_profile_value(raw_profile, left_valley, radius, "min")
            i_min_right = self._local_profile_value(raw_profile, right_valley, radius, "min")
            i_min = (i_min_left + i_min_right) * 0.5
            if i_max - i_min < min_diff:
                continue
            denom = i_max + i_min
            if denom <= 1e-9:
                continue
            gamma = max(0.0, min(1.0, (i_max - i_min) / denom))
            gammas.append(gamma)
            i_max_values.append(i_max)
            i_min_values.append(i_min)

        return {
            "gammas": gammas,
            "i_max_values": i_max_values,
            "i_min_values": i_min_values,
            "total_pair_count": total_pair_count,
        }

    def _contrast_smoothing_window(self, period):
        if period is None:
            window = 5
        else:
            window = int(round(float(period) * 0.12))
            window = max(5, min(21, window))
        if window % 2 == 0:
            window += 1
        return window

    def _peak_value_radius(self, period):
        if period is None:
            return 2
        return max(1, min(5, int(round(float(period) * 0.06))))

    def _moving_average(self, profile, window):
        np = self.np
        window = max(1, int(window))
        if window <= 1 or len(profile) < window:
            return profile.astype(np.float32)
        kernel = np.ones(window, dtype=np.float32) / np.float32(window)
        return np.convolve(profile.astype(np.float32), kernel, mode="same")

    def _robust_profile_range(self, profile):
        if profile is None or len(profile) == 0:
            return 0.0
        return float(self.np.percentile(profile, 95) - self.np.percentile(profile, 5))

    def _profile_noise_floor(self, profile):
        if profile is None or len(profile) < 7:
            return 0.0
        smooth = self._moving_average(profile, 5)
        residual = profile.astype(self.np.float32) - smooth.astype(self.np.float32)
        mad = float(self.np.median(self.np.abs(residual - float(self.np.median(residual)))))
        return mad * 1.4826

    def _local_extrema(self, values, kind):
        if values is None or len(values) < 3:
            return []
        result = []
        for idx in range(1, len(values) - 1):
            left = values[idx - 1]
            center = values[idx]
            right = values[idx + 1]
            if kind == "peak" and center > left and center >= right:
                result.append(idx)
            elif kind == "valley" and center < left and center <= right:
                result.append(idx)
        return result

    def _local_profile_value(self, profile, index, radius, mode):
        start = max(0, int(index) - int(radius))
        end = min(len(profile), int(index) + int(radius) + 1)
        values = profile[start:end]
        if values.size == 0:
            return float(profile[int(index)])
        if mode == "min":
            return float(self.np.min(values))
        return float(self.np.max(values))

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

    def _round_digits(self, value, digits):
        return None if value is None else round(float(value), int(digits))
