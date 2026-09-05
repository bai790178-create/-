import math


def estimate_spacing(centerlines):
    """Estimate perpendicular stripe spacing from ordered centerline polylines."""
    try:
        import numpy as np
    except Exception as exc:
        raise RuntimeError("计算标注间距需要 NumPy。") from exc

    usable = []
    for line in centerlines or []:
        points = np.asarray(line.get("points") or [], dtype=np.float64)
        if points.ndim != 2 or points.shape[0] < 2 or points.shape[1] != 2:
            continue
        usable.append((int(line["order"]), points))

    if len(usable) < 2:
        return None

    orders = np.asarray([item[0] for item in usable], dtype=np.float64)
    if len(set(orders.tolist())) != len(orders):
        raise ValueError("条纹序号不能重复。")
    if float(np.ptp(orders)) < 1.0:
        return None

    directions = []
    reference = None
    for _, points in usable:
        direction = points[-1] - points[0]
        length = float(np.linalg.norm(direction))
        if length < 1e-6:
            continue
        direction = direction / length
        if reference is None:
            reference = direction
        elif float(np.dot(direction, reference)) < 0.0:
            direction = -direction
        directions.append(direction)

    if not directions:
        return None

    tangent = np.mean(np.asarray(directions), axis=0)
    tangent_length = float(np.linalg.norm(tangent))
    if tangent_length < 1e-6:
        return None
    tangent = tangent / tangent_length
    normal = np.asarray([-tangent[1], tangent[0]], dtype=np.float64)

    centers = np.asarray([np.mean(points, axis=0) for _, points in usable])
    positions = centers @ normal
    design = np.column_stack([np.ones_like(orders), orders])
    coefficients, _, _, _ = np.linalg.lstsq(design, positions, rcond=None)
    fitted = design @ coefficients
    residuals = positions - fitted
    spacing = abs(float(coefficients[1]))
    if spacing < 1e-6:
        return None

    residual_std = float(np.std(residuals, ddof=1)) if len(residuals) >= 2 else 0.0
    uncertainty = max(0.2, residual_std / math.sqrt(float(len(residuals))))
    orientation_deg = math.degrees(math.atan2(float(tangent[1]), float(tangent[0]))) % 180.0

    return {
        "spacing_px": round(spacing, 4),
        "spacing_uncertainty_px": round(uncertainty, 4),
        "fit_residual_std_px": round(residual_std, 4),
        "orientation_deg": round(orientation_deg, 4),
        "stripe_count": len(usable),
    }
