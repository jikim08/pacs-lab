"""PACS 검토 배정 화면.

화면 요소와 입력 처리는 Streamlit 위젯으로 구성하고, 모양은 Figma 디자인(1440×960 기준)을 CSS로 맞춥니다.
검사 목록 화면(case_list.py)과 같은 방식(key가 붙은 st.container + CSS 좌표)으로 만들었고,
검사 데이터·상태 값·아이콘은 case_list.py의 것을 그대로 씁니다(목록 캐시도 함께 씀).

화면 구성:
- 상태 카드 5개(전체 / 미배정 / 배정 완료 / 검토 중 / 검토 완료): 검사 목록 화면과 같은 기준입니다.
- 환자 검색 + 등록일 기간 달력 + 검색 버튼: 검사 목록 화면과 같은 동작입니다(검색 버튼을 눌러야 적용).
- 검사 표: "미배정" 탭과 "배정 완료" 탭. 줄을 누르면 왼쪽 체크박스가 켜집니다(한 번에 한 건만, 다른 줄을 누르면
  기존 체크는 풀림). "배정" 버튼을 누르면 화면 가운데 배정 창이 뜹니다(배정 완료 탭에서는 재배정 창).
- 배정 창: 선택한 검사, (재배정이면) 기존 검토자, 검토자 선택(현재 업무량 표시)을 보여주고
  "배정하기"를 누르면 저장합니다. 결과는 완료 안내 또는 실패 안내 창으로 보여줍니다.
  재배정 창의 검토자 보기에는 기존 검토자에 "현재 담당" 표시가 붙고, 그를 다시 고르면 저장하지 않고
  "재배정이 실패하였습니다" 안내를 띄웁니다.
- 검토자 업무 현황: 검토 담당자마다 "남은 검토(배정 완료 + 검토 중) / 지금까지 배정된 전체" 건수와,
  그 비율만큼 채운 검은 막대.
- 배정 이력(오른쪽 위 버튼): 화면 가운데 창에 케이스 배정·재배정 기록을 최신순으로 보여줍니다(스크롤).

권한: 화면, 검토자 업무 현황, 배정 이력은 모든 역할이 볼 수 있고,
체크박스와 배정 버튼은 업무 담당자(ADMIN)에게만 보입니다.

저장: ``save_assignment(user, request)``가 담당자 문서(case_assignees)를 바꾼 뒤 배정 이력(case_history)을 따로 남깁니다.
- 담당자 저장이 실패하면 아무것도 바뀌지 않았으므로 실패 안내를 띄웁니다.
- 담당자는 저장됐는데 이력 저장이 실패하면 같은 이력(같은 history_id)으로 몇 번 더 시도합니다. 그래도 안 되면
  배정은 완료로 안내하고, 그 이력을 세션에 남겨 두었다가 배정 이력 창을 열거나 다음 배정을 할 때 다시 저장합니다.
팀의 배정 서비스 함수(담당자·이력을 한 번에 저장)가 생기면 이 함수 안을 그 함수 호출로 바꾸면 됩니다.

app.py에서는 ``render(current_user)``를 호출하고, 반환된 ``ReviewAssignEvent``로 로그아웃을 처리합니다.
로그아웃은 화면 가운데 확인창을 거친 뒤에 EVENT_LOGOUT을 보냅니다.
왼쪽 사이드바(WORKSPACE 메뉴)는 이 화면이 그리지 않고, app.py가 공통 부품 ``components.sidebar.render``로 그립니다.
- ``reset_state()``: 이 화면이 세션에 저장한 검색 조건·탭·선택만 지웁니다.
- ``refresh_reviewers()``: 배정 후 호출하면 다음 화면 표시 때 검토 담당자 목록을 새로 불러옵니다.
  (검사 목록은 case_list.refresh_exams()로 새로 불러옵니다.)

백엔드 연결(아직 구현 전인 함수는 화면이 빈 상태로 안내합니다):
- 검사 목록: case_list.load_exams (services.case.get_case_list + 배정 조회 get_case_assignment)
- 검토 담당자: repositories.user.list_reviewers() — 진료과 구분 없이 전체 검토 담당자
- 배정 이력: repositories.review.list_case_history(case_id) 중 kind가 ASSIGNMENT_CHANGED인 기록
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import streamlit as st

from src.exception import AppError, ConflictError
from src.ui import case_list as _list
from src.ui.case_list import (
    CALENDAR_PRESETS,
    EVENT_LOGOUT,
    KST,
    ROLE_ADMIN,
    STATUS_ASSIGNED,
    STATUS_DONE,
    STATUS_REVIEWING,
    STATUS_UNASSIGNED,
    USER_NAME_FALLBACK,
    WEEKDAY_LABELS,
    CurrentUser,
    ExamRecord,
    role_label,
)

_logger = logging.getLogger(__name__)

# ── 업무 규칙 ────────────────────────────────────────────────────────────────

# 검사 표의 탭: 미배정 검사(배정) / 배정 완료 검사(재배정)
TAB_UNASSIGNED = "unassigned"
TAB_ASSIGNED = "assigned"
TAB_STATUS = {TAB_UNASSIGNED: STATUS_UNASSIGNED, TAB_ASSIGNED: STATUS_ASSIGNED}

# 검토자 업무 현황의 "남은 검토": 배정됐지만 아직 검토 완료가 아닌 검사(배정 완료 + 검토 중).
# 업무 현황은 "남은 검토 / 그 검토자에게 배정된 전체(검토 완료 포함)"로 보여줍니다.
PENDING_STATUSES = frozenset({STATUS_ASSIGNED, STATUS_REVIEWING})

# 업무 변경 이력(models.py의 CaseHistory.kind) 중 담당자 배정·변경 기록
HISTORY_ASSIGNMENT = "ASSIGNMENT_CHANGED"

# 담당자 저장 뒤 배정 이력 저장이 일시적으로 실패하면 같은 이력으로 이 횟수까지 시도합니다(시도 사이 0.3초, 0.6초).
HISTORY_SAVE_ATTEMPTS = 3
HISTORY_RETRY_SECONDS = 0.3

# 같은 사용자 화면에서 이 시간(초) 안에는 검토 담당자 목록을 다시 불러오지 않습니다.
REVIEWER_CACHE_SECONDS = 30

# ── 화면 이동 이벤트 (render가 돌려주는 ReviewAssignEvent.kind 값) ──────────────────
# EVENT_LOGOUT은 검사 목록 화면과 같은 값을 씁니다. 왼쪽 메뉴 이동은 components.sidebar가 처리합니다.

# ── 안내 문구 ────────────────────────────────────────────────────────────────

MSG_NEED_ADMIN = "업무 담당자만 검사를 배정할 수 있습니다."
MSG_NO_UNASSIGNED = "조건에 맞는 미배정 검사가 없습니다."
MSG_NO_ASSIGNED = "조건에 맞는 배정 완료 검사가 없습니다."
MSG_NO_REVIEWERS = "표시할 검토 담당자가 없습니다."
MSG_REVIEWERS_FAILED = "검토 담당자 목록을 불러오지 못했습니다."
MSG_HISTORY_EMPTY = "배정 이력이 없습니다."
MSG_HISTORY_FAILED = "배정 이력을 불러오지 못했습니다."
UNASSIGNED_LABEL = "미배정"  # 배정 이력에서 최초 배정의 이전 담당자 자리

# 배정 창(Figma "검사 배정", "검사 배정(재배정)")
ASSIGN_TITLES = {False: "검토자 배정", True: "검토자 재배정"}
ASSIGN_GUIDE = "선택한 검사를 담당할 검토자를 지정하세요."
CURRENT_REVIEWER_TAG = "현재 담당"  # 재배정 창에서 기존 검토자 보기 옆 표시

# 결과 안내 창(Figma "배정 완료 안내", "배정 실패 안내"): 제목과 (안내, 보조 문구)
DONE_TITLES = {False: "배정이 완료되었습니다.", True: "재배정이 완료되었습니다."}
DONE_LINES = {
    False: ("선택한 검사를 담당 검토자에게 배정했습니다.", "배정 이력에서 처리 내역을 확인할 수 있습니다."),
    True: ("선택한 검사의 담당 검토자를 변경했습니다.", "배정 이력에서 처리 내역을 확인할 수 있습니다."),
}
# 담당자는 저장했지만 배정 이력을 기록하지 못했을 때(제목은 완료 제목을 씀)
DONE_HISTORY_PENDING = ("담당 검토자는 지정했지만 배정 이력을 기록하지 못했습니다.", "배정 이력 창을 열면 기록을 다시 시도합니다.")
MSG_HISTORY_PENDING = "기록하지 못한 배정 이력 {count}건 · 창을 다시 열면 다시 시도합니다."
FAIL_TITLES = {False: "배정이 실패하였습니다.", True: "재배정이 실패하였습니다."}
FAIL_SAME_REVIEWER = ("이미 배정된 담당 검토자입니다.", "검토자를 다시 확인해주세요.")
FAIL_CASE_CHANGED = ("검사 상태가 바뀌었습니다.", "목록을 새로 불러왔습니다. 다시 확인해주세요.")
FAIL_NOT_ADMIN = (MSG_NEED_ADMIN, "로그인한 사용자의 역할을 확인해주세요.")
FAIL_NOT_READY = ("배정 저장 기능이 아직 준비되지 않았습니다.", "백엔드 연결 후 다시 시도해주세요.")
FAIL_SAVE = ("배정을 저장하지 못했습니다.", "잠시 후 다시 시도해주세요.")

# 로그아웃 확인창 문구(검사 목록 화면과 같음)
LOGOUT_TEXTS = _list.CONFIRM_TEXTS["logout"]

# ── 세션 상태 키 (모두 _STATE_PREFIX로 시작, reset_state가 한꺼번에 지움) ──────────

_STATE_PREFIX = "_pacs_asg_"
_TAB_KEY = "_pacs_asg_tab"
_SELECTED_KEY = "_pacs_asg_selected"  # 체크한 검사의 case_id(한 건)
_QUERY_KEY = "_pacs_asg_query"  # 환자 검색 입력칸
_DATES_KEY = "_pacs_asg_dates"  # 등록일 칸에 들어 있는 기간(검색 전)
_APPLIED_KEY = "_pacs_asg_applied"  # 검색 버튼으로 적용한 조건(AssignFilter)
_USER_KEY = "_pacs_asg_user"  # 화면을 그린 사용자(배정 버튼 콜백에서 권한을 다시 확인)
_REVIEWERS_CACHE_KEY = "_pacs_asg_reviewers_cache"
_HISTORY_OPEN_KEY = "_pacs_asg_history_open"
_HISTORY_CACHE_KEY = "_pacs_asg_history_cache"  # 배정 이력 창을 연 동안만 보관
_DIALOG_KEY = "_pacs_asg_dialog"  # 열려 있는 배정 창(AssignTarget)
_PICK_KEY = "_pacs_asg_pick"  # 배정 창에서 고른 검토자 UID
_RESULT_KEY = "_pacs_asg_result"  # 배정 결과 안내 창(AssignResult)
# 저장하지 못한 배정 이력 [이력 문서]. 다른 화면에 다녀와도 남도록 _STATE_PREFIX와 다른 이름을 씁니다(reset_state가 안 지움).
_PENDING_HISTORY_KEY = "_pacs_assign_pending_history"
_CONFIRM_KEY = "_pacs_asg_confirm"  # 로그아웃 확인창 열림
_EVENT_KEY = "_pacs_asg_event"
_NOTICE_KEY = "_pacs_asg_notice"
_CAL_OPEN_KEY = "_pacs_asg_cal_open"
_CAL_DRAFT_KEY = "_pacs_asg_cal_draft"
_CAL_MONTH_KEY = "_pacs_asg_cal_month"


# ── 데이터 형식 ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Reviewer:
    uid: str
    name: str


@dataclass(frozen=True)
class ReviewerLoad:
    """검토자 업무 현황 한 줄."""

    uid: str
    name: str
    pending: int  # 남은 검토(배정 완료 + 검토 중)
    total: int  # 지금까지 배정된 전체(검토 완료 포함)

    @property
    def ratio(self) -> float:
        return self.pending / self.total if self.total else 0.0


@dataclass(frozen=True)
class HistoryEntry:
    """배정 이력 한 건."""

    case_id: str
    at: datetime
    changed_by: str  # 배정한 사람 이름
    patient_name: str
    exam_name: str
    before: str  # 이전 담당자 이름(최초 배정이면 UNASSIGNED_LABEL)
    after: str  # 새 담당자 이름
    reassign: bool  # 재배정이면 True, 신규 배정이면 False


@dataclass(frozen=True)
class AssignFilter:
    query: str
    start: date
    end: date


@dataclass(frozen=True)
class AssignTarget:
    """배정 창을 연 검사. 배정 완료 탭에서 열었으면 재배정입니다."""

    case_id: str
    reassign: bool


@dataclass(frozen=True)
class AssignRequest:
    """배정하기 버튼으로 저장할 내용."""

    case_id: str
    reviewer_uid: str  # 새 담당 검토자
    previous_uid: str  # 기존 담당 검토자(신규 배정이면 "")
    reassign: bool


@dataclass(frozen=True)
class AssignResult:
    """결과 안내 창에 보여줄 내용."""

    ok: bool
    reassign: bool
    lines: tuple[str, str]  # (안내, 보조 문구)


class HistoryNotSavedError(Exception):
    """담당자는 저장했지만 배정 이력을 기록하지 못했을 때 save_assignment가 냅니다(이력은 나중에 다시 저장)."""


@dataclass(frozen=True)
class ReviewAssignEvent:
    """사용자가 로그아웃할 때 render가 돌려주는 값(왼쪽 메뉴 이동은 components.sidebar가 돌려줌)."""

    kind: str  # EVENT_LOGOUT


# ── 데이터 불러오기 ──────────────────────────────────────────────────────────

def _clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def load_reviewers(user: CurrentUser) -> list[Reviewer]:
    """배정할 수 있는 검토 담당자를 진료과 구분 없이 모두 불러옵니다(이름순).

    진료과별로 나누기로 정하면 list_reviewers(user.department_id)처럼 진료과를 넘기면 됩니다.
    """
    from src.repositories.user import list_reviewers

    reviewers = []
    for row in list_reviewers(None):
        uid = _clean(row.get("uid"))
        if uid:
            reviewers.append(Reviewer(uid=uid, name=_clean(row.get("name")) or uid))
    return sorted(reviewers, key=lambda r: (r.name, r.uid))


def load_assignment_history(exams: list[ExamRecord], known_names: Mapping[str, str] | None = None) -> list[HistoryEntry]:
    """불러온 검사들의 담당자 배정·변경 기록을 최신순으로 돌려줍니다.

    known_names({UID: 이름})에 없는 사용자 이름만 Firestore users 문서에서 찾습니다.
    """
    from src.repositories.review import list_case_history
    from src.repositories.user import get_user

    histories = {exam.case_id: list_case_history(exam.case_id) for exam in exams}
    names = dict(known_names or {})
    uids = [
        _clean(item.get(field))
        for items in histories.values() for item in items
        for field in ("changed_by", "before_value", "after_value")
    ]
    for uid, profile in _list._lookup_each(get_user, (u for u in uids if u and u not in names)).items():
        names[uid] = _clean((profile or {}).get("name")) or uid
    return history_entries(exams, histories, names)


def history_entries(
    exams: Iterable[ExamRecord], histories: Mapping[str, Iterable[Mapping[str, object]]], names: Mapping[str, str]
) -> list[HistoryEntry]:
    """케이스별 변경 이력(CaseHistory 문서들) 중 담당자 배정·변경 기록만 골라 화면용 HistoryEntry로 바꿉니다(최신순).

    names({UID: 이름})에 없는 사용자는 UID를 그대로 표시합니다. 시각을 알 수 없는 기록은 뺍니다.
    """
    by_id = {exam.case_id: exam for exam in exams}
    records = [
        (by_id[case_id], item)
        for case_id, items in histories.items() if case_id in by_id
        for item in items if _clean(item.get("kind")) == HISTORY_ASSIGNMENT
    ]
    entries = []
    for exam, item in records:
        at = _list._to_datetime(item.get("created_at"))
        if at is None:
            continue
        before = _clean(item.get("before_value"))
        after = _clean(item.get("after_value"))
        changed_by = _clean(item.get("changed_by"))
        entries.append(HistoryEntry(
            case_id=exam.case_id,
            at=at,
            changed_by=names.get(changed_by, changed_by) or "-",
            patient_name=exam.patient_name,
            exam_name=exam.exam_name,
            before=names.get(before, before) if before else UNASSIGNED_LABEL,
            after=names.get(after, after) or "-",
            reassign=bool(before),
        ))
    return sorted(entries, key=lambda e: e.at, reverse=True)


def reviewer_workload(reviewers: Iterable[Reviewer], exams: Iterable[ExamRecord]) -> list[ReviewerLoad]:
    """검토 담당자마다 (남은 검토, 지금까지 배정된 전체) 건수를 셉니다. 검색 조건과 관계없이 불러온 검사 전체 기준입니다."""
    exams = list(exams)
    loads = []
    for reviewer in reviewers:
        mine = [e for e in exams if e.assignee_uid == reviewer.uid]
        pending = sum(1 for e in mine if e.status in PENDING_STATUSES)
        loads.append(ReviewerLoad(reviewer.uid, reviewer.name, pending, len(mine)))
    return loads


def split_by_tab(exams: Iterable[ExamRecord]) -> dict[str, list[ExamRecord]]:
    """검사를 탭별(미배정 / 배정 완료)로 나눕니다. 검토 중·검토 완료 검사는 어느 탭에도 넣지 않습니다."""
    tabs: dict[str, list[ExamRecord]] = {TAB_UNASSIGNED: [], TAB_ASSIGNED: []}
    for exam in exams:
        for tab, status in TAB_STATUS.items():
            if exam.status == status:
                tabs[tab].append(exam)
    return tabs


def can_assign(user: CurrentUser) -> bool:
    return user.role == ROLE_ADMIN


def save_assignment(user: CurrentUser, request: AssignRequest) -> None:
    """담당자 배정을 저장합니다: 배정 권한 확인 → 현재 담당자 문서 교체 → 배정 이력 추가.

    - 권한 확인이나 담당자 저장이 실패하면 그 예외를 그대로 냅니다(아무것도 바뀌지 않음).
    - 담당자 저장 뒤 이력 저장이 끝내 실패하면 그 이력을 세션에 남기고 HistoryNotSavedError를 냅니다.
      남긴 이력은 retry_pending_history()가 다시 저장합니다.
    """
    from src.repositories.case import save_case_assignment
    from src.services.access_policy import require_case_assignment

    require_case_assignment(user.uid)
    now = datetime.now(timezone.utc)
    save_case_assignment({
        "case_id": request.case_id, "user_id": request.reviewer_uid, "assigned_by": user.uid, "assigned_at": now,
    })
    history = {
        "history_id": uuid.uuid4().hex, "case_id": request.case_id, "kind": HISTORY_ASSIGNMENT,
        "before_value": request.previous_uid or None, "after_value": request.reviewer_uid,
        "changed_by": user.uid, "created_at": now,
    }
    if not save_history(history):
        st.session_state.setdefault(_PENDING_HISTORY_KEY, []).append(history)
        raise HistoryNotSavedError(request.case_id)


def save_history(history: Mapping[str, object], attempts: int = HISTORY_SAVE_ATTEMPTS) -> bool:
    """배정 이력 한 건을 저장하고, 저장됐으면 True를 돌려줍니다.

    - 일시적인 오류(네트워크 등)는 같은 이력(같은 history_id)으로 attempts번까지 시도합니다.
    - "같은 history_id가 이미 있음"(ConflictError)은 앞선 시도가 실제로는 저장된 것이므로 성공으로 봅니다.
    - 미구현·권한·입력 오류는 다시 해도 같으므로 바로 False를 돌려줍니다.
    """
    from src.repositories.review import save_case_history

    for attempt in range(1, attempts + 1):
        try:
            save_case_history(dict(history))
            return True
        except ConflictError:
            return True
        except NotImplementedError:
            _logger.info("변경 이력 저장(save_case_history)이 아직 구현되지 않았습니다.")
            return False
        except (AppError, PermissionError):
            _logger.exception("배정 이력 저장 실패(다시 시도하지 않음)")
            return False
        except Exception:
            _logger.warning("배정 이력 저장 실패(%d/%d)", attempt, attempts, exc_info=True)
            if attempt < attempts:
                time.sleep(HISTORY_RETRY_SECONDS * attempt)
    return False


def pending_history_count() -> int:
    return len(st.session_state.get(_PENDING_HISTORY_KEY) or [])


def retry_pending_history() -> int:
    """저장하지 못하고 남겨 둔 배정 이력을 한 번씩 다시 저장합니다. 그래도 남은 건수를 돌려줍니다."""
    pending = st.session_state.get(_PENDING_HISTORY_KEY) or []
    left = [history for history in pending if not save_history(history, attempts=1)]
    if left:
        st.session_state[_PENDING_HISTORY_KEY] = left
    else:
        st.session_state.pop(_PENDING_HISTORY_KEY, None)
    return len(left)


def _get_reviewers(user: CurrentUser) -> tuple[list[Reviewer], bool]:
    """세션 캐시를 거쳐 검토 담당자 목록을 가져옵니다. (목록, 불러오기 실패 여부)를 돌려줍니다."""
    cached = st.session_state.get(_REVIEWERS_CACHE_KEY)
    now = time.monotonic()
    if cached is not None and cached[0] == user.uid and now - cached[1] < REVIEWER_CACHE_SECONDS:
        return cached[2], False
    try:
        reviewers = load_reviewers(user)
    except NotImplementedError:
        _logger.info("검토 담당자 조회(list_reviewers)가 아직 구현되지 않았습니다.")
        return [], True
    except Exception:
        _logger.exception("검토 담당자 목록 불러오기 실패")
        return [], True
    st.session_state[_REVIEWERS_CACHE_KEY] = (user.uid, now, reviewers)
    return reviewers, False


def _get_history(exams: list[ExamRecord], reviewers: list[Reviewer]) -> tuple[list[HistoryEntry], bool]:
    """배정 이력 창을 연 동안 한 번만 불러옵니다. (이력, 불러오기 실패 여부)를 돌려줍니다."""
    cached = st.session_state.get(_HISTORY_CACHE_KEY)
    if cached is not None:
        return cached
    try:
        result = (load_assignment_history(exams, {r.uid: r.name for r in reviewers}), False)
    except NotImplementedError:
        _logger.info("변경 이력 조회(list_case_history)가 아직 구현되지 않았습니다.")
        result = ([], True)
    except Exception:
        _logger.exception("배정 이력 불러오기 실패")
        result = ([], True)
    st.session_state[_HISTORY_CACHE_KEY] = result
    return result


def refresh_reviewers() -> None:
    """저장해 둔 검토 담당자 목록을 지워, 다음 화면 표시 때 새로 불러오게 합니다(배정 후 호출)."""
    st.session_state.pop(_REVIEWERS_CACHE_KEY, None)


def reset_state() -> None:
    """이 화면이 세션에 저장한 값(검색 조건, 탭, 선택, 캐시 등)만 지웁니다."""
    for key in [k for k in st.session_state.keys() if str(k).startswith(_STATE_PREFIX)]:
        del st.session_state[key]


# ── 검색 ────────────────────────────────────────────────────────────────────

def default_filter() -> AssignFilter:
    """기본 검색 조건: 검사 목록 화면과 같은 기본 기간(오늘을 포함한 최근 7일)."""
    base = _list.default_filter()
    return AssignFilter("", base.start, base.end)


def filter_exams(exams: list[ExamRecord], condition: AssignFilter) -> list[ExamRecord]:
    """검색어(환자명·환자 ID·케이스 ID)와 등록일 기간으로 거르고, 등록 최신순으로 돌려줍니다."""
    return _list.filter_exams(
        exams, _list.SearchFilter(condition.query, condition.start, condition.end, _list.ALL, _list.ALL)
    )


# ── 화면 배치 CSS ────────────────────────────────────────────────────────────
# 크기는 화면 높이에 비례하는 --u(= 화면 높이 / 960) 단위이고, 좌표는 Figma 레이어 값을 그대로 씁니다.
# 화면이 디자인(1440)보다 넓으면 검사 표가 넓어지고, 검토자 업무 현황(384)은 오른쪽 끝에 붙습니다.

_STYLE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap');

html, body, .stApp, .stMain { background:#f6f6f6 !important; }

/* 처리 중 화면(검사 목록 화면과 같음): 실행이 0.4초 넘게 걸리면 화면을 살짝 덮고 회전 표시를 띄웁니다. */
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

.st-key-pacs_asg_stage {
  --u:calc(100vh / 960);
  position:fixed !important; inset:0; z-index:1; overflow:hidden;
  background:#f6f6f6; color:#222; font-family:'Noto Sans KR', sans-serif; font-weight:400;
}
.st-key-pacs_asg_stage * { box-sizing:border-box; }

/* Streamlit 요소의 기본 간격·여백·글꼴을 없앱니다. */
.st-key-pacs_asg_stage [data-testid="stVerticalBlock"],
.st-key-pacs_asg_stage [data-testid="stHorizontalBlock"] { gap:0 !important; }
.st-key-pacs_asg_stage [data-testid="stElementContainer"] { margin:0 !important; }
.st-key-pacs_asg_stage [data-testid="stMarkdown"],
.st-key-pacs_asg_stage [data-testid="stMarkdownContainer"],
.st-key-pacs_asg_stage [data-testid="stHeading"],
.st-key-pacs_asg_stage [data-testid="stHeadingWithActionElements"],
.st-key-pacs_asg_stage [data-testid="stMarkdownContainer"] p,
.st-key-pacs_asg_stage h1, .st-key-pacs_asg_stage h2, .st-key-pacs_asg_stage h3 {
  margin:0 !important; padding:0 !important; font-family:inherit; font-size:inherit;
  font-weight:inherit; line-height:inherit; letter-spacing:normal; color:inherit; white-space:inherit;
}
.st-key-pacs_asg_stage [data-testid="stHeaderActionElements"] { display:none !important; }
.st-key-pacs_asg_stage button { font-family:inherit !important; }
.st-key-pacs_asg_stage [data-testid="stButtonShortcut"], .st-key-pacs_asg_stage kbd { display:none !important; }
.st-key-pacs_asg_stage button:focus-visible { outline:2px solid #555 !important; outline-offset:2px; }

/* 절대 좌표로 배치하는 블록 */
.st-key-pacs_asg_header, .st-key-pacs_asg_main,
.st-key-pacs_asg_clock, .st-key-pacs_asg_logout, .st-key-pacs_asg_title, .st-key-pacs_asg_history_open,
.st-key-pacs_asg_stats, .st-key-pacs_asg_filters, .st-key-pacs_asg_table, .st-key-pacs_asg_load {
  position:absolute !important;
}
/* 절대 좌표로 배치하는 글자 칸: 내용 너비만큼만 차지하고 한 줄로 표시합니다. */
.st-key-pacs_asg_crumb_home, .st-key-pacs_asg_crumb_page, .st-key-pacs_asg_user,
[class*="st-key-pacs_asg_stat_label_"], [class*="st-key-pacs_asg_stat_value_"], [class*="st-key-pacs_asg_stat_note_"],
[class*="st-key-pacs_asg_th_"], [class*="st-key-pacs_asg_cname_"], [class*="st-key-pacs_asg_cmeta_"],
[class*="st-key-pacs_asg_cpid_"], [class*="st-key-pacs_asg_cexam_"], [class*="st-key-pacs_asg_ctime_"],
.st-key-pacs_asg_load_title, .st-key-pacs_asg_load_sub,
[class*="st-key-pacs_asg_rv_name_"], [class*="st-key-pacs_asg_rv_wait_"], [class*="st-key-pacs_asg_rv_count_"] {
  position:absolute !important; width:auto !important; white-space:nowrap;
}
/* 넘치면 말줄임(…)으로 자르는 글자 칸 */
[class*="st-key-pacs_asg_cname_"], [class*="st-key-pacs_asg_cmeta_"], [class*="st-key-pacs_asg_cexam_"],
[class*="st-key-pacs_asg_rv_name_"] { overflow:hidden; }
:is([class*="st-key-pacs_asg_cname_"], [class*="st-key-pacs_asg_cmeta_"], [class*="st-key-pacs_asg_cexam_"],
    [class*="st-key-pacs_asg_rv_name_"])
  :is([data-testid="stElementContainer"], [data-testid="stMarkdown"], [data-testid="stMarkdownContainer"], p) {
  width:100% !important; max-width:100%; overflow:hidden; text-overflow:ellipsis;
}

/* ── 상단 바 (64 높이) ── */
.st-key-pacs_asg_header {
  left:calc(204 * var(--u)); right:0; top:0; height:calc(64 * var(--u));
  width:auto !important; background:#fff; border-bottom:1px solid #e5e5e5;
}
.st-key-pacs_asg_crumb_home {
  left:calc(32 * var(--u)); top:calc(23 * var(--u)); font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#777;
}
.st-key-pacs_asg_crumb_page {
  left:calc(143 * var(--u)); top:calc(23 * var(--u)); font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:500; color:#222;
}
.st-key-pacs_asg_crumb_page::before {
  content:''; position:absolute; left:calc(-27 * var(--u)); top:calc(1 * var(--u));
  width:calc(16 * var(--u)); height:calc(16 * var(--u)); background:@ICON_CHEVRON_RIGHT@ center / contain no-repeat;
}
.st-key-pacs_asg_user { left:calc(100% - 242 * var(--u)); top:calc(15 * var(--u)); overflow:visible; }
.st-key-pacs_asg_user::before {
  content:''; position:absolute; left:calc(-36 * var(--u)); top:calc(6 * var(--u)); width:1px; height:calc(22 * var(--u)); background:#e5e5e5;
}
.st-key-pacs_asg_user_role { font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#777; }
.st-key-pacs_asg_user_name { margin-top:calc(-1 * var(--u)); font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:500; color:#222; }
.st-key-pacs_asg_user_role, .st-key-pacs_asg_user_name { max-width:calc(160 * var(--u)); }
.st-key-pacs_asg_user_role p, .st-key-pacs_asg_user_name p { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.st-key-pacs_asg_clock {
  right:calc(303 * var(--u)); top:calc(23 * var(--u)); width:auto !important; text-align:right; white-space:nowrap;
  font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#666; font-variant-numeric:tabular-nums;
}
.st-key-pacs_asg_logout { right:calc(31 * var(--u)); top:calc(18 * var(--u)); width:calc(28 * var(--u)) !important; height:calc(28 * var(--u)); }
.st-key-pacs_asg_logout button {
  width:calc(28 * var(--u)) !important; height:calc(28 * var(--u)) !important; min-height:0 !important; padding:0 !important;
  border:0 !important; border-radius:calc(4 * var(--u)) !important; box-shadow:none !important;
  background:transparent @ICON_LOGOUT@ center / calc(16 * var(--u)) no-repeat !important;
}
.st-key-pacs_asg_logout button:hover { background-color:#f2f2f2 !important; }
.st-key-pacs_asg_logout button p { font-size:0 !important; }

/* ── 본문 (상단 바 아래, 사이드바 오른쪽) ── */
.st-key-pacs_asg_main { left:calc(204 * var(--u)); right:0; top:calc(64 * var(--u)); bottom:0; width:auto !important; }
.st-key-pacs_asg_title { left:calc(32 * var(--u)); top:calc(25 * var(--u)); width:auto !important; white-space:nowrap; }
.st-key-pacs_asg_title_text { font-size:calc(26 * var(--u)); line-height:calc(39 * var(--u)); font-weight:700; color:#1d1d1d; }
.st-key-pacs_asg_subtitle { margin:calc(5 * var(--u)) 0 0 1px; font-size:calc(13 * var(--u)); line-height:calc(20 * var(--u)); color:#727272; }

/* 배정 이력 버튼: 오른쪽 위, 높이 38, 안쪽 여백 9·16 */
.st-key-pacs_asg_history_open { right:calc(30 * var(--u)); top:calc(26 * var(--u)); width:auto !important; }
.st-key-pacs_asg_history_open [data-testid="stButton"] { display:flex !important; }
.st-key-pacs_asg_history_open button {
  width:auto !important; height:calc(38 * var(--u)) !important; min-height:0 !important; padding:0 calc(16 * var(--u)) !important;
  background:#fff !important; border:1px solid #dcdcdc !important; border-radius:calc(5 * var(--u)) !important; box-shadow:none !important;
  color:#333 !important; cursor:pointer; white-space:nowrap;
}
.st-key-pacs_asg_history_open button:hover { background:#f5f5f5 !important; }
.st-key-pacs_asg_history_open button p { font-size:calc(12 * var(--u)) !important; line-height:calc(18 * var(--u)) !important; font-weight:500 !important; }

/* 상태 카드 5개(검사 목록 화면과 같음): 높이 88, 화면 폭을 5등분 */
.st-key-pacs_asg_stage .st-key-pacs_asg_stats {
  left:calc(32 * var(--u)); right:calc(30 * var(--u)); top:calc(110 * var(--u)); width:auto !important;
  flex-wrap:nowrap !important; gap:calc(17.33 * var(--u)) !important;
}
.st-key-pacs_asg_stats > div { flex:1 1 0 !important; width:auto !important; min-width:0 !important; }
[class*="st-key-pacs_asg_stat_card_"] {
  position:relative; width:100% !important; height:calc(88 * var(--u)) !important; flex:0 0 auto !important;
  background:#fff; border:1px solid #e1e1e1; border-radius:calc(7 * var(--u)); overflow:hidden;
}
[class*="st-key-pacs_asg_stat_card_accent_"]::before {
  content:''; position:absolute; left:-1px; top:calc(16 * var(--u)); width:3px; height:calc(54 * var(--u)); border-radius:1px; background:#333;
}
.st-key-pacs_asg_stat_card_accent_reviewing::before { background:#3266c3; }
[class*="st-key-pacs_asg_stat_label_"] {
  left:calc(17 * var(--u)); top:calc(12 * var(--u)); font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:500; color:#626262;
}
[class*="st-key-pacs_asg_stat_value_"] {
  left:calc(17 * var(--u)); top:calc(33 * var(--u)); font-size:calc(28 * var(--u)); line-height:calc(42 * var(--u)); font-weight:700; color:#222;
}
[class*="st-key-pacs_asg_stat_note_"] {
  left:calc(107 * var(--u)); top:calc(53 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#777;
}

/* 검색 조건 줄: 검사 표와 같은 폭(오른쪽은 업무 현황 앞 30), 칸 사이 16 */
.st-key-pacs_asg_stage .st-key-pacs_asg_filters {
  left:calc(32 * var(--u)); right:calc(444 * var(--u)); top:calc(220 * var(--u)); width:auto !important;
  flex-wrap:nowrap !important; align-items:flex-end !important; gap:calc(16 * var(--u)) !important;
}
.st-key-pacs_asg_filters > div { flex:0 0 auto !important; width:auto !important; min-width:0 !important; }
.st-key-pacs_asg_filters > div:has(> .st-key-pacs_asg_field_query) { flex:1 1 auto !important; }
[class*="st-key-pacs_asg_field_"] { gap:0 !important; }
.st-key-pacs_asg_field_query { width:100% !important; min-width:calc(160 * var(--u)) !important; }
.st-key-pacs_asg_field_dates { width:calc(280 * var(--u)) !important; }
.st-key-pacs_asg_field_search { width:calc(68 * var(--u)) !important; }
[class*="st-key-pacs_asg_label_"] {
  margin-bottom:calc(5 * var(--u)); white-space:nowrap; font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); font-weight:500; color:#666;
}
.st-key-pacs_asg_field_query [data-testid="stTextInput"] { width:100% !important; }
.st-key-pacs_asg_field_query [data-testid="stTextInputRootElement"] {
  height:calc(42 * var(--u)) !important; min-height:0 !important;
  background:#fff @ICON_SEARCH_FIELD@ calc(12 * var(--u)) center / calc(18 * var(--u)) no-repeat !important;
  border:1px solid #d7d7d7 !important; border-radius:calc(6 * var(--u)) !important; box-shadow:none !important;
}
.st-key-pacs_asg_field_query [data-testid="stTextInputRootElement"]:focus-within { border-color:#555 !important; }
.st-key-pacs_asg_field_query [data-testid="stTextInputRootElement"] > div,
.st-key-pacs_asg_field_query [data-baseweb="base-input"] { background:transparent !important; }
.st-key-pacs_asg_field_query input {
  height:100% !important; padding:0 calc(12 * var(--u)) 0 calc(39 * var(--u)) !important;
  background:transparent !important; color:#444 !important; -webkit-text-fill-color:#444;
  caret-color:#222; font-family:inherit !important; font-size:calc(12 * var(--u)) !important;
}
.st-key-pacs_asg_field_query input::placeholder { color:#858585 !important; -webkit-text-fill-color:#858585; opacity:1; }
.st-key-pacs_asg_field_query [data-testid="InputInstructions"] { display:none !important; }
/* 등록일 칸: 버튼을 입력칸 모양으로. 누르면 아래 달력이 열림 */
.st-key-pacs_asg_field_dates { position:relative !important; overflow:visible !important; }
.st-key-pacs_asg_dates_button button {
  width:100% !important; height:calc(42 * var(--u)) !important; min-height:0 !important;
  padding:0 calc(12 * var(--u)) 0 calc(39 * var(--u)) !important; justify-content:flex-start !important;
  background:#fff @ICON_CALENDAR@ calc(12 * var(--u)) center / calc(18 * var(--u)) no-repeat !important;
  border:1px solid #d7d7d7 !important; border-radius:calc(6 * var(--u)) !important; box-shadow:none !important;
  color:#444 !important; font-weight:400 !important; cursor:pointer;
}
.st-key-pacs_asg_dates_button button > div { justify-content:flex-start !important; width:100%; }
.st-key-pacs_asg_dates_button button p { font-size:calc(12 * var(--u)) !important; white-space:pre; }
.st-key-pacs_asg_dates_button button:hover,
.st-key-pacs_asg_dates_box_open .st-key-pacs_asg_dates_button button { border-color:#555 !important; }
/* 검색 버튼: 68×42, 배경 #f7f7f7 */
.st-key-pacs_asg_field_search button {
  width:100% !important; height:calc(42 * var(--u)) !important; min-height:0 !important; padding:0 !important;
  border:1px solid #d7d7d7 !important; border-radius:calc(6 * var(--u)) !important; box-shadow:none !important;
  background:#f7f7f7 @ICON_SEARCH_BUTTON@ center / calc(18 * var(--u)) no-repeat !important;
}
.st-key-pacs_asg_field_search button:hover { background-color:#eee !important; }
.st-key-pacs_asg_field_search button p { font-size:0 !important; }

/* ── 등록일 기간 선택 달력(검사 목록 화면과 같음): 등록일 칸과 같은 폭 ── */
.st-key-pacs_asg_cal_backdrop { position:fixed !important; inset:0; z-index:30; width:100vw !important; height:100vh !important; opacity:0; }
.st-key-pacs_asg_cal_backdrop * { width:100% !important; height:100% !important; cursor:default !important; }
.st-key-pacs_asg_stage .st-key-pacs_asg_cal {
  position:absolute !important; z-index:31; left:0; top:calc(100% + 6 * var(--u));
  width:100% !important; min-width:0 !important; max-width:none !important;
  padding:calc(20 * var(--u)) !important; gap:calc(16 * var(--u)) !important;
  background:#fff; border:1px solid #dedede; border-radius:calc(8 * var(--u));
  box-shadow:0 calc(8 * var(--u)) calc(24 * var(--u)) rgba(0, 0, 0, 0.12); color:#252525; line-height:1.5;
}
.st-key-pacs_asg_cal > div { width:100% !important; max-width:none !important; }
.st-key-pacs_asg_cal :where([data-testid="stElementContainer"], [data-testid="stButton"]) { width:100% !important; height:100% !important; }
.st-key-pacs_asg_cal [data-testid="stButton"] { display:flex !important; line-height:0; }
.st-key-pacs_asg_cal button {
  width:100% !important; height:100% !important; min-height:0 !important; padding:0 !important;
  border:0 !important; border-radius:calc(5 * var(--u)) !important; background:transparent !important; box-shadow:none !important;
  color:inherit !important; font-size:inherit !important; font-weight:inherit !important; line-height:1.5 !important;
}
.st-key-pacs_asg_cal button p { font-size:inherit !important; font-weight:inherit !important; line-height:inherit !important; }
.st-key-pacs_asg_stage .st-key-pacs_asg_cal_head { flex-wrap:nowrap !important; align-items:center !important; }
.st-key-pacs_asg_cal_head > div:first-child { flex:1 1 auto !important; min-width:0; }
.st-key-pacs_asg_cal_title { font-size:calc(15 * var(--u)); font-weight:700; color:#252525; }
.st-key-pacs_asg_cal_head > div:last-child, .st-key-pacs_asg_cal_close {
  flex:0 0 auto !important; width:calc(20 * var(--u)) !important; height:calc(20 * var(--u)) !important;
}
.st-key-pacs_asg_cal_close button, .st-key-pacs_asg_cal_prev button, .st-key-pacs_asg_cal_next button {
  font-size:0 !important; background-position:center !important; background-size:calc(20 * var(--u)) !important;
  background-repeat:no-repeat !important; cursor:pointer;
}
.st-key-pacs_asg_cal_close button { background-image:@ICON_CLOSE@ !important; }
.st-key-pacs_asg_cal_close button p, .st-key-pacs_asg_cal_prev button p, .st-key-pacs_asg_cal_next button p { font-size:0 !important; }
.st-key-pacs_asg_stage .st-key-pacs_asg_cal_range { flex-wrap:nowrap !important; gap:calc(8 * var(--u)) !important; }
.st-key-pacs_asg_cal_range > div { flex:1 1 0 !important; min-width:0; }
.st-key-pacs_asg_stage [class*="st-key-pacs_asg_cal_box_"] {
  gap:calc(4 * var(--u)) !important; padding:calc(8 * var(--u)) calc(12 * var(--u)) !important;
  background:#f5f5f5; border:1px solid #dedede; border-radius:calc(5 * var(--u));
}
[class*="st-key-pacs_asg_cal_boxlabel_"] { font-size:calc(11 * var(--u)); color:#626262; }
[class*="st-key-pacs_asg_cal_boxvalue_"] { font-size:calc(13 * var(--u)); font-weight:500; color:#252525; }
.st-key-pacs_asg_stage .st-key-pacs_asg_cal_presets { flex-wrap:nowrap !important; gap:calc(6 * var(--u)) !important; }
.st-key-pacs_asg_cal_presets > div { flex:0 0 auto !important; width:auto !important; }
[class*="st-key-pacs_asg_cal_preset_"] { width:auto !important; }
[class*="st-key-pacs_asg_cal_preset_"] :is([data-testid="stElementContainer"], [data-testid="stButton"]) { width:auto !important; }
.st-key-pacs_asg_cal [class*="st-key-pacs_asg_cal_preset_"] button {
  width:auto !important; height:calc(32 * var(--u)) !important; padding:0 calc(10 * var(--u)) !important;
  border:1px solid #dedede !important; background:#fff !important; color:#252525 !important;
  font-size:calc(13 * var(--u)) !important; font-weight:500 !important; cursor:pointer; white-space:nowrap;
}
.st-key-pacs_asg_cal [class*="st-key-pacs_asg_cal_preset_"] button:hover { background:#f5f5f5 !important; }
.st-key-pacs_asg_cal [class*="st-key-pacs_asg_cal_preset_on_"] button { background:#252525 !important; border-color:#252525 !important; color:#fff !important; }
.st-key-pacs_asg_cal_rule_top, .st-key-pacs_asg_cal_rule_bottom {
  height:1px !important; min-height:1px; flex:0 0 auto !important; overflow:hidden; background:#dedede; font-size:0 !important; line-height:0 !important;
}
.st-key-pacs_asg_stage .st-key-pacs_asg_cal_nav { flex-wrap:nowrap !important; align-items:center !important; justify-content:space-between !important; }
.st-key-pacs_asg_cal_nav > div { flex:0 0 auto !important; width:auto !important; }
.st-key-pacs_asg_cal_prev, .st-key-pacs_asg_cal_next,
:is(.st-key-pacs_asg_cal_prev, .st-key-pacs_asg_cal_next, .st-key-pacs_asg_cal_close) :is([data-testid="stButton"], button) {
  width:calc(20 * var(--u)) !important; height:calc(20 * var(--u)) !important;
}
.st-key-pacs_asg_cal_prev button { background-image:@ICON_MONTH_PREV@ !important; }
.st-key-pacs_asg_cal_next button { background-image:@ICON_MONTH_NEXT@ !important; }
.st-key-pacs_asg_cal_month { font-size:calc(14 * var(--u)); font-weight:700; color:#252525; white-space:nowrap; }
.st-key-pacs_asg_stage .st-key-pacs_asg_cal_grid { gap:calc(4 * var(--u)) !important; }
.st-key-pacs_asg_stage :is(.st-key-pacs_asg_cal_weekdays, [class*="st-key-pacs_asg_cal_week_"]) { flex-wrap:nowrap !important; }
:is(.st-key-pacs_asg_cal_weekdays, [class*="st-key-pacs_asg_cal_week_"]) > div { flex:1 1 0 !important; width:auto !important; min-width:0 !important; }
[class*="st-key-pacs_asg_cal_wd_"] {
  width:100% !important; height:calc(24 * var(--u)) !important; flex:0 0 auto !important; display:flex !important;
  flex-direction:column; justify-content:center; font-size:calc(11 * var(--u)); color:#626262;
}
[class*="st-key-pacs_asg_cal_d_"] {
  width:100% !important; height:calc(34 * var(--u)) !important; flex:0 0 auto !important;
  border-radius:calc(5 * var(--u)); font-size:calc(12 * var(--u)); color:#626262;
}
[class*="st-key-pacs_asg_cal_d_"] button { cursor:pointer; }
[class*="st-key-pacs_asg_cal_d_in_"]:hover, [class*="st-key-pacs_asg_cal_d_out_"]:hover { background:#f5f5f5; }
[class*="st-key-pacs_asg_cal_d_out_"] { color:#a1a1a1; }
[class*="st-key-pacs_asg_cal_d_mid_"] { background:#f5f5f5; color:#252525; }
[class*="st-key-pacs_asg_cal_d_sel_"] { background:#252525; color:#fff; font-weight:700; }
.st-key-pacs_asg_stage .st-key-pacs_asg_cal_foot {
  flex-wrap:nowrap !important; align-items:flex-start !important; justify-content:space-between !important; gap:calc(8 * var(--u)) !important;
}
.st-key-pacs_asg_cal_foot > div { flex:0 1 auto !important; width:auto !important; min-width:0; }
.st-key-pacs_asg_cal_summary { font-size:calc(11 * var(--u)); color:#626262; }
.st-key-pacs_asg_cal_apply { width:auto !important; flex:0 0 auto !important; }
.st-key-pacs_asg_cal_apply :is([data-testid="stElementContainer"], [data-testid="stButton"]) { width:auto !important; }
.st-key-pacs_asg_cal .st-key-pacs_asg_cal_apply button {
  width:auto !important; height:calc(42 * var(--u)) !important; padding:0 calc(16 * var(--u)) !important;
  background:#252525 !important; color:#fff !important; font-size:calc(13 * var(--u)) !important; font-weight:500 !important; cursor:pointer;
}
.st-key-pacs_asg_cal .st-key-pacs_asg_cal_apply button:hover { background:#3a3a3a !important; }

/* ── 검사 표: 왼쪽 32, 오른쪽은 업무 현황(384) 앞 30. 탭 42 + 머리글 39 + 줄(48씩, 스크롤) ── */
.st-key-pacs_asg_table {
  left:calc(32 * var(--u)); right:calc(444 * var(--u)); top:calc(328 * var(--u)); height:calc(520 * var(--u));
  width:auto !important; background:#fff; border:1px solid #dedede; border-radius:calc(7 * var(--u)); overflow:hidden;
}
/* 탭 줄: 탭 78×42(글자 가운데), 고른 탭은 굵은 글자 + 아래 2px 막대. 오른쪽 끝에 배정 버튼 */
.st-key-pacs_asg_tabbar { position:relative; flex:0 0 auto; height:calc(42 * var(--u)); }
[class*="st-key-pacs_asg_tab_"] { position:absolute !important; top:0; width:calc(78 * var(--u)) !important; height:calc(42 * var(--u)); }
[class*="st-key-pacs_asg_tab_"][class*="_unassigned"] { left:0; }
[class*="st-key-pacs_asg_tab_"][class*="_assigned"]:not([class*="_unassigned"]) { left:calc(81 * var(--u)); }
[class*="st-key-pacs_asg_tab_"] :is([data-testid="stElementContainer"], [data-testid="stButton"]) { width:100% !important; height:100% !important; }
[class*="st-key-pacs_asg_tab_"] button {
  width:100% !important; height:100% !important; min-height:0 !important; padding:0 !important;
  border:0 !important; border-radius:0 !important; background:transparent !important; box-shadow:none !important; cursor:pointer;
}
[class*="st-key-pacs_asg_tab_"] button p {
  font-size:calc(12 * var(--u)) !important; line-height:calc(18 * var(--u)) !important; color:#757575 !important; white-space:pre;
}
[class*="st-key-pacs_asg_tab_on_"] button p { color:#212121 !important; font-weight:700 !important; }
[class*="st-key-pacs_asg_tab_on_"]::after {
  content:''; position:absolute; left:calc(1 * var(--u)); right:calc(-1 * var(--u)); bottom:calc(-1 * var(--u)); height:2px; background:#292929; z-index:1;
}
.st-key-pacs_asg_assign {
  position:absolute !important; right:calc(10 * var(--u)); top:calc(6 * var(--u)); width:calc(45 * var(--u)) !important; height:calc(29 * var(--u));
}
.st-key-pacs_asg_assign :is([data-testid="stElementContainer"], [data-testid="stButton"], [data-testid="stButton"] > div,
  [data-testid="stTooltipIcon"], [data-testid="stTooltipHoverTarget"], button) { width:100% !important; height:100% !important; min-height:0 !important; }
.st-key-pacs_asg_assign button {
  padding:0 !important; border:1px solid #242424 !important; border-radius:calc(4 * var(--u)) !important; box-shadow:none !important;
  background:#242424 !important; cursor:pointer;
}
.st-key-pacs_asg_assign button:hover:enabled { background:#3a3a3a !important; }
.st-key-pacs_asg_assign button:disabled { background:#cfcfcf !important; border-color:#cfcfcf !important; cursor:not-allowed; }
.st-key-pacs_asg_assign button p { color:#fff !important; font-size:calc(11 * var(--u)) !important; line-height:calc(17 * var(--u)) !important; font-weight:500 !important; }
/* 머리글: 환자명 x 53, 환자 ID·검사명·등록 시간은 칸 가운데 정렬 */
.st-key-pacs_asg_thead { position:relative; flex:0 0 auto; height:calc(39 * var(--u)); background:#f8f8f8; }
[class*="st-key-pacs_asg_th_"] { top:calc(11 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); font-weight:500; color:#6d6d6d; }
.st-key-pacs_asg_th_name { left:calc(53 * var(--u)); }
.st-key-pacs_asg_th_pid, [class*="st-key-pacs_asg_cpid_"] { left:calc(199 * var(--u)); transform:translateX(-50%); text-align:center; }
.st-key-pacs_asg_th_exam, [class*="st-key-pacs_asg_cexam_"] { left:49.4%; transform:translateX(-50%); text-align:center; }
.st-key-pacs_asg_th_time, [class*="st-key-pacs_asg_ctime_"] { left:77.1%; transform:translateX(-50%); text-align:center; }
.st-key-pacs_asg_rows {
  flex:0 0 auto !important; min-height:0 !important; display:block !important;
  height:calc(437 * var(--u)) !important; max-height:none !important; overflow-y:auto !important; overflow-x:hidden; scrollbar-width:thin; scrollbar-color:#cfcfcf transparent;
}
/* 줄: 고를 수 있는 줄(row_N), 선택된 줄(selrow_N), 업무 담당자가 아니면 보기만 하는 줄(viewrow_N) */
:is([class*="st-key-pacs_asg_row_"], [class*="st-key-pacs_asg_selrow_"], [class*="st-key-pacs_asg_viewrow_"]) {
  position:relative; flex:0 0 auto; height:calc(48 * var(--u)); background:#fff; border-bottom:1px solid #ececec;
}
[class*="st-key-pacs_asg_row_"]:hover { background:#fafafa; }
[class*="st-key-pacs_asg_selrow_"] { background:#eee; }
[class*="st-key-pacs_asg_selrow_"]::before { content:''; position:absolute; left:0; top:0; width:3px; height:100%; background:#292929; z-index:1; }
/* 체크박스 16×16 (20, 16): 선택 안 됨은 흰 칸, 선택됨은 검정 칸 + ✓ */
:is([class*="st-key-pacs_asg_row_"], [class*="st-key-pacs_asg_selrow_"])::after {
  content:''; position:absolute; left:calc(20 * var(--u)); top:calc(16 * var(--u)); z-index:1; pointer-events:none;
  width:calc(16 * var(--u)); height:calc(16 * var(--u)); border:1px solid #c8c8c8; border-radius:calc(3 * var(--u)); background:#fff;
  display:flex; align-items:center; justify-content:center; color:#fff; font-size:calc(11 * var(--u)); font-weight:700; line-height:1;
}
[class*="st-key-pacs_asg_selrow_"]::after { content:'✓'; background:#242424; border-color:#242424; }
[class*="st-key-pacs_asg_cname_"] {
  left:calc(53 * var(--u)); top:calc(6 * var(--u)); max-width:calc(110 * var(--u));
  font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:500; color:#292929;
}
[class*="st-key-pacs_asg_cmeta_"] {
  left:calc(53 * var(--u)); top:calc(25 * var(--u)); max-width:calc(110 * var(--u)); font-size:calc(9 * var(--u)); line-height:calc(14 * var(--u)); color:#7a7a7a;
}
[class*="st-key-pacs_asg_cpid_"] { top:calc(16 * var(--u)); font-size:calc(10 * var(--u)); line-height:calc(15 * var(--u)); color:#656565; }
[class*="st-key-pacs_asg_cexam_"] {
  top:calc(15 * var(--u)); max-width:calc(26% - 8 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#333;
}
[class*="st-key-pacs_asg_ctime_"] {
  top:calc(15 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#666; font-variant-numeric:tabular-nums;
}
/* 줄 전체를 덮는 투명 선택 버튼(누르면 체크가 켜지고 꺼짐) */
:is([class*="st-key-pacs_asg_row_"], [class*="st-key-pacs_asg_selrow_"]) [data-testid="stElementContainer"]:has([data-testid="stButton"]) {
  position:absolute !important; inset:0; z-index:2; width:100% !important; height:100% !important;
}
:is([class*="st-key-pacs_asg_row_"], [class*="st-key-pacs_asg_selrow_"]) :is([data-testid="stButton"], button) {
  width:100% !important; height:100% !important; min-height:0 !important;
}
:is([class*="st-key-pacs_asg_row_"], [class*="st-key-pacs_asg_selrow_"]) button {
  padding:0 !important; border:0 !important; border-radius:0 !important; box-shadow:none !important; background:transparent !important; cursor:pointer;
}
:is([class*="st-key-pacs_asg_row_"], [class*="st-key-pacs_asg_selrow_"]) button p { font-size:0 !important; }
:is([class*="st-key-pacs_asg_row_"], [class*="st-key-pacs_asg_selrow_"]) button:focus-visible { outline:2px solid #555 !important; outline-offset:-2px; }
.st-key-pacs_asg_empty {
  display:flex !important; align-items:center; justify-content:center; height:calc(437 * var(--u)) !important;
  padding:0 calc(24 * var(--u)); text-align:center; white-space:normal; font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#858585;
}

/* ── 검토자 업무 현황: 384×628, 오른쪽 끝 30 ── */
.st-key-pacs_asg_load {
  right:calc(30 * var(--u)); top:calc(220 * var(--u)); width:calc(384 * var(--u)) !important; height:calc(628 * var(--u));
  background:#fff; border:1px solid #ddd; border-radius:calc(6 * var(--u)); overflow:hidden;
}
.st-key-pacs_asg_load_title { left:calc(18 * var(--u)); top:calc(23 * var(--u)); font-size:calc(14 * var(--u)); line-height:calc(21 * var(--u)); font-weight:700; color:#333; }
.st-key-pacs_asg_load_sub { left:calc(18 * var(--u)); top:calc(49 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#737373; }
.st-key-pacs_asg_load_list {
  position:absolute !important; left:calc(18 * var(--u)); right:calc(18 * var(--u)); top:calc(66 * var(--u)); height:calc(452 * var(--u));
  width:auto !important; display:block !important; overflow-y:auto !important; overflow-x:hidden; scrollbar-width:thin; scrollbar-color:#cfcfcf transparent;
}
/* 검토자 한 명: 이름(y 18) · 남은 검토 건수(y 49) · 업무량 막대(y 72, 높이 4) */
[class*="st-key-pacs_asg_rv_item_"] { position:relative; flex:0 0 auto; height:calc(74 * var(--u)); }
[class*="st-key-pacs_asg_rv_name_"] {
  left:0; top:calc(18 * var(--u)); max-width:100%; font-size:calc(13 * var(--u)); line-height:calc(20 * var(--u)); font-weight:700; color:#333;
}
[class*="st-key-pacs_asg_rv_wait_"] { left:0; top:calc(49 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#737373; }
[class*="st-key-pacs_asg_rv_count_"] {
  right:0; top:calc(49 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); font-weight:500; color:#333;
}
[class*="st-key-pacs_asg_rv_count_"] strong { font-weight:500; color:#000; }
[class*="st-key-pacs_asg_rv_bar_"] { position:absolute !important; left:0; right:0; top:calc(72 * var(--u)); width:auto !important; }
.pacs-asg-bar { height:calc(4 * var(--u)); border-radius:2px; background:#eee; overflow:hidden; }
.pacs-asg-bar > div { height:100%; border-radius:2px; background:#252525; }  /* 남은 검토 비율만큼 검은 막대 */
.st-key-pacs_asg_load_empty {
  padding-top:calc(40 * var(--u)); text-align:center; white-space:normal; font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#858585;
}
/* 배정 전 확인 안내: y 528, 배경 #f7f7f7, 안쪽 여백 12 */
.st-key-pacs_asg_load_note {
  position:absolute !important; left:calc(18 * var(--u)); right:calc(18 * var(--u)); top:calc(528 * var(--u)); width:auto !important;
  padding:calc(12 * var(--u)) !important; gap:calc(5 * var(--u)) !important; background:#f7f7f7;
}
.st-key-pacs_asg_load_note_title { font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); font-weight:700; color:#333; }
.st-key-pacs_asg_load_note_body { font-size:calc(10 * var(--u)); line-height:calc(15 * var(--u)); color:#737373; }

/* ── 화면 가운데 창(배정 이력, 로그아웃 확인): 뒤는 뿌옇게 덮고, 누르면 닫힘 ── */
:is(.st-key-pacs_asg_modal_backdrop, .st-key-pacs_asg_hist_backdrop) {
  position:fixed !important; inset:0; z-index:40; width:100vw !important; height:100vh !important;
  background:rgba(37, 37, 37, 0.35); backdrop-filter:blur(2px); -webkit-backdrop-filter:blur(2px);
}
:is(.st-key-pacs_asg_modal_backdrop, .st-key-pacs_asg_hist_backdrop) * { width:100% !important; height:100% !important; }
:is(.st-key-pacs_asg_modal_backdrop, .st-key-pacs_asg_hist_backdrop) button { opacity:0; cursor:default !important; }

/* 배정 이력 창: 753×705, 안쪽 여백 28·24, 줄 간격 20. 기록 목록만 스크롤 */
.st-key-pacs_asg_stage .st-key-pacs_asg_hist {
  position:fixed !important; z-index:41; left:50%; top:50%; transform:translate(-50%, -50%);
  width:calc(753 * var(--u)) !important; max-width:calc(100vw - 32px) !important;
  height:calc(705 * var(--u)) !important; max-height:calc(100vh - 32px);
  padding:calc(28 * var(--u)) calc(24 * var(--u)) !important; gap:calc(20 * var(--u)) !important; flex-wrap:nowrap !important;
  background:#fff; border-radius:calc(8 * var(--u)); box-shadow:0 calc(8 * var(--u)) calc(24 * var(--u)) rgba(0, 0, 0, 0.12);
}
.st-key-pacs_asg_hist > div { width:100% !important; flex:0 0 auto; }
.st-key-pacs_asg_stage .st-key-pacs_asg_hist_head { flex-wrap:nowrap !important; align-items:center !important; justify-content:space-between !important; }
.st-key-pacs_asg_hist_head > div { flex:0 0 auto !important; width:auto !important; }
.st-key-pacs_asg_hist_title { font-size:calc(20 * var(--u)); line-height:calc(30 * var(--u)); font-weight:700; color:#333; white-space:nowrap; }
.st-key-pacs_asg_hist_close [data-testid="stButton"] { display:flex !important; }
.st-key-pacs_asg_hist_close button {
  width:calc(46 * var(--u)) !important; height:calc(38 * var(--u)) !important; min-height:0 !important; padding:0 !important;
  background:#fff !important; border:1px solid #ddd !important; border-radius:calc(5 * var(--u)) !important; box-shadow:none !important; cursor:pointer;
}
.st-key-pacs_asg_hist_close button:hover { background:#f5f5f5 !important; }
.st-key-pacs_asg_hist_close button p { font-size:calc(12 * var(--u)) !important; line-height:calc(18 * var(--u)) !important; font-weight:500 !important; color:#333 !important; }
.st-key-pacs_asg_hist_sub { font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#737373; }
.st-key-pacs_asg_hist_pending { margin-top:calc(-12 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); font-weight:500; color:#c0392b; }
.st-key-pacs_asg_stage .st-key-pacs_asg_hist:has(.st-key-pacs_asg_hist_pending) .st-key-pacs_asg_hist_list { height:calc(528 * var(--u)) !important; }
/* 기록 목록 높이 = 705 - 위아래 여백 56 - 제목 줄 38 - 안내 18 - 간격 40 */
.st-key-pacs_asg_stage .st-key-pacs_asg_hist_list {
  flex:0 0 auto !important; height:calc(553 * var(--u)) !important; min-height:0 !important; overflow-y:auto !important; overflow-x:hidden; gap:calc(20 * var(--u)) !important;
  flex-wrap:nowrap !important; scrollbar-width:thin; scrollbar-color:#cfcfcf transparent;
}
.st-key-pacs_asg_hist_list > div { flex:0 0 auto !important; }
[class*="st-key-pacs_asg_hist_day_"] {
  padding:calc(12 * var(--u)) calc(14 * var(--u)) !important; background:#f5f5f5; white-space:pre;
  font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:500; color:#333;
}
.st-key-pacs_asg_stage [class*="st-key-pacs_asg_hist_item_"] {
  gap:calc(6 * var(--u)) !important; padding-bottom:calc(18 * var(--u)) !important; border-bottom:1px solid #e7e7e7;
}
[class*="st-key-pacs_asg_hist_when_"], [class*="st-key-pacs_asg_hist_kind_"] { font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#737373; white-space:pre; }
[class*="st-key-pacs_asg_hist_case_"] { font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:700; color:#333; white-space:pre; }
[class*="st-key-pacs_asg_hist_change_"] { font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:500; color:#333; }
.st-key-pacs_asg_hist_empty {
  padding-top:calc(60 * var(--u)); text-align:center; font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#858585;
}

/* ── 배정 창(Figma "검사 배정(재배정)"): 640 폭, 안쪽 여백 24·28, 줄 간격 20 ── */
:is(.st-key-pacs_asg_dlg_backdrop, .st-key-pacs_asg_res_backdrop) {
  position:fixed !important; inset:0; width:100vw !important; height:100vh !important;
  background:rgba(37, 37, 37, 0.35); backdrop-filter:blur(2px); -webkit-backdrop-filter:blur(2px);
}
.st-key-pacs_asg_dlg_backdrop { z-index:40; }
.st-key-pacs_asg_res_backdrop { z-index:50; }
:is(.st-key-pacs_asg_dlg_backdrop, .st-key-pacs_asg_res_backdrop) * { width:100% !important; height:100% !important; }
:is(.st-key-pacs_asg_dlg_backdrop, .st-key-pacs_asg_res_backdrop) button { opacity:0; cursor:default !important; }
.st-key-pacs_asg_stage .st-key-pacs_asg_dlg {
  position:fixed !important; z-index:41; left:50%; top:50%; transform:translate(-50%, -50%);
  width:calc(640 * var(--u)) !important; max-width:calc(100vw - 32px) !important; max-height:calc(100vh - 32px); overflow-y:auto;
  padding:calc(24 * var(--u)) calc(28 * var(--u)) !important; gap:calc(20 * var(--u)) !important; flex-wrap:nowrap !important;
  background:#fff; border:1px solid #ddd; border-radius:calc(10 * var(--u)); box-shadow:0 calc(8 * var(--u)) calc(24 * var(--u)) rgba(0, 0, 0, 0.12);
}
.st-key-pacs_asg_dlg > div { width:100% !important; flex:0 0 auto !important; }
.st-key-pacs_asg_stage .st-key-pacs_asg_dlg_head { flex-wrap:nowrap !important; align-items:center !important; justify-content:space-between !important; }
.st-key-pacs_asg_dlg_head > div { flex:0 0 auto !important; width:auto !important; }
.st-key-pacs_asg_dlg_title { font-size:calc(20 * var(--u)); line-height:calc(30 * var(--u)); font-weight:700; color:#222; white-space:nowrap; }
.st-key-pacs_asg_dlg_close [data-testid="stButton"] { display:flex !important; }
.st-key-pacs_asg_dlg_close button {
  width:calc(50 * var(--u)) !important; height:calc(40 * var(--u)) !important; min-height:0 !important; padding:0 !important;
  background:#fff !important; border:1px solid #dadada !important; border-radius:calc(5 * var(--u)) !important; box-shadow:none !important; cursor:pointer;
}
.st-key-pacs_asg_dlg_close button:hover { background:#f5f5f5 !important; }
:is(.st-key-pacs_asg_hist_close, .st-key-pacs_asg_dlg_close) button :is(p, span, div) { color:#333 !important; }
.st-key-pacs_asg_dlg_close button p { font-size:calc(12 * var(--u)) !important; line-height:calc(18 * var(--u)) !important; font-weight:500 !important; color:#333 !important; }
.st-key-pacs_asg_dlg_guide { font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#737373; }
/* 선택한 검사·기존 검토자 칸: 배경 #f7f7f7, 안쪽 여백 12·16, 줄 간격 8 */
.st-key-pacs_asg_stage :is(.st-key-pacs_asg_dlg_case, .st-key-pacs_asg_dlg_prev) {
  padding:calc(12 * var(--u)) calc(16 * var(--u)) !important; gap:calc(8 * var(--u)) !important; background:#f7f7f7;
}
:is(.st-key-pacs_asg_dlg_case_title, .st-key-pacs_asg_dlg_prev_title) { font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:700; color:#333; }
.st-key-pacs_asg_stage .st-key-pacs_asg_dlg_caserow { flex-wrap:nowrap !important; gap:calc(24 * var(--u)) !important; }
.st-key-pacs_asg_dlg_caserow > div { flex:0 1 auto !important; width:auto !important; min-width:0; }
:is(.st-key-pacs_asg_dlg_case_name, .st-key-pacs_asg_dlg_case_exam, .st-key-pacs_asg_dlg_prev_name) {
  font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#333; white-space:nowrap;
}
:is(.st-key-pacs_asg_dlg_case_name, .st-key-pacs_asg_dlg_prev_name) { font-weight:500; }
/* 검토자 선택: 제목 13 Bold, 보기 사이 10. 보기가 많으면 이 부분만 스크롤 */
.st-key-pacs_asg_stage .st-key-pacs_asg_dlg_pick { gap:calc(10 * var(--u)) !important; }
.st-key-pacs_asg_dlg_pick_title { font-size:calc(13 * var(--u)); line-height:calc(20 * var(--u)); font-weight:700; color:#333; }
.st-key-pacs_asg_stage .st-key-pacs_asg_dlg_opts {
  gap:calc(10 * var(--u)) !important; max-height:calc(228 * var(--u)); overflow-y:auto; flex-wrap:nowrap !important;
  scrollbar-width:thin; scrollbar-color:#cfcfcf transparent;
}
.st-key-pacs_asg_dlg_opts > div { flex:0 0 auto !important; }
.st-key-pacs_asg_dlg_opts_empty { font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#858585; }
/* 검토자 보기: 높이 49, 테두리 #dbdbdb. 왼쪽 동그라미(고르면 안에 점), 이름 12 Bold, 오른쪽 업무량 12 Medium */
[class*="st-key-pacs_asg_opt_"]:is([class*="_on_"], [class*="_off_"]) {
  position:relative; height:calc(49 * var(--u)) !important; flex:0 0 auto !important;
  background:#fff; border:1px solid #dbdbdb; border-radius:calc(5 * var(--u));
}
[class*="st-key-pacs_asg_opt_"]:is([class*="_on_"], [class*="_off_"])::before {
  content:''; position:absolute; left:calc(14 * var(--u)); top:50%; transform:translateY(-50%); z-index:1; pointer-events:none;
  width:calc(18 * var(--u)); height:calc(18 * var(--u)); border:1.5px solid #333; border-radius:50%; background:#fff;
}
[class*="st-key-pacs_asg_opt_on_"][class*="st-key-pacs_asg_opt_on_"] { border-color:#333; }
[class*="st-key-pacs_asg_opt_on_"][class*="st-key-pacs_asg_opt_on_"]::before { background:radial-gradient(circle, #333 0 calc(4.5 * var(--u)), #fff calc(5 * var(--u))); }
[class*="st-key-pacs_asg_opt_off_"][class*="st-key-pacs_asg_opt_off_"]:hover { background:#fafafa; }
[class*="st-key-pacs_asg_opt_who_"], [class*="st-key-pacs_asg_opt_count_"] {
  position:absolute !important; top:50%; transform:translateY(-50%); width:auto !important; white-space:nowrap;
  font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#333;
}
/* 이름(넘치면 …) 바로 옆에 "현재 담당" 표시(재배정 창의 기존 검토자) */
.st-key-pacs_asg_stage [class*="st-key-pacs_asg_opt_who_"] {
  left:calc(44 * var(--u)); right:calc(100 * var(--u)); flex-wrap:nowrap !important; align-items:center !important; gap:calc(8 * var(--u)) !important;
}
[class*="st-key-pacs_asg_opt_who_"] > div { width:auto !important; min-width:0; flex:0 1 auto !important; }
[class*="st-key-pacs_asg_opt_who_"] > div:has([class*="st-key-pacs_asg_opt_tag_"]) { flex:0 0 auto !important; }
[class*="st-key-pacs_asg_opt_name_"] { font-weight:700; overflow:hidden; }
[class*="st-key-pacs_asg_opt_name_"] p { overflow:hidden; text-overflow:ellipsis; }
[class*="st-key-pacs_asg_opt_tag_"] {
  padding:calc(1 * var(--u)) calc(7 * var(--u)) !important; border:1px solid #cfcfcf; border-radius:calc(9 * var(--u)); background:#f5f5f5;
  font-size:calc(10 * var(--u)); line-height:calc(15 * var(--u)); font-weight:500; color:#555;
}
[class*="st-key-pacs_asg_opt_count_"] { right:calc(14 * var(--u)); font-weight:500; }
:is([class*="st-key-pacs_asg_opt_on_"], [class*="st-key-pacs_asg_opt_off_"]) [data-testid="stElementContainer"]:has([data-testid="stButton"]) {
  position:absolute !important; inset:0; z-index:2; width:100% !important; height:100% !important;
}
:is([class*="st-key-pacs_asg_opt_on_"], [class*="st-key-pacs_asg_opt_off_"]) :is([data-testid="stButton"], button) {
  width:100% !important; height:100% !important; min-height:0 !important;
}
:is([class*="st-key-pacs_asg_opt_on_"], [class*="st-key-pacs_asg_opt_off_"]) button {
  padding:0 !important; border:0 !important; border-radius:calc(5 * var(--u)) !important; box-shadow:none !important; background:transparent !important; cursor:pointer;
}
:is([class*="st-key-pacs_asg_opt_on_"], [class*="st-key-pacs_asg_opt_off_"]) button p { font-size:0 !important; }
/* 아래 버튼: 오른쪽 정렬, 높이 40, 사이 8 */
.st-key-pacs_asg_stage :is(.st-key-pacs_asg_dlg_actions, .st-key-pacs_asg_res_actions) {
  flex-wrap:nowrap !important; justify-content:flex-end !important; gap:calc(8 * var(--u)) !important;
}
:is(.st-key-pacs_asg_dlg_actions, .st-key-pacs_asg_res_actions) > div { flex:0 0 auto !important; width:auto !important; }
:is(.st-key-pacs_asg_dlg_cancel, .st-key-pacs_asg_dlg_submit, .st-key-pacs_asg_res_ok) [data-testid="stButton"] { display:flex !important; }
:is(.st-key-pacs_asg_dlg_cancel, .st-key-pacs_asg_dlg_submit) button {
  height:calc(40 * var(--u)) !important; padding:0 calc(18 * var(--u)) !important;
}
:is(.st-key-pacs_asg_dlg_cancel, .st-key-pacs_asg_dlg_submit, .st-key-pacs_asg_res_ok) button {
  width:auto !important; min-height:0 !important; border-radius:calc(5 * var(--u)) !important; box-shadow:none !important;
  cursor:pointer; white-space:nowrap; font-size:calc(12 * var(--u)) !important; line-height:calc(18 * var(--u)) !important;
}
:is(.st-key-pacs_asg_dlg_cancel, .st-key-pacs_asg_dlg_submit, .st-key-pacs_asg_res_ok) button p {
  font-size:calc(12 * var(--u)) !important; line-height:calc(18 * var(--u)) !important; font-weight:500 !important;
}
.st-key-pacs_asg_dlg_cancel button { background:#fff !important; border:1px solid #dadada !important; color:#333 !important; }
.st-key-pacs_asg_dlg_cancel button:hover { background:#f5f5f5 !important; }
:is(.st-key-pacs_asg_dlg_submit, .st-key-pacs_asg_res_ok) button { background:#242424 !important; border:1px solid #242424 !important; color:#fff !important; }
:is(.st-key-pacs_asg_dlg_submit, .st-key-pacs_asg_res_ok) button:hover:enabled { background:#3a3a3a !important; }
.st-key-pacs_asg_dlg_submit button:disabled { background:#cfcfcf !important; border-color:#cfcfcf !important; cursor:not-allowed; }

/* ── 결과 안내 창(Figma "배정 완료 안내", "배정 실패 안내"): 400 폭, 안쪽 여백 28, 줄 간격 14 ── */
.st-key-pacs_asg_stage :is(.st-key-pacs_asg_res_done, .st-key-pacs_asg_res_fail) {
  position:fixed !important; z-index:51; left:50%; top:50%; transform:translate(-50%, -50%);
  width:calc(400 * var(--u)) !important; max-width:calc(100vw - 32px) !important;
  padding:calc(28 * var(--u)) !important; gap:calc(14 * var(--u)) !important;
  background:#fff; border:1px solid #ddd; border-radius:calc(8 * var(--u)); box-shadow:0 calc(8 * var(--u)) calc(24 * var(--u)) rgba(0, 0, 0, 0.12);
}
:is(.st-key-pacs_asg_res_done, .st-key-pacs_asg_res_fail) > div { width:100% !important; }
.st-key-pacs_asg_res_title { font-size:calc(19 * var(--u)); line-height:calc(29 * var(--u)); font-weight:700; color:#333; }
.st-key-pacs_asg_res_message { font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#666; }
.st-key-pacs_asg_res_detail { font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#737373; }
.st-key-pacs_asg_res_ok button { height:calc(38 * var(--u)) !important; padding:0 calc(16 * var(--u)) !important; }

/* 로그아웃 확인창(검사 목록 화면과 같음): 440 폭, 안쪽 여백 28, 줄 간격 20 */
.st-key-pacs_asg_stage .st-key-pacs_asg_modal_logout {
  position:fixed !important; z-index:41; left:50%; top:50%; transform:translate(-50%, -50%);
  width:calc(440 * var(--u)) !important; max-width:calc(100vw - 32px) !important;
  padding:calc(28 * var(--u)) !important; gap:calc(20 * var(--u)) !important;
  background:#fff; border:1px solid #dedede; border-radius:calc(8 * var(--u));
  box-shadow:0 calc(8 * var(--u)) calc(24 * var(--u)) rgba(0, 0, 0, 0.12); line-height:1.5;
}
.st-key-pacs_asg_modal_logout > div { width:100% !important; }
.st-key-pacs_asg_stage .st-key-pacs_asg_modal_head { flex-wrap:nowrap !important; align-items:center !important; }
.st-key-pacs_asg_modal_head > div:first-child { flex:1 1 auto !important; min-width:0; }
.st-key-pacs_asg_modal_title { font-size:calc(20 * var(--u)); font-weight:700; color:#252525; }
.st-key-pacs_asg_modal_head > div:last-child { flex:0 0 auto !important; }
.st-key-pacs_asg_modal_close, .st-key-pacs_asg_modal_close :is([data-testid="stButton"], button) {
  width:calc(20 * var(--u)) !important; height:calc(20 * var(--u)) !important;
}
.st-key-pacs_asg_modal_close [data-testid="stButton"] { display:flex !important; line-height:0; }
.st-key-pacs_asg_modal_close button {
  min-height:0 !important; padding:0 !important; border:0 !important; box-shadow:none !important; cursor:pointer;
  background:transparent @ICON_CLOSE@ center / calc(20 * var(--u)) no-repeat !important;
}
.st-key-pacs_asg_modal_close button p { font-size:0 !important; }
.st-key-pacs_asg_modal_body { font-size:calc(13 * var(--u)); color:#626262; }
.st-key-pacs_asg_stage .st-key-pacs_asg_modal_actions { flex-wrap:nowrap !important; justify-content:flex-end !important; gap:calc(8 * var(--u)) !important; }
.st-key-pacs_asg_modal_actions > div { flex:0 0 auto !important; width:auto !important; }
:is(.st-key-pacs_asg_modal_cancel, .st-key-pacs_asg_modal_confirm) [data-testid="stButton"] { display:flex !important; }
:is(.st-key-pacs_asg_modal_cancel, .st-key-pacs_asg_modal_confirm) button {
  width:auto !important; height:calc(42 * var(--u)) !important; min-height:0 !important; padding:0 calc(16 * var(--u)) !important;
  border-radius:calc(5 * var(--u)) !important; box-shadow:none !important; cursor:pointer;
  font-size:calc(13 * var(--u)) !important; font-weight:500 !important; white-space:nowrap;
}
:is(.st-key-pacs_asg_modal_cancel, .st-key-pacs_asg_modal_confirm) button p { font-size:inherit !important; font-weight:inherit !important; }
.st-key-pacs_asg_modal_cancel button { background:#fff !important; border:1px solid #dedede !important; color:#252525 !important; }
.st-key-pacs_asg_modal_cancel button:hover { background:#f5f5f5 !important; }
.st-key-pacs_asg_modal_confirm button { background:#252525 !important; border:1px solid #252525 !important; color:#fff !important; }
.st-key-pacs_asg_modal_confirm button:hover { background:#3a3a3a !important; }
</style>
"""

