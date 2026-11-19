"""Apply image processing algorithms to the entire grayscale image."""

from numbers import Integral, Real

import cv2
import numpy as np

from src.exception import ValidationError

from .image_io import validate_grayscale_image


def apply_clahe(
    image: np.ndarray, *, clip_limit: float = 2.0, tile_grid_size: int = 8
) -> np.ndarray:
    """Enhance local contrast using a square grid with the given tile count."""
    validate_grayscale_image(image)
    if (
        isinstance(clip_limit, bool)
        or not isinstance(clip_limit, Real)
        or not 0.1 <= clip_limit <= 10.0
    ):
        raise ValidationError("CLAHE 대비 제한값은 0.1 이상 10 이하여야 합니다.")
    if not isinstance(tile_grid_size, Integral) or tile_grid_size not in (4, 8, 16):
        raise ValidationError("CLAHE 타일 격자는 4, 8, 16 중 선택해 주세요.")

    tiles = int(tile_grid_size)
    clahe = cv2.createCLAHE(clipLimit=float(clip_limit), tileGridSize=(tiles, tiles))
    return clahe.apply(image)


def apply_median(image: np.ndarray, *, kernel_size: int = 5) -> np.ndarray:
    """Reduce noise using a square median filter."""
    validate_grayscale_image(image)
    if (
        not isinstance(kernel_size, Integral)
        or not 3 <= kernel_size <= 15
        or kernel_size % 2 == 0
    ):
        raise ValidationError("Median 필터 크기는 3 이상 15 이하인 홀수여야 합니다.")
    return cv2.medianBlur(image, int(kernel_size))


def apply_canny(
    image: np.ndarray, *, low_threshold: int = 10, high_threshold: int = 30
) -> np.ndarray:
    """Smooth with a 5 × 5 Gaussian kernel before detecting edges."""
    validate_grayscale_image(image)
    if (
        isinstance(low_threshold, bool)
        or isinstance(high_threshold, bool)
        or not isinstance(low_threshold, Integral)
        or not isinstance(high_threshold, Integral)
        or not 0 <= low_threshold < high_threshold <= 255
    ):
        raise ValidationError("Canny 임계값은 0 이상 255 이하이며 하한이 상한보다 작아야 합니다.")

    smoothed = cv2.GaussianBlur(image, (5, 5), sigmaX=0)
    return cv2.Canny(smoothed, int(low_threshold), int(high_threshold))
