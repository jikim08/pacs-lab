'''케이스를 DB에 저장 및 조회'''
from google.cloud import firestore

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