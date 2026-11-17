'''사용자 정보와 역할 조회'''

from src.config import get_db

def get_user(user_id: str) -> dict | None:
    db = get_db()

    document = db.collection("users").document(user_id).get()

    if not document.exists:
        return None
    
    return document.to_dict()