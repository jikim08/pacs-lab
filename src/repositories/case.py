'''케이스를 DB에 저장 및 조회'''
from google.cloud import firestore

from src.firebase_client import get_db

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
