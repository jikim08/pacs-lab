"""PACS 로그인 화면과 Firebase 백엔드 연결.

화면 요소와 입력 처리는 Streamlit 위젯으로 구성하고, 배치는 CSS로 조정합니다. app.py에서는
``render(authenticate=authenticate_with_backend)``를 호출할 수 있습니다.
"""

from collections.abc import Callable
from dataclasses import dataclass

import streamlit as st

from src.auth.firebase_auth import sign_in
from src.auth.session import save_session
from src.services.access_policy import get_current_user



@dataclass(frozen=True)
class LoginResult:
    """백엔드에서 확인한 사용자 UID와 역할."""

    uid: str
    role: str


Authenticate = Callable[[str, str], LoginResult | None]

_EMAIL_KEY = "_pacs_login_email"
_PASSWORD_KEY = "_pacs_login_password"
_REMEMBER_EMAIL_KEY = "_pacs_login_remember_email"
_FEEDBACK_KEY = "_pacs_login_feedback"
_PREVIEW_ACCOUNT_KEY = "_pacs_login_preview_account"

_INVALID_CREDENTIALS = "이메일 또는 비밀번호를 확인해 주세요."
_SERVICE_ERROR = "로그인을 처리할 수 없습니다. 잠시 후 다시 시도해 주세요."
_PROFILE_ERROR = "사용자 정보 또는 권한을 확인할 수 없습니다. 관리자에게 문의해 주세요."
_CONNECTION_ERROR = "인증 서버에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."
_MAX_EMAIL_LENGTH = 128
_MAX_PASSWORD_LENGTH = 1024

# 기존 HTML/CSS 디자인(1440×960 기준)을 그대로 옮긴 배치입니다. 모든 크기는 화면 높이에 비례하는
# --u(= 화면 높이 / 960) 단위로 정하고, 위치 값은 기존 login.css의 좌표를 그대로 사용합니다.
# CSS는 화면 배치에만 사용하며, 입력과 인증은 아래의 Streamlit 및 Python 코드가 처리합니다.
_LOGIN_STYLE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap');

