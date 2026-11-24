"""Store and retrieve processing history for authorized services."""

from typing import cast

from google.api_core.exceptions import Conflict, GoogleAPICallError, RetryError
from google.cloud.firestore_v1 import DocumentSnapshot, FieldFilter

from src.exception import ConflictError, StorageError, ValidationError
from src.firebase_client import get_db
from src.models import FileInfo, ProcessingResult


_RESULTS_COLLECTION = "processing_results"


def _validate_id(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or "/" in value or value in (".", ".."):
        raise ValidationError("올바른 처리 기록 식별자가 필요합니다.")


def _read_result(document: DocumentSnapshot) -> ProcessingResult:
    return cast(ProcessingResult, {**document.to_dict(), "processing_result_id": document.id})


def save_processing_result(result: ProcessingResult) -> None:
    """Append one execution record. A reused ID raises ConflictError."""
    data = dict(result)
    result_id = data.pop("processing_result_id")
    _validate_id(result_id)
    try:
        get_db().collection(_RESULTS_COLLECTION).document(result_id).create(data)
    except Conflict as error:
        raise ConflictError("이미 저장된 처리 기록입니다.") from error
    except (GoogleAPICallError, RetryError, ValueError) as error:
        raise StorageError("처리 기록을 저장하지 못했습니다.") from error


def save_processing_result_with_file(result: ProcessingResult, output_file: FileInfo) -> None:
    """Create the completed execution and its output metadata in one batch."""
    result_data = dict(result)
    file_data = dict(output_file)
    result_id = result_data.pop("processing_result_id")
    file_id = file_data.pop("file_id")
    _validate_id(result_id)
    _validate_id(file_id)
    if (
        result["status"] != "COMPLETED"
        or output_file["kind"] != "RESULT"
        or output_file["case_id"] != result["case_id"]
        or output_file["processing_result_id"] != result_id
    ):
        raise ValidationError("처리 기록과 결과 영상의 연결 정보가 일치하지 않습니다.")

    try:
        db = get_db()
        batch = db.batch()
        batch.create(db.collection(_RESULTS_COLLECTION).document(result_id), result_data)
        batch.create(db.collection("files").document(file_id), file_data)
        batch.commit()
    except Conflict as error:
        raise ConflictError("이미 저장된 처리 기록 또는 결과 영상입니다.") from error
    except (GoogleAPICallError, RetryError, ValueError) as error:
        raise StorageError("처리 기록과 결과 영상 정보를 저장하지 못했습니다.") from error


def get_processing_result(case_id: str, result_id: str) -> ProcessingResult | None:
    """Read one record from the given case, or return None if it is absent."""
    _validate_id(case_id)
    _validate_id(result_id)
    try:
        document = get_db().collection(_RESULTS_COLLECTION).document(result_id).get()
    except (GoogleAPICallError, RetryError, ValueError) as error:
        raise StorageError("처리 기록을 조회하지 못했습니다.") from error
    if not document.exists:
        return None
    result = _read_result(document)
    return result if result["case_id"] == case_id else None


def list_processing_results(case_id: str, *, input_file_id: str | None = None) -> list[ProcessingResult]:
    """Return the case or source image history in newest-first order."""
    _validate_id(case_id)
    if input_file_id is not None:
        _validate_id(input_file_id)
    try:
        query = get_db().collection(_RESULTS_COLLECTION).where(filter=FieldFilter("case_id", "==", case_id))
        if input_file_id is not None:
            query = query.where(filter=FieldFilter("input_file_id", "==", input_file_id))
        records = [_read_result(document) for document in query.stream()]
    except (GoogleAPICallError, RetryError, ValueError) as error:
        raise StorageError("처리 이력을 조회하지 못했습니다.") from error
    return sorted(records, key=lambda record: (record["created_at"], record["processing_result_id"]), reverse=True)
