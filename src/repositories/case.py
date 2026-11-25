'''케이스를 DB에 저장 및 조회'''
from datetime import datetime

from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter

from src.exception import StorageError
from src.firebase_client import get_db
from src.models import Case, CaseAssignment
from src.models import CaseStatus

# DB에 케이스 데이터 저장
def save_case(case_data:dict) -> str:
    db = get_db()

    case_ref = db.collection("cases").document()

    # 데이터에 생성 및 수정 시각 추가
    data = {
        **case_data,
        "created_at" : firestore.SERVER_TIMESTAMP,
        "updated_at" : firestore.SERVER_TIMESTAMP
    }

    case_ref.set(data)

    return case_ref.id


# DB에서 케이스 목록 조회 (등록 시각 최신순)
def list_cases(limit: int = 50) -> list[dict]:
    db = get_db()

    query = (
        db.collection("cases")
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )

    return [{"case_id": doc.id, **doc.to_dict()} for doc in query.stream()]


# DB에서 케이스 1건 조회, 없으면 None
def get_case(case_id: str) -> dict | None:
    db = get_db()

    document = db.collection("cases").document(case_id).get()

    if not document.exists:
        return None

    return {"case_id": document.id, **document.to_dict()}

# 조건에 맞는 케이스 검색 (등록 시각 최신순)
# 일치 조건은 Firestore where로 범위를 줄이고, 모든 조건 확인과 정렬은 Python에서 처리 → 복합 색인 불필요
# created_from 이상, created_to 미만 (끝 시각은 포함하지 않음)
# 모든 매개변수의 기본값이 None이다. 그렇기에 모든 조건은 선택사항이 될 수 있다.
def search_cases(
    case_id: str | None = None,
    status: str | None = None,
    exam_type: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> list[dict]:
    try:
        if case_id is not None:
            # 케이스 ID는 문서 ID이므로 1건 직접 조회
            case = get_case(case_id)
            cases = [] if case is None else [case]
        else:
            query = get_db().collection("cases")
            # 조건이 존재한다면 where 붙여서 검색
            # case_id 는 위에서 처리, created_from / to 는 _matches에서 조건 적용.
            # 필드 일치 조건과 범위 조건을 함께 사용하면 Firestore에서 복합 색인이 필요하다.
            # 그렇기에 필드 일치 조건은 Firestore에서, 범위 조건은 Python에서 확인하도록 작성했다.
            if status is not None:
                query = query.where(filter=FieldFilter("status", "==", status))
            if exam_type is not None:
                query = query.where(filter=FieldFilter("exam_type", "==", exam_type))
            cases = [{"case_id": doc.id, **doc.to_dict()} for doc in query.stream()]
    except (GoogleAPICallError, RetryError) as error:
        raise StorageError("케이스를 검색하지 못했습니다.") from error

    matched = [
        case for case in cases
        if _matches(case, status, exam_type, created_from, created_to)
    ]
    return sorted(matched, key=lambda case: case["created_at"], reverse=True)


def _matches(
    case: dict,
    status: str | None,
    exam_type: str | None,
    created_from: datetime | None,
    created_to: datetime | None,
) -> bool:
    """케이스가 모든 검색 조건을 만족하는지 확인한다."""
    created_at = case.get("created_at")
    if created_at is None:
        return False
    if status is not None and case.get("status") != status:
        return False
    if exam_type is not None and case.get("exam_type") != exam_type:
        return False
    if created_from is not None and created_at < created_from:
        return False
    if created_to is not None and created_at >= created_to:
        return False
    return True

def get_case_assignment(
    case_id: str,
) -> CaseAssignment | None:
    """현재 담당자 정보를 조회한다.

    미배정 상태이면 None을 반환한다.
    """
    raise NotImplementedError


def save_case_assignment(
    assignment: CaseAssignment,
) -> None:
    """현재 담당자 정보를 저장한다.

    같은 케이스의 기존 배정이 있으면 교체한다.
    배정 권한과 재배정 가능 여부는 서비스에서 검사한다.
    """
    raise NotImplementedError


def update_case_status(
    case_id: str,
    expected_status: CaseStatus,
    new_status: CaseStatus,
) -> None:
    """현재 상태를 확인한 뒤 새 상태와 수정 시간을 저장한다.

    현재 상태 확인과 갱신은 트랜잭션에서 함께 수행한다.

    케이스 없음: NotFoundError
    현재 상태가 expected_status와 다름: ConflictError
    """
    raise NotImplementedError