_STYLE = (
    _STYLE.replace("@ICON_CHEVRON_RIGHT@", _list._css_icon(_list._ICON_CHEVRON_RIGHT))
    .replace("@ICON_LOGOUT@", _list._css_icon(_list._ICON_LOGOUT))
    .replace("@ICON_SEARCH_FIELD@", _list._css_icon(_list._ICON_SEARCH_FIELD))
    .replace("@ICON_CALENDAR@", _list._css_icon(_list._ICON_CALENDAR))
    .replace("@ICON_SEARCH_BUTTON@", _list._css_icon(_list._ICON_SEARCH_BUTTON))
    .replace("@ICON_CLOSE@", _list._css_icon(_list._ICON_CLOSE))
    .replace("@ICON_MONTH_PREV@", _list._css_icon(_list._ICON_MONTH_PREV))
    .replace("@ICON_MONTH_NEXT@", _list._css_icon(_list._ICON_MONTH_NEXT))
)


# ── 이벤트 처리 (버튼 콜백) ──────────────────────────────────────────────────

def _emit(kind: str) -> None:
    st.session_state[_EVENT_KEY] = ReviewAssignEvent(kind=kind)


def _current_tab() -> str:
    tab = st.session_state.get(_TAB_KEY)
    return tab if tab in TAB_STATUS else TAB_UNASSIGNED


