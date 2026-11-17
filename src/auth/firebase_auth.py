import os

import requests
from firebase_admin import auth
from src.firebase_client import get_db

def verify_token(id_token: str) -> dict:
    if not id_token:
        raise PermissionError("로그인이 필요합니다.")
    get_db()

    try:
        decode = auth.verify_id_token(
            id_token,
            check_revoked= True,
        )
    except (ValueError, auth.InvalidIdTokenError, auth.UserDisabledError):
        raise PermissionError(
            "인증이 만료되었거나 유효하지 않습니다. 다시 로그인하세요."
        ) from None

    return {
        "uid": decode["uid"],
        "email": decode.get("email",""),
    }

def sign_in(email: str, password: str) -> dict:
    if not email or not email.strip() or not password:
        raise ValueError("이메일과 비밀번호를 입력하세요.")
    
    api_key = os.environ.get("FIREBASE_WEB_API_KEY")

    if not api_key:
        raise RuntimeError("FIREBASE_WEB_API_KEY 설정이 필요합니다.")

    url = (
        "https://identitytoolkit.googleapis.com/v1/"
        "accounts:signInWithPassword"
    )

    try:
        response = requests.post(
            url,
            params={"key": api_key},
            json={
                "email": email.strip(),
                "password": password,
                "returnSecureToken": True,
            },
            timeout=10,
        )
    except requests.RequestException:
        raise ConnectionError(
            "인증 서버에 연결할 수 없습니다."
        ) from None

    if not response.ok:
        code = response.json().get("error", {}).get("message", "")
        if code in {
            "INVALID_LOGIN_CREDENTIALS",
            "EMAIL_NOT_FOUND",
            "INVALID_PASSWORD",
            "INVALID_EMAIL",
        }:
            raise ValueError("이메일 또는 비밀번호를 확인하세요.")

        if code.startswith("TOO_MANY_ATTEMPTS"):
            raise ValueError("잠시 후 다시 로그인하세요.")

        raise RuntimeError("로그인에 실패했습니다. Firebase 설정을 확인하세요.")

    result = response.json()
    id_token = result.get("idToken")
    if not id_token:
        raise RuntimeError("로그인 응답에 인증 토큰이 없습니다.")

    user = verify_token(id_token)

    return {
        "uid": user["uid"],
        "email": user["email"],
        "id_token": id_token,
    }

