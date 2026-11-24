"""PACS 검사 목록 화면.

화면 요소와 입력 처리는 Streamlit 위젯으로 구성하고, 모양은 Figma 디자인(1440×960 기준)을 CSS로 맞춥니다.
검사 데이터는 ``load_exams``가 백엔드(services.case.get_case_list 등)에서 불러옵니다.

app.py에서는 ``render(current_user)``를 호출하고, 반환된 ``ExamListEvent``로 화면 이동(영상 뷰어, 수정,
다른 메뉴, 로그아웃)을 처리합니다. 단독 실행하면 샘플 사용자로 화면을 미리 볼 수 있고, 주소 끝에
``?role=REVIEWER``처럼 역할을 붙이면 그 역할로 볼 수 있습니다(REGISTRAR / REVIEWER / ADMIN).
샘플 사용자는 Firestore에 없는 사용자라 단독 실행에서는 목록 조회가 권한 검사에서 거절되어
"불러오지 못했습니다" 문구가 표시됩니다.

app.py에서 함께 쓰는 함수:
- ``current_user_from_profile(profile)``: ``session.get_current_user()``가 돌려준 사용자 정보(dict)를 ``render``에 넘길
  CurrentUser로 바꿉니다. 화면을 그릴 때마다 session.get_current_user()로 다시 확인한 값을 넘기면
  토큰 만료·사용자 삭제 시 로그인 화면으로 돌려보낼 수 있습니다.
- ``refresh_exams()``: 검사 등록·수정·배정 후 호출. 다음 화면 표시 때 목록을 새로 불러옵니다.
- ``reset_state()``: 이 화면이 세션에 저장한 검색 조건·선택·목록 캐시만 지웁니다. 이 화면만 처음 상태로 돌리고 싶을 때 씁니다.

로그아웃(EVENT_LOGOUT)은 팀의 ``session.logout()``으로 처리합니다. session.logout()이 세션 값을 모두 지우므로
(이메일 기억만 유지) 이 화면의 값도 함께 지워지고, reset_state()를 따로 부르지 않아도 됩니다.
"""

from __future__ import annotations

import base64
import calendar
import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import streamlit as st

_logger = logging.getLogger(__name__)

# 한국 시간(UTC+9). 서버 시간이 UTC여도 화면에는 한국 시간을 표시합니다. 한국은 서머타임이 없습니다.
KST = timezone(timedelta(hours=9), "KST")

# ── 업무 규칙 ────────────────────────────────────────────────────────────────

# 화면에 보여주는 검사 상태 4가지입니다(검토 흐름 순서).
STATUS_UNASSIGNED = "미배정"  # 등록됐지만 업무 담당자가 아직 검토 담당자를 배정하지 않음
STATUS_ASSIGNED = "배정 완료"  # 검토 담당자가 배정됐지만 아직 검토를 시작하지 않음
STATUS_REVIEWING = "검토 중"  # 검토 담당자가 "검토 시작"을 누름. 저장만 하고 나가도 검토 완료 전까지 유지
STATUS_DONE = "검토 완료"  # 검토 담당자가 "검토 완료"를 누름
STATUS_OPTIONS = (STATUS_UNASSIGNED, STATUS_ASSIGNED, STATUS_REVIEWING, STATUS_DONE)

# 백엔드(Firestore cases.status)에 지금 저장되는 상태 값입니다(models.py의 CaseStatus).
# 화면에는 _display_status가 위의 세 화면 상태로 바꿔 보여줍니다.
BACKEND_STATUS_WAITING = "검토 대기"
BACKEND_STATUS_REVIEWING = "검토 중"
BACKEND_STATUS_DONE = "검토 완료"

# 수정할 수 있는 상태입니다. 미배정 검사만 수정할 수 있고, 검토 담당자가 배정된 뒤
# (배정 완료·검토 중·검토 완료)에는 수정할 수 없습니다.
EDITABLE_STATUSES = frozenset({STATUS_UNASSIGNED})

# 사용자 역할입니다. Firestore users 문서의 role 값, models.py의 UserRole과 글자까지 같아야 합니다.
ROLE_REGISTRAR = "REGISTRAR"  # 등록 담당자
ROLE_REVIEWER = "REVIEWER"  # 검토 담당자
ROLE_ADMIN = "ADMIN"  # 업무 담당자

# 상단 바에 표시할 역할 이름입니다.
ROLE_LABELS = {
    ROLE_REGISTRAR: "등록 담당자",
    ROLE_REVIEWER: "검토 담당자",
    ROLE_ADMIN: "업무 담당자",
}

MODALITY_OPTIONS = ("CT", "MR", "CR", "US")
ALL = "전체"
DEFAULT_RANGE_DAYS = 7  # 등록일 검색 기본값: 오늘을 포함한 최근 7일

# 같은 사용자 화면에서 이 시간(초) 안에는 검사 목록을 다시 불러오지 않습니다.
# 줄 선택처럼 화면이 다시 그려질 때마다 서버(Firestore)를 조회하지 않기 위해서입니다.
EXAM_CACHE_SECONDS = 30

# 백엔드에서 한 번에 불러오는 검사 수(등록 최신순). 검색은 불러온 검사 안에서 합니다.
CASE_LIST_LIMIT = 50

# ── 화면 이동 이벤트 (render가 돌려주는 ExamListEvent.kind / target 값) ─────────────

EVENT_OPEN_VIEWER = "open_viewer"  # 영상 뷰어 버튼 (case_id 포함)
EVENT_EDIT = "edit"  # 수정 버튼 (case_id 포함)
EVENT_NAVIGATE = "navigate"  # 왼쪽 메뉴 (target 포함)
EVENT_LOGOUT = "logout"  # 로그아웃 버튼

TARGET_CASE_REGISTER = "case_register"  # 검사 등록 화면
TARGET_REVIEW_ASSIGN = "review_assign"  # 검토 배정 화면

# ── 안내 문구 (디자인 확정 전 임시 문구) ──────────────────────────────────────────

MSG_NO_RESULT = "조건에 맞는 검사가 없습니다. 검색 조건을 변경해 보세요."
MSG_NO_SELECTION = "목록에서 검사를 선택하면 상세 정보가 표시됩니다."
MSG_LOAD_FAILED = "검사 목록을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요."
MSG_EDIT_NOT_ALLOWED = "본인이 등록한 미배정 검사만 수정할 수 있습니다."
MSG_INVALID_DATES = "등록일 시작일이 종료일보다 늦을 수 없습니다."
USER_NAME_FALLBACK = "사용자"  # 사용자 정보에 이름이 없을 때 상단 바에 표시

# ── 세션 상태 키 ─────────────────────────────────────────────────────────────
# 이 화면이 세션에 저장하는 값은 모두 _STATE_PREFIX로 시작합니다(reset_state가 이 이름으로 한꺼번에 지움).

_STATE_PREFIX = "_pacs_list_"
_EXAMS_CACHE_KEY = "_pacs_list_exams_cache"
_APPLIED_FILTER_KEY = "_pacs_list_applied_filter"
_FILTER_VERSION_KEY = "_pacs_list_filter_version"
_SELECTED_KEY = "_pacs_list_selected_case"
_EVENT_KEY = "_pacs_list_event"
_NOTICE_KEY = "_pacs_list_notice"
# 등록일 달력: 열림 여부, 고르는 중인 기간(시작일, 종료일 또는 None), 달력에 보이는 달(1일)
_CAL_OPEN_KEY = "_pacs_list_cal_open"
_CAL_DRAFT_KEY = "_pacs_list_cal_draft"
_CAL_MONTH_KEY = "_pacs_list_cal_month"

# 화면 가운데 확인창: ("logout", None) 또는 ("edit", case_id)
_CONFIRM_KEY = "_pacs_list_confirm"

# 확인창 문구(Figma "로그아웃 확인", "검사 목록 · 수정 확인"): 제목, 안내 두 줄, 확인 버튼 글자
CONFIRM_TEXTS = {
    "logout": (
        "로그아웃하시겠습니까?",
        ("저장하지 않은 변경 사항은 사라질 수 있습니다.", "로그아웃하면 로그인 화면으로 이동합니다."),
        "로그아웃",
    ),
    "edit": (
        "수정하시겠습니까?",
        ("선택한 환자의 검사 정보를 수정합니다.", "환자와 검사 정보를 확인한 후 진행해 주세요."),
        "수정",
    ),
}

# 등록일 달력의 빠른 기간 버튼: (key 이름, 표시 글자, 오늘을 포함한 일수)
CALENDAR_PRESETS = (("today", "오늘", 1), ("week", "최근 7일", 7), ("month", "최근 30일", 30))
WEEKDAY_LABELS = ("일", "월", "화", "수", "목", "금", "토")


# ── 데이터 형식 ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CurrentUser:
    """화면에 필요한 로그인 사용자 정보."""

    uid: str
    role: str  # ROLE_REGISTRAR | ROLE_REVIEWER | ROLE_ADMIN (대문자, 로그인 화면에서 확인된 값)
    name: str  # 비어 있으면 화면에 USER_NAME_FALLBACK을 표시합니다.
    department_id: str = ""  # 소속 진료과 ID(users 문서의 department_id). 화면에는 표시하지 않고 검사 등록 저장에 씁니다.


@dataclass(frozen=True)
class ExamRecord:
    """검사 목록 한 줄과 상세 패널에 표시할 정보."""

    case_id: str
    accession_no: str  # 접수번호. 팀 데이터에 없어 case_id를 씁니다(화면 상세에는 "케이스 ID"로 case_id를 표시).
    patient_id: str
    patient_name: str
    patient_sex: str  # "남" / "여" / "" (알 수 없음)
    patient_age: int | None
    modality: str  # 검사 장비(백엔드 exam_type). CT, MR, CR, US ...
    exam_name: str  # 검사명(백엔드 exam_info)
    exam_at: datetime | None  # 검사 시각. 상세 패널의 검사일시에만 표시합니다.
    registered_at: datetime  # 등록 시각(백엔드 created_at). 목록의 시간 표시·정렬·기간 검색 기준입니다.
    status: str  # STATUS_OPTIONS 중 하나(화면 상태)
    department: str  # 진료과 이름(departments 문서의 name). 없으면 ""
    created_by: str  # 등록자 UID
    assignee_uid: str | None = None  # 배정된 검토 담당자 UID. 배정 정보가 없으면 None


@dataclass(frozen=True)
class SearchFilter:
    query: str
    start: date
    end: date
    modality: str
    status: str


@dataclass(frozen=True)
class ExamListEvent:
    """사용자가 다른 화면으로 이동하려 할 때 render가 돌려주는 값."""

    kind: str  # EVENT_OPEN_VIEWER | EVENT_EDIT | EVENT_NAVIGATE | EVENT_LOGOUT
    case_id: str | None = None
    target: str | None = None  # EVENT_NAVIGATE일 때: TARGET_CASE_REGISTER | TARGET_REVIEW_ASSIGN


# ── 로그인 사용자 ────────────────────────────────────────────────────────────

def _clean(value: object) -> str:
    """None은 빈 칸으로, 나머지는 앞뒤 공백을 지운 글자로 바꿉니다."""
    return "" if value is None else str(value).strip()

# 로그인 결과(백엔드가 준 사전 형태의 데이터)를 검사 목록 화면이 쓰는 CurrentUser 형태로 바꿔 주는 함수
def current_user_from_profile(profile: Mapping[str, object]) -> CurrentUser:
    """로그인으로 확인된 사용자 정보(dict)를 이 화면의 CurrentUser로 바꿉니다(app.py에서 호출).

    profile에는 session.get_current_user()가 돌려준 사용자 정보(models.py의 User 형식)를 넣습니다.
    받은 값만 정리하고 데이터베이스는 조회하지 않습니다.
    - uid, role: 꼭 필요합니다. 비어 있으면 ValueError를 내므로, app.py는 로그인 화면으로 돌려보내면 됩니다.
    - role: 앞뒤 공백을 지우고 대문자로 맞춥니다.
    - name: 없으면 빈 칸으로 두고, 화면에는 USER_NAME_FALLBACK을 표시합니다.
    - department_id: 소속 진료과 ID. 없으면(None 포함) 빈 칸입니다.
    """
    uid = _clean(profile.get("uid"))
    role = _clean(profile.get("role")).upper()
    if not uid or not role:
        raise ValueError("로그인 사용자 정보에 uid와 role이 모두 있어야 합니다.")
    return CurrentUser(
        uid=uid,
        role=role,
        name=_clean(profile.get("name")),
        department_id=_clean(profile.get("department_id")),
    )


def role_label(user: CurrentUser) -> str:
    """상단 바에 표시할 역할 이름(예: 등록 담당자)."""
    return ROLE_LABELS.get(user.role, user.role)


# ── 데이터 불러오기 ──────────────────────────────────────────────────────────

_PREVIEW_USER = CurrentUser(uid="preview-registrar", role=ROLE_REGISTRAR, name="김의사")


