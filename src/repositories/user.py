'''사용자 정보와 역할 조회'''

from src.firebase_client import get_db
from src.models import User

def get_user(user_id: str) -> dict | None:
    db = get_db()

    document = db.collection("users").document(user_id).get()

    if not document.exists:
        return None
    
    return document.to_dict()

def list_reviewers(
    department_id: str | None = None,
) -> list[User]:
    """배정 가능한 검토 담당자를 조회한다.

    department_id가 있으면 해당 진료과로 제한한다.
    결과가 없으면 빈 리스트를 반환한다.
    """
    raise NotImplementedError