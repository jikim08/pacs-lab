from src.config import get_db

def department_exists(department_id: str) -> bool:
    db = get_db()
    document = db.collection("departments").document(department_id).get()

    return document.exists