def load_exams(user: CurrentUser) -> list[ExamRecord]:
    """로그인 사용자가 볼 수 있는 검사 목록을 백엔드에서 불러와 화면용 ExamRecord로 바꿉니다.

    1. 케이스 목록: services.case.get_case_list(uid, limit). 안에서 조회 권한(require_case_read)을 검사합니다.
    2. 환자·진료과 정보: repositories.patient.get_patient, repositories.department.get_department.
       같은 환자·진료과는 한 번만 조회합니다.
    3. 담당자 배정: repositories.case.get_case_assignment. 아직 구현되지 않았으면 배정 정보 없이 표시합니다.

    목록 조회가 실패하면 오류를 그대로 올려 화면에 불러오기 실패 문구가 나오게 합니다(_get_exams가 처리).
    케이스 한 건의 값이 잘못되어 바꿀 수 없으면 그 건만 빼고 로그를 남깁니다.
    """
    # 함수 안에서 불러와, Firebase 설정이 없어도 이 파일을 불러올 수 있게 합니다(login.py와 같은 방식).
    from src.repositories.department import get_department
    from src.repositories.patient import get_patient
    from src.services.case import get_case_list

    cases = get_case_list(user.uid, limit=CASE_LIST_LIMIT)
    patients = _lookup_each(get_patient, (case.get("patient_id") for case in cases))
    departments = _lookup_each(get_department, (case.get("department_id") for case in cases))
    assignees = _load_assignees([_clean(case.get("case_id")) for case in cases])

    exams = []
    for case in cases:
        try:
            exams.append(_exam_from_case(
                case,
                patient=patients.get(_clean(case.get("patient_id"))),
                department=departments.get(_clean(case.get("department_id"))),
                assignee_uid=assignees.get(_clean(case.get("case_id"))),
            ))
        except ValueError:
            _logger.warning("검사 목록에서 제외한 케이스: %s", case.get("case_id"), exc_info=True)
    return exams


def _lookup_each(
    fetch: Callable[[str], Mapping[str, object] | None], ids: Iterable[object]
) -> dict[str, Mapping[str, object] | None]:
    """ID마다 fetch(id)를 한 번씩만 불러 {ID: 결과} 사전을 만듭니다. 빈 ID는 건너뜁니다."""
    found: dict[str, Mapping[str, object] | None] = {}
    for raw_id in ids:
        item_id = _clean(raw_id)
        if item_id and item_id not in found:
            found[item_id] = fetch(item_id)
    return found


def _load_assignees(case_ids: list[str]) -> dict[str, str | None]:
    """케이스마다 배정된 검토 담당자 UID를 조회합니다. 조회 함수가 아직 구현되지 않았으면 빈 사전을 돌려줍니다."""
    from src.repositories.case import get_case_assignment

    assignees: dict[str, str | None] = {}
    for case_id in case_ids:
        if not case_id:
            continue
        try:
            assignment = get_case_assignment(case_id)
        except NotImplementedError:
            _logger.info("담당자 배정 조회가 아직 구현되지 않아 배정 정보 없이 목록을 표시합니다.")
            return {}
        assignees[case_id] = _assignee_uid(assignment)
    return assignees


def _assignee_uid(assignment: Mapping[str, object] | None) -> str | None:
    """배정 문서에서 담당자 UID를 꺼냅니다. 조회 전용(READ_ONLY) 권한은 배정으로 보지 않습니다.

    필드 이름은 팀에서 확정 전이라 models.py의 user_id와 설계 문서의 user_uid를 모두 읽습니다.
    """
    if not assignment:
        return None
    if _clean(assignment.get("access_type")).upper() not in ("", "ASSIGNED"):
        return None
    return _clean(assignment.get("user_id") or assignment.get("user_uid")) or None


_SEX_LABELS = {"M": "남", "MALE": "남", "남": "남", "남성": "남", "F": "여", "FEMALE": "여", "여": "여", "여성": "여"}


def _exam_from_case(
    case: Mapping[str, object],
    patient: Mapping[str, object] | None,
    department: Mapping[str, object] | None,
    assignee_uid: str | None,
) -> ExamRecord:
    """백엔드 케이스 문서 1건과 환자·진료과·담당자 정보를 화면 한 줄(ExamRecord)로 바꿉니다.

    케이스 ID, 등록 시각, 상태를 알 수 없으면 ValueError를 냅니다(load_exams가 그 건만 제외).
    """
    case_id = _clean(case.get("case_id"))
    registered_at = _to_datetime(case.get("created_at"))
    if not case_id or registered_at is None:
        raise ValueError("케이스 ID 또는 등록 시각(created_at)이 없습니다.")
    patient = patient or {}
    department = department or {}
    return ExamRecord(
        case_id=case_id,
        accession_no=_clean(case.get("accession_no")) or case_id,
        patient_id=_clean(case.get("patient_id")),
        patient_name=_clean(patient.get("name")) or "-",
        patient_sex=_SEX_LABELS.get(_clean(patient.get("sex")).upper(), ""),
        patient_age=_age(patient.get("birth_date")),
        modality=_clean(case.get("exam_type")),
        exam_name=_clean(case.get("exam_info")),
        exam_at=_to_datetime(case.get("exam_at")),
        registered_at=registered_at,
        status=_display_status(case.get("status"), assignee_uid),
        department=_clean(department.get("name")),
        created_by=_clean(case.get("created_by")),
        assignee_uid=assignee_uid,
    )


def _display_status(raw: object, assignee_uid: str | None) -> str:
    """백엔드 상태 값을 화면 상태(미배정 / 배정 완료 / 검토 중 / 검토 완료)로 바꿉니다.

    - 검토 대기: 담당자가 배정되었으면 배정 완료, 아니면 미배정 (배정은 case_assignees 문서로 판단)
    - 검토 중: 검토 중 (검토 담당자가 "검토 시작"을 누른 뒤, 검토 완료 전까지)
    - 검토 완료: 검토 완료
    - 팀이 화면 상태 값을 그대로 저장하기로 하면 그 값을 그대로 씁니다.
    """
    status = _clean(raw)
    if status in STATUS_OPTIONS:
        return status
    if status == BACKEND_STATUS_WAITING:
        return STATUS_ASSIGNED if assignee_uid else STATUS_UNASSIGNED
    if status == BACKEND_STATUS_REVIEWING:
        return STATUS_REVIEWING
    if status == BACKEND_STATUS_DONE:
        return STATUS_DONE
    raise ValueError(f"알 수 없는 상태 값입니다: {status!r}")


def _to_datetime(value: object) -> datetime | None:
    """Firestore 시각 값을 시간대가 있는 datetime으로 바꿉니다. 없거나 알 수 없는 형식이면 None입니다.

    시간대가 없는 값은 한국 시간으로 봅니다. ISO 형식 글자("2026-11-20T10:38:00")도 받습니다.
    """
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=KST)


def _age(birth_date: object) -> int | None:
    """생년월일("YYYY-MM-DD" 또는 날짜 값)로 오늘(한국 시간) 기준 만 나이를 계산합니다. 알 수 없으면 None입니다."""
    if isinstance(birth_date, datetime):
        born = birth_date.date()
    elif isinstance(birth_date, date):
        born = birth_date
    else:
        try:
            born = date.fromisoformat(_clean(birth_date))
        except ValueError:
            return None
    today = _today()
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return age if age >= 0 else None


def _get_exams(user: CurrentUser) -> tuple[list[ExamRecord], bool]:
    """세션 캐시를 거쳐 검사 목록을 가져옵니다. (목록, 불러오기 실패 여부)를 돌려줍니다.

    캐시는 사용자 세션마다 따로 저장되고, 저장한 사용자의 UID가 다르면 쓰지 않으므로
    다른 사용자의 목록과 섞이지 않습니다. 불러오기에 실패하면 캐시에 남기지 않아 다음 화면 표시 때 다시 시도합니다.
    """
    cached = st.session_state.get(_EXAMS_CACHE_KEY)
    now = time.monotonic()
    if cached is not None and cached[0] == user.uid and now - cached[1] < EXAM_CACHE_SECONDS:
        return cached[2], False
    try:
        exams = load_exams(user)
    except Exception:
        _logger.exception("검사 목록 불러오기 실패")
        return [], True
    st.session_state[_EXAMS_CACHE_KEY] = (user.uid, now, exams)
    return exams, False


def refresh_exams() -> None:
    """저장해 둔 검사 목록을 지워, 다음 화면 표시 때 새로 불러오게 합니다(등록·수정·배정 후 호출)."""
    st.session_state.pop(_EXAMS_CACHE_KEY, None)


def reset_state() -> None:
    """이 화면이 세션에 저장한 값(검색 조건, 선택, 필터 칸, 목록 캐시 등)만 지웁니다.

    로그아웃할 때는 session.logout()이 세션 전체를 지우므로 따로 부르지 않아도 됩니다.
    """
    for key in [k for k in st.session_state.keys() if str(k).startswith(_STATE_PREFIX)]:
        del st.session_state[key]


# ── 검색·정렬·권한 ───────────────────────────────────────────────────────────

def _today() -> date:
    return datetime.now(KST).date()


def default_filter() -> SearchFilter:
    today = _today()
    return SearchFilter("", today - timedelta(days=DEFAULT_RANGE_DAYS - 1), today, ALL, ALL)


def filter_exams(exams: list[ExamRecord], condition: SearchFilter) -> list[ExamRecord]:
    """검색 조건을 모두 만족하는 검사를 등록 시간 최신순으로 돌려줍니다.

    검색어는 환자명, 환자 ID, 케이스 ID 중 하나에 들어 있으면 찾습니다(대소문자 구분 없음).
    기간은 등록일(한국 시간) 기준입니다.
    """
    query = condition.query.strip().lower()
    matched = []
    for exam in exams:
        searchable = (exam.patient_name, exam.patient_id, exam.case_id)
        if query and not any(query in text.lower() for text in searchable):
            continue
        registered_day = exam.registered_at.astimezone(KST).date()
        if not condition.start <= registered_day <= condition.end:
            continue
        if condition.modality != ALL and exam.modality != condition.modality:
            continue
        if condition.status != ALL and exam.status != condition.status:
            continue
        matched.append(exam)
    # 최근에 등록한 검사가 위로 옵니다.
    return sorted(matched, key=lambda e: e.registered_at, reverse=True)


def is_registrar(user: CurrentUser) -> bool:
    return user.role == ROLE_REGISTRAR


def can_edit(user: CurrentUser, exam: ExamRecord) -> bool:
    """등록 담당자가 본인이 등록한, 수정 가능한 상태의 검사일 때만 수정할 수 있습니다."""
    return is_registrar(user) and exam.created_by == user.uid and exam.status in EDITABLE_STATUSES


# ── 아이콘 (Figma에서 받은 SVG) ──────────────────────────────────────────────

def _svg(width: float, height: float, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" fill="none">{body}</svg>'
    )


_STROKE = 'stroke-linecap="round" stroke-linejoin="round"'
_ICON_LIST = _svg(20, 20, (
    f'<path d="M5 2.5H15C15.9205 2.5 16.6667 3.24619 16.6667 4.16667V15.8333C16.6667 16.7538 15.9205 17.5 15 17.5H5'
    f'C4.07953 17.5 3.33333 16.7538 3.33333 15.8333V4.16667C3.33333 3.24619 4.07953 2.5 5 2.5Z" '
    f'stroke-width="1.6" {_STROKE} stroke="#202020"/>'
    f'<path d="M6.66667 6.66667H13.3333M6.66667 10H13.3333M6.66667 13.3333H10.8333" '
    f'stroke-width="1.6" {_STROKE} stroke="#202020"/>'
))
# 문서(14×16)와 더하기(6×6)를 Figma 배치대로 20×20 한 장에 합칩니다.
_ICON_REGISTER = _svg(20, 20, (
    '<g transform="translate(3 2)">'
    f'<path d="M8.25 0.75H2.25C1.85218 0.75 1.47064 0.908035 1.18934 1.18934C0.908035 1.47064 0.75 1.85218 0.75 2.25'
    f'V13.75C0.75 14.1478 0.908035 14.5294 1.18934 14.8107C1.47064 15.092 1.85218 15.25 2.25 15.25H11.75'
    f'C12.1478 15.25 12.5294 15.092 12.8107 14.8107C13.092 14.5294 13.25 14.1478 13.25 13.75V5.75L8.25 0.75Z" '
    f'stroke-width="1.5" {_STROKE} stroke="#C1C1C1"/>'
    f'<path d="M8.25 0.75V4.75C8.25 5.01522 8.35536 5.26957 8.54289 5.45711C8.73043 5.64464 8.98478 5.75 9.25 5.75'
    f'H13.25" stroke-width="1.5" {_STROKE} stroke="#C1C1C1"/></g>'
    '<g transform="translate(7 9.25)"><path d="M3 0.75V5.25M0.75 3H5.25" stroke-width="1.5" '
    'stroke-linecap="round" stroke="#C1C1C1"/></g>'
))
# 사람(10.5×15.75)과 체크(6.5×4.75)를 Figma 배치대로 합칩니다.
_ICON_REVIEW = _svg(20, 20, (
    '<g transform="translate(2 2.25)">'
    f'<path d="M8.25 3.75C8.25 5.40685 6.90685 6.75 5.25 6.75C3.59315 6.75 2.25 5.40685 2.25 3.75'
    f'C2.25 2.09315 3.59315 0.75 5.25 0.75C6.90685 0.75 8.25 2.09315 8.25 3.75Z" '
    f'stroke-width="1.5" {_STROKE} stroke="#C1C1C1"/>'
    f'<path d="M0.75 15V13.25C0.75 12.1891 1.17143 11.1717 1.92157 10.4216C2.67172 9.67143 3.68913 9.25 4.75 9.25'
    f'H5.75C6.81087 9.25 7.82828 9.67143 8.57843 10.4216C9.32857 11.1717 9.75 12.1891 9.75 13.25V15" '
    f'stroke-width="1.5" {_STROKE} stroke="#C1C1C1"/></g>'
    f'<g transform="translate(11.5 7)"><path d="M0.75 2L2.75 4L5.75 0.750004" stroke-width="1.5" '
    f'{_STROKE} stroke="#C1C1C1"/></g>'
))
_ICON_SHIELD = _svg(18, 18, (
    f'<path d="M9 2.25L15 4.5V9C15 12.75 9 15.75 9 15.75C9 15.75 3 12.75 3 9V4.5L9 2.25Z" '
    f'stroke-width="1.6" {_STROKE} stroke="#BDBDBD"/>'
    f'<path d="M6 9L8.25 11.25L12 6.75" stroke-width="1.6" {_STROKE} stroke="#BDBDBD"/>'
))
_ICON_CHEVRON_RIGHT = _svg(16, 16, f'<path d="M6 4L10 8L6 12" stroke-width="1.6" {_STROKE} stroke="#999999"/>')
_ICON_LOGOUT = _svg(16, 16, (
    '<path d="M7.5 2.5H2.5V13.5H7.5M7.5 8H13.5M10.5 11L13.5 8L10.5 5" stroke-width="1.6" stroke="#555555"/>'
))
_ICON_SEARCH_FIELD = _svg(18, 18, (
    f'<path d="M12.75 7.875C12.75 10.5674 10.5674 12.75 7.875 12.75C5.18261 12.75 3 10.5674 3 7.875'
    f'C3 5.18261 5.18261 3 7.875 3C10.5674 3 12.75 5.18261 12.75 7.875Z" stroke-width="1.6" {_STROKE} stroke="#555555"/>'
    f'<path d="M12 12L15 15" stroke-width="1.6" {_STROKE} stroke="#555555"/>'
))
_ICON_CALENDAR = _svg(18, 18, (
    f'<path d="M3.75 3.75H14.25C15.0784 3.75 15.75 4.42157 15.75 5.25V14.25C15.75 15.0784 15.0784 15.75 14.25 15.75'
    f'H3.75C2.92157 15.75 2.25 15.0784 2.25 14.25V5.25C2.25 4.42157 2.92157 3.75 3.75 3.75Z" '
    f'stroke-width="1.6" {_STROKE} stroke="#555555"/>'
    f'<path d="M5.25 2.25V5.25M12.75 2.25V5.25M2.25 8.25H15.75" stroke-width="1.6" {_STROKE} stroke="#555555"/>'
))
_ICON_CHEVRON_DOWN = _svg(18, 18, f'<path d="M5.25 7.5L9 11.25L12.75 7.5" stroke-width="1.6" {_STROKE} stroke="#555555"/>')
_ICON_SEARCH_BUTTON = _svg(18, 18, (
    '<path d="M11.5355 10.8293L15 15.7062M13 7.29378C13 8.61987 12.4732 9.89164 11.5355 10.8293'
    'C10.5979 11.767 9.32608 12.2938 8 12.2938C6.67392 12.2938 5.40215 11.767 4.46447 10.8293'
    'C3.52678 9.89164 3 8.61987 3 7.29378C3 5.9677 3.52678 4.69593 4.46447 3.75825C5.40215 2.82057 6.67392 2.29378 8 2.29378'
    'C9.32608 2.29378 10.5979 2.82057 11.5355 3.75825C12.4732 4.69593 13 5.9677 13 7.29378Z" '
    'stroke-width="1.6" stroke="#555555"/>'
))


