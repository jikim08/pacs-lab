"""PACS 공통 왼쪽 사이드바(WORKSPACE 메뉴).

검사 목록·검사 등록·검토 배정·영상 뷰어 화면이 같이 쓰는 왼쪽 메뉴입니다. 화면마다 따로 그리지 않고,
app.py(로컬에서는 test_app.py)가 화면을 그리기 전에 한 번 ``render(active)``를 호출합니다.

    target = sidebar.render(active=current_screen)
    if target is not None:          # 다른 메뉴를 눌렀을 때만 화면 이름이 옵니다.
        ...떠나는 화면 정리(reset_state 등) 후 current_screen = target, st.rerun()

배치: 화면 왼쪽에 고정(position: fixed)된 204 × 화면 높이 막대입니다(Figma 1440×960 기준, 크기는 화면 높이에 비례).
- 검사 목록·등록·배정 화면은 원래 왼쪽 204 자리를 비워 두고 그리므로 그대로 맞습니다.
- 일반 Streamlit 배치를 쓰는 화면(영상 뷰어 등)은 본문이 사이드바 폭만큼 오른쪽에서 시작하도록 여백을 둡니다.
- 화면 가운데 창(확인창·배정 창 등)이 열리면 사이드바도 함께 흐리게 덮입니다. 화면 쪽 규칙: 흐린 덮개 버튼의 key는
  ``pacs_..._backdrop``으로 짓고, 달력처럼 투명한 덮개는 ``pacs_..._cal_backdrop``으로 짓습니다(흐려지지 않음).
"""

from __future__ import annotations

import base64

import streamlit as st

# ── 화면 이름 (render의 active 인자와 반환값) ─────────────────────────────────
# 검사 목록·등록·배정 화면의 TARGET_* 값도 이 이름을 그대로 씁니다.

SCREEN_CASE_LIST = "case_list"
SCREEN_CASE_REGISTER = "case_register"
SCREEN_REVIEW_ASSIGN = "review_assign"
SCREEN_IMAGE_VIEWER = "image_viewer"

# (화면 이름, 메뉴 글자) — 위에서부터 이 순서로 그립니다.
MENUS = (
    (SCREEN_CASE_LIST, "검사 목록"),
    (SCREEN_CASE_REGISTER, "검사 등록"),
    (SCREEN_REVIEW_ASSIGN, "검토 배정"),
    (SCREEN_IMAGE_VIEWER, "영상 뷰어"),
)

BRAND_NAME = "PACS"
BRAND_SUB = "의료영상 관리 시스템"
WORKSPACE_LABEL = "WORKSPACE"
SERVER_TITLE = "서버 연결 정상"
SERVER_SUB = "DICOM 서비스 운영 중"
VERSION_LABEL = "PACS  /  v1.1"

WIDTH = 204  # 사이드바 폭(Figma 기준 px). 실제 폭은 WIDTH × 화면 높이 / 960

_CLICKED_KEY = "_pacs_nav_clicked"  # 누른 메뉴의 화면 이름(다음 render가 돌려주고 지움)


# ── 아이콘 (Figma에서 받은 SVG) ──────────────────────────────────────────────

_ON_COLOR = "#202020"  # 지금 화면 메뉴(밝은 바탕)
_OFF_COLOR = "#C1C1C1"  # 다른 메뉴(어두운 바탕)
_STROKE = 'stroke-linecap="round" stroke-linejoin="round"'


def _svg(width: float, height: float, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" fill="none">{body}</svg>'
    )


def _css_icon(svg: str) -> str:
    """CSS background-image에 넣을 수 있는 data URI를 만듭니다."""
    return "url('data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii") + "')"


