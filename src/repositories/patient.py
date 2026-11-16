from src.config import get_db

def patient_exists(patient_id: str) -> bool:
    db = get_db()
    document = db.collection("patients").document(patient_id).get()

    return document.exists