def _selected() -> str | None:
    return st.session_state.get(_SELECTED_KEY)


def _switch_tab(tab: str) -> None:
    """탭을 바꾸면 체크를 지웁니다(다른 탭의 검사가 배정 창에 열리지 않게)."""
    if tab != _current_tab():
        st.session_state[_TAB_KEY] = tab
        st.session_state.pop(_SELECTED_KEY, None)


def _toggle(case_id: str) -> None:
    """줄을 누르면 그 검사만 체크합니다(기존 체크는 풀림). 체크된 줄을 다시 누르면 풀립니다."""
    if _selected() == case_id:
        st.session_state.pop(_SELECTED_KEY, None)
    else:
        st.session_state[_SELECTED_KEY] = case_id


def _clear_dialog() -> None:
    for key in (_DIALOG_KEY, _PICK_KEY):
        st.session_state.pop(key, None)


def _on_assign() -> None:
    """배정 버튼: 업무 담당자인지 다시 확인하고, 체크한 검사의 배정 창(배정 완료 탭이면 재배정 창)을 엽니다."""
    user = st.session_state.get(_USER_KEY)
    if not isinstance(user, CurrentUser) or not can_assign(user):
        st.session_state[_NOTICE_KEY] = MSG_NEED_ADMIN
        return
    case_id = _selected()
    if not case_id:
        return
    _cal_close()
    _clear_dialog()
    st.session_state[_DIALOG_KEY] = AssignTarget(case_id=case_id, reassign=_current_tab() == TAB_ASSIGNED)


