'''케이스를 DB에 저장'''
from google.cloud import firestore

from src.config import get_db

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