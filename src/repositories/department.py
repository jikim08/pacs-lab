'''진료과 정보 조회'''

from src.config import get_db

def department_exists(department_id: str) -> bool:
    db = get_db()
    document = db.collection("departments").document(department_id).get()

    return document.exists


# 진료과 1개 조회, 없으면 None
def get_department(department_id: str) -> dict | None:
    db = get_db()
    document = db.collection("departments").document(department_id).get()

    if not document.exists:
        return None

    return {"department_id": document.id, **document.to_dict()}
