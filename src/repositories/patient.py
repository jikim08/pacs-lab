'''모의 환자 정보 조회'''

from src.firebase_client import get_db

# DB에 환자 존재 확인
def patient_exists(patient_id: str) -> bool:
    db = get_db()
    document = db.collection("patients").document(patient_id).get()

    return document.exists


# 환자 1명 조회, 없으면 None
def get_patient(patient_id: str) -> dict | None:
    db = get_db()
    document = db.collection("patients").document(patient_id).get()

    if not document.exists:
        return None

    return {"patient_id": document.id, **document.to_dict()}