# 등록일 달력과 선택 메뉴 아이콘(Figma "검사일 · 기간 선택 달력", "선택 메뉴"에서 받은 20×20 SVG)
_FIGMA_STROKE = f'stroke-width="1.41667" {_STROKE} stroke="#626262"'
_ICON_CLOSE = _svg(20, 20, f'<path d="M5 5L15 15M15 5L5 15" {_FIGMA_STROKE}/>')
_ICON_MONTH_PREV = _svg(20, 20, f'<path d="M12.5 5L7.5 10L12.5 15" {_FIGMA_STROKE}/>')
_ICON_MONTH_NEXT = _svg(20, 20, f'<path d="M7.5 5L12.5 10L7.5 15" {_FIGMA_STROKE}/>')
_ICON_CHECK = _svg(20, 20, f'<path d="M4.16667 10L8.33333 14.1667L15.8333 5.83333" {_FIGMA_STROKE}/>')


def _css_icon(svg: str) -> str:
    """CSS background-image에 넣을 수 있는 data URI를 만듭니다."""
    return "url('data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii") + "')"


# ── 화면 배치 CSS ────────────────────────────────────────────────────────────
# 모든 크기는 화면 높이에 비례하는 --u(= 화면 높이 / 960) 단위이고, 좌표는 Figma 레이어 값을 그대로 씁니다.
# 화면이 디자인(1440)보다 넓으면 본문과 표 너비가 늘어나고, 오른쪽 요소는 오른쪽 끝을 기준으로 붙습니다.
# 글자는 로그인 화면과 같이 key가 붙은 st.container 안의 st.markdown으로 그리고, 위치와 글꼴은 key 이름으로 정합니다.

_STYLE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap');

