"""Run image processing and manage results within an authorized case."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

import cv2
import numpy as np

from src.exception import AppError, ConflictError, NotFoundError, ValidationError
from src.models import Case, FileInfo, ProcessingParameters, ProcessingResult
from src.processing.algorithm import apply_canny, apply_clahe, apply_median
from src.processing.image_io import decode_image
from src.repositories import case as cases, file_storage, image as images
from src.services import access_policy


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


@dataclass(frozen=True)
class ProcessingPreview:
    record: ProcessingResult
    pixels: np.ndarray


def _require_processing(uid: str, case_id: str) -> None:
    case = cases.get_case(case_id)
    if case is None:
        raise NotFoundError("해당 검사를 찾을 수 없어요.")
    access_policy.require_case_processing(uid, cast(Case, case), cases.get_case_assignment(case_id))
    if case["status"] != "검토 중":
        raise ConflictError("검토 중인 검사에서만 영상처리를 할 수 있어요.")


def _get_original(case_id: str, input_file_id: str) -> FileInfo:
    original = images.get_file_info(input_file_id)
    if original is None or original["case_id"] != case_id or original["kind"] != "ORIGINAL":
        raise NotFoundError("선택한 검사의 원본 영상을 찾을 수 없어요.")
    return original


def create_processing_preview(
    uid: str, case_id: str, input_file_id: str, algorithm: str, parameters: ProcessingParameters,
) -> ProcessingPreview:
    """Process the actual case file and keep the metadata for a later save."""
    _require_processing(uid, case_id)
    original = _get_original(case_id, input_file_id)
    pixels = decode_image(file_storage.read_file(original["storage_path"]))
    settings = dict(parameters)
    started_at = datetime.now(timezone.utc)
    result = run_processing(pixels, algorithm, settings)
    completed_at = datetime.now(timezone.utc)
    return ProcessingPreview({
        "processing_result_id": uuid4().hex,
        "case_id": case_id,
        "processing_type_id": algorithm,
        "algorithm_version": "1",
        "parameters": settings,
        "performed_by": uid,
        "input_file_id": input_file_id,
        "status": "COMPLETED",
        "roi": None,
        "statistics": None,
        "error_message": None,
        "created_at": started_at,
        "started_at": started_at,
        "completed_at": completed_at,
    }, result)