# 메뉴 아이콘(20×20). 선 색 자리는 @C@로 두고, 켜짐·꺼짐 색으로 바꿔 씁니다.
_MENU_ICONS = {
    # 목록(문서 + 세 줄)
    SCREEN_CASE_LIST: (
        f'<path d="M5 2.5H15C15.9205 2.5 16.6667 3.24619 16.6667 4.16667V15.8333C16.6667 16.7538 15.9205 17.5 15 17.5H5'
        f'C4.07953 17.5 3.33333 16.7538 3.33333 15.8333V4.16667C3.33333 3.24619 4.07953 2.5 5 2.5Z" '
        f'stroke-width="1.6" {_STROKE} stroke="@C@"/>'
        f'<path d="M6.66667 6.66667H13.3333M6.66667 10H13.3333M6.66667 13.3333H10.8333" '
        f'stroke-width="1.6" {_STROKE} stroke="@C@"/>'
    ),
    # 등록: 문서(14×16)와 더하기(6×6)를 Figma 배치대로 합칩니다.
    SCREEN_CASE_REGISTER: (
        '<g transform="translate(3 2)">'
        f'<path d="M8.25 0.75H2.25C1.85218 0.75 1.47064 0.908035 1.18934 1.18934C0.908035 1.47064 0.75 1.85218 0.75 2.25'
        f'V13.75C0.75 14.1478 0.908035 14.5294 1.18934 14.8107C1.47064 15.092 1.85218 15.25 2.25 15.25H11.75'
        f'C12.1478 15.25 12.5294 15.092 12.8107 14.8107C13.092 14.5294 13.25 14.1478 13.25 13.75V5.75L8.25 0.75Z" '
        f'stroke-width="1.5" {_STROKE} stroke="@C@"/>'
        f'<path d="M8.25 0.75V4.75C8.25 5.01522 8.35536 5.26957 8.54289 5.45711C8.73043 5.64464 8.98478 5.75 9.25 5.75'
        f'H13.25" stroke-width="1.5" {_STROKE} stroke="@C@"/></g>'
        '<g transform="translate(7 9.25)"><path d="M3 0.75V5.25M0.75 3H5.25" stroke-width="1.5" '
        'stroke-linecap="round" stroke="@C@"/></g>'
    ),
    # 배정: 사람(10.5×15.75)과 체크(6.5×4.75)를 Figma 배치대로 합칩니다.
    SCREEN_REVIEW_ASSIGN: (
        '<g transform="translate(2 2.25)">'
        f'<path d="M8.25 3.75C8.25 5.40685 6.90685 6.75 5.25 6.75C3.59315 6.75 2.25 5.40685 2.25 3.75'
        f'C2.25 2.09315 3.59315 0.75 5.25 0.75C6.90685 0.75 8.25 2.09315 8.25 3.75Z" '
        f'stroke-width="1.5" {_STROKE} stroke="@C@"/>'
        f'<path d="M0.75 15V13.25C0.75 12.1891 1.17143 11.1717 1.92157 10.4216C2.67172 9.67143 3.68913 9.25 4.75 9.25'
        f'H5.75C6.81087 9.25 7.82828 9.67143 8.57843 10.4216C9.32857 11.1717 9.75 12.1891 9.75 13.25V15" '
        f'stroke-width="1.5" {_STROKE} stroke="@C@"/></g>'
        f'<g transform="translate(11.5 7)"><path d="M0.75 2L2.75 4L5.75 0.750004" stroke-width="1.5" '
        f'{_STROKE} stroke="@C@"/></g>'
    ),
    # 영상 뷰어: 모니터(Figma 17.6×16.6을 20×20 위치에 맞춤)
    SCREEN_IMAGE_VIEWER: (
        '<g transform="translate(1.2 2.2)">'
        f'<path d="M2.3 0.8H15.3C16.1284 0.8 16.8 1.47157 16.8 2.3V10.3C16.8 11.1284 16.1284 11.8 15.3 11.8H2.3'
        f'C1.47157 11.8 0.8 11.1284 0.8 10.3V2.3C0.8 1.47157 1.47157 0.8 2.3 0.8Z" stroke-width="1.6" {_STROKE} stroke="@C@"/>'
        f'<path d="M8.8 11.8V15.8M4.8 15.8H12.8" stroke-width="1.6" {_STROKE} stroke="@C@"/>'
        f'<path d="M3.8 7.8L5.8 4.8L7.8 7.8" stroke-width="1.6" {_STROKE} stroke="@C@"/>'
        f'<path d="M9.8 7.8L11.8 4.8L13.8 7.8" stroke-width="1.6" {_STROKE} stroke="@C@"/></g>'
    ),
}
_ICON_SHIELD = _svg(18, 18, (
    f'<path d="M9 2.25L15 4.5V9C15 12.75 9 15.75 9 15.75C9 15.75 3 12.75 3 9V4.5L9 2.25Z" '
    f'stroke-width="1.6" {_STROKE} stroke="#BDBDBD"/>'
    f'<path d="M6 9L8.25 11.25L12 6.75" stroke-width="1.6" {_STROKE} stroke="#BDBDBD"/>'
))


