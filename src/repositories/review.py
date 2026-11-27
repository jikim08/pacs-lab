"""Firestore 검토 기록과 업무 변경 이력 관리."""

from datetime import datetime
from typing import cast

from google.api_core.exceptions import Conflict, GoogleAPICallError, RetryError
from google.cloud import firestore
from google.cloud.firestore_v1 import DocumentSnapshot, FieldFilter

from src.exception import ConflictError, NotFoundError, StorageError, ValidationError
from src.firebase_client import get_db
from src.models import CaseAssignment, CaseHistory, ReviewRecord


_RECORDS_COLLECTION = "review_records"
_HISTORY_COLLECTION = "case_history"
_ASSIGNMENTS_COLLECTION = "case_assignees"
_CASES_COLLECTION = "cases"

# Firestore 연결·저장 실패 (ValueError: 인증 설정 없음, 트랜잭션 재시도 초과)
_STORAGE_ERRORS = (GoogleAPICallError, RetryError, ValueError)


def _validate_id(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or "/" in value or value in (".", ".."):
        raise ValidationError("올바른 검토 기록 식별자가 필요합니다.")


# 문서 ID로 쓸 필드를 데이터에서 빼서 (ID, 나머지 데이터)로 나눈다
def _split_id(data: dict, id_field: str) -> tuple[str, dict]:
    copied = dict(data)
    document_id = copied.pop(id_field, None)
    _validate_id(document_id)
    return document_id, copied


def _read_document(document: DocumentSnapshot, id_field: str) -> dict:
    return {**(document.to_dict() or {}), id_field: document.id}


# created_at 오름차순, 시각이 같으면 ID순. created_at이 없는 문서는 제외
def _sorted_by_time(items: list[dict], id_field: str) -> list[dict]:
    valid = [item for item in items if isinstance(item.get("created_at"), datetime)]
    return sorted(valid, key=lambda item: (item["created_at"], item[id_field]))


def _query_by_case(collection: str, case_id: str, id_field: str, message: str) -> list[dict]:
    _validate_id(case_id)
    try:
        query = get_db().collection(collection).where(filter=FieldFilter("case_id", "==", case_id))
        return [_read_document(document, id_field) for document in query.stream()]
    except _STORAGE_ERRORS as error:
        raise StorageError(message) from error


def save_review_record(record: ReviewRecord) -> None:
    """메모 또는 리포트를 신규 저장한다.

    같은 review_id가 이미 존재하면 ConflictError를 발생시킨다.
    """
    review_id, data = _split_id(record, "review_id")
    try:
        get_db().collection(_RECORDS_COLLECTION).document(review_id).create(data)
    except Conflict as error:
        raise ConflictError("이미 저장된 검토 기록입니다.") from error
    except _STORAGE_ERRORS as error:
        raise StorageError("검토 기록을 저장하지 못했습니다.") from error


def list_review_records(
    case_id: str,
) -> list[ReviewRecord]:
    """케이스의 검토 기록을 작성 시간 오름차순으로 반환한다.

    결과가 없으면 빈 리스트를 반환한다.
    """
    records = _query_by_case(_RECORDS_COLLECTION, case_id, "review_id", "검토 기록을 조회하지 못했습니다.")
    return cast(list[ReviewRecord], _sorted_by_time(records, "review_id"))


def save_case_history(history: CaseHistory) -> None:
    """담당자 또는 상태 변경 이력을 신규 저장한다.

    같은 history_id가 이미 존재하면 ConflictError를 발생시킨다.
    """
    history_id, data = _split_id(history, "history_id")
    try:
        get_db().collection(_HISTORY_COLLECTION).document(history_id).create(data)
    except Conflict as error:
        raise ConflictError("이미 저장된 변경 이력입니다.") from error
    except _STORAGE_ERRORS as error:
        raise StorageError("변경 이력을 저장하지 못했습니다.") from error


def list_case_history(case_id: str) -> list[CaseHistory]:
    """케이스의 변경 이력을 시간 오름차순으로 반환한다.

    결과가 없으면 빈 리스트를 반환한다.
    """
    items = _query_by_case(_HISTORY_COLLECTION, case_id, "history_id", "변경 이력을 조회하지 못했습니다.")
    return cast(list[CaseHistory], _sorted_by_time(items, "history_id"))


def save_assignment_with_history(assignment: CaseAssignment, history: CaseHistory) -> None:
    """현재 담당자 교체와 담당자 변경 이력 추가를 한 트랜잭션으로 저장한다.

    실제 담당자가 history.before_value와 다르거나 같은 history_id가 있으면 ConflictError.
    """
    case_id, assignment_data = _split_id(assignment, "case_id")
    history_id, history_data = _split_id(history, "history_id")
    if (
        history["case_id"] != case_id
        or history["kind"] != "ASSIGNMENT_CHANGED"
        or history["after_value"] != assignment["user_id"]
    ):
        raise ValidationError("담당자 정보와 변경 이력이 서로 맞지 않습니다.")

    def _run(transaction) -> None:
        assignment_ref = db.collection(_ASSIGNMENTS_COLLECTION).document(case_id)
        snapshot = assignment_ref.get(transaction=transaction)
        current_uid = (snapshot.to_dict() or {}).get("user_id") if snapshot.exists else None
        if current_uid != history["before_value"]:
            raise ConflictError("그사이 담당자가 바뀌었습니다. 화면을 새로고침한 뒤 다시 배정해 주세요.")
        transaction.set(assignment_ref, assignment_data)
        transaction.create(db.collection(_HISTORY_COLLECTION).document(history_id), history_data)

    try:
        db = get_db()
        firestore.transactional(_run)(db.transaction())
    except Conflict as error:
        raise ConflictError("이미 저장된 변경 이력입니다.") from error
    except _STORAGE_ERRORS as error:
        raise StorageError("담당자와 변경 이력을 저장하지 못했습니다.") from error


def change_status_with_history(history: CaseHistory) -> None:
    """케이스 상태 변경과 상태 변경 이력 추가를 한 트랜잭션으로 저장한다.

    before_value는 현재 상태, after_value는 바꿀 상태.
    케이스 없음: NotFoundError / 현재 상태가 다르거나 같은 history_id가 있음: ConflictError
    """
    history_id, history_data = _split_id(history, "history_id")
    case_id = history["case_id"]
    _validate_id(case_id)
    if history["kind"] != "STATUS_CHANGED" or not history["before_value"] or not history["after_value"]:
        raise ValidationError("상태 변경 이력에는 바꾸기 전과 후의 상태가 필요합니다.")

    def _run(transaction) -> None:
        case_ref = db.collection(_CASES_COLLECTION).document(case_id)
        snapshot = case_ref.get(transaction=transaction)
        if not snapshot.exists:
            raise NotFoundError("해당 검사를 찾을 수 없습니다.")
        if (snapshot.to_dict() or {}).get("status") != history["before_value"]:
            raise ConflictError("그사이 검사 상태가 바뀌었습니다. 화면을 새로고침해 주세요.")
        transaction.update(case_ref, {"status": history["after_value"], "updated_at": history["created_at"]})
        transaction.create(db.collection(_HISTORY_COLLECTION).document(history_id), history_data)

    try:
        db = get_db()
        firestore.transactional(_run)(db.transaction())
    except Conflict as error:
        raise ConflictError("이미 저장된 변경 이력입니다.") from error
    except _STORAGE_ERRORS as error:
        raise StorageError("검사 상태와 변경 이력을 저장하지 못했습니다.") from error
