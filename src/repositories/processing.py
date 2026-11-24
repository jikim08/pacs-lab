"""Store processing history for authorized services."""

from google.api_core.exceptions import Conflict, GoogleAPICallError, RetryError

from src.exception import ConflictError, StorageError, ValidationError
from src.firebase_client import get_db
from src.models import ProcessingResult


_RESULTS_COLLECTION = "processing_results"


def _validate_id(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or "/" in value or value in (".", ".."):
        raise ValidationError("올바른 처리 기록 식별자가 필요합니다.")


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
