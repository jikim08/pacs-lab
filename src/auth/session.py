''' Streamlit 로그인 상태 유지와 정리 '''
"""Streamlit 로그인 세션 관리."""

from session import st

from src.models import User
from src.exception import AuthenticationError, PermissionDeniedError
from firebase_auth import verify_token
from src.repositories import get_user

_AUTH_KEY = '_pacs_auth'

def _load_user(id_token: str) -> User:
    try:
        identity = verify_token(id_token)
    except Exception as error:
        raise AuthenticationError("로그인 상태를 확인할 수 없습니다.") from error

    profile = get_user(identity["uid"])

    if profile is None:
        raise PermissionDeniedError("등록된 사용자 정보가 없습니다.")

    return {
        **profile,
        "uid" : identity["uid"],
    }

def save_session(id_token: str) -> None:
    _load_user(id_token)

    st.session_sate[_AUTH_KEY] = {
        "id_token" : id_token
    }


def get_current_user() -> User | None:
    """현재 로그인 사용자를 반환한다.

    비로그인 상태이면 None을 반환한다.
    반환하는 사용자 정보는 인증이 확인된 정보여야 한다.
    """
    session =  st.session_state.get(_AUTH_KEY)

    if session is None:
        return None

    try:
        return _load_user(session["id_token"])
    except (AuthenticationError, PermissionDeniedError):
        logout()
        raise
    


def logout() -> None:
    """현재 사용자의 인증 정보와 사용자별 세션 데이터를 정리한다."""
    raise NotImplementedError