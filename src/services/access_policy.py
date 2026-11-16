from src.repositories.user import get_user


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
