from src.repositories.user import get_user
from src.auth.firebase_auth import verify_token

# 사용자 권한 확인
def require_case_registration(user_id: str) -> None:
    user = get_user(user_id)

    if user is None:
        raise PermissionError("등록된 사용자 정보가 없습니다.")

    if user.get("role") != "registrar":
        raise PermissionError("등록 담당자만 케이스를 등록할 수 있습니다.")


# 케이스 조회 권한 검사
# 등록된 사용자는 모든 케이스 조회 가능
# 역할별 조회 허용(D-01)이나 과/담당 범위로 바뀌면 이 함수에서 확장
def require_case_read(uid: str) -> dict:
    if not uid or not uid.strip():
        raise PermissionError("로그인이 필요합니다.")

    user = get_user(uid.strip())

    if user is None:
        raise PermissionError("등록된 사용자 정보가 없습니다.")

    return user

def get_current_user(id_token: str) -> dict:
    # 1. 토큰 검증으로 실제 사용자 UID 확인
    identity = verify_token(id_token)

    # 2. Firestore에서 사용자 프로필 조회
    profile = get_user(identity["uid"])

    if profile is None:
        raise PermissionError("등록된 사용자 정보가 없습니다.")

    # 3. 앱에서 허용하는 역할인지 확인
    if profile.get("role") not in {"registrar", "reviewer", "manager"}:
        raise PermissionError("사용자 역할 설정을 확인하세요.")

    return {
        "uid": identity["uid"],
        "email": identity["email"],
        "name": profile.get("name", ""),
        "role": profile["role"],
    }
