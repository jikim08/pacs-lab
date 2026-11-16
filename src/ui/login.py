"""PACS 로그인 화면.

이 파일은 입력 검사와 인증 결과 표시만 담당합니다. 실제 계정 확인은
``render(authenticate=...)``로 전달받은 서버 함수가 담당합니다.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import streamlit as st


@dataclass(frozen=True)
class LoginResult:
    """인증 서비스가 확인한 사용자 정보. role 값은 app.py와 합의해야 합니다."""

    uid: str
    role: str


# 잘못된 계정 정보는 None, 성공은 LoginResult를 반환하는 함수 형식입니다.
# 인증 서비스 장애는 예외로 전달합니다.
Authenticate = Callable[[str, str], LoginResult | None]


# 현재 디자인의 HTML, CSS, JavaScript를 Streamlit 컴포넌트로 연결합니다.
_ASSETS = Path(__file__).with_name("login_component")
_LOGIN_COMPONENT = st.components.v2.component(
    name="pacs_login",
    html=(_ASSETS / "login.html").read_text(encoding="utf-8"),
    css=(_ASSETS / "login.css").read_text(encoding="utf-8"),
    js=(_ASSETS / "login.js").read_text(encoding="utf-8"),
    # 기존 CSS가 Streamlit의 기본 여백과 헤더도 조정하므로 스타일을 분리하지 않습니다.
    isolate_styles=False,
)

_FEEDBACK_KEY = "_pacs_login_feedback"
_INVALID_CREDENTIALS = "아이디 또는 비밀번호를 확인해 주세요."
_SERVICE_ERROR = "로그인을 처리할 수 없습니다. 잠시 후 다시 시도해 주세요."
# login.html의 maxlength와 같은 값입니다. 브라우저 검사를 우회한 제출도 제한합니다.
_MAX_USER_ID_LENGTH = 128
_MAX_PASSWORD_LENGTH = 1024


def _read_credentials(submission: object) -> tuple[str, str]:
    """브라우저에서 받은 제출값을 검사하고 아이디와 비밀번호를 반환합니다."""
    if not isinstance(submission, dict):
        raise ValueError("입력값을 확인할 수 없습니다. 다시 시도해 주세요.")

    user_id = submission.get("user_id")
    password = submission.get("password")
    if not isinstance(user_id, str) or not isinstance(password, str):
        raise ValueError("입력값을 확인할 수 없습니다. 다시 시도해 주세요.")
    normalized_user_id = user_id.strip()
    if not normalized_user_id:
        raise ValueError("아이디를 입력해 주세요.")
    if not password:
        raise ValueError("비밀번호를 입력해 주세요.")
    if len(normalized_user_id) > _MAX_USER_ID_LENGTH or len(password) > _MAX_PASSWORD_LENGTH:
        raise ValueError("입력값이 너무 깁니다. 다시 확인해 주세요.")

    # 아이디 양쪽 공백만 제거합니다. 비밀번호는 입력한 그대로 인증 서비스에 전달합니다.
    return normalized_user_id, password


def _show_feedback(message: str, kind: str = "error") -> None:
    """안내 문구를 저장하고 컴포넌트에 반영하기 위해 화면을 다시 실행합니다."""
    st.session_state[_FEEDBACK_KEY] = {"message": message, "kind": kind}
    st.rerun()


def render(authenticate: Authenticate | None = None) -> LoginResult | None:
    """로그인 화면을 표시합니다. 성공 시에만 확인된 사용자 정보를 반환합니다.

    백엔드를 연결할 때 app.py에서 ``render(authenticate=인증함수)``를 호출합니다.
    인증함수는 서버에서 계정과 역할을 확인한 뒤 LoginResult를 반환해야 합니다.
    """
    feedback = st.session_state.get(_FEEDBACK_KEY, {})
    result = _LOGIN_COMPONENT(
        key="pacs_login_screen",
        data={
            "message": feedback.get("message", ""),
            "message_kind": feedback.get("kind", "error"),
        },
        on_submit_change=lambda: None,
        on_forgot_password_change=lambda: None,
        width="stretch",
    )

    # 비밀번호 재설정 절차는 미정이므로 현재는 관리자 문의만 안내합니다.
    if result.forgot_password:
        _show_feedback("비밀번호 재설정은 시스템 관리자에게 문의해 주세요.", "info")
        return None

    if result.submit is None:
        return None

    try:
        user_id, password = _read_credentials(result.submit)
    except ValueError as error:
        _show_feedback(str(error))
        return None

    # 인증 서비스가 연결되기 전에는 어떤 입력도 로그인 성공으로 취급하지 않습니다.
    if authenticate is None:
        _show_feedback("계정 인증 기능은 아직 연결되지 않았습니다.", "info")
        return None

    try:
        account = authenticate(user_id, password)
    except Exception:
        # 서버 오류의 상세 내용과 비밀번호를 화면에 표시하지 않습니다.
        _show_feedback(_SERVICE_ERROR)
        return None

    if account is None:
        _show_feedback(_INVALID_CREDENTIALS)
        return None
    if not isinstance(account, LoginResult) or not account.uid or not account.role:
        _show_feedback(_SERVICE_ERROR)
        return None

    # 로그인 상태 저장과 역할별 화면 이동은 app.py가 담당합니다.
    st.session_state.pop(_FEEDBACK_KEY, None)
    return account


if __name__ == "__main__":
    render()
