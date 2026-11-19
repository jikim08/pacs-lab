"""Analyze rectangular regions in original image pixel coordinates."""

from numbers import Integral

import numpy as np

from src.exception import ValidationError
from src.models import ROI, ROIStatistics

from .image_io import validate_grayscale_image


def extract_roi(image: np.ndarray, roi: ROI) -> np.ndarray:
    """Return the selected rectangle without rounding or clipping coordinates."""
    validate_grayscale_image(image)
    if not isinstance(roi, dict):
        raise ValidationError("ROI 좌표와 크기를 입력해 주세요.")

    coordinates = (roi.get("x"), roi.get("y"), roi.get("width"), roi.get("height"))
    if any(isinstance(value, bool) or not isinstance(value, Integral) for value in coordinates):
        raise ValidationError("ROI 좌표와 크기는 정수 픽셀 단위여야 합니다.")

    x, y, width, height = (int(value) for value in coordinates)
    rows, columns = image.shape
    if x < 0 or y < 0 or width <= 0 or height <= 0:
        raise ValidationError("ROI 시작 좌표는 0 이상이고 너비와 높이는 양수여야 합니다.")
    if x + width > columns or y + height > rows:
        raise ValidationError("ROI가 영상 범위를 벗어났습니다.")
    return image[y : y + height, x : x + width]


def analyze_roi(image: np.ndarray, roi: ROI) -> ROIStatistics:
    """Return population statistics and 32 brightness bins of width eight."""
    region = extract_roi(image, roi)
    return {
        "mean": float(region.mean(dtype=np.float64)),
        "variance": float(region.var(dtype=np.float64)),
        "histogram": np.bincount((region // 8).ravel(), minlength=32).tolist(),
        "bin_edges": [float(edge) for edge in range(0, 257, 8)],
    }