html, body, .stApp, .stMain { background:#f6f6f6 !important; }

/* 처리 중 화면: 목록 조회나 화면 이동처럼 실행이 0.4초 넘게 걸리면 화면을 살짝 회색으로 덮고
   가운데에 회전 표시를 띄웁니다. 끝나면 자동으로 사라집니다. 짧은 실행(줄 선택, 시계 갱신)에서는 보이지 않습니다.
   Streamlit이 실행 중일 때 stApp에 붙이는 data-test-script-state="running" 표시를 이용합니다.
   보이는 동안에는 덮개가 클릭을 막아 같은 버튼을 여러 번 누르는 것도 막습니다. */
[data-testid="stApp"][data-test-script-state="running"]::before,
[data-testid="stApp"][data-test-script-state="running"]::after {
  content:''; position:fixed; z-index:1000000; visibility:hidden; opacity:0;
  animation:pacs-loading-show 0.2s ease 0.4s forwards;
}
[data-testid="stApp"][data-test-script-state="running"]::before { inset:0; background:rgba(40, 40, 40, 0.18); }
[data-testid="stApp"][data-test-script-state="running"]::after {
  left:50%; top:50%; width:calc(44 * 100vh / 960); height:calc(44 * 100vh / 960);
  margin:calc(-22 * 100vh / 960) 0 0 calc(-22 * 100vh / 960); border-radius:50%;
  border:calc(4 * 100vh / 960) solid rgba(255, 255, 255, 0.8); border-top-color:#292929;
  animation:pacs-loading-show 0.2s ease 0.4s forwards, pacs-loading-spin 0.8s linear infinite;
}
@keyframes pacs-loading-show { to { visibility:visible; opacity:1; } }
@keyframes pacs-loading-spin { to { transform:rotate(360deg); } }
[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"],
[data-testid="stSidebar"], #MainMenu, footer { display:none !important; }
.stMainBlockContainer { max-width:none !important; padding:0 !important; margin:0 !important; }

.st-key-pacs_list_stage {
  --u:calc(100vh / 960);
  position:fixed !important; inset:0; z-index:1; overflow:hidden;
  background:#f6f6f6; color:#222; font-family:'Noto Sans KR', sans-serif; font-weight:400;
}
.st-key-pacs_list_stage * { box-sizing:border-box; }

/* Streamlit 요소의 기본 간격·여백·글꼴을 없애고, 아래에서 지정한 값만 쓰게 합니다. */
.st-key-pacs_list_stage [data-testid="stVerticalBlock"],
.st-key-pacs_list_stage [data-testid="stHorizontalBlock"] { gap:0 !important; }
.st-key-pacs_list_stage [data-testid="stElementContainer"] { margin:0 !important; }
.st-key-pacs_list_stage [data-testid="stMarkdown"],
.st-key-pacs_list_stage [data-testid="stMarkdownContainer"],
.st-key-pacs_list_stage [data-testid="stHeading"],
.st-key-pacs_list_stage [data-testid="stHeadingWithActionElements"],
.st-key-pacs_list_stage [data-testid="stMarkdownContainer"] p,
.st-key-pacs_list_stage h1, .st-key-pacs_list_stage h2, .st-key-pacs_list_stage h3 {
  margin:0 !important; padding:0 !important; font-family:inherit; font-size:inherit;
  font-weight:inherit; line-height:inherit; letter-spacing:normal; color:inherit; white-space:inherit;
}
.st-key-pacs_list_stage [data-testid="stHeaderActionElements"] { display:none !important; }
.st-key-pacs_list_stage button { font-family:inherit !important; }
.st-key-pacs_list_stage [data-testid="stButtonShortcut"], .st-key-pacs_list_stage kbd { display:none !important; }

/* 절대 좌표로 배치하는 블록 */
.st-key-pacs_list_sidebar, .st-key-pacs_list_header, .st-key-pacs_list_main,
.st-key-pacs_list_menu_list, .st-key-pacs_list_menu_register, .st-key-pacs_list_menu_review,
.st-key-pacs_list_clock, .st-key-pacs_list_logout, .st-key-pacs_list_title,
.st-key-pacs_list_stats, .st-key-pacs_list_filters, .st-key-pacs_list_count,
.st-key-pacs_list_table, .st-key-pacs_list_detail, .st-key-pacs_list_edit, .st-key-pacs_list_viewer {
  position:absolute !important;
}
/* 절대 좌표로 배치하는 글자 칸: 내용 너비만큼만 차지하고 한 줄로 표시합니다. */
.st-key-pacs_list_brand, .st-key-pacs_list_workspace, .st-key-pacs_list_server, .st-key-pacs_list_version,
.st-key-pacs_list_crumb_home, .st-key-pacs_list_crumb_page, .st-key-pacs_list_user,
[class*="st-key-pacs_list_stat_label_"], [class*="st-key-pacs_list_stat_value_"], [class*="st-key-pacs_list_stat_note_"],
[class*="st-key-pacs_list_th_"], [class*="st-key-pacs_list_cname_"], [class*="st-key-pacs_list_cmeta_"],
[class*="st-key-pacs_list_cpid_"], [class*="st-key-pacs_list_cmod_"], [class*="st-key-pacs_list_cexam_"],
[class*="st-key-pacs_list_ctime_"], [class*="st-key-pacs_list_cbadge_"],
.st-key-pacs_list_dtitle, .st-key-pacs_list_dname, [class*="st-key-pacs_list_dbadge_"], .st-key-pacs_list_dmeta,
[class*="st-key-pacs_list_dlabel_"], [class*="st-key-pacs_list_dvalue_"], .st-key-pacs_list_dempty {
  position:absolute !important; width:auto !important; white-space:nowrap;
}
/* 넘치면 말줄임(…)으로 자르는 글자 칸 */
[class*="st-key-pacs_list_cname_"], [class*="st-key-pacs_list_cmeta_"], [class*="st-key-pacs_list_cexam_"],
.st-key-pacs_list_dname, [class*="st-key-pacs_list_dvalue_"] { overflow:hidden; }
:is([class*="st-key-pacs_list_cname_"], [class*="st-key-pacs_list_cmeta_"], [class*="st-key-pacs_list_cexam_"],
    .st-key-pacs_list_dname, [class*="st-key-pacs_list_dvalue_"])
  :is([data-testid="stElementContainer"], [data-testid="stMarkdown"], [data-testid="stMarkdownContainer"], p) {
  width:100% !important; max-width:100%; overflow:hidden; text-overflow:ellipsis;
}

/* ── 사이드바 (204 × 화면 높이) ── */
.st-key-pacs_list_sidebar {
  left:0; top:0; width:calc(204 * var(--u)) !important; height:100vh; background:#191919; color:#fff;
}
.st-key-pacs_list_sidebar::before {
  content:''; position:absolute; left:calc(20 * var(--u)); top:calc(111 * var(--u));
  width:calc(164 * var(--u)); height:1px; background:#363636;
}
.st-key-pacs_list_brand { left:calc(24 * var(--u)); top:calc(24 * var(--u)); }
.st-key-pacs_list_brand::before {
  content:''; position:absolute; left:0; top:calc(2 * var(--u));
  width:calc(28 * var(--u)); height:calc(28 * var(--u)); border-radius:calc(6 * var(--u));
  background:
    linear-gradient(#191919, #191919) center / calc(16 * var(--u)) calc(4 * var(--u)) no-repeat,
    linear-gradient(#191919, #191919) center / calc(4 * var(--u)) calc(16 * var(--u)) no-repeat,
    #f4f4f4;
}
.st-key-pacs_list_brand_name {
  padding-left:calc(39 * var(--u)); color:#fff;
  font-size:calc(23 * var(--u)); line-height:calc(35 * var(--u)); font-weight:700;
}
.st-key-pacs_list_brand_sub { margin-top:calc(8 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#a7a7a7; }
.st-key-pacs_list_workspace {
  left:calc(24 * var(--u)); top:calc(137 * var(--u));
  font-size:calc(10 * var(--u)); line-height:calc(15 * var(--u)); font-weight:500; color:#9a9a9a;
}
.st-key-pacs_list_server {
  left:calc(16 * var(--u)); top:calc(842 * var(--u)); width:calc(172 * var(--u)) !important; height:calc(65 * var(--u));
  padding:calc(11 * var(--u)) calc(12 * var(--u)) 0 !important; border-radius:calc(6 * var(--u)); background:#252525;
}
.st-key-pacs_list_server::before {
  content:''; position:absolute; left:calc(12 * var(--u)); top:calc(11 * var(--u));
  width:calc(18 * var(--u)); height:calc(18 * var(--u)); background:@ICON_SHIELD@ center / contain no-repeat;
}
.st-key-pacs_list_server_title {
  padding-left:calc(26 * var(--u)); font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:500; color:#d7d7d7;
}
.st-key-pacs_list_server_sub { margin-top:calc(7 * var(--u)); font-size:calc(10 * var(--u)); line-height:calc(15 * var(--u)); color:#a5a5a5; }
.st-key-pacs_list_version {
  left:calc(24 * var(--u)); top:calc(927 * var(--u)); white-space:pre;
  font-size:calc(10 * var(--u)); line-height:calc(15 * var(--u)); color:#8e8e8e;
}

/* 메뉴 버튼: 180×44, 아이콘 (14, 12), 글자 x 46 */
.st-key-pacs_list_menu_list, .st-key-pacs_list_menu_register, .st-key-pacs_list_menu_review {
  left:calc(12 * var(--u)); width:calc(180 * var(--u)) !important; height:calc(44 * var(--u));
}
.st-key-pacs_list_menu_list { top:calc(174 * var(--u)); }
.st-key-pacs_list_menu_register { top:calc(226 * var(--u)); }
.st-key-pacs_list_menu_review { top:calc(278 * var(--u)); }
.st-key-pacs_list_sidebar [data-testid="stButton"], .st-key-pacs_list_sidebar [data-testid="stButton"] button {
  width:100% !important; height:calc(44 * var(--u)) !important; min-height:0 !important;
}
.st-key-pacs_list_sidebar [data-testid="stButton"] button {
  justify-content:flex-start !important; padding:0 0 0 calc(46 * var(--u)) !important;
  border:0 !important; border-radius:calc(6 * var(--u)) !important; box-shadow:none !important;
  background-color:transparent !important; background-repeat:no-repeat !important;
  background-position:calc(14 * var(--u)) center !important; background-size:calc(20 * var(--u)) !important;
}
.st-key-pacs_list_sidebar [data-testid="stButton"] button > div { width:100%; justify-content:flex-start !important; }
.st-key-pacs_list_sidebar [data-testid="stButton"] button p {
  font-size:calc(14 * var(--u)) !important; line-height:calc(21 * var(--u)) !important; color:#c1c1c1 !important;
}
.st-key-pacs_list_sidebar .st-key-pacs_list_menu_list button { background-color:#f2f2f2 !important; background-image:@ICON_LIST@ !important; }
.st-key-pacs_list_sidebar .st-key-pacs_list_menu_list button p { color:#202020 !important; font-weight:700 !important; }
.st-key-pacs_list_sidebar .st-key-pacs_list_menu_register button { background-image:@ICON_REGISTER@ !important; }
.st-key-pacs_list_sidebar .st-key-pacs_list_menu_review button { background-image:@ICON_REVIEW@ !important; }
.st-key-pacs_list_sidebar .st-key-pacs_list_menu_register button:hover,
.st-key-pacs_list_sidebar .st-key-pacs_list_menu_review button:hover { background-color:#242424 !important; }

/* ── 상단 바 (64 높이) ── */
.st-key-pacs_list_header {
  left:calc(204 * var(--u)); right:0; top:0; height:calc(64 * var(--u));
  width:auto !important; background:#fff; border-bottom:1px solid #e5e5e5;
}
.st-key-pacs_list_crumb_home {
  left:calc(32 * var(--u)); top:calc(23 * var(--u)); font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#777;
}
.st-key-pacs_list_crumb_page {
  left:calc(143 * var(--u)); top:calc(23 * var(--u));
  font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:500; color:#222;
}
.st-key-pacs_list_crumb_page::before {
  content:''; position:absolute; left:calc(-27 * var(--u)); top:calc(1 * var(--u));
  width:calc(16 * var(--u)); height:calc(16 * var(--u)); background:@ICON_CHEVRON_RIGHT@ center / contain no-repeat;
}
.st-key-pacs_list_user { left:calc(100% - 242 * var(--u)); top:calc(15 * var(--u)); overflow:visible; }
.st-key-pacs_list_user::before {
  content:''; position:absolute; left:calc(-36 * var(--u)); top:calc(6 * var(--u));
  width:1px; height:calc(22 * var(--u)); background:#e5e5e5;
}
.st-key-pacs_list_user_role { font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#777; }
.st-key-pacs_list_user_name {
  margin-top:calc(-1 * var(--u)); font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:500; color:#222;
}
/* 이름이 길어도 로그아웃 버튼을 덮지 않도록 넘치는 부분은 …로 줄입니다. */
.st-key-pacs_list_user_role, .st-key-pacs_list_user_name { max-width:calc(160 * var(--u)); }
.st-key-pacs_list_user_role p, .st-key-pacs_list_user_name p { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.st-key-pacs_list_clock {
  right:calc(303 * var(--u)); top:calc(23 * var(--u)); width:auto !important; text-align:right; white-space:nowrap;
  font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#666; font-variant-numeric:tabular-nums;
}
.st-key-pacs_list_logout { right:calc(31 * var(--u)); top:calc(18 * var(--u)); width:calc(28 * var(--u)) !important; height:calc(28 * var(--u)); }
.st-key-pacs_list_logout button {
  width:calc(28 * var(--u)) !important; height:calc(28 * var(--u)) !important; min-height:0 !important; padding:0 !important;
  border:0 !important; border-radius:calc(4 * var(--u)) !important; box-shadow:none !important;
  background:transparent @ICON_LOGOUT@ center / calc(16 * var(--u)) no-repeat !important;
}
.st-key-pacs_list_logout button:hover { background-color:#f2f2f2 !important; }
.st-key-pacs_list_logout button p { font-size:0 !important; }

/* ── 본문 (상단 바 아래, 사이드바 오른쪽) ── */
.st-key-pacs_list_main {
  left:calc(204 * var(--u)); right:0; top:calc(64 * var(--u)); bottom:0; width:auto !important;
}
.st-key-pacs_list_title { left:calc(32 * var(--u)); top:calc(25 * var(--u)); width:auto !important; white-space:nowrap; }
.st-key-pacs_list_title_text { font-size:calc(26 * var(--u)); line-height:calc(39 * var(--u)); font-weight:700; color:#1d1d1d; }
.st-key-pacs_list_subtitle { margin:calc(5 * var(--u)) 0 0 1px; font-size:calc(13 * var(--u)); line-height:calc(20 * var(--u)); color:#727272; }

/* 통계 카드 5개(전체 + 상태 4개): 높이 88, 간격 17~18. 너비는 화면 폭을 5등분합니다. */
.st-key-pacs_list_stage .st-key-pacs_list_stats {
  left:calc(32 * var(--u)); right:calc(32 * var(--u)); top:calc(110 * var(--u)); width:auto !important;
  flex-wrap:nowrap !important; gap:calc(17.33 * var(--u)) !important;
}
.st-key-pacs_list_stats > div { flex:1 1 0 !important; width:auto !important; min-width:0 !important; }
[class*="st-key-pacs_list_stat_card_"] {
  position:relative; width:100% !important; height:calc(88 * var(--u)) !important; flex:0 0 auto !important;
  background:#fff; border:1px solid #e1e1e1;
  border-radius:calc(7 * var(--u)); overflow:hidden;
}
[class*="st-key-pacs_list_stat_card_accent_"]::before {
  content:''; position:absolute; left:-1px; top:calc(16 * var(--u)); width:3px; height:calc(54 * var(--u));
  border-radius:1px; background:#333;
}
.st-key-pacs_list_stat_card_accent_reviewing::before { background:#3266c3; }  /* 검토 중 카드는 파란 막대 */
[class*="st-key-pacs_list_stat_label_"] {
  left:calc(17 * var(--u)); top:calc(12 * var(--u)); font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:500; color:#626262;
}
[class*="st-key-pacs_list_stat_value_"] {
  left:calc(17 * var(--u)); top:calc(33 * var(--u)); font-size:calc(28 * var(--u)); line-height:calc(42 * var(--u)); font-weight:700; color:#222;
}
[class*="st-key-pacs_list_stat_note_"] {
  left:calc(107 * var(--u)); top:calc(53 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#777;
}

/* 검색 조건 줄: 라벨(11) + 입력칸(42), 칸 사이 16, 검색 버튼 앞 12 */
.st-key-pacs_list_stage .st-key-pacs_list_filters {
  left:calc(32 * var(--u)); right:calc(32 * var(--u)); top:calc(220 * var(--u)); width:auto !important;
  flex-wrap:nowrap !important; align-items:flex-end !important; gap:calc(16 * var(--u)) !important;
}
.st-key-pacs_list_filters > div { flex:0 0 auto !important; width:auto !important; min-width:0 !important; }
.st-key-pacs_list_filters > div:has(> .st-key-pacs_list_field_query) { flex:1 1 auto !important; }
[class*="st-key-pacs_list_field_"] { gap:0 !important; }
.st-key-pacs_list_field_query { width:100% !important; min-width:calc(200 * var(--u)) !important; }
.st-key-pacs_list_field_dates { width:calc(280 * var(--u)) !important; }
.st-key-pacs_list_field_modality { width:calc(202 * var(--u)) !important; }
.st-key-pacs_list_field_status { width:calc(182 * var(--u)) !important; }
.st-key-pacs_list_field_search { width:calc(68 * var(--u)) !important; }
.st-key-pacs_list_filters > div:has(> .st-key-pacs_list_field_search) { margin-left:calc(-4 * var(--u)) !important; }
[class*="st-key-pacs_list_label_"] {
  margin-bottom:calc(5 * var(--u)); white-space:nowrap;
  font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); font-weight:500; color:#666;
}
[class*="st-key-pacs_list_field_"] [data-testid="stTextInput"],
[class*="st-key-pacs_list_field_"] [data-testid="stDateInput"],
[class*="st-key-pacs_list_field_"] [data-testid="stSelectbox"] { width:100% !important; }
[class*="st-key-pacs_list_field_"] [data-testid="stTextInputRootElement"],
[class*="st-key-pacs_list_field_"] [data-testid="stDateInputField"],
[class*="st-key-pacs_list_field_"] [data-testid="stSelectbox"] > div > div {
  height:calc(42 * var(--u)) !important; min-height:0 !important;
  background-color:#fff !important; border:1px solid #d7d7d7 !important; border-radius:calc(6 * var(--u)) !important;
  box-shadow:none !important;
}
[class*="st-key-pacs_list_field_"] [data-testid="stTextInputRootElement"]:focus-within,
[class*="st-key-pacs_list_field_"] [data-testid="stDateInputField"]:focus-within,
[class*="st-key-pacs_list_field_"] [data-testid="stSelectbox"] > div > div:focus-within { border-color:#555 !important; }
[class*="st-key-pacs_list_field_"] [data-testid="stTextInputRootElement"] > div,
[class*="st-key-pacs_list_field_"] [data-baseweb="base-input"] { background:transparent !important; }
[class*="st-key-pacs_list_field_"] input {
  height:100% !important; background:transparent !important; color:#444 !important; -webkit-text-fill-color:#444;
  caret-color:#222; font-family:inherit !important; font-size:calc(12 * var(--u)) !important;
}
[class*="st-key-pacs_list_field_"] input::placeholder { color:#858585 !important; -webkit-text-fill-color:#858585; opacity:1; }
[class*="st-key-pacs_list_field_"] [data-testid="InputInstructions"] { display:none !important; }
/* 환자 검색 칸: 왼쪽 아이콘 (12, 11) 18×18, 글자는 x 40부터 */
.st-key-pacs_list_field_query [data-testid="stTextInputRootElement"] {
  background:#fff @ICON_SEARCH_FIELD@ calc(12 * var(--u)) center / calc(18 * var(--u)) no-repeat !important;
}
.st-key-pacs_list_field_query input { padding:0 calc(12 * var(--u)) 0 calc(39 * var(--u)) !important; }
/* 등록일 칸: 버튼을 입력칸 모양으로(왼쪽 달력 아이콘 (12, 12) 18×18, 글자는 x 39부터). 누르면 아래 달력이 열림 */
.st-key-pacs_list_field_dates { position:relative !important; overflow:visible !important; }
.st-key-pacs_list_dates_button button {
  width:100% !important; height:calc(42 * var(--u)) !important; min-height:0 !important;
  padding:0 calc(12 * var(--u)) 0 calc(39 * var(--u)) !important; justify-content:flex-start !important;
  background:#fff @ICON_CALENDAR@ calc(12 * var(--u)) center / calc(18 * var(--u)) no-repeat !important;
  border:1px solid #d7d7d7 !important; border-radius:calc(6 * var(--u)) !important; box-shadow:none !important;
  color:#444 !important; font-size:calc(12 * var(--u)) !important; font-weight:400 !important; cursor:pointer;
}
.st-key-pacs_list_dates_button button > div { justify-content:flex-start !important; width:100%; }
.st-key-pacs_list_dates_button button p { font-size:calc(12 * var(--u)) !important; white-space:pre; }
.st-key-pacs_list_dates_button button:hover,
.st-key-pacs_list_dates_box_open .st-key-pacs_list_dates_button button { border-color:#555 !important; }

/* ── 등록일 기간 선택 달력: 등록일 칸과 같은 폭, 안쪽 여백 20, 줄 간격 16 (Figma "검사일 · 기간 선택 달력") ── */
.st-key-pacs_list_cal_backdrop {
  position:fixed !important; inset:0; z-index:30; width:100vw !important; height:100vh !important; opacity:0;
}
.st-key-pacs_list_cal_backdrop * { width:100% !important; height:100% !important; cursor:default !important; }
.st-key-pacs_list_stage .st-key-pacs_list_cal {
  position:absolute !important; z-index:31; left:0; top:calc(100% + 6 * var(--u));
  width:100% !important; min-width:0 !important; max-width:none !important;  /* 등록일 칸(280)과 같은 폭 */
  padding:calc(20 * var(--u)) !important; gap:calc(16 * var(--u)) !important;
  background:#fff; border:1px solid #dedede; border-radius:calc(8 * var(--u));
  box-shadow:0 calc(8 * var(--u)) calc(24 * var(--u)) rgba(0, 0, 0, 0.12);
  color:#252525; line-height:1.5;
}
.st-key-pacs_list_cal > div { width:100% !important; max-width:none !important; }
/* 달력 안 버튼: Streamlit 기본 모양을 없애고 칸을 꽉 채웁니다(:where로 우선순위를 낮춰 아래 개별 크기가 이기게 함). */
.st-key-pacs_list_cal :where([data-testid="stElementContainer"], [data-testid="stButton"]) { width:100% !important; height:100% !important; }
/* 버튼을 줄 기준선이 아닌 칸 위쪽에 맞춥니다(아이콘만 있는 버튼이 아래로 밀리지 않게). */
.st-key-pacs_list_cal [data-testid="stButton"] { display:flex !important; line-height:0; }
.st-key-pacs_list_cal button {
  width:100% !important; height:100% !important; min-height:0 !important; padding:0 !important;
  border:0 !important; border-radius:calc(5 * var(--u)) !important; background:transparent !important; box-shadow:none !important;
  color:inherit !important; font-size:inherit !important; font-weight:inherit !important; line-height:1.5 !important;
}
.st-key-pacs_list_cal button p { font-size:inherit !important; font-weight:inherit !important; line-height:inherit !important; }
/* 제목 줄: 15 Bold + 닫기 20×20 */
.st-key-pacs_list_stage .st-key-pacs_list_cal_head { flex-wrap:nowrap !important; align-items:center !important; }
.st-key-pacs_list_cal_head > div:first-child { flex:1 1 auto !important; min-width:0; }
.st-key-pacs_list_cal_title { font-size:calc(15 * var(--u)); font-weight:700; color:#252525; }
.st-key-pacs_list_cal_head > div:last-child, .st-key-pacs_list_cal_close {
  flex:0 0 auto !important; width:calc(20 * var(--u)) !important; height:calc(20 * var(--u)) !important;
}
.st-key-pacs_list_cal_close button, .st-key-pacs_list_cal_prev button, .st-key-pacs_list_cal_next button {
  font-size:0 !important; background-position:center !important; background-size:calc(20 * var(--u)) !important;
  background-repeat:no-repeat !important; cursor:pointer;
}
.st-key-pacs_list_cal_close button { background-image:@ICON_CLOSE@ !important; }
.st-key-pacs_list_cal_close button p, .st-key-pacs_list_cal_prev button p, .st-key-pacs_list_cal_next button p { font-size:0 !important; }
/* 시작일·종료일 칸: 143×59, 배경 #f5f5f5, 사이 8 */
.st-key-pacs_list_stage .st-key-pacs_list_cal_range { flex-wrap:nowrap !important; gap:calc(8 * var(--u)) !important; }
.st-key-pacs_list_cal_range > div { flex:1 1 0 !important; min-width:0; }
.st-key-pacs_list_stage [class*="st-key-pacs_list_cal_box_"] {
  gap:calc(4 * var(--u)) !important; padding:calc(8 * var(--u)) calc(12 * var(--u)) !important;
  background:#f5f5f5; border:1px solid #dedede; border-radius:calc(5 * var(--u));
}
[class*="st-key-pacs_list_cal_boxlabel_"] { font-size:calc(11 * var(--u)); color:#626262; }
[class*="st-key-pacs_list_cal_boxvalue_"] { font-size:calc(13 * var(--u)); font-weight:500; color:#252525; }
/* 빠른 기간 버튼: 높이 32, 사이 6, 고른 버튼은 검정 */
.st-key-pacs_list_stage .st-key-pacs_list_cal_presets { flex-wrap:nowrap !important; gap:calc(6 * var(--u)) !important; }
.st-key-pacs_list_cal_presets > div { flex:0 0 auto !important; width:auto !important; }
[class*="st-key-pacs_list_cal_preset_"] { width:auto !important; }
[class*="st-key-pacs_list_cal_preset_"] :is([data-testid="stElementContainer"], [data-testid="stButton"]) { width:auto !important; }
.st-key-pacs_list_cal [class*="st-key-pacs_list_cal_preset_"] button {
  width:auto !important; height:calc(32 * var(--u)) !important; padding:0 calc(10 * var(--u)) !important;
  border:1px solid #dedede !important; background:#fff !important; color:#252525 !important;
  font-size:calc(13 * var(--u)) !important; font-weight:500 !important; cursor:pointer; white-space:nowrap;
}
.st-key-pacs_list_cal [class*="st-key-pacs_list_cal_preset_"] button:hover { background:#f5f5f5 !important; }
.st-key-pacs_list_cal [class*="st-key-pacs_list_cal_preset_on_"] button {
  background:#252525 !important; border-color:#252525 !important; color:#fff !important;
}
/* 구분선 */
.st-key-pacs_list_cal_rule_top, .st-key-pacs_list_cal_rule_bottom {
  height:1px !important; min-height:1px; flex:0 0 auto !important; overflow:hidden;
  background:#dedede; font-size:0 !important; line-height:0 !important;
}
/* 월 이동: 양 끝 20×20 화살표, 가운데 14 Bold */
.st-key-pacs_list_stage .st-key-pacs_list_cal_nav { flex-wrap:nowrap !important; align-items:center !important; justify-content:space-between !important; }
.st-key-pacs_list_cal_nav > div { flex:0 0 auto !important; width:auto !important; }
.st-key-pacs_list_cal_prev, .st-key-pacs_list_cal_next,
:is(.st-key-pacs_list_cal_prev, .st-key-pacs_list_cal_next, .st-key-pacs_list_cal_close) :is([data-testid="stButton"], button) {
  width:calc(20 * var(--u)) !important; height:calc(20 * var(--u)) !important;
}
.st-key-pacs_list_cal_prev button { background-image:@ICON_MONTH_PREV@ !important; }
.st-key-pacs_list_cal_next button { background-image:@ICON_MONTH_NEXT@ !important; }
.st-key-pacs_list_cal_month { font-size:calc(14 * var(--u)); font-weight:700; color:#252525; white-space:nowrap; }
/* 날짜 표: 요일 줄 높이 24, 날짜 칸은 폭을 7등분 × 높이 34, 줄 사이 4 */
.st-key-pacs_list_stage .st-key-pacs_list_cal_grid { gap:calc(4 * var(--u)) !important; }
.st-key-pacs_list_stage :is(.st-key-pacs_list_cal_weekdays, [class*="st-key-pacs_list_cal_week_"]) { flex-wrap:nowrap !important; }
:is(.st-key-pacs_list_cal_weekdays, [class*="st-key-pacs_list_cal_week_"]) > div { flex:1 1 0 !important; width:auto !important; min-width:0 !important; }
[class*="st-key-pacs_list_cal_wd_"] {
  width:100% !important; height:calc(24 * var(--u)) !important; flex:0 0 auto !important; display:flex !important;
  flex-direction:column; justify-content:center; font-size:calc(11 * var(--u)); color:#626262;
}
[class*="st-key-pacs_list_cal_d_"] {
  width:100% !important; height:calc(34 * var(--u)) !important; flex:0 0 auto !important;
  border-radius:calc(5 * var(--u)); font-size:calc(12 * var(--u)); color:#626262;
}
[class*="st-key-pacs_list_cal_d_"] button { cursor:pointer; }
[class*="st-key-pacs_list_cal_d_in_"]:hover, [class*="st-key-pacs_list_cal_d_out_"]:hover { background:#f5f5f5; }
[class*="st-key-pacs_list_cal_d_out_"] { color:#a1a1a1; }
[class*="st-key-pacs_list_cal_d_mid_"] { background:#f5f5f5; color:#252525; }
[class*="st-key-pacs_list_cal_d_sel_"] { background:#252525; color:#fff; font-weight:700; }
/* 아래 줄: 고른 기간(11) + 적용 버튼(검정, 높이 42) */
.st-key-pacs_list_stage .st-key-pacs_list_cal_foot {
  flex-wrap:nowrap !important; align-items:flex-start !important; justify-content:space-between !important; gap:calc(8 * var(--u)) !important;
}
.st-key-pacs_list_cal_foot > div { flex:0 1 auto !important; width:auto !important; min-width:0; }
.st-key-pacs_list_cal_summary { font-size:calc(11 * var(--u)); color:#626262; }
.st-key-pacs_list_cal_apply { width:auto !important; flex:0 0 auto !important; }
.st-key-pacs_list_cal_apply :is([data-testid="stElementContainer"], [data-testid="stButton"]) { width:auto !important; }
.st-key-pacs_list_cal .st-key-pacs_list_cal_apply button {
  width:auto !important; height:calc(42 * var(--u)) !important; padding:0 calc(16 * var(--u)) !important;
  background:#252525 !important; color:#fff !important; font-size:calc(13 * var(--u)) !important; font-weight:500 !important; cursor:pointer;
}
.st-key-pacs_list_cal .st-key-pacs_list_cal_apply button:hover { background:#3a3a3a !important; }

/* ── 검사 장비·검토 상태 선택 메뉴 (Figma "선택 메뉴"): 화면 높이 기준 크기(메뉴는 화면 밖 층에 그려져 --u를 못 씀) ── */
[data-testid="stSelectboxVirtualDropdown"] {
  height:auto !important; max-height:none !important; padding:calc(6 * 100vh / 960) !important;
  background:#fff !important; border:1px solid #dedede !important; border-radius:calc(8 * 100vh / 960) !important;
  box-shadow:0 calc(8 * 100vh / 960) calc(24 * 100vh / 960) rgba(0, 0, 0, 0.12) !important;
  font-family:'Noto Sans KR', sans-serif !important;
}
[data-testid="stSelectboxVirtualDropdown"] [role="listbox"] {
  height:auto !important; max-height:none !important; display:flex !important; flex-direction:column; gap:calc(2 * 100vh / 960);
}
[data-testid="stSelectboxVirtualDropdown"] [role="option"] { height:calc(36 * 100vh / 960) !important; padding:0 !important; }
[data-testid="stSelectboxVirtualDropdown"] [role="option"] > div {
  display:flex !important; align-items:center; width:100%; height:100% !important;
  padding:0 calc(10 * 100vh / 960) !important; border-radius:calc(4 * 100vh / 960) !important;
  background:transparent !important; color:#252525 !important;
  font-size:calc(12 * 100vh / 960) !important; font-weight:400; line-height:1.5;
}
[data-testid="stSelectboxVirtualDropdown"] [role="option"]:hover > div { background:#fafafa !important; }
[data-testid="stSelectboxVirtualDropdown"] [role="option"][aria-selected="true"] > div {
  background:#f5f5f5 @ICON_CHECK@ right calc(10 * 100vh / 960) center / calc(20 * 100vh / 960) no-repeat !important;
  font-weight:700;
}

/* ── 확인창(로그아웃·수정): 화면 가운데 440 폭, 안쪽 여백 28, 줄 간격 20. 뒤는 뿌옇게 덮고 누르면 닫힘 ── */
.st-key-pacs_list_modal_backdrop {
  position:fixed !important; inset:0; z-index:40; width:100vw !important; height:100vh !important;
  background:rgba(37, 37, 37, 0.35); backdrop-filter:blur(2px); -webkit-backdrop-filter:blur(2px);
}
.st-key-pacs_list_modal_backdrop * { width:100% !important; height:100% !important; }
.st-key-pacs_list_modal_backdrop button { opacity:0; cursor:default !important; }
.st-key-pacs_list_stage [class*="st-key-pacs_list_modal_"]:is(.st-key-pacs_list_modal_logout, .st-key-pacs_list_modal_edit) {
  position:fixed !important; z-index:41; left:50%; top:50%; transform:translate(-50%, -50%);
  width:calc(440 * var(--u)) !important; max-width:calc(100vw - 32px) !important;
  padding:calc(28 * var(--u)) !important; gap:calc(20 * var(--u)) !important;
  background:#fff; border:1px solid #dedede; border-radius:calc(8 * var(--u));
  box-shadow:0 calc(8 * var(--u)) calc(24 * var(--u)) rgba(0, 0, 0, 0.12); line-height:1.5;
}
:is(.st-key-pacs_list_modal_logout, .st-key-pacs_list_modal_edit) > div { width:100% !important; }
.st-key-pacs_list_stage .st-key-pacs_list_modal_head { flex-wrap:nowrap !important; align-items:center !important; }
.st-key-pacs_list_modal_head > div:first-child { flex:1 1 auto !important; min-width:0; }
.st-key-pacs_list_modal_title { font-size:calc(20 * var(--u)); font-weight:700; color:#252525; }
.st-key-pacs_list_modal_head > div:last-child { flex:0 0 auto !important; }
.st-key-pacs_list_modal_close, .st-key-pacs_list_modal_close :is([data-testid="stButton"], button) {
  width:calc(20 * var(--u)) !important; height:calc(20 * var(--u)) !important;
}
.st-key-pacs_list_modal_close [data-testid="stButton"] { display:flex !important; line-height:0; }
.st-key-pacs_list_modal_close button {
  min-height:0 !important; padding:0 !important; border:0 !important; box-shadow:none !important; cursor:pointer;
  background:transparent @ICON_CLOSE@ center / calc(20 * var(--u)) no-repeat !important;
}
.st-key-pacs_list_modal_close button p { font-size:0 !important; }
.st-key-pacs_list_modal_body { font-size:calc(13 * var(--u)); color:#626262; }
.st-key-pacs_list_stage .st-key-pacs_list_modal_actions {
  flex-wrap:nowrap !important; justify-content:flex-end !important; gap:calc(8 * var(--u)) !important;
}
.st-key-pacs_list_modal_actions > div { flex:0 0 auto !important; width:auto !important; }
:is(.st-key-pacs_list_modal_cancel, .st-key-pacs_list_modal_confirm) [data-testid="stButton"] { display:flex !important; }
:is(.st-key-pacs_list_modal_cancel, .st-key-pacs_list_modal_confirm) button {
  width:auto !important; height:calc(42 * var(--u)) !important; min-height:0 !important; padding:0 calc(16 * var(--u)) !important;
  border-radius:calc(5 * var(--u)) !important; box-shadow:none !important; cursor:pointer;
  font-size:calc(13 * var(--u)) !important; font-weight:500 !important; white-space:nowrap;
}
:is(.st-key-pacs_list_modal_cancel, .st-key-pacs_list_modal_confirm) button p { font-size:inherit !important; font-weight:inherit !important; }
.st-key-pacs_list_modal_cancel button { background:#fff !important; border:1px solid #dedede !important; color:#252525 !important; }
.st-key-pacs_list_modal_cancel button:hover { background:#f5f5f5 !important; }
.st-key-pacs_list_modal_confirm button { background:#252525 !important; border:1px solid #252525 !important; color:#fff !important; }
.st-key-pacs_list_modal_confirm button:hover { background:#3a3a3a !important; }
/* 선택 상자: 글자 x 13, 오른쪽 ▾ 아이콘 (13, 11) */
[class*="st-key-pacs_list_field_"] [data-testid="stSelectbox"] > div > div { display:flex; align-items:center; overflow:hidden; }
[class*="st-key-pacs_list_field_"] [data-testid="stSelectbox"] input {
  flex:1 1 auto; min-width:0; padding:0 0 0 calc(13 * var(--u)) !important; cursor:pointer;
}
[class*="st-key-pacs_list_field_"] [data-testid="stSelectbox"] button {
  flex:0 0 auto; width:calc(44 * var(--u)) !important; height:100% !important; padding:0 !important; border:0 !important;
  background:transparent @ICON_CHEVRON_DOWN@ right calc(13 * var(--u)) center / calc(18 * var(--u)) no-repeat !important;
}
[class*="st-key-pacs_list_field_"] [data-testid="stSelectbox"] button svg { display:none !important; }
/* 검색 버튼: 68×42, 배경 #f7f7f7 */
.st-key-pacs_list_field_search button {
  width:100% !important; height:calc(42 * var(--u)) !important; min-height:0 !important; padding:0 !important;
  border:1px solid #d7d7d7 !important; border-radius:calc(6 * var(--u)) !important; box-shadow:none !important;
  background:#f7f7f7 @ICON_SEARCH_BUTTON@ center / calc(18 * var(--u)) no-repeat !important;
}
.st-key-pacs_list_field_search button:hover { background-color:#eee !important; }
.st-key-pacs_list_field_search button p { font-size:0 !important; }

/* 총 N건 · 적용된 검색 조건 */
.st-key-pacs_list_stage .st-key-pacs_list_count {
  left:calc(32 * var(--u)); top:calc(301 * var(--u)); width:auto !important;
  flex-wrap:nowrap !important; align-items:baseline !important; white-space:nowrap;
}
.st-key-pacs_list_count > div { flex:0 0 auto !important; width:auto !important; }
.st-key-pacs_list_count_total { font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:700; color:#222; }
.st-key-pacs_list_count_cond { margin-left:calc(8 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(18 * var(--u)); color:#858585; }

/* ── 검사 표: 왼쪽 32, 오른쪽은 상세 패널(324) 앞 16 ── */
.st-key-pacs_list_table {
  left:calc(32 * var(--u)); right:calc(372 * var(--u)); top:calc(328 * var(--u)); height:calc(520 * var(--u));
  width:auto !important; background:#fff; border:1px solid #dedede; border-radius:calc(7 * var(--u)); overflow:hidden;
}
.st-key-pacs_list_thead { position:relative; flex:0 0 auto; height:calc(39 * var(--u)); background:#f8f8f8; }
[class*="st-key-pacs_list_th_"] {
  top:calc(11 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); font-weight:500; color:#6d6d6d;
}
.st-key-pacs_list_th_name { left:calc(27 * var(--u)); }
.st-key-pacs_list_th_pid { left:calc(141 * var(--u)); }
.st-key-pacs_list_th_mod { left:calc(259 * var(--u)); }
.st-key-pacs_list_th_exam { left:calc(322 * var(--u)); }
.st-key-pacs_list_th_time { right:calc(212 * var(--u)); }
.st-key-pacs_list_th_status { right:calc(78 * var(--u)); }
.st-key-pacs_list_rows {
  flex:0 0 auto !important; min-height:0 !important; display:block !important;
  height:calc(479 * var(--u)) !important; max-height:none !important; overflow-y:auto !important; overflow-x:hidden;
  scrollbar-width:thin; scrollbar-color:#cfcfcf transparent;
}
/* 줄: 일반 줄(pacs_list_row_N)과 선택된 줄(pacs_list_selrow_N) */
:is([class*="st-key-pacs_list_row_"], [class*="st-key-pacs_list_selrow_"]) {
  position:relative; flex:0 0 auto; height:calc(48 * var(--u)); background:#fff; border-bottom:1px solid #ececec;
}
[class*="st-key-pacs_list_row_"]:hover { background:#fafafa; }
[class*="st-key-pacs_list_selrow_"] { background:#eee; }
[class*="st-key-pacs_list_selrow_"]::before {
  content:''; position:absolute; left:0; top:0; width:3px; height:100%; background:#292929; z-index:1;
}
[class*="st-key-pacs_list_cname_"] {
  left:calc(28 * var(--u)); top:calc(6 * var(--u)); max-width:calc(108 * var(--u));
  font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:500; color:#292929;
}
[class*="st-key-pacs_list_cmeta_"] {
  left:calc(28 * var(--u)); top:calc(25 * var(--u)); max-width:calc(108 * var(--u));
  font-size:calc(9 * var(--u)); line-height:calc(14 * var(--u)); color:#7a7a7a;
}
[class*="st-key-pacs_list_cpid_"] {
  left:calc(143 * var(--u)); top:calc(15 * var(--u)); font-size:calc(10 * var(--u)); line-height:calc(15 * var(--u)); color:#656565;
}
[class*="st-key-pacs_list_cmod_"] {
  left:calc(258 * var(--u)); top:calc(14 * var(--u)); width:calc(32 * var(--u)) !important; height:calc(21 * var(--u));
  display:flex !important; align-items:center; justify-content:center;
  border:1px solid #d9d9d9; border-radius:calc(3 * var(--u)); background:#f4f4f4; text-align:center;
  font-size:calc(10 * var(--u)); line-height:calc(15 * var(--u)); font-weight:500; color:#555;
}
[class*="st-key-pacs_list_cexam_"] {
  /* 오른쪽 끝은 등록 일시(예: 11.22 10:38)와 겹치지 않는 위치입니다. */
  left:calc(324 * var(--u)); right:calc(306 * var(--u)); top:calc(15 * var(--u));
  font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#333;
}
[class*="st-key-pacs_list_ctime_"] {
  right:calc(228 * var(--u)); top:calc(15 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u));
  color:#666; font-variant-numeric:tabular-nums; text-align:right;
}
[class*="st-key-pacs_list_cbadge_"] { right:calc(47 * var(--u)); top:calc(12 * var(--u)); }
/* 줄 전체를 덮는 투명 선택 버튼 */
:is([class*="st-key-pacs_list_row_"], [class*="st-key-pacs_list_selrow_"]) [data-testid="stElementContainer"]:has([data-testid="stButton"]) {
  position:absolute !important; inset:0; z-index:2; width:100% !important; height:100% !important;
}
:is([class*="st-key-pacs_list_row_"], [class*="st-key-pacs_list_selrow_"]) [data-testid="stButton"],
:is([class*="st-key-pacs_list_row_"], [class*="st-key-pacs_list_selrow_"]) [data-testid="stButton"] button {
  width:100% !important; height:100% !important; min-height:0 !important;
}
:is([class*="st-key-pacs_list_row_"], [class*="st-key-pacs_list_selrow_"]) [data-testid="stButton"] button {
  padding:0 !important; border:0 !important; border-radius:0 !important; box-shadow:none !important;
  background:transparent !important; cursor:pointer;
}
:is([class*="st-key-pacs_list_row_"], [class*="st-key-pacs_list_selrow_"]) [data-testid="stButton"] button p { font-size:0 !important; }
:is([class*="st-key-pacs_list_row_"], [class*="st-key-pacs_list_selrow_"]) [data-testid="stButton"] button:focus-visible {
  outline:2px solid #555 !important; outline-offset:-2px;
}
.st-key-pacs_list_empty {
  display:flex !important; flex:0 0 auto !important; align-items:center; justify-content:center;
  height:calc(479 * var(--u)) !important;
  padding:0 calc(24 * var(--u)); text-align:center; white-space:normal;
  font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#858585;
}

/* 상태 배지: 80×26. key에 상태 종류(done / reviewing / assigned / unassigned)가 들어갑니다. */
[class*="st-key-pacs_list_cbadge_"], [class*="st-key-pacs_list_dbadge_"] {
  width:calc(80 * var(--u)) !important; height:calc(26 * var(--u)); display:flex !important;
  align-items:center; justify-content:center; border-radius:calc(6 * var(--u)); text-align:center;
  font-size:calc(11 * var(--u)); line-height:calc(15 * var(--u));
}
[class*="st-key-pacs_list_cbadge_done_"], .st-key-pacs_list_dbadge_done { background:#252525; color:#fff; }
/* 검토 중: 진행 중인 검사가 눈에 띄도록 파란 박스(검사 등록 화면의 파란색과 같은 색) */
[class*="st-key-pacs_list_cbadge_reviewing_"], .st-key-pacs_list_dbadge_reviewing {
  background:#edf3fc; color:#3266c3; border:1px solid #c5d6f2; font-weight:700;
}
[class*="st-key-pacs_list_cbadge_assigned_"], .st-key-pacs_list_dbadge_assigned { background:#f4f4f4; color:#222; font-weight:700; }
[class*="st-key-pacs_list_cbadge_unassigned_"], .st-key-pacs_list_dbadge_unassigned { background:#fff; color:#666; border:1px solid #d8d8d8; }

/* ── 검사 상세 패널: 324×520 ── */
.st-key-pacs_list_detail {
  right:calc(32 * var(--u)); top:calc(328 * var(--u)); width:calc(324 * var(--u)) !important; height:calc(520 * var(--u));
  background:#fff; border:1px solid #dedede; border-radius:calc(7 * var(--u)); overflow:hidden;
}
.st-key-pacs_list_detail::before {
  content:''; position:absolute; left:0; right:0; top:calc(46 * var(--u)); height:1px; background:#e5e5e5;
}
.st-key-pacs_list_dtitle {
  left:calc(19 * var(--u)); top:calc(14 * var(--u)); font-size:calc(13 * var(--u)); line-height:calc(20 * var(--u)); font-weight:700; color:#333;
}
.st-key-pacs_list_dname {
  left:calc(19 * var(--u)); top:calc(64 * var(--u)); max-width:calc(196 * var(--u));
  font-size:calc(21 * var(--u)); line-height:calc(32 * var(--u)); font-weight:700; color:#222;
}
[class*="st-key-pacs_list_dbadge_"] { left:calc(223 * var(--u)); top:calc(71 * var(--u)); }
.st-key-pacs_list_dmeta {
  left:calc(19 * var(--u)); top:calc(99 * var(--u)); white-space:pre; overflow:visible;
  font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#777;
}
.st-key-pacs_list_dmeta::after {
  content:''; position:absolute; left:0; top:calc(33 * var(--u)); width:calc(284 * var(--u)); height:1px; background:#e8e8e8;
}
[class*="st-key-pacs_list_dlabel_"] { left:calc(19 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#7a7a7a; }
[class*="st-key-pacs_list_dvalue_"] {
  left:calc(98 * var(--u)); max-width:calc(205 * var(--u)); white-space:pre;
  font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#333;
}
.st-key-pacs_list_dvalue_0 { font-weight:500; }
.st-key-pacs_list_dlabel_0, .st-key-pacs_list_dvalue_0 { top:calc(150 * var(--u)); }
.st-key-pacs_list_dlabel_1, .st-key-pacs_list_dvalue_1 { top:calc(178 * var(--u)); }
.st-key-pacs_list_dlabel_2, .st-key-pacs_list_dvalue_2 { top:calc(206 * var(--u)); }
.st-key-pacs_list_dlabel_3, .st-key-pacs_list_dvalue_3 { top:calc(234 * var(--u)); }
.st-key-pacs_list_dempty {
  left:calc(19 * var(--u)); right:calc(19 * var(--u)); top:calc(47 * var(--u)); bottom:0;
  display:flex !important; align-items:center; justify-content:center; text-align:center; white-space:normal;
  font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#858585;
}
.st-key-pacs_list_edit { left:calc(242 * var(--u)); top:calc(11 * var(--u)); width:calc(67 * var(--u)) !important; height:calc(23 * var(--u)); }
.st-key-pacs_list_viewer { left:calc(19 * var(--u)); top:calc(458 * var(--u)); width:calc(284 * var(--u)) !important; height:calc(41 * var(--u)); }
/* 영상 뷰어 버튼 위 구분선(패널 기준 y 440) */
.st-key-pacs_list_viewer::before {
  content:''; position:absolute; left:0; top:calc(-18 * var(--u)); width:100%; height:1px; background:#e8e8e8;
}
.st-key-pacs_list_edit [data-testid="stElementContainer"], .st-key-pacs_list_viewer [data-testid="stElementContainer"],
.st-key-pacs_list_edit [data-testid="stButton"], .st-key-pacs_list_viewer [data-testid="stButton"],
.st-key-pacs_list_edit [data-testid="stButton"] > div, .st-key-pacs_list_edit [data-testid="stTooltipIcon"],
.st-key-pacs_list_edit [data-testid="stTooltipHoverTarget"],
.st-key-pacs_list_edit button, .st-key-pacs_list_viewer button { width:100% !important; height:100% !important; min-height:0 !important; }
.st-key-pacs_list_edit button, .st-key-pacs_list_viewer button {
  padding:0 !important; border:0 !important; border-radius:calc(5 * var(--u)) !important; box-shadow:none !important;
  background:#252525 !important; color:#fff !important;
}
.st-key-pacs_list_edit button:hover:enabled, .st-key-pacs_list_viewer button:hover:enabled { background:#353535 !important; }
.st-key-pacs_list_edit button:disabled { background:#cfcfcf !important; cursor:not-allowed; }
.st-key-pacs_list_edit button p, .st-key-pacs_list_viewer button p {
  color:#fff !important; font-size:calc(12 * var(--u)) !important; line-height:calc(18 * var(--u)) !important; font-weight:500;
}
.st-key-pacs_list_stage button:focus-visible { outline:2px solid #555 !important; outline-offset:2px; }
</style>
"""

_STYLE = (
    _STYLE.replace("@ICON_LIST@", _css_icon(_ICON_LIST))
    .replace("@ICON_REGISTER@", _css_icon(_ICON_REGISTER))
    .replace("@ICON_REVIEW@", _css_icon(_ICON_REVIEW))
    .replace("@ICON_SHIELD@", _css_icon(_ICON_SHIELD))
    .replace("@ICON_CHEVRON_RIGHT@", _css_icon(_ICON_CHEVRON_RIGHT))
    .replace("@ICON_LOGOUT@", _css_icon(_ICON_LOGOUT))
    .replace("@ICON_SEARCH_FIELD@", _css_icon(_ICON_SEARCH_FIELD))
    .replace("@ICON_CALENDAR@", _css_icon(_ICON_CALENDAR))
    .replace("@ICON_CHEVRON_DOWN@", _css_icon(_ICON_CHEVRON_DOWN))
    .replace("@ICON_SEARCH_BUTTON@", _css_icon(_ICON_SEARCH_BUTTON))
    .replace("@ICON_CLOSE@", _css_icon(_ICON_CLOSE))
    .replace("@ICON_MONTH_PREV@", _css_icon(_ICON_MONTH_PREV))
    .replace("@ICON_MONTH_NEXT@", _css_icon(_ICON_MONTH_NEXT))
    .replace("@ICON_CHECK@", _css_icon(_ICON_CHECK))
)


# ── 이벤트 처리 (버튼 콜백) ──────────────────────────────────────────────────

def _emit(kind: str, case_id: str | None = None, target: str | None = None) -> None:
    st.session_state[_EVENT_KEY] = ExamListEvent(kind=kind, case_id=case_id, target=target)


def _select(case_id: str) -> None:
    st.session_state[_SELECTED_KEY] = case_id


def _filter_key(name: str) -> str:
    # 검색 후 필터 칸을 비우기 위해, 칸의 key에 버전 번호를 붙여 새 칸으로 다시 만듭니다.
    return f"_pacs_list_{name}_{st.session_state.get(_FILTER_VERSION_KEY, 0)}"


def _normalize_dates(value: object) -> tuple[date, date] | None:
    if isinstance(value, date):
        return value, value
    if isinstance(value, (tuple, list)) and value:
        start = value[0]
        end = value[1] if len(value) > 1 else value[0]
        if isinstance(start, date) and isinstance(end, date):
            return start, end
    return None


def _pending_dates() -> tuple[date, date]:
    """등록일 칸에 지금 들어 있는 기간(검색 버튼을 누르기 전). 고른 적이 없으면 기본 기간(최근 7일)입니다."""
    dates = _normalize_dates(st.session_state.get(_filter_key("dates")))
    if dates:
        return dates
    defaults = default_filter()
    return defaults.start, defaults.end


def _preset_range(days: int) -> tuple[date, date]:
    """오늘을 포함한 최근 days일 기간."""
    today = _today()
    return today - timedelta(days=days - 1), today


def _month_start(day: date) -> date:
    return day.replace(day=1)


def _shift_month(month: date, step: int) -> date:
    index = month.year * 12 + month.month - 1 + step
    return date(index // 12, index % 12 + 1, 1)


def _calendar_weeks(month: date) -> list[list[date]]:
    """month가 들어 있는 달력의 주(일요일 시작). 앞뒤 달의 날짜도 칸을 채우려고 함께 들어갑니다."""
    return calendar.Calendar(firstweekday=6).monthdatescalendar(month.year, month.month)


def _cal_toggle() -> None:
    """등록일 칸을 누르면 달력을 열고(지금 칸의 기간으로 시작), 열려 있으면 닫습니다."""
    state = st.session_state
    if state.get(_CAL_OPEN_KEY):
        _cal_close()
        return
    start, end = _pending_dates()
    state[_CAL_OPEN_KEY] = True
    state[_CAL_DRAFT_KEY] = (start, end)
    state[_CAL_MONTH_KEY] = _month_start(end)


def _cal_close() -> None:
    """달력을 닫습니다. 적용 버튼을 누르지 않았으면 고르던 기간은 버립니다."""
    for key in (_CAL_OPEN_KEY, _CAL_DRAFT_KEY, _CAL_MONTH_KEY):
        st.session_state.pop(key, None)


def _cal_pick(day: date) -> None:
    """날짜를 누르면 기간을 고릅니다: 첫 번째는 시작일, 두 번째는 종료일(앞 날짜를 누르면 둘을 바꿔 정렬)."""
    state = st.session_state
    start, end = state.get(_CAL_DRAFT_KEY) or (day, day)
    if end is not None:
        state[_CAL_DRAFT_KEY] = (day, None)  # 기간이 이미 정해져 있으면 새로 고르기 시작
    elif day < start:
        state[_CAL_DRAFT_KEY] = (day, start)
    else:
        state[_CAL_DRAFT_KEY] = (start, day)
    if _month_start(day) != state.get(_CAL_MONTH_KEY):
        state[_CAL_MONTH_KEY] = _month_start(day)  # 앞뒤 달의 날짜를 누르면 그 달로 넘어갑니다


def _cal_preset(days: int) -> None:
    st.session_state[_CAL_DRAFT_KEY] = _preset_range(days)
    st.session_state[_CAL_MONTH_KEY] = _month_start(_today())


def _cal_move(step: int) -> None:
    state = st.session_state
    state[_CAL_MONTH_KEY] = _shift_month(state.get(_CAL_MONTH_KEY) or _month_start(_today()), step)


def _cal_apply() -> None:
    """고른 기간을 등록일 칸에 넣고 달력을 닫습니다. 실제 목록 검색은 검색 버튼을 누를 때 적용됩니다.

    종료일을 고르지 않았으면 시작일 하루만 고른 것으로 봅니다.
    """
    state = st.session_state
    draft = state.get(_CAL_DRAFT_KEY)
    if draft:
        start, end = draft
        state[_filter_key("dates")] = (start, end or start)
    _cal_close()


def _ask_confirm(kind: str, case_id: str | None = None) -> None:
    """확인창을 띄웁니다(kind: "logout" | "edit"). 열려 있던 달력은 닫습니다."""
    _cal_close()
    st.session_state[_CONFIRM_KEY] = (kind, case_id)


def _close_confirm() -> None:
    st.session_state.pop(_CONFIRM_KEY, None)


def _on_confirm() -> None:
    """확인창의 확인 버튼: 로그아웃이면 EVENT_LOGOUT, 수정이면 EVENT_EDIT(case_id)을 app.py로 보냅니다."""
    kind, case_id = st.session_state.pop(_CONFIRM_KEY, (None, None))
    if kind == "logout":
        _emit(EVENT_LOGOUT)
    elif kind == "edit" and case_id:
        _emit(EVENT_EDIT, case_id)


def _on_search() -> None:
    """현재 입력한 조건을 적용하고, 필터 칸은 기본값으로 되돌립니다."""
    state = st.session_state
    _cal_close()
    start, end = _pending_dates()
    if start > end:
        state[_NOTICE_KEY] = MSG_INVALID_DATES
        return
    state[_APPLIED_FILTER_KEY] = SearchFilter(
        query=str(state.get(_filter_key("query")) or "").strip(),
        start=start,
        end=end,
        modality=str(state.get(_filter_key("modality")) or ALL),
        status=str(state.get(_filter_key("status")) or ALL),
    )
    state[_FILTER_VERSION_KEY] = state.get(_FILTER_VERSION_KEY, 0) + 1
    state.pop(_SELECTED_KEY, None)
    refresh_exams()  # 검색할 때는 최신 목록으로 다시 불러옵니다.


# ── 화면 그리기 ──────────────────────────────────────────────────────────────

# Markdown 기호를 글자 그대로 보이게 하기 위해 앞에 \를 붙일 문자들입니다.
# 예: "2026. 11. 19."이 번호 목록이 되거나, "L-spine"의 기호가 서식으로 바뀌는 것을 막습니다.
_MARKDOWN_SYMBOLS = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~:$=&^])")


def _md(text: object) -> str:
    return _MARKDOWN_SYMBOLS.sub(r"\\\1", str(text))


def _text(key: str, body: object, align: str = "left") -> None:
    """글자 한 덩어리를 표시합니다(로그인 화면과 같은 방식). 글꼴·색·위치는 key 이름으로 위의 CSS가 정합니다.

    화면 데이터(환자명, 검사명 등)를 그대로 넘겨도 Markdown 서식으로 바뀌지 않습니다. 줄바꿈(\\n)은 줄을 바꿉니다.
    """
    with st.container(key=key):
        st.markdown("  \n".join(_md(line) for line in str(body).split("\n")), text_alignment=align)


def _badge_kind(status: str) -> str:
    return {STATUS_DONE: "done", STATUS_REVIEWING: "reviewing", STATUS_ASSIGNED: "assigned"}.get(status, "unassigned")


def _patient_meta(exam: ExamRecord) -> str:
    age = f"{exam.patient_age}세" if exam.patient_age is not None else "-"
    return f"{exam.patient_sex or '-'} · {age}"


def _render_sidebar() -> None:
    with st.container(key="pacs_list_sidebar"):
        with st.container(key="pacs_list_brand"):
            _text("pacs_list_brand_name", "PACS")
            _text("pacs_list_brand_sub", "의료영상 관리 시스템")
        _text("pacs_list_workspace", "WORKSPACE")
        with st.container(key="pacs_list_menu_list"):
            st.button("검사 목록", key="pacs_list_nav_list", width="stretch")
        with st.container(key="pacs_list_menu_register"):
            st.button(
                "검사 등록", key="pacs_list_nav_register", width="stretch",
                on_click=_emit, args=(EVENT_NAVIGATE, None, TARGET_CASE_REGISTER),
            )
        with st.container(key="pacs_list_menu_review"):
            st.button(
                "검토 배정", key="pacs_list_nav_review", width="stretch",
                on_click=_emit, args=(EVENT_NAVIGATE, None, TARGET_REVIEW_ASSIGN),
            )
        with st.container(key="pacs_list_server"):
            _text("pacs_list_server_title", "서버 연결 정상")
            _text("pacs_list_server_sub", "DICOM 서비스 운영 중")
        _text("pacs_list_version", "PACS  /  v1.1")


@st.fragment(run_every=1)
def _render_clock() -> None:
    """상단 바의 실시간 시계. 1초마다 이 부분만 다시 그립니다."""
    _text("pacs_list_clock", f"{datetime.now(KST):%Y. %m. %d. %H:%M:%S}")


def _render_header(user: CurrentUser) -> None:
    with st.container(key="pacs_list_header"):
        _text("pacs_list_crumb_home", "워크스페이스")
        _text("pacs_list_crumb_page", "검사 목록")
        with st.container(key="pacs_list_user"):
            _text("pacs_list_user_role", role_label(user))
            _text("pacs_list_user_name", user.name or USER_NAME_FALLBACK)
        _render_clock()
        with st.container(key="pacs_list_logout"):
            # 바로 로그아웃하지 않고 확인창을 띄웁니다(확인창의 로그아웃 버튼이 EVENT_LOGOUT을 보냄).
            st.button("로그아웃", key="pacs_list_logout_button", on_click=_ask_confirm, args=("logout",))


def _render_title() -> None:
    with st.container(key="pacs_list_title"):
        with st.container(key="pacs_list_title_text"):
            st.title("검사 목록", anchor=False)
        _text("pacs_list_subtitle", "환자의 검사 영상을 확인하고 검토 업무를 시작하세요.")


def _render_stats(exams: list[ExamRecord]) -> None:
    counts = {status: sum(1 for e in exams if e.status == status) for status in STATUS_OPTIONS}
    # (key 이름, 라벨, 숫자, 보조 문구, 강조 막대 여부)
    cards = [
        ("total", "전체 검사", len(exams), "접수된 검사", False),
        ("unassigned", STATUS_UNASSIGNED, counts[STATUS_UNASSIGNED], "배정 대기 중", True),
        ("assigned", STATUS_ASSIGNED, counts[STATUS_ASSIGNED], "검토 대기 중", True),
        ("reviewing", STATUS_REVIEWING, counts[STATUS_REVIEWING], "진행 중", True),
        ("done", STATUS_DONE, counts[STATUS_DONE], "", False),
    ]
    with st.container(key="pacs_list_stats", horizontal=True):
        for name, label, value, note, accent in cards:
            with st.container(key=f"pacs_list_stat_card_{'accent_' if accent else ''}{name}"):
                _text(f"pacs_list_stat_label_{name}", label)
                _text(f"pacs_list_stat_value_{name}", value)
                if note:
                    _text(f"pacs_list_stat_note_{name}", note)


def _render_calendar() -> None:
    """등록일 기간 선택 달력(Figma "검사일 · 기간 선택 달력", 336 폭). 등록일 칸 바로 아래에 뜹니다."""
    state = st.session_state
    start, end = state.get(_CAL_DRAFT_KEY) or _pending_dates()
    month = state.get(_CAL_MONTH_KEY) or _month_start(end or start)
    last = end or start

    # 달력 바깥(화면 전체)을 덮는 투명 버튼: 바깥을 누르면 달력이 닫힙니다.
    st.button("달력 닫기", key="pacs_list_cal_backdrop", on_click=_cal_close)
    with st.container(key="pacs_list_cal"):
        with st.container(key="pacs_list_cal_head", horizontal=True):
            _text("pacs_list_cal_title", "등록일 선택")
            st.button("달력 닫기", key="pacs_list_cal_close", on_click=_cal_close)
        with st.container(key="pacs_list_cal_range", horizontal=True):
            for name, label, value in (("start", "시작일", start), ("end", "종료일", end)):
                with st.container(key=f"pacs_list_cal_box_{name}"):
                    _text(f"pacs_list_cal_boxlabel_{name}", label)
                    _text(f"pacs_list_cal_boxvalue_{name}", f"{value:%Y.%m.%d}" if value else "-")
        with st.container(key="pacs_list_cal_presets", horizontal=True):
            for name, label, days in CALENDAR_PRESETS:
                active = (start, end) == _preset_range(days)
                with st.container(key=f"pacs_list_cal_preset_{'on' if active else 'off'}_{name}"):
                    st.button(label, key=f"pacs_list_cal_presetbtn_{name}", on_click=_cal_preset, args=(days,))
        _text("pacs_list_cal_rule_top", " ")
        with st.container(key="pacs_list_cal_nav", horizontal=True):
            st.button("이전 달", key="pacs_list_cal_prev", on_click=_cal_move, args=(-1,))
            _text("pacs_list_cal_month", f"{month.year}년 {month.month}월", align="center")
            st.button("다음 달", key="pacs_list_cal_next", on_click=_cal_move, args=(1,))
        with st.container(key="pacs_list_cal_grid"):
            with st.container(key="pacs_list_cal_weekdays", horizontal=True):
                for index, label in enumerate(WEEKDAY_LABELS):
                    _text(f"pacs_list_cal_wd_{index}", label, align="center")
            for week_index, week in enumerate(_calendar_weeks(month)):
                with st.container(key=f"pacs_list_cal_week_{week_index}", horizontal=True):
                    for day in week:
                        if day in (start, end):
                            kind = "sel"  # 시작일·종료일
                        elif end is not None and start < day < end:
                            kind = "mid"  # 기간 안의 날짜
                        elif day.month != month.month:
                            kind = "out"  # 앞뒤 달의 날짜
                        else:
                            kind = "in"
                        with st.container(key=f"pacs_list_cal_d_{kind}_{day:%Y%m%d}"):
                            st.button(str(day.day), key=f"pacs_list_cal_pick_{day:%Y%m%d}", on_click=_cal_pick, args=(day,))
        _text("pacs_list_cal_rule_bottom", " ")
        with st.container(key="pacs_list_cal_foot", horizontal=True):
            if end is None or end == start:
                summary = f"{start:%Y.%m.%d} 선택"
            else:
                summary = f"{start:%Y.%m.%d} – {last:%Y.%m.%d} 선택 · {(last - start).days + 1}일"
            _text("pacs_list_cal_summary", summary)
            st.button("적용", key="pacs_list_cal_apply", on_click=_cal_apply)


def _render_confirm(kind: str) -> None:
    """화면 가운데 확인창(440 폭)과 뒤를 뿌옇게 덮는 배경. 배경·닫기·취소를 누르면 아무것도 하지 않고 닫힙니다."""
    title, lines, confirm_label = CONFIRM_TEXTS[kind]
    st.button("확인창 닫기", key="pacs_list_modal_backdrop", on_click=_close_confirm)
    with st.container(key=f"pacs_list_modal_{kind}"):
        with st.container(key="pacs_list_modal_head", horizontal=True):
            _text("pacs_list_modal_title", title)
            st.button("확인창 닫기", key="pacs_list_modal_close", on_click=_close_confirm)
        _text("pacs_list_modal_body", "\n".join(lines))
        with st.container(key="pacs_list_modal_actions", horizontal=True):
            st.button("취소", key="pacs_list_modal_cancel", on_click=_close_confirm)
            st.button(confirm_label, key="pacs_list_modal_confirm", on_click=_on_confirm)


def _render_filters() -> None:
    is_calendar_open = bool(st.session_state.get(_CAL_OPEN_KEY))
    start, end = _pending_dates()
    with st.container(key="pacs_list_filters", horizontal=True):
        with st.container(key="pacs_list_field_query"):
            _text("pacs_list_label_query", "환자 검색")
            st.text_input(
                "환자 검색",
                key=_filter_key("query"),
                placeholder="환자명, 환자 ID, 케이스 ID 검색",
                max_chars=64,
                label_visibility="collapsed",
            )
        with st.container(key="pacs_list_field_dates"):
            _text("pacs_list_label_dates", "등록일")
            # 등록일 칸: 누르면 아래에 기간 선택 달력이 열립니다(열려 있을 때 테두리를 진하게).
            with st.container(key=f"pacs_list_dates_box_{'open' if is_calendar_open else 'closed'}"):
                st.button(
                    _md(f"{start:%Y.%m.%d}  –  {end:%Y.%m.%d}"),
                    key="pacs_list_dates_button",
                    width="stretch",
                    on_click=_cal_toggle,
                )
            if is_calendar_open:
                _render_calendar()
        with st.container(key="pacs_list_field_modality"):
            _text("pacs_list_label_modality", "검사 장비")
            st.selectbox("검사 장비", (ALL, *MODALITY_OPTIONS), key=_filter_key("modality"), label_visibility="collapsed")
        with st.container(key="pacs_list_field_status"):
            _text("pacs_list_label_status", "검토 상태")
            st.selectbox("검토 상태", (ALL, *STATUS_OPTIONS), key=_filter_key("status"), label_visibility="collapsed")
        with st.container(key="pacs_list_field_search"):
            st.button("검색", key="pacs_list_search_button", width="stretch", on_click=_on_search)


def _render_count(total: int, condition: SearchFilter) -> None:
    parts = [f"{condition.start:%Y.%m.%d} – {condition.end:%Y.%m.%d}"]
    if condition.query:
        parts.append(f"‘{condition.query}’")
    if condition.modality != ALL:
        parts.append(condition.modality)
    if condition.status != ALL:
        parts.append(condition.status)
    with st.container(key="pacs_list_count", horizontal=True):
        _text("pacs_list_count_total", f"총 {total}건")
        _text("pacs_list_count_cond", f"검색 조건: {' · '.join(parts)}")


def _render_row(index: int, exam: ExamRecord, selected: bool) -> None:
    """검사 한 줄. 선택된 줄은 key가 pacs_list_selrow_N이 되어 배경과 왼쪽 막대가 바뀝니다."""
    time_text = exam.registered_at.astimezone(KST).strftime("%m.%d %H:%M")  # 등록 일시, 예: 11.22 10:38
    with st.container(key=f"pacs_list_{'selrow' if selected else 'row'}_{index}"):
        _text(f"pacs_list_cname_{index}", exam.patient_name)
        _text(f"pacs_list_cmeta_{index}", _patient_meta(exam))
        _text(f"pacs_list_cpid_{index}", exam.patient_id)
        _text(f"pacs_list_cmod_{index}", exam.modality, align="center")
        _text(f"pacs_list_cexam_{index}", exam.exam_name)
        _text(f"pacs_list_ctime_{index}", time_text, align="right")
        _text(f"pacs_list_cbadge_{_badge_kind(exam.status)}_{index}", exam.status, align="center")
        st.button(
            f"{exam.patient_name} 검사 선택",
            key=f"pacs_list_pick_{index}_{exam.case_id}",
            width="stretch",
            on_click=_select,
            args=(exam.case_id,),
        )


def _render_table(rows: list[ExamRecord], selected_id: str | None, load_failed: bool) -> None:
    with st.container(key="pacs_list_table"):
        with st.container(key="pacs_list_thead"):
            for name, label in (
                ("name", "환자명"), ("pid", "환자 ID"), ("mod", "장비"),
                ("exam", "검사명"), ("time", "등록 시간 ↓"), ("status", "상태"),
            ):
                _text(f"pacs_list_th_{name}", label)
        # 줄 영역 높이(479)와 스크롤은 CSS로 정합니다. 화면 크기에 맞춰 함께 늘어나게 하기 위해서입니다.
        with st.container(key="pacs_list_rows"):
            if load_failed or not rows:
                _text("pacs_list_empty", MSG_LOAD_FAILED if load_failed else MSG_NO_RESULT, align="center")
                return
            for index, exam in enumerate(rows):
                _render_row(index, exam, exam.case_id == selected_id)


def _render_detail(user: CurrentUser, exam: ExamRecord | None) -> None:
    with st.container(key="pacs_list_detail"):
        with st.container(key="pacs_list_dtitle"):
            st.subheader("검사 상세", anchor=False)
        if exam is None:
            _text("pacs_list_dempty", MSG_NO_SELECTION, align="center")
            return

        exam_time = exam.exam_at.astimezone(KST).strftime("%Y.%m.%d  %H:%M") if exam.exam_at else "-"
        fields = [
            ("검사명", exam.exam_name),
            ("검사일시", exam_time),
            ("케이스 ID", exam.case_id),
            ("진료과", exam.department),
        ]
        _text("pacs_list_dname", exam.patient_name)
        _text(f"pacs_list_dbadge_{_badge_kind(exam.status)}", exam.status, align="center")
        _text("pacs_list_dmeta", f"{_patient_meta(exam)}  /  {exam.patient_id}")
        for index, (label, value) in enumerate(fields):
            _text(f"pacs_list_dlabel_{index}", label)
            _text(f"pacs_list_dvalue_{index}", value or "-")

        # 수정 버튼은 등록 담당자에게만 보이고, 본인이 등록한 수정 가능 상태의 검사에서만 누를 수 있습니다.
        if is_registrar(user):
            allowed = can_edit(user, exam)
            with st.container(key="pacs_list_edit"):
                st.button(
                    "수정",
                    key="pacs_list_edit_button",
                    width="stretch",
                    disabled=not allowed,
                    help=None if allowed else MSG_EDIT_NOT_ALLOWED,
                    on_click=_ask_confirm,  # 확인창의 수정 버튼이 EVENT_EDIT(case_id)을 보냄
                    args=("edit", exam.case_id),
                )
        with st.container(key="pacs_list_viewer"):
            st.button(
                "영상 뷰어", key="pacs_list_viewer_button", width="stretch",
                on_click=_emit, args=(EVENT_OPEN_VIEWER, exam.case_id),
            )


def render(user: CurrentUser) -> ExamListEvent | None:
    """검사 목록 화면을 그립니다. 다른 화면으로 이동할 버튼이 눌리면 ExamListEvent를 돌려줍니다."""
    st.html(_STYLE)

    notice = st.session_state.pop(_NOTICE_KEY, None)
    if notice:
        st.toast(notice)

    exams, load_failed = _get_exams(user)

    condition = st.session_state.get(_APPLIED_FILTER_KEY) or default_filter()
    rows = filter_exams(exams, condition)

    # 선택한 검사가 결과에 없으면 첫 번째 검사를 선택합니다.
    selected_id = st.session_state.get(_SELECTED_KEY)
    if rows and selected_id not in {e.case_id for e in rows}:
        selected_id = rows[0].case_id
        st.session_state[_SELECTED_KEY] = selected_id
    selected = next((e for e in rows if e.case_id == selected_id), None)

    with st.container(key="pacs_list_stage"):
        _render_sidebar()
        _render_header(user)
        with st.container(key="pacs_list_main"):
            _render_title()
            _render_stats(exams)
            _render_filters()
            _render_count(len(rows), condition)
            _render_table(rows, selected_id, load_failed)
            _render_detail(user, selected)
        confirm = st.session_state.get(_CONFIRM_KEY)
        if confirm and confirm[0] in CONFIRM_TEXTS:
            _render_confirm(confirm[0])

    return st.session_state.pop(_EVENT_KEY, None)


def _preview_user() -> CurrentUser:
    """미리보기 사용자. 주소 끝에 ?role=REVIEWER처럼 붙이면 그 역할로 봅니다(없거나 잘못된 값이면 등록 담당자)."""
    role = _clean(st.query_params.get("role")).upper()
    if role not in ROLE_LABELS or role == ROLE_REGISTRAR:
        return _PREVIEW_USER
    return current_user_from_profile({"uid": f"preview-{role.lower()}", "role": role, "name": _PREVIEW_USER.name})


if __name__ == "__main__":
    # 단독 실행 시 샘플 사용자로 화면을 미리 봅니다. 화면 이동은 app.py에서 처리할 예정이라 안내만 띄웁니다.
    st.set_page_config(page_title="PACS 검사 목록", layout="wide")
    event = render(_preview_user())
    if event is not None and event.kind == EVENT_LOGOUT:
        # 로그아웃 동작 미리보기: 이 화면의 세션 값을 지우고 처음 상태로 다시 그립니다.
        reset_state()
        st.session_state[_NOTICE_KEY] = "검색 조건과 선택을 초기화했습니다. 로그인 화면 이동은 app.py 연결 후 동작합니다."
        st.rerun()
    elif event is not None:
        preview_messages = {
            EVENT_EDIT: "수정 화면은 준비 중입니다.",
            EVENT_OPEN_VIEWER: "영상 뷰어 화면은 준비 중입니다.",
        }
        targets = {TARGET_CASE_REGISTER: "검사 등록", TARGET_REVIEW_ASSIGN: "검토 배정"}
        message = preview_messages.get(event.kind) or f"{targets.get(event.target or '', '해당')} 화면은 준비 중입니다."
        st.toast(message)
