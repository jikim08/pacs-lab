"""PACS 로그인 화면.

이 파일은 입력 검사와 인증 결과 표시를 담당합니다. 직접 실행하면
기존 Firebase 인증 및 사용자 권한 확인 함수를 호출해 로그인 흐름을 시험합니다.
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
_INVALID_CREDENTIALS = "이메일 또는 비밀번호를 확인해 주세요."
_SERVICE_ERROR = "로그인을 처리할 수 없습니다. 잠시 후 다시 시도해 주세요."
_PROFILE_ERROR = "사용자 정보 또는 권한을 확인할 수 없습니다. 관리자에게 문의해 주세요."
_CONNECTION_ERROR = "인증 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."
# login.html의 maxlength와 같은 값입니다. 브라우저 검사를 우회한 제출도 제한합니다.
_MAX_EMAIL_LENGTH = 128
_MAX_PASSWORD_LENGTH = 1024


def _read_credentials(submission: object) -> tuple[str, str]:
    """브라우저에서 받은 제출값을 검사하고 이메일과 비밀번호를 반환합니다."""
    if not isinstance(submission, dict):
        raise ValueError("입력값을 확인할 수 없습니다. 다시 시도해 주세요.")

    user_id = submission.get("user_id")
    password = submission.get("password")
    if not isinstance(user_id, str) or not isinstance(password, str):
        raise ValueError("입력값을 확인할 수 없습니다. 다시 시도해 주세요.")
    email = user_id.strip()
    if not email:
        raise ValueError("이메일을 입력해 주세요.")
    if not password:
        raise ValueError("비밀번호를 입력해 주세요.")
    if len(email) > _MAX_EMAIL_LENGTH or len(password) > _MAX_PASSWORD_LENGTH:
        raise ValueError("입력값이 너무 깁니다. 다시 확인해 주세요.")

    # 컴포넌트의 제출값 이름은 user_id입니다. 이메일 양쪽 공백만 제거하고 비밀번호는 그대로 전달합니다.
    return email, password


def authenticate_with_backend(email: str, password: str) -> LoginResult:
    """기존 백엔드에 계정 확인과 역할 조회를 요청합니다."""
    from src.auth.firebase_auth import sign_in
    from src.services.access_policy import get_current_user

    signed_in = sign_in(email, password)
    profile = get_current_user(signed_in["id_token"])
    if signed_in["uid"] != profile["uid"]:
        raise PermissionError("인증 사용자와 사용자 정보가 일치하지 않습니다.")

    return LoginResult(uid=profile["uid"], role=profile["role"])


def _show_feedback(message: str, kind: str = "error") -> None:
    """안내 문구를 저장하고 컴포넌트에 반영하기 위해 화면을 다시 실행합니다."""
    st.session_state[_FEEDBACK_KEY] = {"message": message, "kind": kind}
    st.rerun()


def render(authenticate: Authenticate | None = None) -> LoginResult | None:
    """로그인 화면을 표시합니다. 성공 시에만 확인된 사용자 정보를 반환합니다.

    app.py에서는 ``render(authenticate=authenticate_with_backend)``로 연결할 수 있습니다.
    직접 실행할 때의 인증 함수와 결과 표시는 아래 __main__ 블록에만 적용됩니다.
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
        email, password = _read_credentials(result.submit)
    except ValueError as error:
        _show_feedback(str(error))
        return None

    # 인증 서비스가 연결되기 전에는 어떤 입력도 로그인 성공으로 취급하지 않습니다.
    if authenticate is None:
        _show_feedback("계정 인증 기능은 아직 연결되지 않았습니다.", "info")
        return None

    try:
        account = authenticate(email, password)
    except ValueError as error:
        # 기존 sign_in은 잘못된 계정 정보와 로그인 시도 제한을 모두 ValueError로 전달합니다.
        _show_feedback(str(error))
        return None
    except PermissionError:
        _show_feedback(_PROFILE_ERROR)
        return None
    except ConnectionError:
        _show_feedback(_CONNECTION_ERROR)
        return None
    except Exception:
        # Firebase 설정 오류 등 서버 오류의 상세 내용과 비밀번호를 화면에 표시하지 않습니다.
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
    # login.py를 Streamlit에서 직접 실행할 때만 사용하는 결과 확인 화면입니다.
    # app.py에서 이 모듈을 가져올 때는 실행되지 않습니다.
    preview_key = "_pacs_login_preview_account"
    if preview_key in st.session_state:
        account = st.session_state[preview_key]
        role_names = {
            "registrar": "등록 담당자",
            "reviewer": "검토 담당자",
            "manager": "업무 관리자",
        }
        st.success("로그인과 사용자 권한 확인이 완료되었습니다.")
        st.write(f"확인된 역할: {role_names[account.role]}")
        if st.button("다시 로그인"):
            del st.session_state[preview_key]
            st.rerun()
    else:
        account = render(authenticate=authenticate_with_backend)
        if account is not None:
            st.session_state[preview_key] = account
            st.rerun()