def _menu_icon(screen: str, on: bool) -> str:
    return _css_icon(_svg(20, 20, _MENU_ICONS[screen].replace("@C@", _ON_COLOR if on else _OFF_COLOR)))


# ── CSS ─────────────────────────────────────────────────────────────────────
# 크기는 화면 높이에 비례하는 --u(= 화면 높이 / 960) 단위이고, 좌표는 Figma 레이어 값을 그대로 씁니다.

_STYLE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap');
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], [data-testid="stSidebar"] { display:none !important; }
/* 일반 배치 화면(영상 뷰어 등)의 본문이 사이드바 뒤에 가려지지 않게 합니다. 좌표 고정 화면(검사 목록 등)은 영향 없음. */
.stMainBlockContainer { padding-left:calc(@WIDTH@ * 100vh / 960 + 2rem) !important; }

.st-key-pacs_nav {
  --u:calc(100vh / 960);
  position:fixed !important; left:0; top:0; z-index:2; overflow:hidden;
  width:calc(@WIDTH@ * var(--u)) !important; height:100vh; background:#191919; color:#fff;
  font-family:'Noto Sans KR', sans-serif; font-weight:400;
}
.st-key-pacs_nav * { box-sizing:border-box; }
/* Streamlit 요소의 기본 간격·여백·글꼴을 없앱니다. */
.st-key-pacs_nav [data-testid="stVerticalBlock"], .st-key-pacs_nav [data-testid="stHorizontalBlock"] { gap:0 !important; }
.st-key-pacs_nav [data-testid="stElementContainer"] { margin:0 !important; }
.st-key-pacs_nav [data-testid="stMarkdown"], .st-key-pacs_nav [data-testid="stMarkdownContainer"],
.st-key-pacs_nav [data-testid="stMarkdownContainer"] p {
  margin:0 !important; padding:0 !important; font-family:inherit; font-size:inherit;
  font-weight:inherit; line-height:inherit; letter-spacing:normal; color:inherit; white-space:inherit;
}
.st-key-pacs_nav button { font-family:inherit !important; }
.st-key-pacs_nav [data-testid="stButtonShortcut"], .st-key-pacs_nav kbd { display:none !important; }
.st-key-pacs_nav button:focus-visible { outline:2px solid #8a8a8a !important; outline-offset:-2px; }

/* 브랜드 아래 구분선 */
.st-key-pacs_nav::before {
  content:''; position:absolute; left:calc(20 * var(--u)); top:calc(111 * var(--u));
  width:calc(164 * var(--u)); height:1px; background:#363636;
}
/* 화면 가운데 창이 열리면(흐린 덮개 *_backdrop) 사이드바도 같이 흐리게 덮고 누를 수 없게 합니다. */
.stApp:has([class*="st-key-pacs_"][class*="_backdrop"]:not([class*="_cal_backdrop"])) .st-key-pacs_nav::after {
  content:''; position:absolute; inset:0; z-index:10;
  background:rgba(37, 37, 37, 0.35); backdrop-filter:blur(2px); -webkit-backdrop-filter:blur(2px);
}

/* 절대 좌표로 배치하는 글자 칸: 내용 너비만큼만 차지하고 한 줄로 표시합니다. */
.st-key-pacs_nav_brand, .st-key-pacs_nav_workspace, .st-key-pacs_nav_server, .st-key-pacs_nav_version {
  position:absolute !important; width:auto !important; white-space:nowrap;
}
.st-key-pacs_nav_brand { left:calc(24 * var(--u)); top:calc(24 * var(--u)); }
.st-key-pacs_nav_brand::before {
  content:''; position:absolute; left:0; top:calc(2 * var(--u));
  width:calc(28 * var(--u)); height:calc(28 * var(--u)); border-radius:calc(6 * var(--u));
  background:
    linear-gradient(#191919, #191919) center / calc(16 * var(--u)) calc(4 * var(--u)) no-repeat,
    linear-gradient(#191919, #191919) center / calc(4 * var(--u)) calc(16 * var(--u)) no-repeat,
    #f4f4f4;
}
.st-key-pacs_nav_brand_name {
  padding-left:calc(39 * var(--u)); color:#fff;
  font-size:calc(23 * var(--u)); line-height:calc(35 * var(--u)); font-weight:700;
}
.st-key-pacs_nav_brand_sub { margin-top:calc(8 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#a7a7a7; }
.st-key-pacs_nav_workspace {
  left:calc(24 * var(--u)); top:calc(137 * var(--u));
  font-size:calc(10 * var(--u)); line-height:calc(15 * var(--u)); font-weight:500; color:#9a9a9a;
}
.st-key-pacs_nav_server {
  left:calc(16 * var(--u)); top:calc(842 * var(--u)); width:calc(172 * var(--u)) !important; height:calc(65 * var(--u));
  padding:calc(11 * var(--u)) calc(12 * var(--u)) 0 !important; border-radius:calc(6 * var(--u)); background:#252525;
}
.st-key-pacs_nav_server::before {
  content:''; position:absolute; left:calc(12 * var(--u)); top:calc(11 * var(--u));
  width:calc(18 * var(--u)); height:calc(18 * var(--u)); background:@ICON_SHIELD@ center / contain no-repeat;
}
.st-key-pacs_nav_server_title {
  padding-left:calc(26 * var(--u)); font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:500; color:#d7d7d7;
}
.st-key-pacs_nav_server_sub { margin-top:calc(7 * var(--u)); font-size:calc(10 * var(--u)); line-height:calc(15 * var(--u)); color:#a5a5a5; }
.st-key-pacs_nav_version {
  left:calc(24 * var(--u)); top:calc(927 * var(--u)); white-space:pre;
  font-size:calc(10 * var(--u)); line-height:calc(15 * var(--u)); color:#8e8e8e;
}

/* 메뉴 버튼: 180×44, 52 간격, 아이콘 (14, 12), 글자 x 46. 지금 화면은 밝은 바탕 + 굵은 글자 */
[class*="st-key-pacs_nav_menu_"], [class*="st-key-pacs_nav_active_"] {
  position:absolute !important; left:calc(12 * var(--u)); width:calc(180 * var(--u)) !important; height:calc(44 * var(--u));
}
.st-key-pacs_nav [data-testid="stButton"], .st-key-pacs_nav [data-testid="stButton"] button {
  width:100% !important; height:calc(44 * var(--u)) !important; min-height:0 !important;
}
.st-key-pacs_nav [data-testid="stButton"] button {
  justify-content:flex-start !important; padding:0 0 0 calc(46 * var(--u)) !important;
  border:0 !important; border-radius:calc(6 * var(--u)) !important; box-shadow:none !important;
  background-color:transparent !important; background-repeat:no-repeat !important;
  background-position:calc(14 * var(--u)) center !important; background-size:calc(20 * var(--u)) !important;
  cursor:pointer;
}
.st-key-pacs_nav [data-testid="stButton"] button > div { width:100%; justify-content:flex-start !important; }
.st-key-pacs_nav [data-testid="stButton"] button p {
  font-size:calc(14 * var(--u)) !important; line-height:calc(21 * var(--u)) !important; color:#c1c1c1 !important;
}
/* 위의 공통 버튼 규칙보다 우선하도록 .st-key-pacs_nav와 [data-testid="stButton"]을 같이 씁니다. */
.st-key-pacs_nav [class*="st-key-pacs_nav_menu_"] [data-testid="stButton"] button:hover { background-color:#242424 !important; }
.st-key-pacs_nav [class*="st-key-pacs_nav_active_"] [data-testid="stButton"] button { background-color:#f2f2f2 !important; cursor:default !important; }
.st-key-pacs_nav [class*="st-key-pacs_nav_active_"] [data-testid="stButton"] button p { color:#202020 !important; font-weight:700 !important; }
@MENU_RULES@
</style>
"""


def _menu_rules() -> str:
    """메뉴마다 세로 위치(174부터 52 간격)와 켜짐·꺼짐 아이콘 규칙을 만듭니다."""
    rules = []
    for index, (screen, _label) in enumerate(MENUS):
        top = f"top:calc({174 + 52 * index} * var(--u));"
        rules.append(f".st-key-pacs_nav_menu_{screen}, .st-key-pacs_nav_active_{screen} {{ {top} }}")
        rules.append(f".st-key-pacs_nav_menu_{screen} button {{ background-image:{_menu_icon(screen, False)} !important; }}")
        rules.append(f".st-key-pacs_nav_active_{screen} button {{ background-image:{_menu_icon(screen, True)} !important; }}")
    return "\n".join(rules)


_CSS = (
    _STYLE.replace("@WIDTH@", str(WIDTH))
    .replace("@ICON_SHIELD@", _css_icon(_ICON_SHIELD))
    .replace("@MENU_RULES@", _menu_rules())
)


# ── 화면 그리기 ──────────────────────────────────────────────────────────────

def _text(key: str, body: str) -> None:
    """글자 한 덩어리. 글꼴·색·위치는 key 이름으로 위의 CSS가 정합니다(고정 문구만 넘기므로 Markdown 처리 없음)."""
    with st.container(key=key):
        st.markdown(body)


def _on_click(screen: str) -> None:
    st.session_state[_CLICKED_KEY] = screen


def render(active: str | None) -> str | None:
    """왼쪽 사이드바를 그리고, 사용자가 다른 메뉴를 눌렀으면 그 화면 이름(SCREEN_*)을 돌려줍니다.

    active: 지금 보여 주는 화면 이름. 그 메뉴를 밝게 표시하고, 눌러도 아무 일도 하지 않습니다.
    돌려준 값으로 화면을 바꾸는 일(떠나는 화면 정리 포함)은 호출한 쪽(app.py)이 합니다.
    """
    st.html(_CSS)
    with st.container(key="pacs_nav"):
        with st.container(key="pacs_nav_brand"):
            _text("pacs_nav_brand_name", BRAND_NAME)
            _text("pacs_nav_brand_sub", BRAND_SUB)
        _text("pacs_nav_workspace", WORKSPACE_LABEL)
        for screen, label in MENUS:
            if screen == active:
                with st.container(key=f"pacs_nav_active_{screen}"):
                    st.button(label, key=f"pacs_nav_btn_{screen}", width="stretch")
            else:
                with st.container(key=f"pacs_nav_menu_{screen}"):
                    st.button(label, key=f"pacs_nav_btn_{screen}", width="stretch", on_click=_on_click, args=(screen,))
        with st.container(key="pacs_nav_server"):
            _text("pacs_nav_server_title", SERVER_TITLE)
            _text("pacs_nav_server_sub", SERVER_SUB)
        _text("pacs_nav_version", VERSION_LABEL)  # 띄어쓰기는 CSS(white-space:pre)로 그대로 보입니다.

    clicked = st.session_state.pop(_CLICKED_KEY, None)
    return clicked if clicked is not None and clicked != active else None
