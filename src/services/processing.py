"""Run image processing and manage results within an authorized case."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

import cv2
import numpy as np

from src.exception import AppError, ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from src.models import Case, FileInfo, ProcessingParameters, ProcessingResult, ROI
from src.processing.algorithm import apply_canny, apply_clahe, apply_median
from src.processing.image_io import decode_image, encode_png
from src.processing.roi import analyze_roi
from src.repositories import case as cases, file_storage, image as images, processing as records
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


def _save_result_png(case_id: str, result_id: str, png: bytes) -> str:
    try:
        return file_storage.save_file(case_id, result_id, png, "png")
    except ConflictError:
        # A prior attempt may have written the PNG before its metadata became available.
        path = f"cases/{case_id}/files/{result_id}.png"
        if file_storage.read_file(path) != png:
            raise ConflictError("같은 저장 위치에 다른 결과 영상이 있습니다.") from None
        return path


def _confirm_saved_result(expected: ProcessingResult) -> ProcessingResult | None:
    saved = records.get_processing_result(expected["case_id"], expected["processing_result_id"])
    if saved is None:
        return None
    match_fields = ("input_file_id", "performed_by", "processing_type_id", "parameters", "roi", "status")
    if any(saved[field] != expected[field] for field in match_fields):
        raise ConflictError("다른 설정 또는 ROI로 저장된 결과예요. 이력에서 다시 열어 주세요.")
    return saved


def save_processing_preview(
    uid: str, case_id: str, input_file_id: str, preview: ProcessingPreview, roi: ROI | None,
) -> ProcessingResult:
    """Save an authorized preview and publish its linked metadata together."""
    _require_processing(uid, case_id)
    record = preview.record
    if record["case_id"] != case_id or record["input_file_id"] != input_file_id or record["performed_by"] != uid:
        raise PermissionDeniedError("현재 선택한 원본 영상의 처리 결과만 저장할 수 있어요.")
    original = _get_original(case_id, input_file_id)
    png = encode_png(preview.pixels)
    if roi is not None:
        original_pixels = decode_image(file_storage.read_file(original["storage_path"]))
        statistics = analyze_roi(original_pixels, roi)
    else:
        statistics = None
    now = datetime.now(timezone.utc)
    result: ProcessingResult = {**record, "created_at": now, "roi": roi, "statistics": statistics}
    saved = _confirm_saved_result(result)
    if saved is not None:
        return saved

    result_id = record["processing_result_id"]
    path = _save_result_png(case_id, result_id, png)
    output: FileInfo = {
        "file_id": result_id,
        "case_id": case_id,
        "processing_result_id": result_id,
        "kind": "RESULT",
        "original_name": f"{result_id}.png",
        "storage_path": path,
        "content_type": "image/png",
        "size_bytes": len(png),
        "uploaded_by": uid,
        "created_at": now,
    }
    try:
        records.save_processing_result_with_file(result, output)
    except ConflictError:
        saved = _confirm_saved_result(result)
        if saved is None:
            raise
        return saved
    return result
