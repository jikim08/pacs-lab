"""Run image processing for callers that have checked case access."""

import cv2
import numpy as np

from src.exception import AppError, ValidationError
from src.models import ProcessingParameters
from src.processing.algorithm import apply_canny, apply_clahe, apply_median


_ALGORITHMS = {
    "CLAHE": (apply_clahe, {"clip_limit", "tile_grid_size"}),
    "Median": (apply_median, {"kernel_size"}),
    "Canny": (apply_canny, {"low_threshold", "high_threshold"}),
}


def run_processing(
    original: np.ndarray, algorithm: str, parameters: ProcessingParameters,
) -> np.ndarray:
    """Process the entire source image and return the preview pixels."""
    selected = _ALGORITHMS.get(algorithm)
    if selected is None:
        raise ValidationError("지원하는 영상처리 알고리즘을 선택해 주세요.")
    apply_algorithm, supported_parameters = selected
    if parameters.keys() - supported_parameters:
        raise ValidationError(f"{algorithm}에 사용할 수 없는 처리 설정이 있어요.")

    try:
        return apply_algorithm(original, **parameters)
    except cv2.error as error:
        raise AppError("영상처리 중 오류가 발생했어요.") from error
