''' Streamlit 로그인 상태 유지와 정리 '''
"""Streamlit 로그인 세션 관리."""

from src.models import User


def get_current_user() -> User | None:
    """현재 로그인 사용자를 반환한다.

    비로그인 상태이면 None을 반환한다.
    반환하는 사용자 정보는 인증이 확인된 정보여야 한다.
    """
    raise NotImplementedError


def logout() -> None:
    """현재 사용자의 인증 정보와 사용자별 세션 데이터를 정리한다."""
    raise NotImplementedError