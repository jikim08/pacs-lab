from src.repositories.user import get_user


def require_case_registration(user_id: str) -> None:
    user = get_user(user_id)

    if user is None:
        raise PermissionError("등록된 사용자 정보가 없습니다.")

    if user.get("role") != "registrar":
        raise PermissionError("등록 담당자만 케이스를 등록할 수 있습니다.")