def _pick_reviewer(uid: str) -> None:
    st.session_state[_PICK_KEY] = uid


def _close_assign() -> None:
    """배정 창의 ×·취소·바깥: 저장하지 않고 닫습니다(체크는 그대로)."""
    _clear_dialog()


def _show_result(ok: bool, reassign: bool, lines: tuple[str, str]) -> None:
    st.session_state[_RESULT_KEY] = AssignResult(ok=ok, reassign=reassign, lines=lines)


def _close_result() -> None:
    st.session_state.pop(_RESULT_KEY, None)


def _on_assign_submit() -> None:
    """배정하기: 최신 상태를 다시 확인하고 저장한 뒤, 완료·실패 안내 창을 띄웁니다.

    - 재배정에서 기존 검토자를 고르면 저장하지 않고 실패 안내를 띄우고, 배정 창은 그대로 둡니다(다시 고르기).
    - 그 사이 검사 상태가 바뀌었거나 저장에 실패하면 배정 창을 닫고 실패 안내를 띄웁니다.
    - 성공하면 체크와 배정 창을 지우고, 목록·검토자 업무 현황을 새로 불러옵니다.
    - 담당자는 저장했지만 이력을 기록하지 못하면 완료로 안내하되, 이력을 기록하지 못했다고 알립니다.
    """
    state = st.session_state
    target = state.get(_DIALOG_KEY)
    user = state.get(_USER_KEY)
    reviewer_uid = state.get(_PICK_KEY)
    if not isinstance(target, AssignTarget) or not isinstance(user, CurrentUser) or not reviewer_uid:
        return
    reassign = target.reassign
    if not can_assign(user):
        _clear_dialog()
        _show_result(False, reassign, FAIL_NOT_ADMIN)
        return

    _list.refresh_exams()  # 다른 사람이 그 사이 배정했을 수 있으니 최신 목록으로 확인합니다.
    exams, _ = _list._get_exams(user)
    exam = next((e for e in exams if e.case_id == target.case_id), None)
    expected = STATUS_ASSIGNED if reassign else STATUS_UNASSIGNED
    if exam is None or exam.status != expected:
        _clear_dialog()
        state.pop(_SELECTED_KEY, None)
        _show_result(False, reassign, FAIL_CASE_CHANGED)
        return
    previous_uid = exam.assignee_uid or ""
    if reassign and reviewer_uid == previous_uid:
        _show_result(False, reassign, FAIL_SAME_REVIEWER)
        return

    request = AssignRequest(
        case_id=target.case_id, reviewer_uid=reviewer_uid, previous_uid=previous_uid, reassign=reassign,
    )
    retry_pending_history()  # 전에 기록하지 못한 이력이 있으면 먼저 다시 저장해 봅니다.
    try:
        save_assignment(user, request)
    except HistoryNotSavedError:
        # 담당자는 바뀌었으므로 완료로 안내하고, 이력은 세션에 남겨 나중에 다시 저장합니다.
        _clear_dialog()
        state.pop(_SELECTED_KEY, None)
        _list.refresh_exams()
        refresh_reviewers()
        _show_result(True, reassign, DONE_HISTORY_PENDING)
        return
    except NotImplementedError:
        _logger.info("배정 저장 기능(save_case_assignment 등)이 아직 구현되지 않았습니다.")
        lines = FAIL_NOT_READY
    except (AppError, PermissionError) as error:
        lines = (str(error) or FAIL_SAVE[0], FAIL_SAVE[1])
    except Exception:
        _logger.exception("배정 저장 실패")
        lines = FAIL_SAVE
    else:
        _clear_dialog()
        state.pop(_SELECTED_KEY, None)
        _list.refresh_exams()
        refresh_reviewers()
        _show_result(True, reassign, DONE_LINES[reassign])
        return
    _clear_dialog()
    _list.refresh_exams()  # 응답만 늦게 실패한 경우 실제로는 저장됐을 수 있으니 목록을 새로 불러옵니다.
    _show_result(False, reassign, lines)


