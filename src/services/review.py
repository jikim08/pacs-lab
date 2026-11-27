'''담당자 배정, 상태 변경, 메모와 리포트'''

from datetime import datetime, timezone
from uuid import uuid4

from src.exception import ConflictError, NotFoundError, PermissionDeniedError, ValidationError
from src.models import CaseAssignment, CaseHistory, CaseStatus, ReviewKind, ReviewRecord
from src.repositories import case as cases, image as images, processing as processing_records
from src.repositories import review as reviews
from src.repositories.user import get_user
from src.services import access_policy


_WAITING: CaseStatus = "검토 대기"
_REVIEWING: CaseStatus = "검토 중"
_DONE: CaseStatus = "검토 완료"

_ROLE_ADMIN = "ADMIN"
_ROLE_REVIEWER = "REVIEWER"

MAX_CONTENT_LENGTH = 5000

# 검토 완료 조건: 요약 리포트 1건 이상
REQUIRE_REPORT_TO_COMPLETE = True
MSG_REPORT_REQUIRED = "요약 리포트를 저장해야 검토를 완료할 수 있습니다."


def _clean_id(value: str | None, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} 정보가 필요합니다.")
    return value.strip()


def _clean_content(content: str | None, name: str) -> str:
    if not isinstance(content, str) or not content.strip():
        raise ValidationError(f"{name} 내용을 입력해 주세요.")
    content = content.strip()
    if len(content) > MAX_CONTENT_LENGTH:
        raise ValidationError(f"{name} 내용은 {MAX_CONTENT_LENGTH}자 이하로 입력해 주세요.")
    return content


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_case(case_id: str) -> dict:
    case = cases.get_case(case_id)
    if case is None:
        raise NotFoundError("해당 검사를 찾을 수 없습니다.")
    return case


def _assignee_uid(case_id: str) -> str | None:
    assignment = cases.get_case_assignment(case_id)
    return assignment["user_id"] if assignment else None


# 권한 검사(임시): access_policy의 케이스별 권한 함수가 구현되면 교체
# 화면은 AppError만 처리하므로 PermissionError를 PermissionDeniedError로 바꾼다
def _login_user(uid: str) -> dict:
    try:
        return access_policy.require_case_read(uid)
    except PermissionError as error:
        raise PermissionDeniedError(str(error)) from error


# TODO: access_policy.require_case_assignment로 교체
def _require_assigner(uid: str) -> None:
    if _login_user(uid).get("role") != _ROLE_ADMIN:
        raise PermissionDeniedError("업무 담당자만 검토 담당자를 배정할 수 있습니다.")


# TODO: access_policy.require_case_review로 교체
def _require_reviewer_of(uid: str, case_id: str) -> None:
    if _login_user(uid).get("role") != _ROLE_REVIEWER:
        raise PermissionDeniedError("검토 담당자만 검토 작업을 할 수 있습니다.")
    if _assignee_uid(case_id) != uid:
        raise PermissionDeniedError("본인에게 배정된 검사만 검토할 수 있습니다.")


# TODO: access_policy.require_case_access로 교체
def _require_viewer(uid: str) -> None:
    _login_user(uid)


def assign_reviewer(uid: str, case_id: str, reviewer_uid: str) -> CaseAssignment:
    """검토 담당자를 배정하거나 변경한다. 검토 대기 상태에서만 가능하다."""
    case_id = _clean_id(case_id, "검사")
    reviewer_uid = _clean_id(reviewer_uid, "검토 담당자")
    _require_assigner(uid)

    if _get_case(case_id).get("status") != _WAITING:
        raise ConflictError("검토가 시작되었거나 완료된 검사는 담당자를 바꿀 수 없습니다.")

    reviewer = get_user(reviewer_uid)
    if reviewer is None or reviewer.get("role") != _ROLE_REVIEWER:
        raise ValidationError("검토 담당자로 등록된 사용자에게만 배정할 수 있습니다.")

    previous_uid = _assignee_uid(case_id)
    if previous_uid == reviewer_uid:
        raise ConflictError("이미 이 검토 담당자에게 배정된 검사입니다.")

    now = _now()
    assignment: CaseAssignment = {
        "case_id": case_id,
        "user_id": reviewer_uid,
        "assigned_by": uid.strip(),
        "assigned_at": now,
    }
    history: CaseHistory = {
        "history_id": uuid4().hex,
        "case_id": case_id,
        "kind": "ASSIGNMENT_CHANGED",
        "before_value": previous_uid,
        "after_value": reviewer_uid,
        "changed_by": uid.strip(),
        "created_at": now,
    }
    reviews.save_assignment_with_history(assignment, history)
    return assignment


# 상태 변경과 STATUS_CHANGED 이력을 함께 저장
def _change_status(uid: str, case_id: str, before: CaseStatus, after: CaseStatus) -> None:
    history: CaseHistory = {
        "history_id": uuid4().hex,
        "case_id": case_id,
        "kind": "STATUS_CHANGED",
        "before_value": before,
        "after_value": after,
        "changed_by": uid.strip(),
        "created_at": _now(),
    }
    reviews.change_status_with_history(history)


