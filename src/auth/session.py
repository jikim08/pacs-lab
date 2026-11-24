''' Streamlit 로그인 상태 유지와 정리 '''
"""Streamlit 로그인 세션 관리."""

import streamlit as st
import time

from src.models import User
from src.exception import AuthenticationError, PermissionDeniedError
from .firebase_auth import verify_token, refresh_id_token
from src.repositories.user import get_user

from streamlit_cookies_controller import CookieController

from src.auth.session_store import (
    create_session,
    read_session,
    update_session,
    delete_session,
)

_AUTH_KEY = '_pacs_auth'
_REFRESH_MARGIN = 60

_COOKIE_NAME = "pacs_session_id"
_SESSION_ID_KEY = "_pacs_session_id"
_COOKIE_MANAGER_KEY = "_pacs_cookie_manager"

def _load_user(id_token: str) -> User:
    try:
        identity = verify_token(id_token)
    except PermissionError as error:
        raise AuthenticationError("로그인 상태를 확인할 수 없습니다.") from error

    profile = get_user(identity["uid"])

    if profile is None:
        raise PermissionDeniedError("등록된 사용자 정보가 없습니다.")

    return {
        **profile,
        "uid" : identity["uid"],
    }

def init_session() -> None:
    """현재 화면 실행에서 사용할 쿠키 컴포넌트를 준비한다."""
    st.session_state[_COOKIE_MANAGER_KEY] = CookieController()

def save_session(id_token: str, refresh_token: str, expires_in: int) -> None:
    """인증을 확인한 뒤 토큰과 만료 시각을 저장한다."""
    if not refresh_token or expires_in <= 0:
        raise AuthenticationError("로그인 응답을 확인할 수 없습니다.")

    issued_at = time.time()
    user = _load_user(id_token)

    session_data = {
        "uid": user["uid"],
        "id_token": id_token,
        "refresh_token": refresh_token,
        "expires_at": issued_at + expires_in,
    }
    cookies = st.session_state[_COOKIE_MANAGER_KEY]

    # 같은 브라우저에 남아 있는 이전 서버 세션을 정리합니다.
    previous_id = (
        st.session_state.get(_SESSION_ID_KEY)
        or cookies.get(_COOKIE_NAME)
    )
    if isinstance(previous_id, str) and previous_id:
        delete_session(previous_id)

    # 토큰 정보는 서버에 저장하고, 임의의 세션 ID만 받습니다.
    session_id = create_session(session_data)

    # 현재 화면에서도 계속 사용할 수 있도록 저장합니다.
    st.session_state[_AUTH_KEY] = session_data
    st.session_state[_SESSION_ID_KEY] = session_id

    # 브라우저에는 토큰 대신 세션 ID를 저장합니다.
    cookies.set(_COOKIE_NAME, session_id)



def get_current_user() -> User | None:
    """현재 로그인 사용자를 반환한다.

    비로그인 상태이면 None을 반환한다.
    반환하는 사용자 정보는 인증이 확인된 정보여야 한다.
    """
    cookies = st.session_state[_COOKIE_MANAGER_KEY]

    # 현재 세션에 ID가 없으면 브라우저 쿠키에서 찾습니다.
    session_id = (
        st.session_state.get(_SESSION_ID_KEY)
        or cookies.get(_COOKIE_NAME)
    )

    if not isinstance(session_id, str) or not session_id:
        return None

    st.session_state[_SESSION_ID_KEY] = session_id

    # 현재 화면이 로그인 상태여도 서버의 유지 기한을 확인합니다.
    stored = read_session(session_id)

    if stored is None:
        logout()
        return None

    session = st.session_state.get(_AUTH_KEY)

    # 새로고침으로 사라진 현재 세션을 서버 정보로 복원합니다.
    if session is None:
        session = stored
        st.session_state[_AUTH_KEY] = session

    required_keys = {
        "uid",
        "id_token",
        "refresh_token",
        "expires_at",
    }
    if not required_keys.issubset(session):
        logout()
        return None
    
    try:
        if time.time() >= session["expires_at"] - _REFRESH_MARGIN:
            requested_at = time.time()
            refreshed = refresh_id_token(session["refresh_token"])

            # 토큰 갱신 과정에서 사용자가 바뀌면 거절한다.
            if refreshed["uid"] != session["uid"]:
                raise AuthenticationError(
                    "로그인 사용자 정보가 일치하지 않습니다."
                )

            user = _load_user(refreshed["id_token"])

            if user["uid"] != session["uid"]:
                raise AuthenticationError(
                    "로그인 사용자 정보가 일치하지 않습니다."
                )

            updated_session = {
                "uid": user["uid"],
                "id_token": refreshed["id_token"],
                "refresh_token": refreshed["refresh_token"],
                "expires_at": requested_at + refreshed["expires_in"],
            }

            # 새로고침 후에도 최신 토큰을 가져오도록 서버에도 반영합니다.
            if not update_session(session_id, updated_session):
                raise AuthenticationError(
                    "로그인 유지 시간이 끝났습니다. 다시 로그인해 주세요."
                )

            st.session_state[_AUTH_KEY] = updated_session

            return user

        return _load_user(session["id_token"])

    except (AuthenticationError, PermissionDeniedError):
        logout()
        raise
    


def logout() -> None:
    """현재 사용자의 인증 정보와 사용자별 세션 데이터를 정리한다."""
    cookies = st.session_state.get(_COOKIE_MANAGER_KEY)

    session_id = st.session_state.get(_SESSION_ID_KEY)

    if not session_id and cookies is not None:
        session_id = cookies.get(_COOKIE_NAME)

    # 서버 기록을 먼저 삭제해 재사용을 막습니다.
    if isinstance(session_id, str) and session_id:
        delete_session(session_id)

    # 브라우저에 남은 복원용 ID도 삭제합니다.
    if cookies is not None:
        if cookies.get(_COOKIE_NAME) is not None:
            cookies.remove(_COOKIE_NAME)

    # 아래에는 기존 이메일 기억 처리와 세션 정리 코드 유지    
    remember_email = st.session_state.get("_pacs_login_remember_email", False)
    email = st.session_state.get("_pacs_login_email", "")

    for key in list(st.session_state.keys()):
        del st.session_state[key]

    if remember_email:
        st.session_state["_pacs_login_remember_email"] = True
        st.session_state["_pacs_login_email"] = email