def _open_history() -> None:
    _cal_close()
    st.session_state[_HISTORY_OPEN_KEY] = True
    st.session_state.pop(_HISTORY_CACHE_KEY, None)  # 열 때마다 최신 이력을 불러옵니다.
    retry_pending_history()  # 기록하지 못한 이력이 있으면 먼저 다시 저장해 이번 목록에 나오게 합니다.


def _close_history() -> None:
    st.session_state.pop(_HISTORY_OPEN_KEY, None)
    st.session_state.pop(_HISTORY_CACHE_KEY, None)


def _ask_logout() -> None:
    _cal_close()
    _close_history()
    st.session_state[_CONFIRM_KEY] = True


def _close_confirm() -> None:
    st.session_state.pop(_CONFIRM_KEY, None)


def _on_confirm_logout() -> None:
    _close_confirm()
    _emit(EVENT_LOGOUT)


def _pending_dates() -> tuple[date, date]:
    """등록일 칸에 지금 들어 있는 기간(검색 버튼을 누르기 전). 고른 적이 없으면 적용된 조건의 기간입니다."""
    dates = st.session_state.get(_DATES_KEY)
    if dates:
        return dates
    applied = st.session_state.get(_APPLIED_KEY) or default_filter()
    return applied.start, applied.end


def _on_search() -> None:
    """입력한 검색어와 등록일 기간을 적용합니다. 입력칸 값은 그대로 두고, 선택은 지웁니다."""
    _cal_close()
    start, end = _pending_dates()
    if start > end:
        start, end = end, start
    query = str(st.session_state.get(_QUERY_KEY) or "").strip()
    st.session_state[_APPLIED_KEY] = AssignFilter(query, start, end)
    st.session_state.pop(_SELECTED_KEY, None)
    _list.refresh_exams()  # 검색할 때는 최신 목록으로 다시 불러옵니다.
    refresh_reviewers()


