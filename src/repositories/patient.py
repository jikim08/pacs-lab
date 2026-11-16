from src.config import get_db

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