/* Streamlit 기본 화면 요소 숨김 */
html, body, .stApp, .stMain { background:#f6f6f6 !important; }
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stSidebar"], #MainMenu, footer { display:none !important; }
.stMainBlockContainer { max-width:none !important; padding:0 !important; margin:0 !important; }

/* 전체 무대: 화면을 고정해서 채우고, 왼쪽 26.94% / 오른쪽 73.06%로 나눕니다. */
.st-key-pacs_login_stage {
  --u:calc(100vh / 960);
  position:fixed !important; inset:0; z-index:1; overflow:hidden;
  background:#f6f6f6; color:#1d1d1d;
  font-family:'Noto Sans KR', sans-serif; font-weight:400;
}
.st-key-pacs_login_stage * { box-sizing:border-box; }

/* Streamlit 요소의 기본 간격·여백·글꼴을 없애고, 아래에서 지정한 값만 쓰게 합니다. */
.st-key-pacs_login_stage [data-testid="stVerticalBlock"],
.st-key-pacs_login_stage [data-testid="stHorizontalBlock"] { gap:0 !important; }
.st-key-pacs_login_stage [data-testid="stElementContainer"] { margin:0 !important; }
.st-key-pacs_login_stage [data-testid="stMarkdown"],
.st-key-pacs_login_stage [data-testid="stMarkdownContainer"],
.st-key-pacs_login_stage [data-testid="stHeading"],
.st-key-pacs_login_stage [data-testid="stHeadingWithActionElements"],
.st-key-pacs_login_stage [data-testid="stMarkdownContainer"] p,
.st-key-pacs_login_stage h1, .st-key-pacs_login_stage h2 {
  margin:0 !important; padding:0 !important;
  font-family:inherit; font-size:inherit; font-weight:inherit;
  line-height:inherit; letter-spacing:normal; color:inherit;
}
.st-key-pacs_login_stage [data-testid="stHeaderActionElements"] { display:none !important; }

/* 왼쪽·오른쪽 영역. 안의 블록은 각 영역 기준 절대 좌표로 배치합니다. */
.st-key-pacs_login_left, .st-key-pacs_login_right {
  position:relative; width:100%; height:100vh !important; min-height:100vh !important;
}
.st-key-pacs_login_left { background:#191919; color:#f8f8f8; }
.st-key-pacs_login_right { background:#f6f6f6; color:#1d1d1d; }
.st-key-pacs_login_left::before, .st-key-pacs_login_left::after,
.st-key-pacs_login_right::before, .st-key-pacs_login_right::after {
  content:''; position:absolute; height:1px;
}
.st-key-pacs_login_left::before { left:calc(48 * var(--u)); right:calc(40 * var(--u)); top:calc(137 * var(--u)); background:#363636; }
.st-key-pacs_login_left::after { left:calc(48 * var(--u)); right:calc(40 * var(--u)); top:calc(852 * var(--u)); background:#363636; }
.st-key-pacs_login_right::before { left:calc(40 * var(--u)); right:calc(62 * var(--u)); top:calc(134 * var(--u)); background:#e2e2e2; }
.st-key-pacs_login_right::after { left:calc(48 * var(--u)); right:calc(54 * var(--u)); top:calc(852 * var(--u)); background:#e2e2e2; }

.st-key-pacs_login_brand, .st-key-pacs_login_intro, .st-key-pacs_login_left_footer,
.st-key-pacs_login_version, .st-key-pacs_login_security, .st-key-pacs_login_card,
.st-key-pacs_login_contact, .st-key-pacs_login_footer_name, .st-key-pacs_login_footer_note,
.st-key-pacs_login_policy, .st-key-pacs_login_help {
  position:absolute !important; width:auto !important; white-space:nowrap;
}

/* 왼쪽: 로고와 서비스명 */
.st-key-pacs_login_brand { left:calc(48 * var(--u)); top:calc(43 * var(--u)); }
.st-key-pacs_login_brand::before {
  content:''; position:absolute; left:0; top:calc(5 * var(--u));
  width:calc(28 * var(--u)); height:calc(28 * var(--u)); border-radius:calc(6 * var(--u));
  background:
    linear-gradient(#191919, #191919) center / calc(16 * var(--u)) calc(4 * var(--u)) no-repeat,
    linear-gradient(#191919, #191919) center / calc(4 * var(--u)) calc(16 * var(--u)) no-repeat,
    #f4f4f4;
}
.st-key-pacs_login_brand_name {
  padding-left:calc(39 * var(--u)); color:#fff;
  font-size:calc(23 * var(--u)); font-weight:700; line-height:calc(36 * var(--u));
}
.st-key-pacs_login_brand_sub {
  margin-top:calc(11 * var(--u)); color:#acacac;
  font-size:calc(12 * var(--u)); line-height:calc(19 * var(--u));
}

/* 왼쪽: 소개 문구 */
.st-key-pacs_login_intro { left:calc(48 * var(--u)); top:calc(268 * var(--u)); }
.st-key-pacs_login_eyebrow {
  color:#a6a6a6; font-size:calc(10 * var(--u)); font-weight:500; line-height:calc(16 * var(--u));
}
.st-key-pacs_login_intro_title {
  margin-top:calc(21 * var(--u)); color:#f8f8f8;
  font-size:calc(34 * var(--u)); font-weight:700; line-height:calc(53 * var(--u));
}
.st-key-pacs_login_intro_copy {
  margin-top:calc(74 * var(--u)); color:#b1b1b1;
  font-size:calc(14 * var(--u)); line-height:calc(22 * var(--u));
}

/* 왼쪽: 하단 안내와 버전 */
.st-key-pacs_login_left_footer { left:calc(48 * var(--u)); top:calc(874 * var(--u)); }
.st-key-pacs_login_staff {
  color:#cdcdcd; font-size:calc(12 * var(--u)); font-weight:500; line-height:calc(19 * var(--u));
}
.st-key-pacs_login_staff_note {
  margin-top:calc(6 * var(--u)); color:#a3a3a3;
  font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u));
}
.st-key-pacs_login_version {
  right:calc(40 * var(--u)); top:calc(890 * var(--u)); color:#a3a3a3;
  font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u));
}

/* 오른쪽: 상단·하단 안내 문구 */
.st-key-pacs_login_security {
  right:calc(88 * var(--u)); top:calc(65 * var(--u)); color:#666;
  font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u));
}
.st-key-pacs_login_contact {
  left:0; right:0; top:calc(798 * var(--u)); color:#696969;
  font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u));
}
.st-key-pacs_login_footer_name, .st-key-pacs_login_policy, .st-key-pacs_login_help {
  top:calc(878 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u));
}
.st-key-pacs_login_footer_name { left:calc(48 * var(--u)); color:#696969; }
.st-key-pacs_login_policy { right:calc(165 * var(--u)); color:#555; font-weight:500; }
.st-key-pacs_login_help { right:calc(77 * var(--u)); color:#555; }
.st-key-pacs_login_footer_note {
  left:calc(48 * var(--u)); top:calc(905 * var(--u)); color:#707070;
  font-size:calc(10 * var(--u)); line-height:calc(15 * var(--u));
}

/* 로그인 카드: 오른쪽 영역 가로 가운데, 460×536, 안쪽 여백 40 */
.st-key-pacs_login_card {
  top:calc(198 * var(--u)); left:50%; transform:translateX(-50%);
  width:calc(460 * var(--u)) !important; height:calc(536 * var(--u));
  padding:calc(40 * var(--u)) !important; white-space:normal;
  background:#fff; border:1px solid #dedede; border-radius:calc(6 * var(--u));
  color:#1d1d1d;
}
.st-key-pacs_login_title {
  color:#1d1d1d; font-size:calc(26 * var(--u)); font-weight:700; line-height:calc(39 * var(--u));
}
.st-key-pacs_login_email_label, .st-key-pacs_login_password_label {
  color:#333; font-size:calc(12 * var(--u)); font-weight:500; line-height:calc(18 * var(--u));
}
.st-key-pacs_login_email_label { margin-top:calc(24 * var(--u)); }
.st-key-pacs_login_password_label { margin-top:calc(20 * var(--u)); }
.st-key-pacs_login_email, .st-key-pacs_login_password { margin-top:calc(9 * var(--u)); }

/* 입력칸: 높이 48, 테두리 #d3d3d3, 포커스 시 #555 */
.st-key-pacs_login_card [data-testid="stTextInputRootElement"] {
  height:calc(48 * var(--u)); min-height:0; padding:0 !important;
  background:#fff !important; border:1px solid #d3d3d3 !important;
  border-radius:calc(4 * var(--u)) !important; box-shadow:none !important;
}
.st-key-pacs_login_card [data-testid="stTextInputRootElement"]:focus-within { border-color:#555 !important; }
.st-key-pacs_login_card [data-testid="stTextInputRootElement"] > div { background:transparent !important; }
.st-key-pacs_login_card input {
  height:100% !important; padding:0 calc(14 * var(--u)) !important;
  background:#fff !important; color:#1d1d1d !important; -webkit-text-fill-color:#1d1d1d;
  caret-color:#1d1d1d; font-family:inherit !important; font-size:calc(13 * var(--u)) !important;
}
.st-key-pacs_login_card input::placeholder { color:#767676 !important; -webkit-text-fill-color:#767676; opacity:1; }
.st-key-pacs_login_card [data-testid="InputInstructions"] { display:none !important; }
.st-key-pacs_login_email [data-testid="stTextInputRootElement"] > div:first-child:not(:has(input)) { display:none !important; }

/* 비밀번호 칸 안의 "표시/숨김": Streamlit 기본 보기 버튼을 글자 버튼으로 바꿉니다. */
.st-key-pacs_login_password [data-testid="stTextInputRootElement"] button {
  height:100%; padding:0 calc(14 * var(--u)) !important; margin:0;
  border:0 !important; background:transparent !important; box-shadow:none !important;
  color:#555 !important; font-family:inherit; font-size:calc(11 * var(--u)); font-weight:500;
  cursor:pointer;
}
.st-key-pacs_login_password [data-testid="stTextInputRootElement"] button > * { display:none !important; }
.st-key-pacs_login_password [data-testid="stTextInputRootElement"] button::after { content:'표시'; }
.st-key-pacs_login_password [data-testid="stTextInputRootElement"] button[aria-label^="Hide"]::after { content:'숨김'; }

/* 이메일 유지 / 비밀번호 찾기 줄 */
.st-key-pacs_login_options {
  margin-top:calc(24 * var(--u)); height:calc(20 * var(--u)) !important; min-height:0 !important;
}
.st-key-pacs_login_options [data-testid="stElementContainer"],
.st-key-pacs_login_options [data-testid="stCheckbox"],
.st-key-pacs_login_options [data-testid="stButton"] { height:calc(20 * var(--u)); min-height:0; display:flex; align-items:center; }
.st-key-pacs_login_options [data-testid="stCheckbox"] label {
  display:flex; align-items:center; gap:calc(8 * var(--u)); min-height:0; cursor:pointer;
}
.st-key-pacs_login_options [data-testid="stCheckbox"] label > div:first-of-type {
  width:calc(16 * var(--u)); height:calc(16 * var(--u)); margin:0 !important;
  background-color:#fff !important; border:1px solid #b3b3b3 !important;
  border-radius:calc(3 * var(--u)) !important;
}
.st-key-pacs_login_options [data-testid="stCheckbox"] label:has(input:checked) > div:first-of-type {
  background-color:#252525 !important; border-color:#252525 !important;
}
.st-key-pacs_login_options [data-testid="stCheckbox"] label:has(input:focus-visible) > div:first-of-type {
  outline:2px solid #555; outline-offset:2px;
}
.st-key-pacs_login_options [data-testid="stCheckbox"] [data-testid="stWidgetLabel"],
.st-key-pacs_login_options [data-testid="stCheckbox"] [data-testid="stWidgetLabel"] * {
  min-height:0 !important; height:auto !important;
  color:#555 !important; font-size:calc(12 * var(--u)) !important; line-height:calc(18 * var(--u)) !important;
}
.st-key-pacs_login_options [data-testid="stButton"] button {
  min-height:0; height:auto; padding:0 !important; border:0 !important;
  background:transparent !important; box-shadow:none !important;
  color:#555 !important; font-family:inherit;
}
.st-key-pacs_login_options [data-testid="stButton"] button * {
  min-height:0 !important; height:auto !important; margin:0 !important; padding:0 !important;
  font-size:calc(12 * var(--u)) !important; font-weight:500; line-height:calc(18 * var(--u)) !important;
  color:#555 !important;
}
.st-key-pacs_login_options [data-testid="stButton"] button:hover p { text-decoration:underline; }

/* 로그인 버튼: 높이 48, #252525 */
.st-key-pacs_login_submit { margin-top:calc(23 * var(--u)); }
.st-key-pacs_login_submit button {
  height:calc(48 * var(--u)); min-height:0; padding:0 !important;
  background:#252525 !important; border:0 !important; border-radius:calc(4 * var(--u)) !important;
  box-shadow:none !important; color:#fff !important; font-family:inherit;
}
.st-key-pacs_login_submit button:hover { background:#353535 !important; }
.st-key-pacs_login_submit button:focus-visible { outline:2px solid #555; outline-offset:2px; }
.st-key-pacs_login_submit button p {
  color:#fff !important; font-size:calc(14 * var(--u)) !important; font-weight:500;
  line-height:calc(21 * var(--u)) !important;
}
.st-key-pacs_login_submit kbd, .st-key-pacs_login_submit [data-testid="stButtonShortcut"] { display:none !important; }

/* 안내 문구: 버튼과 개인정보 상자 사이(카드 기준 top 392)에 띄워 다른 요소를 밀어내지 않습니다. */
.st-key-pacs_login_feedback {
  position:absolute !important; left:calc(40 * var(--u)); top:calc(392 * var(--u));
  width:calc(380 * var(--u)) !important; font-size:calc(10 * var(--u)); line-height:calc(16 * var(--u));
}
.st-key-pacs_login_feedback_error { color:#b42318; }
.st-key-pacs_login_feedback_info { color:#555; }

/* 개인정보 안내 상자 */
.st-key-pacs_login_privacy {
  margin-top:calc(24 * var(--u)); height:calc(84 * var(--u)) !important; padding:calc(14 * var(--u)) calc(16 * var(--u)) !important;
  background:#f7f7f7; border-radius:calc(4 * var(--u));
  color:#696969; font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u));
}
.st-key-pacs_login_privacy_title { margin-bottom:calc(4 * var(--u)); color:#444; font-weight:500; }

/* 좁은 화면에서는 왼쪽 소개 영역을 숨기고 로그인 영역만 보여 줍니다. */
@media (max-width: 900px) {
  .st-key-pacs_login_stage [data-testid="stColumn"]:first-child { display:none !important; }
  .st-key-pacs_login_stage [data-testid="stColumn"]:last-child {
    width:100% !important; flex:1 1 100% !important; min-width:100% !important;
  }
}
</style>
"""


def _read_credentials(email: object, password: object) -> tuple[str, str]:
    """Streamlit 입력값을 검사합니다. 비밀번호의 공백은 변경하지 않습니다."""
    if not isinstance(email, str) or not isinstance(password, str):
        raise ValueError("입력값을 확인할 수 없습니다. 다시 시도해 주세요.")

    email = email.strip()
    if not email:
        raise ValueError("이메일을 입력해 주세요.")
    if not password:
        raise ValueError("비밀번호를 입력해 주세요.")
    if len(email) > _MAX_EMAIL_LENGTH or len(password) > _MAX_PASSWORD_LENGTH:
        raise ValueError("입력값이 너무 깁니다. 다시 확인해 주세요.")

    return email, password


def authenticate_with_backend(email: str, password: str) -> LoginResult:
    """기존 Firebase 로그인과 Firestore 역할 확인 함수를 연결합니다."""
    from src.auth.firebase_auth import sign_in
    from src.services.access_policy import get_current_user

    signed_in = sign_in(email, password)
    profile = get_current_user(signed_in["id_token"])
    if signed_in["uid"] != profile["uid"]:
        raise PermissionError("인증 사용자와 사용자 정보가 일치하지 않습니다.")

    save_session(signed_in["id_token"])

    return LoginResult(uid=profile["uid"], role=profile["role"])


def _set_feedback(message: str, kind: str = "error") -> None:
    st.session_state[_FEEDBACK_KEY] = {"message": message, "kind": kind}


def _attempt_login(
    email: object, password: object, authenticate: Authenticate | None
) -> LoginResult | None:
    """입력을 검사하고 백엔드 결과를 화면에서 사용할 형식으로 받습니다."""
    try:
        email, password = _read_credentials(email, password)
    except ValueError as error:
        _set_feedback(str(error))
        return None

    if authenticate is None:
        _set_feedback("계정 인증 기능은 아직 연결되지 않았습니다.", "info")
        return None

    try:
        account = authenticate(email, password)
    except ValueError as error:
        # sign_in은 잘못된 계정 정보와 로그인 시도 제한을 ValueError로 알립니다.
        _set_feedback(str(error))
        return None
    except PermissionError:
        _set_feedback(_PROFILE_ERROR)
        return None
    except ConnectionError:
        _set_feedback(_CONNECTION_ERROR)
        return None
    except Exception:
        # Firebase 설정 등의 내부 오류와 비밀번호를 화면에 표시하지 않습니다.
        _set_feedback(_SERVICE_ERROR)
        return None

    if account is None:
        _set_feedback(_INVALID_CREDENTIALS)
        return None
    if not isinstance(account, LoginResult) or not account.uid or not account.role:
        _set_feedback(_SERVICE_ERROR)
        return None

    st.session_state.pop(_FEEDBACK_KEY, None)
    return account


def _text(key: str, body: str, align: str = "left") -> None:
    """글자 한 덩어리를 표시합니다. 글꼴·색·위치는 key 이름으로 위의 CSS가 정합니다."""
    with st.container(key=key):
        st.markdown(body, text_alignment=align)


def _render_branding() -> None:
    """왼쪽 소개 영역을 표시합니다."""
    with st.container(key="pacs_login_brand"):
        _text("pacs_login_brand_name", "PACS")
        _text("pacs_login_brand_sub", "의료영상 관리 시스템")
    with st.container(key="pacs_login_intro"):
        _text("pacs_login_eyebrow", "PICTURE ARCHIVING & COMMUNICATION SYSTEM")
        with st.container(key="pacs_login_intro_title"):
            st.title("교육용 PACS", anchor=False)
        # 줄 끝의 공백 두 칸과 \n은 Markdown 줄바꿈입니다.
        _text("pacs_login_intro_copy", "검사 영상 확인부터 판독, 환자 기록까지.  \nPACS에서 진료의 흐름을 이어가세요.")
    with st.container(key="pacs_login_left_footer"):
        _text("pacs_login_staff", "의료진 전용 시스템")
        _text("pacs_login_staff_note", "승인된 계정으로 안전하게 접속하세요.")
    _text("pacs_login_version", "v1.1")


def _render_feedback() -> None:
    """안내 문구를 카드 안 정해진 자리에 글자로 표시합니다."""
    feedback = st.session_state.get(_FEEDBACK_KEY)
    if not feedback:
        return
    kind = "info" if feedback["kind"] == "info" else "error"
    _text(f"pacs_login_feedback_{kind}", feedback["message"])


def _render_login_panel(authenticate: Authenticate | None) -> LoginResult | None:
    """오른쪽 로그인 영역을 표시하고, 로그인 버튼이 눌리면 인증을 시도합니다."""
    _text("pacs_login_security", "의료진 전용 · 보안 접속")

    with st.container(key="pacs_login_card"):
        with st.container(key="pacs_login_title"):
            st.header("로그인", anchor=False)

        remember_email = st.session_state.get(_REMEMBER_EMAIL_KEY, False)
        _text("pacs_login_email_label", "이메일")
        with st.container(key="pacs_login_email"):
            email = st.text_input(
                "이메일",
                key=_EMAIL_KEY,
                type="email",
                max_chars=_MAX_EMAIL_LENGTH,
                placeholder="이메일을 입력하세요",
                autocomplete="email",
                label_visibility="collapsed",
                on_change="ignore",
                persist_state="session" if remember_email else None,
            )

        _text("pacs_login_password_label", "비밀번호")
        with st.container(key="pacs_login_password"):
            # 칸 안의 "표시/숨김"은 Streamlit 기본 보기 버튼이므로 입력값이 유지됩니다.
            password = st.text_input(
                "비밀번호",
                key=_PASSWORD_KEY,
                type="password",
                max_chars=_MAX_PASSWORD_LENGTH,
                placeholder="비밀번호를 입력하세요",
                autocomplete="current-password",
                label_visibility="collapsed",
                on_change="ignore",
            )

        with st.container(
            key="pacs_login_options",
            horizontal=True,
            horizontal_alignment="distribute",
            vertical_alignment="center",
        ):
            st.checkbox("이메일 유지", key=_REMEMBER_EMAIL_KEY, persist_state="session")
            forgot_password = st.button("비밀번호 찾기", type="tertiary")

        with st.container(key="pacs_login_submit"):
            submitted = st.button("로그인", type="primary", width="stretch", shortcut="Enter")

        account = None
        if forgot_password:
            _set_feedback("비밀번호 재설정은 시스템 관리자에게 문의해 주세요.", "info")
        elif submitted:
            account = _attempt_login(email, password, authenticate)
        with st.container(key="pacs_login_feedback"):
            _render_feedback()

        with st.container(key="pacs_login_privacy"):
            _text("pacs_login_privacy_title", "개인정보 보호를 위해")
            _text("pacs_login_privacy_body", "공용 PC에서는 이메일 유지를 해제하고,  \n이용 후 반드시 로그아웃해 주세요.")

    _text("pacs_login_contact", "계정 발급 및 접속 문의는 시스템 관리자에게 문의하세요.", align="center")
    _text("pacs_login_footer_name", "PACS · 의료영상 관리 시스템")
    _text("pacs_login_policy", "개인정보 처리방침")
    _text("pacs_login_help", "이용 안내")
    _text("pacs_login_footer_note", "환자 정보는 승인된 진료 목적으로만 이용할 수 있습니다.")
    return account


def render(authenticate: Authenticate | None = None) -> LoginResult | None:
    """로그인 화면을 표시하고, 인증이 성공하면 사용자 UID와 역할을 반환합니다."""
    st.html(_LOGIN_STYLE)
    with st.container(key="pacs_login_stage"):
        introduction, login_panel = st.columns([26.944444, 73.055556], gap=None)
        with introduction:
            with st.container(key="pacs_login_left"):
                _render_branding()
        with login_panel:
            with st.container(key="pacs_login_right"):
                return _render_login_panel(authenticate)


if __name__ == "__main__":
    # 단독 실행 시에만 성공 결과를 보여줍니다. app.py에서는 render의 반환값을 사용합니다.
    st.set_page_config(page_title="PACS 로그인", layout="wide")
    if _PREVIEW_ACCOUNT_KEY in st.session_state:
        st.session_state.pop(_PASSWORD_KEY, None)
        account = st.session_state[_PREVIEW_ACCOUNT_KEY]
        role_names = {
            "registrar": "등록 담당자",
            "reviewer": "검토 담당자",
            "manager": "업무 관리자",
        }
        st.success("로그인과 사용자 권한 확인이 완료되었습니다.")
        st.write(f"확인된 역할: {role_names[account.role]}")
        if st.button("다시 로그인"):
            del st.session_state[_PREVIEW_ACCOUNT_KEY]
            if not st.session_state.get(_REMEMBER_EMAIL_KEY, False):
                st.session_state.pop(_EMAIL_KEY, None)
            st.rerun()
    else:
        account = render(authenticate=authenticate_with_backend)
        if account is not None:
            st.session_state[_PREVIEW_ACCOUNT_KEY] = account
            st.rerun()