# 등록일 달력(검사 목록 화면과 같은 동작)

def _cal_toggle() -> None:
    state = st.session_state
    if state.get(_CAL_OPEN_KEY):
        _cal_close()
        return
    start, end = _pending_dates()
    state[_CAL_OPEN_KEY] = True
    state[_CAL_DRAFT_KEY] = (start, end)
    state[_CAL_MONTH_KEY] = _list._month_start(end)


def _cal_close() -> None:
    for key in (_CAL_OPEN_KEY, _CAL_DRAFT_KEY, _CAL_MONTH_KEY):
        st.session_state.pop(key, None)


def _cal_pick(day: date) -> None:
    """첫 번째로 누른 날은 시작일, 두 번째는 종료일(앞 날짜를 누르면 둘을 바꿔 정렬)."""
    state = st.session_state
    start, end = state.get(_CAL_DRAFT_KEY) or (day, day)
    if end is not None:
        state[_CAL_DRAFT_KEY] = (day, None)
    elif day < start:
        state[_CAL_DRAFT_KEY] = (day, start)
    else:
        state[_CAL_DRAFT_KEY] = (start, day)
    if _list._month_start(day) != state.get(_CAL_MONTH_KEY):
        state[_CAL_MONTH_KEY] = _list._month_start(day)


