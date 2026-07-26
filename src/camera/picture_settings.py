from copy import deepcopy


DEFAULT_PICTURE_SETTINGS = {
    "auto_exposure": False,
    "exposure_time_ms": 1.0,
    "gain": 0.0,
    "gamma": 1.0,
    "fps_enabled": False,
    "fps": 210.0,
    "brightness": 50,
    "acuity_enabled": False,
    "acuity": 0,
    "denoise_enabled": False,
    "denoise": 0,
    "auto_balance": False,
    "balance_r": 1.0,
    "balance_g": 1.0,
    "balance_b": 1.0,
    "digital_shift": 0,
}


def default_picture_settings():
    return deepcopy(DEFAULT_PICTURE_SETTINGS)


def normalize_picture_settings(settings):
    normalized = default_picture_settings()
    if settings:
        normalized.update({key: settings[key] for key in normalized if key in settings})

    for key in (
        "auto_exposure",
        "fps_enabled",
        "acuity_enabled",
        "denoise_enabled",
        "auto_balance",
    ):
        normalized[key] = bool(normalized[key])

    limits = {
        "exposure_time_ms": (0.01, 1000.0),
        "gain": (0.0, 100.0),
        "gamma": (0.1, 4.0),
        "fps": (1.0, 500.0),
        "brightness": (0, 100),
        "acuity": (0, 10),
        "denoise": (0, 10),
        "balance_r": (0.1, 4.0),
        "balance_g": (0.1, 4.0),
        "balance_b": (0.1, 4.0),
        "digital_shift": (-100, 100),
    }
    integer_keys = {"brightness", "acuity", "denoise", "digital_shift"}
    for key, (minimum, maximum) in limits.items():
        try:
            value = float(normalized[key])
        except (TypeError, ValueError):
            value = float(DEFAULT_PICTURE_SETTINGS[key])
        value = max(minimum, min(maximum, value))
        normalized[key] = int(round(value)) if key in integer_keys else value
    return normalized


def apply_picture_settings(frame, settings):
    """Apply the software part of the source application's picture controls."""
    if frame is None:
        return None

    import numpy as np

    values = normalize_picture_settings(settings)
    result = frame.copy()
    work = result.astype(np.float32)

    if work.ndim == 3 and work.shape[2] >= 3:
        if not values["auto_balance"]:
            # Camera frames are BGR. Keep the UI and persisted values in RGB order.
            work[:, :, 0] *= values["balance_b"]
            work[:, :, 1] *= values["balance_g"]
            work[:, :, 2] *= values["balance_r"]

    brightness_offset = (values["brightness"] - 50) * 2.55
    work += brightness_offset + values["digital_shift"]
    work = np.clip(work, 0.0, 255.0)

    gamma = values["gamma"]
    if abs(gamma - 1.0) > 1e-6:
        work = 255.0 * np.power(work / 255.0, 1.0 / gamma)

    result = np.clip(work, 0.0, 255.0).astype(np.uint8)

    if values["denoise_enabled"] and values["denoise"] > 0:
        import cv2

        kernel = min(11, values["denoise"] * 2 + 1)
        result = cv2.GaussianBlur(result, (kernel, kernel), 0)

    if values["acuity_enabled"] and values["acuity"] > 0:
        import cv2

        blurred = cv2.GaussianBlur(result, (0, 0), 1.2)
        amount = values["acuity"] / 5.0
        result = cv2.addWeighted(result, 1.0 + amount, blurred, -amount, 0)

    return result
