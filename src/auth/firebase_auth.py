import requests
from firebase_admin import auth
from src.firebase_client import get_db
from src.config import FIREBASE_WEB_API_KEY
from src.exception import AuthenticationError

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
    
    api_key = FIREBASE_WEB_API_KEY

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
        "refresh_token": result["refreshToken"],
        "expires_in": int(result["expiresIn"]),
    }

def refresh_id_token(refresh_token: str) -> dict:
    if not refresh_token:
        raise AuthenticationError("다시 로그인 해주세요")

    if not FIREBASE_WEB_API_KEY:
        raise RuntimeError("FIREBASE_WEB_API_KEY가 필요합니다.")

    try:
        response = requests.post(
            "https://securetoken.googleapis.com/v1/token",
            params={"key": FIREBASE_WEB_API_KEY},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=10,
        )
    except requests.RequestException:
        raise ConnectionError(
            "로그인 갱신 서버에 연결할 수 없습니다."
         ) from None
    
    # 일시적인 서버 장애는 재로그인으로 처리하지 않는다.
    if response.status_code == 429 or response.status_code >= 500:
        raise ConnectionError(
            "로그인 갱신을 잠시 후 다시 시도해 주세요."
        )
    
    if not response.ok:
        raise AuthenticationError(
            "로그인을 갱신할 수 없습니다. 다시 로그인해 주세요."
        )

    result = response.json()

    return {
        "uid": result["user_id"],
        "id_token": result["id_token"],
        "refresh_token": result["refresh_token"],
        "expires_in": int(result["expires_in"]),
    }