def _cal_preset(days: int) -> None:
    st.session_state[_CAL_DRAFT_KEY] = _list._preset_range(days)
    st.session_state[_CAL_MONTH_KEY] = _list._month_start(_list._today())


def _cal_move(step: int) -> None:
    state = st.session_state
    state[_CAL_MONTH_KEY] = _list._shift_month(state.get(_CAL_MONTH_KEY) or _list._month_start(_list._today()), step)


def _cal_apply() -> None:
    """고른 기간을 등록일 칸에 넣고 달력을 닫습니다. 목록에는 검색 버튼을 누를 때 적용됩니다."""
    draft = st.session_state.get(_CAL_DRAFT_KEY)
    if draft:
        start, end = draft
        st.session_state[_DATES_KEY] = (start, end or start)
    _cal_close()


# ── 화면 그리기 ──────────────────────────────────────────────────────────────

_text = _list._text


@st.fragment(run_every=1)
def _render_clock() -> None:
    """상단 바의 실시간 시계. 1초마다 이 부분만 다시 그립니다."""
    _text("pacs_asg_clock", f"{datetime.now(KST):%Y. %m. %d. %H:%M:%S}")


def _render_header(user: CurrentUser) -> None:
    with st.container(key="pacs_asg_header"):
        _text("pacs_asg_crumb_home", "워크스페이스")
        _text("pacs_asg_crumb_page", "검토 배정")
        with st.container(key="pacs_asg_user"):
            _text("pacs_asg_user_role", role_label(user))
            _text("pacs_asg_user_name", user.name or USER_NAME_FALLBACK)
        _render_clock()
        with st.container(key="pacs_asg_logout"):
            st.button("로그아웃", key="pacs_asg_logout_button", on_click=_ask_logout)


def _render_title() -> None:
    with st.container(key="pacs_asg_title"):
        with st.container(key="pacs_asg_title_text"):
            st.title("검토 배정", anchor=False)
        _text("pacs_asg_subtitle", "검토자 업무 현황을 확인하고 검사에 검토 담당자를 배정하세요.")
    with st.container(key="pacs_asg_history_open"):
        st.button("배정 이력", key="pacs_asg_history_button", on_click=_open_history)


def _render_stats(exams: list[ExamRecord]) -> None:
    counts = {status: sum(1 for e in exams if e.status == status) for status in _list.STATUS_OPTIONS}
    cards = [
        ("total", "전체 검사", len(exams), "접수된 검사", False),
        ("unassigned", STATUS_UNASSIGNED, counts[STATUS_UNASSIGNED], "배정 대기 중", True),
        ("assigned", STATUS_ASSIGNED, counts[STATUS_ASSIGNED], "검토 대기 중", True),
        ("reviewing", STATUS_REVIEWING, counts[STATUS_REVIEWING], "진행 중", True),
        ("done", STATUS_DONE, counts[STATUS_DONE], "", False),
    ]
    with st.container(key="pacs_asg_stats", horizontal=True):
        for name, label, value, note, accent in cards:
            with st.container(key=f"pacs_asg_stat_card_{'accent_' if accent else ''}{name}"):
                _text(f"pacs_asg_stat_label_{name}", label)
                _text(f"pacs_asg_stat_value_{name}", value)
                if note:
                    _text(f"pacs_asg_stat_note_{name}", note)


def _render_calendar() -> None:
    """등록일 기간 선택 달력(검사 목록 화면과 같은 디자인). 적용을 눌러야 등록일 칸에 들어갑니다."""
    state = st.session_state
    start, end = state.get(_CAL_DRAFT_KEY) or _pending_dates()
    month = state.get(_CAL_MONTH_KEY) or _list._month_start(end or start)
    last = end or start

    st.button("달력 닫기", key="pacs_asg_cal_backdrop", on_click=_cal_close)
    with st.container(key="pacs_asg_cal"):
        with st.container(key="pacs_asg_cal_head", horizontal=True):
            _text("pacs_asg_cal_title", "등록일 선택")
            st.button("달력 닫기", key="pacs_asg_cal_close", on_click=_cal_close)
        with st.container(key="pacs_asg_cal_range", horizontal=True):
            for name, label, value in (("start", "시작일", start), ("end", "종료일", end)):
                with st.container(key=f"pacs_asg_cal_box_{name}"):
                    _text(f"pacs_asg_cal_boxlabel_{name}", label)
                    _text(f"pacs_asg_cal_boxvalue_{name}", f"{value:%Y.%m.%d}" if value else "-")
        with st.container(key="pacs_asg_cal_presets", horizontal=True):
            for name, label, days in CALENDAR_PRESETS:
                active = (start, end) == _list._preset_range(days)
                with st.container(key=f"pacs_asg_cal_preset_{'on' if active else 'off'}_{name}"):
                    st.button(label, key=f"pacs_asg_cal_presetbtn_{name}", on_click=_cal_preset, args=(days,))
        _text("pacs_asg_cal_rule_top", " ")
        with st.container(key="pacs_asg_cal_nav", horizontal=True):
            st.button("이전 달", key="pacs_asg_cal_prev", on_click=_cal_move, args=(-1,))
            _text("pacs_asg_cal_month", f"{month.year}년 {month.month}월", align="center")
            st.button("다음 달", key="pacs_asg_cal_next", on_click=_cal_move, args=(1,))
        with st.container(key="pacs_asg_cal_grid"):
            with st.container(key="pacs_asg_cal_weekdays", horizontal=True):
                for index, label in enumerate(WEEKDAY_LABELS):
                    _text(f"pacs_asg_cal_wd_{index}", label, align="center")
            for week_index, week in enumerate(_list._calendar_weeks(month)):
                with st.container(key=f"pacs_asg_cal_week_{week_index}", horizontal=True):
                    for day in week:
                        if day in (start, end):
                            kind = "sel"
                        elif end is not None and start < day < end:
                            kind = "mid"
                        elif day.month != month.month:
                            kind = "out"
                        else:
                            kind = "in"
                        with st.container(key=f"pacs_asg_cal_d_{kind}_{day:%Y%m%d}"):
                            st.button(str(day.day), key=f"pacs_asg_cal_pick_{day:%Y%m%d}", on_click=_cal_pick, args=(day,))
        _text("pacs_asg_cal_rule_bottom", " ")
        with st.container(key="pacs_asg_cal_foot", horizontal=True):
            if end is None or end == start:
                summary = f"{start:%Y.%m.%d} 선택"
            else:
                summary = f"{start:%Y.%m.%d} – {last:%Y.%m.%d} 선택 · {(last - start).days + 1}일"
            _text("pacs_asg_cal_summary", summary)
            st.button("적용", key="pacs_asg_cal_apply", on_click=_cal_apply)


def _render_filters() -> None:
    is_calendar_open = bool(st.session_state.get(_CAL_OPEN_KEY))
    start, end = _pending_dates()
    with st.container(key="pacs_asg_filters", horizontal=True):
        with st.container(key="pacs_asg_field_query"):
            _text("pacs_asg_label_query", "환자 검색")
            st.text_input(
                "환자 검색", key=_QUERY_KEY, placeholder="환자명, 환자 ID, 케이스 ID 검색",
                max_chars=64, label_visibility="collapsed",
            )
        with st.container(key="pacs_asg_field_dates"):
            _text("pacs_asg_label_dates", "등록일")
            with st.container(key=f"pacs_asg_dates_box_{'open' if is_calendar_open else 'closed'}"):
                st.button(
                    _list._md(f"{start:%Y.%m.%d}  –  {end:%Y.%m.%d}"),
                    key="pacs_asg_dates_button", width="stretch", on_click=_cal_toggle,
                )
            if is_calendar_open:
                _render_calendar()
        with st.container(key="pacs_asg_field_search"):
            st.button("검색", key="pacs_asg_search_button", width="stretch", on_click=_on_search)