def start_review(uid: str, case_id: str) -> None:
    """배정된 검토 담당자가 검토를 시작한다. 검토 대기 → 검토 중"""
    case_id = _clean_id(case_id, "검사")
    _require_reviewer_of(uid, case_id)
    if _get_case(case_id).get("status") != _WAITING:
        raise ConflictError("검토 대기 중인 검사만 검토를 시작할 수 있습니다.")
    _change_status(uid, case_id, _WAITING, _REVIEWING)


def get_completion_block_reason(uid: str, case_id: str) -> str | None:
    """검토를 완료할 수 없는 이유를 반환한다. 완료할 수 있으면 None."""
    case_id = _clean_id(case_id, "검사")
    _require_viewer(uid)
    return _completion_block_reason(case_id)


def _completion_block_reason(case_id: str) -> str | None:
    if REQUIRE_REPORT_TO_COMPLETE:
        has_report = any(record.get("kind") == "REPORT" for record in reviews.list_review_records(case_id))
        if not has_report:
            return MSG_REPORT_REQUIRED
    return None


def complete_review(uid: str, case_id: str) -> None:
    """배정된 검토 담당자가 검토를 완료한다. 검토 중 → 검토 완료"""
    case_id = _clean_id(case_id, "검사")
    _require_reviewer_of(uid, case_id)
    if _get_case(case_id).get("status") != _REVIEWING:
        raise ConflictError("검토 중인 검사만 검토를 완료할 수 있습니다.")
    reason = _completion_block_reason(case_id)
    if reason:
        raise ConflictError(reason)
    _change_status(uid, case_id, _REVIEWING, _DONE)


# 메모·리포트 작성 조건: 배정된 검토 담당자 본인 + 검토 중
def _require_writable(uid: str, case_id: str) -> None:
    _require_reviewer_of(uid, case_id)
    if _get_case(case_id).get("status") != _REVIEWING:
        raise ConflictError("검토 중인 검사에서만 메모와 리포트를 저장할 수 있습니다.")


# 수정도 덮어쓰지 않고 새 기록으로 추가한다(이전 기록 보존)
def _new_record(
    uid: str, case_id: str, kind: ReviewKind, content: str,
    file_id: str | None, processing_result_id: str | None,
) -> ReviewRecord:
    now = _now()
    record: ReviewRecord = {
        "review_id": uuid4().hex,
        "case_id": case_id,
        "kind": kind,
        "content": content,
        "author_id": uid.strip(),
        "file_id": file_id,
        "processing_result_id": processing_result_id,
        "created_at": now,
        "updated_at": now,
    }
    reviews.save_review_record(record)
    return record


def save_memo(
    uid: str, case_id: str, file_id: str, processing_result_id: str | None, content: str,
) -> ReviewRecord:
    """원본 영상 또는 그 영상의 처리 결과에 메모를 저장한다."""
    case_id = _clean_id(case_id, "검사")
    file_id = _clean_id(file_id, "영상")
    content = _clean_content(content, "메모")
    _require_writable(uid, case_id)

    original = images.get_file_info(file_id)
    if original is None or original["case_id"] != case_id or original["kind"] != "ORIGINAL":
        raise ValidationError("이 검사의 원본 영상에만 메모를 남길 수 있습니다.")

    if processing_result_id is not None:
        processing_result_id = _clean_id(processing_result_id, "처리 결과")
        result = processing_records.get_processing_result(case_id, processing_result_id)
        if result is None or result["input_file_id"] != file_id or result["status"] != "COMPLETED":
            raise ValidationError("선택한 영상의 저장된 처리 결과에만 메모를 남길 수 있습니다.")

    return _new_record(uid, case_id, "MEMO", content, file_id, processing_result_id)


def save_report(uid: str, case_id: str, content: str) -> ReviewRecord:
    """케이스 전체에 대한 요약 리포트를 저장한다."""
    case_id = _clean_id(case_id, "검사")
    content = _clean_content(content, "리포트")
    _require_writable(uid, case_id)
    return _new_record(uid, case_id, "REPORT", content, None, None)


def list_review_records(uid: str, case_id: str) -> list[ReviewRecord]:
    """케이스의 메모와 리포트를 작성 시간 오름차순으로 반환한다."""
    case_id = _clean_id(case_id, "검사")
    _require_viewer(uid)
    _get_case(case_id)
    return reviews.list_review_records(case_id)


def list_case_history(uid: str, case_id: str) -> list[CaseHistory]:
    """케이스의 담당자·상태 변경 이력을 시간 오름차순으로 반환한다."""
    case_id = _clean_id(case_id, "검사")
    _require_viewer(uid)
    _get_case(case_id)
    return reviews.list_case_history(case_id)