def _render_row(index: int, exam: ExamRecord, selectable: bool, checked: bool) -> None:
    """검사 한 줄. 업무 담당자는 줄을 눌러 체크(한 건만)할 수 있고, 다른 역할은 체크박스 없이 보기만 합니다."""
    kind = ("selrow" if checked else "row") if selectable else "viewrow"
    with st.container(key=f"pacs_asg_{kind}_{index}"):
        _text(f"pacs_asg_cname_{index}", exam.patient_name)
        _text(f"pacs_asg_cmeta_{index}", _list._patient_meta(exam))
        _text(f"pacs_asg_cpid_{index}", exam.patient_id, align="center")
        _text(f"pacs_asg_cexam_{index}", exam.exam_name, align="center")
        _text(f"pacs_asg_ctime_{index}", exam.registered_at.astimezone(KST).strftime("%m.%d %H:%M"), align="center")
        if selectable:
            st.button(
                f"{exam.patient_name} 검사 {'선택 해제' if checked else '선택'}",
                key=f"pacs_asg_pick_{index}_{exam.case_id}", width="stretch", on_click=_toggle, args=(exam.case_id,),
            )


def _render_table(user: CurrentUser, tabs: dict[str, list[ExamRecord]], tab: str, selected: str | None, load_failed: bool) -> None:
    selectable = can_assign(user)
    rows = tabs[tab]
    with st.container(key="pacs_asg_table"):
        with st.container(key="pacs_asg_tabbar"):
            for name in (TAB_UNASSIGNED, TAB_ASSIGNED):
                with st.container(key=f"pacs_asg_tab_{'on' if name == tab else 'off'}_{name}"):
                    st.button(
                        f"{TAB_STATUS[name]}  {len(tabs[name])}", key=f"pacs_asg_tabbtn_{name}",
                        on_click=_switch_tab, args=(name,),
                    )
            if selectable:  # 배정 버튼은 업무 담당자에게만 보입니다. 체크한 검사가 없으면 꺼집니다.
                with st.container(key="pacs_asg_assign"):
                    st.button(
                        "배정", key="pacs_asg_assign_button", width="stretch",
                        disabled=not selected, on_click=_on_assign,
                    )
        with st.container(key="pacs_asg_thead"):
            for name, label in (("name", "환자명"), ("pid", "환자 ID"), ("exam", "검사명"), ("time", "등록 시간 ↓")):
                _text(f"pacs_asg_th_{name}", label, align="center" if name != "name" else "left")
        with st.container(key="pacs_asg_rows"):
            if load_failed or not rows:
                message = _list.MSG_LOAD_FAILED if load_failed else (MSG_NO_UNASSIGNED if tab == TAB_UNASSIGNED else MSG_NO_ASSIGNED)
                _text("pacs_asg_empty", message, align="center")
                return
            for index, exam in enumerate(rows):
                _render_row(index, exam, selectable, exam.case_id == selected)


def _render_workload(loads: list[ReviewerLoad], failed: bool) -> None:
    with st.container(key="pacs_asg_load"):
        _text("pacs_asg_load_title", "검토자 업무 현황")
        _text("pacs_asg_load_sub", "남은 검토(배정 완료 + 검토 중) / 배정된 전체 검사")
        with st.container(key="pacs_asg_load_list"):
            if failed or not loads:
                _text("pacs_asg_load_empty", MSG_REVIEWERS_FAILED if failed else MSG_NO_REVIEWERS, align="center")
            for index, load in enumerate(loads):
                with st.container(key=f"pacs_asg_rv_item_{index}"):
                    _text(f"pacs_asg_rv_name_{index}", load.name)
                    _text(f"pacs_asg_rv_wait_{index}", "남은 검토")
                    with st.container(key=f"pacs_asg_rv_count_{index}"):
                        st.markdown(f"**{load.pending}** / {load.total}건")
                    with st.container(key=f"pacs_asg_rv_bar_{index}"):
                        # 막대 길이 = 남은 검토 / 지금까지 배정된 전체 (숫자로만 만든 HTML)
                        st.markdown(
                            f'<div class="pacs-asg-bar"><div style="width:{round(load.ratio * 100, 1)}%"></div></div>',
                            unsafe_allow_html=True,
                        )
        with st.container(key="pacs_asg_load_note"):
            _text("pacs_asg_load_note_title", "배정 전 확인")
            _text("pacs_asg_load_note_body", "현재 검토자의 업무량을 확인하고,\n검사를 배정하세요.")


def _day_label(day: date) -> str:
    """배정 이력의 날짜 줄: 2026.11.12  ·  오늘 / 어제 / 요일"""
    today = _list._today()
    if day == today:
        suffix = "오늘"
    elif day == today - timedelta(days=1):
        suffix = "어제"
    else:
        suffix = f"{WEEKDAY_LABELS[(day.weekday() + 1) % 7]}요일"
    return f"{day:%Y.%m.%d}  ·  {suffix}"


def _render_history(entries: list[HistoryEntry], failed: bool) -> None:
    """배정 이력 창(Figma "배정 이력"): 날짜별로 묶어 최신순으로 보여주고, 목록은 스크롤합니다."""
    st.button("배정 이력 닫기", key="pacs_asg_hist_backdrop", on_click=_close_history)
    with st.container(key="pacs_asg_hist"):
        with st.container(key="pacs_asg_hist_head", horizontal=True):
            _text("pacs_asg_hist_title", "배정 이력")
            with st.container(key="pacs_asg_hist_close"):
                st.button("×", key="pacs_asg_hist_close_button", on_click=_close_history)
        _text("pacs_asg_hist_sub", "검토자 지정 및 변경 내역을 확인하세요.")
        pending = pending_history_count()
        if pending:
            _text("pacs_asg_hist_pending", MSG_HISTORY_PENDING.format(count=pending))
        with st.container(key="pacs_asg_hist_list"):
            if failed or not entries:
                _text("pacs_asg_hist_empty", MSG_HISTORY_FAILED if failed else MSG_HISTORY_EMPTY, align="center")
                return
            current_day = None
            for index, entry in enumerate(entries):
                at = entry.at.astimezone(KST)
                if at.date() != current_day:
                    current_day = at.date()
                    _text(f"pacs_asg_hist_day_{index}", _day_label(current_day))
                with st.container(key=f"pacs_asg_hist_item_{index}"):
                    _text(f"pacs_asg_hist_when_{index}", f"{at:%H:%M}  ·  {entry.changed_by}")
                    _text(f"pacs_asg_hist_case_{index}", f"{entry.patient_name}  ·  {entry.exam_name or '-'}")
                    _text(f"pacs_asg_hist_change_{index}", f"{entry.before} → {entry.after}")
                    _text(f"pacs_asg_hist_kind_{index}", "재배정" if entry.reassign else "신규 배정")


def _reviewer_name(uid: str, loads: list[ReviewerLoad]) -> str:
    return next((load.name for load in loads if load.uid == uid), uid) or "-"


def _render_assign(target: AssignTarget, exam: ExamRecord, loads: list[ReviewerLoad], reviewers_failed: bool) -> None:
    """배정 창(Figma "검사 배정", "검사 배정(재배정)"). 검토자를 하나 골라야 배정하기 버튼이 켜집니다."""
    picked = st.session_state.get(_PICK_KEY)
    st.button("배정 창 닫기", key="pacs_asg_dlg_backdrop", on_click=_close_assign)
    with st.container(key="pacs_asg_dlg"):
        with st.container(key="pacs_asg_dlg_head", horizontal=True):
            _text("pacs_asg_dlg_title", ASSIGN_TITLES[target.reassign])
            with st.container(key="pacs_asg_dlg_close"):
                st.button("×", key="pacs_asg_dlg_close_button", on_click=_close_assign)
        _text("pacs_asg_dlg_guide", ASSIGN_GUIDE)
        with st.container(key="pacs_asg_dlg_case"):
            _text("pacs_asg_dlg_case_title", "선택한 검사 1건")
            with st.container(key="pacs_asg_dlg_caserow", horizontal=True):
                _text("pacs_asg_dlg_case_name", exam.patient_name)
                _text("pacs_asg_dlg_case_exam", exam.exam_name or "-")
        if target.reassign:
            with st.container(key="pacs_asg_dlg_prev"):
                _text("pacs_asg_dlg_prev_title", "기존 검토자")
                _text("pacs_asg_dlg_prev_name", _reviewer_name(exam.assignee_uid or "", loads))
        with st.container(key="pacs_asg_dlg_pick"):
            _text("pacs_asg_dlg_pick_title", "검토자 선택 *")
            with st.container(key="pacs_asg_dlg_opts"):
                if not loads:
                    _text("pacs_asg_dlg_opts_empty", MSG_REVIEWERS_FAILED if reviewers_failed else MSG_NO_REVIEWERS)
                for index, load in enumerate(loads):
                    with st.container(key=f"pacs_asg_opt_{'on' if load.uid == picked else 'off'}_{index}"):
                        with st.container(key=f"pacs_asg_opt_who_{index}", horizontal=True):
                            _text(f"pacs_asg_opt_name_{index}", load.name)
                            if target.reassign and load.uid == exam.assignee_uid:
                                _text(f"pacs_asg_opt_tag_{index}", CURRENT_REVIEWER_TAG)
                        _text(f"pacs_asg_opt_count_{index}", f"{load.pending} / {load.total}건")
                        st.button(
                            f"{load.name} 선택", key=f"pacs_asg_optbtn_{index}_{load.uid}",
                            width="stretch", on_click=_pick_reviewer, args=(load.uid,),
                        )
        with st.container(key="pacs_asg_dlg_actions", horizontal=True):
            st.button("취소", key="pacs_asg_dlg_cancel", on_click=_close_assign)
            st.button(
                "배정하기", key="pacs_asg_dlg_submit",
                disabled=not any(load.uid == picked for load in loads), on_click=_on_assign_submit,
            )


def _render_result(result: AssignResult) -> None:
    """배정 결과 안내 창(Figma "배정 완료 안내", "배정 실패 안내"): 확인·바깥을 누르면 닫힙니다."""
    title = (DONE_TITLES if result.ok else FAIL_TITLES)[result.reassign]
    message, detail = result.lines
    st.button("안내 닫기", key="pacs_asg_res_backdrop", on_click=_close_result)
    with st.container(key=f"pacs_asg_res_{'done' if result.ok else 'fail'}"):
        _text("pacs_asg_res_title", title)
        _text("pacs_asg_res_message", message)
        _text("pacs_asg_res_detail", detail)
        with st.container(key="pacs_asg_res_actions", horizontal=True):
            st.button("확인", key="pacs_asg_res_ok", on_click=_close_result)


def _render_logout_confirm() -> None:
    title, lines, confirm_label = LOGOUT_TEXTS
    st.button("확인창 닫기", key="pacs_asg_modal_backdrop", on_click=_close_confirm)
    with st.container(key="pacs_asg_modal_logout"):
        with st.container(key="pacs_asg_modal_head", horizontal=True):
            _text("pacs_asg_modal_title", title)
            st.button("확인창 닫기", key="pacs_asg_modal_close", on_click=_close_confirm)
        _text("pacs_asg_modal_body", "\n".join(lines))
        with st.container(key="pacs_asg_modal_actions", horizontal=True):
            st.button("취소", key="pacs_asg_modal_cancel", on_click=_close_confirm)
            st.button(confirm_label, key="pacs_asg_modal_confirm", on_click=_on_confirm_logout)


def render(user: CurrentUser) -> ReviewAssignEvent | None:
    """검토 배정 화면을 그립니다. 다른 화면으로 이동할 버튼이 눌리면 ReviewAssignEvent를 돌려줍니다."""
    st.html(_STYLE)
    state = st.session_state
    state[_USER_KEY] = user

    notice = state.pop(_NOTICE_KEY, None)
    if notice:
        st.toast(notice)

    exams, load_failed = _list._get_exams(user)
    reviewers, reviewers_failed = _get_reviewers(user)
    condition = state.get(_APPLIED_KEY) or default_filter()
    tabs = split_by_tab(filter_exams(exams, condition))
    tab = _current_tab()

    # 검색·상태 변경으로 탭에서 사라진 검사는 체크를 풉니다. 업무 담당자가 아니면 체크하지 않습니다.
    selected = _selected()
    if selected and (not can_assign(user) or selected not in {e.case_id for e in tabs[tab]}):
        state.pop(_SELECTED_KEY, None)
        selected = None
    loads = reviewer_workload(reviewers, exams)
    # 배정 창: 업무 담당자만, 연 검사가 아직 목록에 있을 때만 그립니다.
    target = state.get(_DIALOG_KEY)
    target_exam = None
    if isinstance(target, AssignTarget):
        target_exam = next((e for e in exams if e.case_id == target.case_id), None)
        if target_exam is None or not can_assign(user):
            _clear_dialog()
            target = None

    with st.container(key="pacs_asg_stage"):
        _render_header(user)
        with st.container(key="pacs_asg_main"):
            _render_title()
            _render_stats(exams)
            _render_filters()
            _render_table(user, tabs, tab, selected, load_failed)
            _render_workload(loads, reviewers_failed)
        if state.get(_HISTORY_OPEN_KEY):
            _render_history(*_get_history(exams, reviewers))
        if target is not None and target_exam is not None:
            _render_assign(target, target_exam, loads, reviewers_failed)
        result = state.get(_RESULT_KEY)
        if isinstance(result, AssignResult):
            _render_result(result)
        if state.get(_CONFIRM_KEY):
            _render_logout_confirm()

    return state.pop(_EVENT_KEY, None)


if __name__ == "__main__":
    # 단독 실행 시 미리보기 사용자(업무 담당자)로 화면을 봅니다. 백엔드 연결 전이라 목록은 비어 있을 수 있습니다.
    st.set_page_config(page_title="PACS 검토 배정", layout="wide")
    # 왼쪽 사이드바는 app.py가 그리는 공통 부품이라, 미리보기에서도 같은 부품으로 그립니다(메뉴 이동은 안내만).
    from src.ui.components import sidebar

    if sidebar.render(sidebar.SCREEN_REVIEW_ASSIGN) is not None:
        st.toast("다른 화면 이동은 app.py 연결 후 동작합니다.")
    event = render(CurrentUser(uid="preview-admin", role=ROLE_ADMIN, name="김의사"))
    if event is not None and event.kind == EVENT_LOGOUT:
        reset_state()
        st.rerun()
