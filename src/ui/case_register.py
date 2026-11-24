"""PACS 검사 등록 화면.

화면 요소와 입력 처리는 Streamlit 위젯으로 구성하고, 모양은 Figma 디자인(1440×960 기준)을 CSS로 맞춥니다.
검사 목록 화면(case_list.py)과 같은 방식(key가 붙은 st.container + CSS 좌표)으로 만들었습니다.

화면 흐름:
1. "+ DICOM 불러오기"로 .dcm 파일을 한 번에 여러 개 추가합니다.
2. 파일마다 DICOM 정보를 읽어 표에 표시하고, 첫 번째로 확인된 영상의 환자 정보·검사 장비를 위 칸에 채웁니다.
   다른 환자의 영상은 "환자 불일치"로 표시하며, 표의 × 버튼으로 뺄 수 있습니다.
3. 검사명을 직접 입력하면 "검사 등록" 버튼이 켜지고, 누르면 등록할 내용(CaseDraft)을 이벤트로 돌려줍니다.
수정 모드(start_edit 후): 기존 검사 값이 채워진 같은 화면에서 검사명·영상을 고치고 "수정 저장"을 누릅니다.

DICOM 사용 여부는 팀에서 아직 결정하지 않았습니다. DICOM 정보 읽기는 pydicom 라이브러리가 설치되어 있을 때만
동작하고, 없으면 파일을 "확인 대기"로 표시합니다(requirements.txt에 pydicom 추가 필요).
실제 저장(케이스 생성·파일 저장)은 아직 연결하지 않았고, app.py가 EVENT_SUBMIT 이벤트를 받아 처리합니다.
케이스 생성은 ``services.case.create_case(**event.draft.case_create_args())``로 이어 붙이면 됩니다
(DICOM 환자번호와 Firestore 환자 연결, 파일 저장·검사일 저장은 팀 결정 후 연결).

app.py에서는 ``render(current_user)``를 호출하고, 반환된 ``RegisterEvent``로 화면 이동·등록을 처리합니다.
current_user는 ``case_list.current_user_from_profile(session.get_current_user())``로 만듭니다.
- ``start_edit(edit_target_from_case(case, files))``: 검사 목록의 수정 확인 후 호출. 기존 검사 정보가 채워진
  수정 화면(제목 "검사 수정", 버튼 "수정 저장")이 되고, 수정 저장을 누르면 EVENT_UPDATE(draft.case_id 포함)를 돌려줍니다.
- ``reset_state()``: 등록을 마친 뒤(저장 성공 후)와 다른 화면으로 이동할 때 호출. 입력한 검사명과 불러온 파일을 모두 지웁니다.
  취소 버튼은 화면이 직접 지웁니다. 다른 화면으로 갈 때 지우지 않으면 Streamlit이 화면에 없는 입력칸 값만 지워서
  불러온 파일만 남은 상태가 됩니다.
- 로그아웃(EVENT_LOGOUT)은 팀의 ``session.logout()``으로 처리합니다. 세션 값을 모두 지우므로 reset_state()는 필요 없습니다.
단독 실행하면 샘플 등록 담당자로 미리 볼 수 있고, 주소 끝에 ``?role=REVIEWER``처럼 역할을 붙이면 그 역할로 봅니다.
"""

from __future__ import annotations

import base64
import io
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime

import streamlit as st

from src.config import MAX_IMAGE_SIZE_MB
from src.ui.case_list import (
    EVENT_LOGOUT,
    EVENT_NAVIGATE,
    KST,
    ROLE_LABELS,
    ROLE_REGISTRAR,
    TARGET_REVIEW_ASSIGN,
    USER_NAME_FALLBACK,
    CurrentUser,
    current_user_from_profile,
    role_label,
)

_logger = logging.getLogger(__name__)

# ── 업무 규칙 ────────────────────────────────────────────────────────────────

DICOM_EXTENSIONS = ("dcm",)  # 불러올 수 있는 파일 확장자
# 파일 하나의 최대 크기. 팀 공통 설정(src/config.py)을 그대로 써서 저장(file_storage)·영상 읽기(image_io)와 기준을 맞춥니다.
MAX_DICOM_FILE_MB = MAX_IMAGE_SIZE_MB
MAX_EXAM_NAME_CHARS = 100

# 표의 "상태" 칸에 표시하는 파일 상태입니다. 저장 대상은 FILE_OK(새 영상)와, 수정 화면의 FILE_SAVED(기존 영상)뿐입니다.
FILE_OK = "확인 완료"
FILE_MISMATCH = "환자 불일치"  # 기준 환자(첫 확인 영상, 수정 화면이면 기존 검사의 환자)와 환자번호가 다름
FILE_FAILED = "읽기 실패"  # DICOM 파일이 아니거나 손상됨
FILE_TOO_LARGE = "크기 초과"
FILE_PENDING = "확인 대기"  # DICOM을 읽을 라이브러리(pydicom)가 아직 없음
FILE_SAVED = "등록됨"  # 수정 화면: 이미 저장되어 있는 영상

# 제목 오른쪽 상태 배지 문구
BADGE_NEW = "새 검사 · 등록 전"  # 불러온 영상이 없음 (디자인 초기 상태)
BADGE_DRAFT = "새 검사 · 작성 중"  # 영상은 확인됐지만 검사명 등 입력이 남음
BADGE_CHECK = "영상 확인 필요"  # 확인되지 않은 영상이 있음
BADGE_READY = "등록 준비 완료"  # 검사 등록 버튼을 누를 수 있음
BADGE_EDIT = "기존 검사 · 수정 중"  # 수정 화면: 아직 바뀐 내용이 없거나 입력이 남음
BADGE_EDIT_READY = "수정 준비 완료"  # 수정 화면: 수정 저장 버튼을 누를 수 있음

# ── 화면 이동·등록 이벤트 (render가 돌려주는 RegisterEvent.kind / target 값) ─────────
# EVENT_NAVIGATE, EVENT_LOGOUT, TARGET_REVIEW_ASSIGN은 검사 목록 화면과 같은 값을 씁니다.

EVENT_SUBMIT = "submit_case"  # 검사 등록 버튼 (draft 포함)
EVENT_UPDATE = "update_case"  # 수정 화면의 수정 저장 버튼 (draft.case_id에 수정할 검사)
TARGET_CASE_LIST = "case_list"  # 검사 목록 화면 (왼쪽 메뉴, 취소 버튼)

# ── 안내 문구 ────────────────────────────────────────────────────────────────

MSG_NEED_REGISTRAR = "등록 담당자만 검사를 등록할 수 있습니다."
MSG_NEED_FILES = "DICOM 영상을 불러오세요."
MSG_FILES_TO_CHECK = "확인되지 않은 영상을 목록에서 빼 주세요."
MSG_NEED_PATIENT = "영상에서 환자 정보(이름·환자번호·성별·나이)와 검사 장비를 모두 읽지 못했습니다."
MSG_NEED_EXAM_NAME = "검사명을 입력하세요."
MSG_DUPLICATE_FILE = "이미 추가한 파일은 제외했습니다: {names}"
MSG_PENDING_READER = "DICOM 정보를 읽는 기능이 아직 준비되지 않았습니다."
MSG_NO_CHANGES = "바뀐 내용이 없습니다. 검사명이나 영상을 수정해 주세요."

# ── 세션 상태 키 (모두 _STATE_PREFIX로 시작, reset_state가 한꺼번에 지움) ──────────

_STATE_PREFIX = "_pacs_reg_"
_FILES_KEY = "_pacs_reg_files"  # 불러온 파일 목록 (DicomFile 튜플)
_FORM_VERSION_KEY = "_pacs_reg_form_version"  # 입력칸을 비울 때 올리는 번호
_UPLOAD_VERSION_KEY = "_pacs_reg_upload_version"  # 파일 선택 버튼을 비울 때 올리는 번호
_NEXT_FILE_ID_KEY = "_pacs_reg_next_file_id"  # 불러온 파일마다 붙이는 번호(삭제 버튼 key에 사용)
_SUBMIT_USER_KEY = "_pacs_reg_submit_user"  # 화면을 그린 사용자(검사 등록·수정 저장 버튼 콜백에서 권한을 다시 확인)
_EVENT_KEY = "_pacs_reg_event"
_NOTICE_KEY = "_pacs_reg_notice"
_EDIT_KEY = "_pacs_reg_edit"  # 수정 화면이면 수정할 검사(EditTarget), 새 검사 등록이면 없음


# ── 데이터 형식 ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DicomFile:
    """불러온 DICOM 파일 하나와 그 안에서 읽은 정보."""

    file_id: int
    file_name: str
    data: bytes
    status: str  # FILE_* 중 하나
    series: str = ""  # 시리즈 설명(SeriesDescription) 또는 "Series 번호"
    modality: str = ""  # 검사 장비(Modality): CT, MR, CR, US ...
    study_date: date | None = None  # 검사일(StudyDate)
    image_count: int | None = None  # 영상 수(NumberOfFrames, 없으면 1)
    patient_name: str = ""
    patient_id: str = ""  # 환자번호(PatientID)
    patient_sex: str = ""  # "남" / "여" / ""
    patient_age: int | None = None
    saved_file_id: str = ""  # 이미 저장된 영상(FILE_SAVED)의 백엔드 files 문서 ID. 새로 불러온 영상은 ""


@dataclass(frozen=True)
class EditTarget:
    """수정할 기존 검사. 목록의 수정 확인 후 app.py가 백엔드 데이터로 만들어 start_edit()에 넘깁니다."""

    case_id: str
    patient_name: str
    patient_id: str
    patient_sex: str
    patient_age: int | None
    exam_name: str
    modality: str
    exam_date: date | None
    department_id: str
    files: tuple[DicomFile, ...]  # 이미 저장된 영상(status FILE_SAVED)


@dataclass(frozen=True)
class CaseDraft:
    """검사 등록·수정 저장 버튼을 눌렀을 때 저장할 내용(수정이면 case_id가 들어 있음).

    팀 백엔드 services.case.create_case 인자와의 짝은 case_create_args()에 모아 두었습니다.
    """

    patient_name: str
    patient_id: str  # DICOM 환자번호(PatientID). create_case는 Firestore patients에 있는 ID만 받습니다.
    patient_sex: str
    patient_age: int
    exam_name: str  # create_case의 exam_info(검사 부가 정보)
    modality: str  # create_case의 exam_type(검사 종류)
    exam_date: date | None  # DICOM 검사일. create_case에 아직 exam_at 인자가 없어 저장되지 않습니다.
    files: tuple[DicomFile, ...]
    department_id: str = ""  # 등록 담당자의 소속 진료과(users 문서의 department_id). 비어 있으면 create_case가 거절합니다.
    created_by: str = ""  # 등록 담당자 UID(로그인 사용자). 수정이면 수정한 사람
    case_id: str = ""  # 수정이면 수정할 검사 ID, 새 검사 등록이면 ""
    removed_file_ids: tuple[str, ...] = ()  # 수정 화면에서 뺀, 이미 저장돼 있던 영상의 files 문서 ID

    @property
    def new_files(self) -> tuple[DicomFile, ...]:
        """새로 저장해야 하는 영상(이번에 불러온 영상). 이미 저장된 영상(FILE_SAVED)은 빠집니다."""
        return tuple(item for item in self.files if item.status == FILE_OK)

    def case_create_args(self) -> dict[str, str]:
        """services.case.create_case(**args)에 그대로 넘길 인자를 만듭니다(케이스 문서만, 파일 저장은 별도)."""
        return {
            "patient_id": self.patient_id,
            "department_id": self.department_id,
            "exam_type": self.modality,
            "exam_info": self.exam_name,
            "created_by": self.created_by,
        }


@dataclass(frozen=True)
class RegisterEvent:
    """사용자가 화면을 이동하거나 등록하려 할 때 render가 돌려주는 값."""

    kind: str  # EVENT_NAVIGATE | EVENT_LOGOUT | EVENT_SUBMIT | EVENT_UPDATE
    target: str | None = None  # EVENT_NAVIGATE일 때: TARGET_CASE_LIST | TARGET_REVIEW_ASSIGN
    draft: CaseDraft | None = None  # EVENT_SUBMIT·EVENT_UPDATE일 때


@dataclass(frozen=True)
class FormSummary:
    """불러온 파일과 입력값으로 정한 화면 상태(채울 칸, 버튼 상태, 배지)."""

    patient: DicomFile | None  # 위 칸에 채울 환자 정보(새 등록: 첫 확인 영상, 수정: 기존 검사 값)
    ok_count: int
    problem_count: int
    can_submit: bool
    block_reason: str | None
    badge: str


# ── DICOM 읽기 ───────────────────────────────────────────────────────────────

_SEX_LABELS = {"M": "남", "F": "여"}


def read_dicom(file_name: str, data: bytes, file_id: int = 0) -> DicomFile:
    """DICOM 파일의 정보(환자·검사 장비·검사일·시리즈·영상 수)를 읽습니다. 영상 픽셀은 읽지 않습니다."""
    if len(data) > MAX_DICOM_FILE_MB * 1024 * 1024:
        return DicomFile(file_id, file_name, data, FILE_TOO_LARGE)
    try:
        import pydicom  # 팀에서 DICOM 사용을 확정하면 requirements.txt에 추가합니다.
    except ImportError:
        return DicomFile(file_id, file_name, data, FILE_PENDING)
    try:
        ds = pydicom.dcmread(io.BytesIO(data), stop_before_pixels=True)
    except Exception:
        _logger.info("DICOM 파일을 읽지 못했습니다: %s", file_name, exc_info=True)
        return DicomFile(file_id, file_name, data, FILE_FAILED)

    study_date = _dicom_date(ds.get("StudyDate"))
    series = _clean(ds.get("SeriesDescription"))
    if not series and _clean(ds.get("SeriesNumber")):
        series = f"Series {_clean(ds.get('SeriesNumber'))}"
    return DicomFile(
        file_id=file_id,
        file_name=file_name,
        data=data,
        status=FILE_OK,
        series=series,
        modality=_clean(ds.get("Modality")).upper(),
        study_date=study_date,
        image_count=_positive_int(ds.get("NumberOfFrames")) or 1,
        patient_name=_clean(ds.get("PatientName")).replace("^", " ").strip(),
        patient_id=_clean(ds.get("PatientID")),
        patient_sex=_SEX_LABELS.get(_clean(ds.get("PatientSex")).upper(), ""),
        patient_age=_dicom_age(ds.get("PatientAge"), ds.get("PatientBirthDate"), study_date),
    )


def _clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def _positive_int(value: object) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _dicom_date(value: object) -> date | None:
    """DICOM 날짜("YYYYMMDD")를 date로 바꿉니다."""
    try:
        return datetime.strptime(_clean(value)[:8], "%Y%m%d").date()
    except ValueError:
        return None


def _dicom_age(age: object, birth_date: object, study_date: date | None) -> int | None:
    """나이(PatientAge, 예: "058Y")를 쓰고, 없으면 생년월일로 검사일(없으면 오늘) 기준 만 나이를 계산합니다."""
    match = re.fullmatch(r"(\d{1,3})([DWMY])", _clean(age).upper())
    if match:
        number, unit = int(match.group(1)), match.group(2)
        return number if unit == "Y" else 0
    born = _dicom_date(birth_date)
    if born is None:
        return None
    base = study_date or datetime.now(KST).date()
    years = base.year - born.year - ((base.month, base.day) < (born.month, born.day))
    return years if years >= 0 else None


def check_same_patient(files: tuple[DicomFile, ...], reference_id: str | None = None) -> tuple[DicomFile, ...]:
    """기준 환자와 환자번호가 다른 영상을 "환자 불일치"로 표시합니다(나머지는 그대로).

    기준 환자: reference_id(수정 화면이면 기존 검사의 환자번호)가 있으면 그 번호, 없으면 먼저 확인된 영상.
    """
    checked = []
    for item in files:
        if item.status in (FILE_OK, FILE_MISMATCH):
            if reference_id is None:
                reference_id = item.patient_id
            item = _with_status(item, FILE_OK if item.patient_id == reference_id else FILE_MISMATCH)
        checked.append(item)
    return tuple(checked)


def _with_status(item: DicomFile, status: str) -> DicomFile:
    return item if item.status == status else replace(item, status=status)


# ── 화면 상태 계산 ───────────────────────────────────────────────────────────

def summarize(
    user: CurrentUser, files: tuple[DicomFile, ...], exam_name: str, edit: EditTarget | None = None,
) -> FormSummary:
    """불러온 파일과 검사명으로 위 칸에 채울 영상, 검사 등록 버튼 상태, 상태 배지를 정합니다.

    edit가 있으면 수정 화면 기준으로 판단합니다(_summarize_edit).
    """
    if edit is not None:
        return _summarize_edit(user, files, exam_name, edit)
    ok_files = [item for item in files if item.status == FILE_OK]
    patient = ok_files[0] if ok_files else None
    problem_count = len(files) - len(ok_files)

    if not is_registrar(user):
        reason = MSG_NEED_REGISTRAR
    elif not ok_files:
        reason = MSG_FILES_TO_CHECK if problem_count else MSG_NEED_FILES
    elif problem_count:
        reason = MSG_FILES_TO_CHECK
    elif not (patient.patient_name and patient.patient_id and patient.patient_sex
              and patient.patient_age is not None and patient.modality):
        reason = MSG_NEED_PATIENT
    elif not exam_name.strip():
        reason = MSG_NEED_EXAM_NAME
    else:
        reason = None

    if not files:
        badge = BADGE_NEW
    elif problem_count:
        badge = BADGE_CHECK
    elif reason is None:
        badge = BADGE_READY
    else:
        badge = BADGE_DRAFT
    return FormSummary(patient, len(ok_files), problem_count, reason is None, reason, badge)


def _edit_patient(edit: EditTarget) -> DicomFile:
    """수정 화면 위 칸에 채울 환자·검사 정보(기존 검사 값)."""
    return DicomFile(
        file_id=0, file_name="", data=b"", status=FILE_SAVED, modality=edit.modality, study_date=edit.exam_date,
        patient_name=edit.patient_name, patient_id=edit.patient_id, patient_sex=edit.patient_sex,
        patient_age=edit.patient_age,
    )


def _saved_ids(files: tuple[DicomFile, ...]) -> set[str]:
    return {item.saved_file_id for item in files if item.status == FILE_SAVED}


def _summarize_edit(user: CurrentUser, files: tuple[DicomFile, ...], exam_name: str, edit: EditTarget) -> FormSummary:
    """수정 화면: 환자 정보는 기존 검사 값을 쓰고, 검사명이나 영상이 바뀌었을 때만 수정 저장을 허용합니다.

    기존 검사에 영상이 없어도(아직 업로드 기능 전) 검사명만 고쳐 저장할 수 있습니다.
    """
    usable = [item for item in files if item.status in (FILE_OK, FILE_SAVED)]
    problem_count = len(files) - len(usable)
    changed = (
        exam_name.strip() != edit.exam_name.strip()
        or _saved_ids(files) != _saved_ids(edit.files)
        or any(item.status == FILE_OK for item in files)
    )
    if not is_registrar(user):
        reason = MSG_NEED_REGISTRAR
    elif problem_count:
        reason = MSG_FILES_TO_CHECK
    elif not exam_name.strip():
        reason = MSG_NEED_EXAM_NAME
    elif not changed:
        reason = MSG_NO_CHANGES
    else:
        reason = None

    if problem_count:
        badge = BADGE_CHECK
    elif reason is None:
        badge = BADGE_EDIT_READY
    else:
        badge = BADGE_EDIT
    return FormSummary(_edit_patient(edit), len(usable), problem_count, reason is None, reason, badge)


def is_registrar(user: CurrentUser) -> bool:
    return user.role == ROLE_REGISTRAR


def build_draft(
    user: CurrentUser, files: tuple[DicomFile, ...], exam_name: str, edit: EditTarget | None = None,
) -> CaseDraft:
    """확인된 영상·검사명과 로그인 사용자로 등록할 내용을 만듭니다(summarize의 can_submit이 True일 때 호출).

    edit가 있으면 수정할 내용: 환자·장비·진료과는 기존 검사 값, files는 남겨 둔 기존 영상 + 새 영상,
    removed_file_ids는 화면에서 뺀 기존 영상입니다.
    """
    if edit is not None:
        kept = tuple(item for item in files if item.status in (FILE_OK, FILE_SAVED))
        return CaseDraft(
            patient_name=edit.patient_name,
            patient_id=edit.patient_id,
            patient_sex=edit.patient_sex,
            patient_age=edit.patient_age,
            exam_name=exam_name.strip(),
            modality=edit.modality,
            exam_date=edit.exam_date,
            files=kept,
            department_id=edit.department_id,
            created_by=user.uid,
            case_id=edit.case_id,
            removed_file_ids=tuple(sorted(_saved_ids(edit.files) - _saved_ids(kept))),
        )
    ok_files = tuple(item for item in files if item.status == FILE_OK)
    patient = ok_files[0]
    return CaseDraft(
        patient_name=patient.patient_name,
        patient_id=patient.patient_id,
        patient_sex=patient.patient_sex,
        patient_age=patient.patient_age,
        exam_name=exam_name.strip(),
        modality=patient.modality,
        exam_date=patient.study_date,
        files=ok_files,
        department_id=user.department_id,
        created_by=user.uid,
    )


def reset_state() -> None:
    """이 화면이 세션에 저장한 값(검사명, 불러온 파일 등)을 모두 지웁니다(등록 완료 후·다른 화면으로 이동할 때 호출).

    로그아웃할 때는 session.logout()이 세션 전체를 지우므로 따로 부르지 않아도 됩니다.
    """
    for key in [k for k in st.session_state.keys() if str(k).startswith(_STATE_PREFIX)]:
        del st.session_state[key]


def _files() -> tuple[DicomFile, ...]:
    return st.session_state.get(_FILES_KEY, ())


def _editing() -> EditTarget | None:
    return st.session_state.get(_EDIT_KEY)


def _patient_reference() -> str | None:
    """환자 일치 확인의 기준 환자번호. 수정 화면이면 기존 검사의 환자번호입니다."""
    edit = _editing()
    return edit.patient_id if edit else None


# ── 검사 수정 시작 (app.py에서 호출) ─────────────────────────────────────────

_SEX_FROM_BACKEND = {"M": "남", "MALE": "남", "남": "남", "남성": "남", "F": "여", "FEMALE": "여", "여": "여", "여성": "여"}


def _to_date(value: object) -> date | None:
    """Firestore 시각·날짜 값(또는 ISO 글자)을 한국 날짜로 바꿉니다. 알 수 없으면 None입니다."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    if isinstance(value, datetime):
        return (value.astimezone(KST) if value.tzinfo else value).date()
    return value if isinstance(value, date) else None


def _age_from_birth(birth_date: object) -> int | None:
    born = _to_date(birth_date)
    if born is None:
        return None
    today = datetime.now(KST).date()
    years = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
    return years if years >= 0 else None


def edit_target_from_case(case: Mapping[str, object], files: Iterable[Mapping[str, object]] = ()) -> EditTarget:
    """백엔드 데이터로 수정할 검사(EditTarget)를 만듭니다. 데이터베이스는 조회하지 않습니다.

    - case: services.case.get_case_detail()이 돌려준 값(케이스 + "patient" + "department")
    - files: repositories.image.list_case_files(case_id, "ORIGINAL")이 돌려준 파일 정보 목록
    case_id가 없으면 ValueError를 냅니다.
    """
    case_id = _clean(case.get("case_id"))
    if not case_id:
        raise ValueError("수정할 검사의 case_id가 없습니다.")
    patient = case.get("patient") or {}
    modality = _clean(case.get("exam_type")).upper()
    exam_date = _to_date(case.get("exam_at"))
    name, patient_id = _clean(patient.get("name")), _clean(case.get("patient_id"))
    sex = _SEX_FROM_BACKEND.get(_clean(patient.get("sex")).upper(), "")
    age = _age_from_birth(patient.get("birth_date"))
    saved = tuple(
        DicomFile(
            file_id=index,
            file_name=_clean(info.get("original_name")) or _clean(info.get("file_id")),
            data=b"",
            status=FILE_SAVED,
            modality=modality,
            study_date=exam_date,
            patient_name=name,
            patient_id=patient_id,
            patient_sex=sex,
            patient_age=age,
            saved_file_id=_clean(info.get("file_id")),
        )
        for index, info in enumerate(files, start=1)
    )
    return EditTarget(
        case_id=case_id, patient_name=name, patient_id=patient_id, patient_sex=sex, patient_age=age,
        exam_name=_clean(case.get("exam_info")), modality=modality, exam_date=exam_date,
        department_id=_clean(case.get("department_id")), files=saved,
    )


def start_edit(target: EditTarget) -> None:
    """검사 등록 화면을 수정 화면으로 준비합니다(기존 값 채우기). 다음 render()부터 수정 화면이 그려집니다.

    작성 중이던 새 검사 내용은 지웁니다. 수정을 마치거나 취소하면 reset_state()로 새 검사 등록 화면으로 돌아갑니다.
    """
    reset_state()
    state = st.session_state
    state[_EDIT_KEY] = target
    state[_FILES_KEY] = target.files
    state[_NEXT_FILE_ID_KEY] = len(target.files) + 1
    state[_form_key("exam_name")] = target.exam_name  # 검사명 입력칸에 기존 검사명을 미리 넣어 둠


def is_editing() -> bool:
    """지금 검사 등록 화면이 수정 화면인지 알려 줍니다."""
    return _editing() is not None


# ── 아이콘 (Figma에서 받은 SVG) ──────────────────────────────────────────────

def _svg(width: float, height: float, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" fill="none">{body}</svg>'
    )


_STROKE = 'stroke-linecap="round" stroke-linejoin="round"'
# 검사 등록 화면에서는 "검사 목록" 메뉴가 비활성(#C1C1C1), "검사 등록" 메뉴가 활성(#202020)입니다.
_ICON_LIST = _svg(20, 20, (
    f'<path d="M5 2.5H15C15.9205 2.5 16.6667 3.24619 16.6667 4.16667V15.8333C16.6667 16.7538 15.9205 17.5 15 17.5H5'
    f'C4.07953 17.5 3.33333 16.7538 3.33333 15.8333V4.16667C3.33333 3.24619 4.07953 2.5 5 2.5Z" '
    f'stroke-width="1.6" {_STROKE} stroke="#C1C1C1"/>'
    f'<path d="M6.66667 6.66667H13.3333M6.66667 10H13.3333M6.66667 13.3333H10.8333" '
    f'stroke-width="1.6" {_STROKE} stroke="#C1C1C1"/>'
))
# 문서(14×16)와 더하기(6×6)를 Figma 배치대로 20×20 한 장에 합칩니다.
_ICON_REGISTER = _svg(20, 20, (
    '<g transform="translate(3 2)">'
    f'<path d="M8.25 0.75H2.25C1.85218 0.75 1.47064 0.908035 1.18934 1.18934C0.908035 1.47064 0.75 1.85218 0.75 2.25'
    f'V13.75C0.75 14.1478 0.908035 14.5294 1.18934 14.8107C1.47064 15.092 1.85218 15.25 2.25 15.25H11.75'
    f'C12.1478 15.25 12.5294 15.092 12.8107 14.8107C13.092 14.5294 13.25 14.1478 13.25 13.75V5.75L8.25 0.75Z" '
    f'stroke-width="1.5" {_STROKE} stroke="#202020"/>'
    f'<path d="M8.25 0.75V4.75C8.25 5.01522 8.35536 5.26957 8.54289 5.45711C8.73043 5.64464 8.98478 5.75 9.25 5.75'
    f'H13.25" stroke-width="1.5" {_STROKE} stroke="#202020"/></g>'
    '<g transform="translate(7 9.25)"><path d="M3 0.75V5.25M0.75 3H5.25" stroke-width="1.5" '
    'stroke-linecap="round" stroke="#202020"/></g>'
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
# 환자 정보 카드의 기본 얼굴(104×104)
_ICON_AVATAR = _svg(104, 104, (
    '<circle cx="52" cy="52" r="52" fill="#F4F6F9"/>'
    '<circle cx="52" cy="39" r="16" fill="#A9B4C2"/>'
    '<rect x="22" y="61" width="60" height="30" rx="15" fill="#A9B4C2"/>'
))
# 파일 줄의 삭제(×) 버튼
_ICON_REMOVE = _svg(16, 16, f'<path d="M4.5 4.5L11.5 11.5M11.5 4.5L4.5 11.5" stroke-width="1.6" {_STROKE} stroke="#8A96A5"/>')


def _css_icon(svg: str) -> str:
    """CSS background-image에 넣을 수 있는 data URI를 만듭니다."""
    return "url('data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii") + "')"


# ── 화면 배치 CSS ────────────────────────────────────────────────────────────
# 모든 크기는 화면 높이에 비례하는 --u(= 화면 높이 / 960) 단위이고, 좌표는 Figma 레이어 값을 그대로 씁니다.
# 화면이 디자인(1440)보다 넓으면 카드 너비가 늘어나고, 오른쪽 요소는 오른쪽 끝을 기준으로 붙습니다.
# 왼쪽 메뉴·상단 바는 검사 목록 화면과 같은 모양입니다(key 이름만 pacs_reg_로 다름).

_STYLE = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap');

html, body, .stApp, .stMain { background:#f7f8fa !important; }

/* 처리 중 화면(검사 목록 화면과 같음): 실행이 0.4초 넘게 걸리면 화면을 살짝 회색으로 덮고 가운데에 회전 표시를 띄웁니다. */
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

.st-key-pacs_reg_stage {
  --u:calc(100vh / 960);
  position:fixed !important; inset:0; z-index:1; overflow:hidden;
  background:#f7f8fa; color:#1d1d1d; font-family:'Noto Sans KR', sans-serif; font-weight:400;
}
.st-key-pacs_reg_stage * { box-sizing:border-box; }

/* Streamlit 요소의 기본 간격·여백·글꼴을 없애고, 아래에서 지정한 값만 쓰게 합니다. */
.st-key-pacs_reg_stage [data-testid="stVerticalBlock"],
.st-key-pacs_reg_stage [data-testid="stHorizontalBlock"] { gap:0 !important; }
.st-key-pacs_reg_stage [data-testid="stElementContainer"] { margin:0 !important; }
.st-key-pacs_reg_stage [data-testid="stMarkdown"],
.st-key-pacs_reg_stage [data-testid="stMarkdownContainer"],
.st-key-pacs_reg_stage [data-testid="stHeading"],
.st-key-pacs_reg_stage [data-testid="stHeadingWithActionElements"],
.st-key-pacs_reg_stage [data-testid="stMarkdownContainer"] p,
.st-key-pacs_reg_stage h1, .st-key-pacs_reg_stage h2, .st-key-pacs_reg_stage h3 {
  margin:0 !important; padding:0 !important; font-family:inherit; font-size:inherit;
  font-weight:inherit; line-height:inherit; letter-spacing:normal; color:inherit; white-space:inherit;
}
.st-key-pacs_reg_stage [data-testid="stHeaderActionElements"] { display:none !important; }
.st-key-pacs_reg_stage button { font-family:inherit !important; }
.st-key-pacs_reg_stage [data-testid="stButtonShortcut"], .st-key-pacs_reg_stage kbd { display:none !important; }
.st-key-pacs_reg_stage button:focus-visible { outline:2px solid #3266c3 !important; outline-offset:2px; }

/* 절대 좌표로 배치하는 블록 */
.st-key-pacs_reg_sidebar, .st-key-pacs_reg_header, .st-key-pacs_reg_main,
.st-key-pacs_reg_menu_list, .st-key-pacs_reg_menu_register, .st-key-pacs_reg_menu_review,
.st-key-pacs_reg_clock, .st-key-pacs_reg_logout, .st-key-pacs_reg_title,
.st-key-pacs_reg_info, .st-key-pacs_reg_dicom, .st-key-pacs_reg_upload, .st-key-pacs_reg_thead,
.st-key-pacs_reg_rows, .st-key-pacs_reg_empty, .st-key-pacs_reg_cancel, .st-key-pacs_reg_submit,
[class*="st-key-pacs_reg_field_"] {
  position:absolute !important;
}
/* 절대 좌표로 배치하는 글자 칸: 내용 너비만큼만 차지하고 한 줄로 표시합니다. */
.st-key-pacs_reg_brand, .st-key-pacs_reg_workspace, .st-key-pacs_reg_server, .st-key-pacs_reg_version,
.st-key-pacs_reg_crumb_home, .st-key-pacs_reg_crumb_page, .st-key-pacs_reg_user,
[class*="st-key-pacs_reg_badge_"], .st-key-pacs_reg_info_title, .st-key-pacs_reg_required, .st-key-pacs_reg_help,
.st-key-pacs_reg_dicom_title, .st-key-pacs_reg_count, .st-key-pacs_reg_dicom_sub,
[class*="st-key-pacs_reg_th_"], [class*="st-key-pacs_reg_cfile_"], [class*="st-key-pacs_reg_cseries_"],
[class*="st-key-pacs_reg_cmod_"], [class*="st-key-pacs_reg_cdate_"], [class*="st-key-pacs_reg_ccount_"],
[class*="st-key-pacs_reg_cstatus_"] {
  position:absolute !important; width:auto !important; white-space:nowrap;
}

/* ── 사이드바 (204 × 화면 높이, 검사 목록 화면과 같음) ── */
.st-key-pacs_reg_sidebar {
  left:0; top:0; width:calc(204 * var(--u)) !important; height:100vh; background:#191919; color:#fff;
}
.st-key-pacs_reg_sidebar::before {
  content:''; position:absolute; left:calc(20 * var(--u)); top:calc(111 * var(--u));
  width:calc(164 * var(--u)); height:1px; background:#363636;
}
.st-key-pacs_reg_brand { left:calc(24 * var(--u)); top:calc(24 * var(--u)); }
.st-key-pacs_reg_brand::before {
  content:''; position:absolute; left:0; top:calc(2 * var(--u));
  width:calc(28 * var(--u)); height:calc(28 * var(--u)); border-radius:calc(6 * var(--u));
  background:
    linear-gradient(#191919, #191919) center / calc(16 * var(--u)) calc(4 * var(--u)) no-repeat,
    linear-gradient(#191919, #191919) center / calc(4 * var(--u)) calc(16 * var(--u)) no-repeat,
    #f4f4f4;
}
.st-key-pacs_reg_brand_name {
  padding-left:calc(39 * var(--u)); color:#fff;
  font-size:calc(23 * var(--u)); line-height:calc(35 * var(--u)); font-weight:700;
}
.st-key-pacs_reg_brand_sub { margin-top:calc(8 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#a7a7a7; }
.st-key-pacs_reg_workspace {
  left:calc(24 * var(--u)); top:calc(137 * var(--u));
  font-size:calc(10 * var(--u)); line-height:calc(15 * var(--u)); font-weight:500; color:#9a9a9a;
}
.st-key-pacs_reg_server {
  left:calc(16 * var(--u)); top:calc(842 * var(--u)); width:calc(172 * var(--u)) !important; height:calc(65 * var(--u));
  padding:calc(11 * var(--u)) calc(12 * var(--u)) 0 !important; border-radius:calc(6 * var(--u)); background:#252525;
}
.st-key-pacs_reg_server::before {
  content:''; position:absolute; left:calc(12 * var(--u)); top:calc(11 * var(--u));
  width:calc(18 * var(--u)); height:calc(18 * var(--u)); background:@ICON_SHIELD@ center / contain no-repeat;
}
.st-key-pacs_reg_server_title {
  padding-left:calc(26 * var(--u)); font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:500; color:#d7d7d7;
}
.st-key-pacs_reg_server_sub { margin-top:calc(7 * var(--u)); font-size:calc(10 * var(--u)); line-height:calc(15 * var(--u)); color:#a5a5a5; }
.st-key-pacs_reg_version {
  left:calc(24 * var(--u)); top:calc(927 * var(--u)); white-space:pre;
  font-size:calc(10 * var(--u)); line-height:calc(15 * var(--u)); color:#8e8e8e;
}

/* 메뉴 버튼: 180×44, 아이콘 (14, 12), 글자 x 46. 검사 등록 메뉴가 활성 상태입니다. */
.st-key-pacs_reg_menu_list, .st-key-pacs_reg_menu_register, .st-key-pacs_reg_menu_review {
  left:calc(12 * var(--u)); width:calc(180 * var(--u)) !important; height:calc(44 * var(--u));
}
.st-key-pacs_reg_menu_list { top:calc(174 * var(--u)); }
.st-key-pacs_reg_menu_register { top:calc(226 * var(--u)); }
.st-key-pacs_reg_menu_review { top:calc(278 * var(--u)); }
.st-key-pacs_reg_sidebar [data-testid="stButton"], .st-key-pacs_reg_sidebar [data-testid="stButton"] button {
  width:100% !important; height:calc(44 * var(--u)) !important; min-height:0 !important;
}
.st-key-pacs_reg_sidebar [data-testid="stButton"] button {
  justify-content:flex-start !important; padding:0 0 0 calc(46 * var(--u)) !important;
  border:0 !important; border-radius:calc(6 * var(--u)) !important; box-shadow:none !important;
  background-color:transparent !important; background-repeat:no-repeat !important;
  background-position:calc(14 * var(--u)) center !important; background-size:calc(20 * var(--u)) !important;
}
.st-key-pacs_reg_sidebar [data-testid="stButton"] button > div { width:100%; justify-content:flex-start !important; }
.st-key-pacs_reg_sidebar [data-testid="stButton"] button p {
  font-size:calc(14 * var(--u)) !important; line-height:calc(21 * var(--u)) !important; color:#c1c1c1 !important;
}
.st-key-pacs_reg_sidebar .st-key-pacs_reg_menu_register button { background-color:#f2f2f2 !important; background-image:@ICON_REGISTER@ !important; }
.st-key-pacs_reg_sidebar .st-key-pacs_reg_menu_register button p { color:#202020 !important; font-weight:700 !important; }
.st-key-pacs_reg_sidebar .st-key-pacs_reg_menu_list button { background-image:@ICON_LIST@ !important; }
.st-key-pacs_reg_sidebar .st-key-pacs_reg_menu_review button { background-image:@ICON_REVIEW@ !important; }
.st-key-pacs_reg_sidebar .st-key-pacs_reg_menu_list button:hover,
.st-key-pacs_reg_sidebar .st-key-pacs_reg_menu_review button:hover { background-color:#242424 !important; }

/* ── 상단 바 (64 높이, 검사 목록 화면과 같음) ── */
.st-key-pacs_reg_header {
  left:calc(204 * var(--u)); right:0; top:0; height:calc(64 * var(--u));
  width:auto !important; background:#fff; border-bottom:1px solid #e5e5e5;
}
.st-key-pacs_reg_crumb_home {
  left:calc(32 * var(--u)); top:calc(23 * var(--u)); font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#777;
}
.st-key-pacs_reg_crumb_page {
  left:calc(143 * var(--u)); top:calc(23 * var(--u));
  font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:500; color:#222;
}
.st-key-pacs_reg_crumb_page::before {
  content:''; position:absolute; left:calc(-27 * var(--u)); top:calc(1 * var(--u));
  width:calc(16 * var(--u)); height:calc(16 * var(--u)); background:@ICON_CHEVRON_RIGHT@ center / contain no-repeat;
}
.st-key-pacs_reg_user { left:calc(100% - 242 * var(--u)); top:calc(15 * var(--u)); overflow:visible; }
.st-key-pacs_reg_user::before {
  content:''; position:absolute; left:calc(-36 * var(--u)); top:calc(6 * var(--u));
  width:1px; height:calc(22 * var(--u)); background:#e5e5e5;
}
.st-key-pacs_reg_user_role { font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#777; }
.st-key-pacs_reg_user_name {
  margin-top:calc(-1 * var(--u)); font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:500; color:#222;
}
.st-key-pacs_reg_user_role, .st-key-pacs_reg_user_name { max-width:calc(160 * var(--u)); }
.st-key-pacs_reg_user_role p, .st-key-pacs_reg_user_name p { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.st-key-pacs_reg_clock {
  right:calc(303 * var(--u)); top:calc(23 * var(--u)); width:auto !important; text-align:right; white-space:nowrap;
  font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#666; font-variant-numeric:tabular-nums;
}
.st-key-pacs_reg_logout { right:calc(31 * var(--u)); top:calc(18 * var(--u)); width:calc(28 * var(--u)) !important; height:calc(28 * var(--u)); }
.st-key-pacs_reg_logout button {
  width:calc(28 * var(--u)) !important; height:calc(28 * var(--u)) !important; min-height:0 !important; padding:0 !important;
  border:0 !important; border-radius:calc(4 * var(--u)) !important; box-shadow:none !important;
  background:transparent @ICON_LOGOUT@ center / calc(16 * var(--u)) no-repeat !important;
}
.st-key-pacs_reg_logout button:hover { background-color:#f2f2f2 !important; }
.st-key-pacs_reg_logout button p { font-size:0 !important; }

/* ── 본문 (상단 바 아래, 사이드바 오른쪽. 배경 #f7f8fa) ── */
.st-key-pacs_reg_main {
  left:calc(204 * var(--u)); right:0; top:calc(64 * var(--u)); bottom:0; width:auto !important; background:#f7f8fa;
}
.st-key-pacs_reg_title { left:calc(32 * var(--u)); top:calc(24 * var(--u)); width:auto !important; white-space:nowrap; }
.st-key-pacs_reg_title_text { font-size:calc(26 * var(--u)); line-height:calc(39 * var(--u)); font-weight:700; color:#1d1d1d; }
.st-key-pacs_reg_subtitle { margin-top:calc(5 * var(--u)); font-size:calc(13 * var(--u)); line-height:calc(20 * var(--u)); color:#626c79; }
/* 상태 배지: 높이 30, 최소 너비 158, 오른쪽 끝에서 32. key에 상태 종류(new / draft / check / ready)가 들어갑니다.
   수정 화면 배지는 같은 모양을 씁니다(수정 중 = draft, 수정 준비 완료 = ready). */
[class*="st-key-pacs_reg_badge_"] {
  right:calc(32 * var(--u)); top:calc(36 * var(--u)); min-width:calc(158 * var(--u)); height:calc(30 * var(--u));
  display:flex !important; align-items:center; justify-content:center; padding:0 calc(20 * var(--u));
  border-radius:calc(15 * var(--u)); background:#edf3fc; color:#3266c3;
  font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:700;
}
.st-key-pacs_reg_badge_check { background:#fdeeee; color:#c43c3c; }
.st-key-pacs_reg_badge_ready { background:#3266c3; color:#fff; }

/* 카드 공통: 흰 배경, 테두리 #dee3e9, 모서리 10 */
.st-key-pacs_reg_info, .st-key-pacs_reg_dicom {
  left:calc(32 * var(--u)); right:calc(32 * var(--u)); width:auto !important;
  background:#fff; border:1px solid #dee3e9; border-radius:calc(10 * var(--u)); overflow:hidden;
}

/* ── 환자 및 검사 정보 카드: 위 112, 높이 292 ── */
.st-key-pacs_reg_info { top:calc(112 * var(--u)); height:calc(292 * var(--u)); }
.st-key-pacs_reg_info::before {   /* 제목 아래 구분선 (y 57) */
  content:''; position:absolute; left:calc(23 * var(--u)); right:calc(23 * var(--u)); top:calc(57 * var(--u));
  height:1px; background:#dee3e9;
}
.st-key-pacs_reg_info::after {    /* 기본 얼굴 (23, 87) 104×104 */
  content:''; position:absolute; left:calc(23 * var(--u)); top:calc(87 * var(--u));
  width:calc(104 * var(--u)); height:calc(104 * var(--u)); background:@ICON_AVATAR@ center / contain no-repeat;
}
.st-key-pacs_reg_info_title {
  left:calc(23 * var(--u)); top:calc(17 * var(--u)); font-size:calc(16 * var(--u)); line-height:calc(24 * var(--u)); font-weight:700;
}
.st-key-pacs_reg_required {
  right:calc(57 * var(--u)); top:calc(22 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#626c79;
}
.st-key-pacs_reg_help {
  left:calc(159 * var(--u)); top:calc(248 * var(--u)); font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#626c79;
}
/* 입력 항목: 라벨(12, 굵게) 아래 6 간격으로 칸(높이 40, 모서리 7) */
[class*="st-key-pacs_reg_field_"] { gap:0 !important; }
.st-key-pacs_reg_field_name { left:calc(159 * var(--u)); top:calc(77 * var(--u)); width:calc(203 * var(--u)) !important; }
.st-key-pacs_reg_field_pid { left:calc(389 * var(--u)); top:calc(77 * var(--u)); width:calc(272 * var(--u)) !important; }
.st-key-pacs_reg_field_sex { left:calc(732 * var(--u)); top:calc(77 * var(--u)); width:calc(74 * var(--u)) !important; }
.st-key-pacs_reg_field_age { left:calc(855 * var(--u)); top:calc(77 * var(--u)); width:calc(76 * var(--u)) !important; }
.st-key-pacs_reg_field_exam { left:calc(159 * var(--u)); top:calc(163 * var(--u)); width:calc(573 * var(--u)) !important; }
.st-key-pacs_reg_field_modality { left:calc(855 * var(--u)); top:calc(163 * var(--u)); width:calc(234 * var(--u)) !important; }
[class*="st-key-pacs_reg_label_"] {
  margin-bottom:calc(6 * var(--u)); white-space:nowrap;
  font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:700; color:#626c79;
}
/* 자동 입력 칸(읽기 전용): 회색 배경. 값이 없으면 안내 글자(#626c79), 있으면 진한 글자 */
[class*="st-key-pacs_reg_value_"] {
  width:100% !important; height:calc(40 * var(--u)) !important; flex:0 0 auto !important;
  display:flex !important; flex-direction:column; justify-content:center;
  padding:0 calc(11 * var(--u)); overflow:hidden; white-space:nowrap;
  background:#f4f6f9; border:1px solid #dee3e9; border-radius:calc(7 * var(--u));
  font-size:calc(13 * var(--u)); line-height:calc(20 * var(--u)); color:#626c79;
}
[class*="st-key-pacs_reg_value_set_"] { color:#1d1d1d; font-weight:500; }
[class*="st-key-pacs_reg_value_"] :is([data-testid="stElementContainer"], [data-testid="stMarkdown"],
  [data-testid="stMarkdownContainer"], p) { width:100% !important; overflow:hidden; text-overflow:ellipsis; }
/* 검사명 입력칸(직접 입력): 흰 배경 */
.st-key-pacs_reg_field_exam [data-testid="stTextInput"] { width:100% !important; }
.st-key-pacs_reg_field_exam [data-testid="stTextInputRootElement"] {
  height:calc(40 * var(--u)) !important; min-height:0 !important;
  background:#fff !important; border:1px solid #dee3e9 !important; border-radius:calc(7 * var(--u)) !important; box-shadow:none !important;
}
.st-key-pacs_reg_field_exam [data-testid="stTextInputRootElement"]:focus-within { border-color:#3266c3 !important; }
.st-key-pacs_reg_field_exam [data-testid="stTextInputRootElement"] > div,
.st-key-pacs_reg_field_exam [data-baseweb="base-input"] { background:transparent !important; }
.st-key-pacs_reg_field_exam input {
  height:100% !important; padding:0 calc(11 * var(--u)) !important; background:transparent !important;
  color:#1d1d1d !important; -webkit-text-fill-color:#1d1d1d; caret-color:#1d1d1d;
  font-family:inherit !important; font-size:calc(13 * var(--u)) !important;
}
.st-key-pacs_reg_field_exam input::placeholder { color:#74808f !important; -webkit-text-fill-color:#74808f; opacity:1; }
.st-key-pacs_reg_field_exam input:disabled { cursor:not-allowed; }
.st-key-pacs_reg_field_exam [data-testid="stTextInputRootElement"]:has(input:disabled) { background:#f4f6f9 !important; }
.st-key-pacs_reg_field_exam [data-testid="InputInstructions"] { display:none !important; }

/* ── DICOM 영상 카드: 위 424, 높이 360 ── */
.st-key-pacs_reg_dicom { top:calc(424 * var(--u)); height:calc(360 * var(--u)); }
.st-key-pacs_reg_dicom_title {
  left:calc(23 * var(--u)); top:calc(19 * var(--u)); font-size:calc(16 * var(--u)); line-height:calc(24 * var(--u)); font-weight:700;
}
.st-key-pacs_reg_count {   /* 불러온 파일 수: 34×22 둥근 배지 */
  left:calc(129 * var(--u)); top:calc(21 * var(--u)); min-width:calc(34 * var(--u)); height:calc(22 * var(--u));
  display:flex !important; align-items:center; justify-content:center; padding:0 calc(9 * var(--u));
  border-radius:calc(11 * var(--u)); background:#edf3fc; color:#3266c3;
  font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); font-weight:700; font-variant-numeric:tabular-nums;
}
.st-key-pacs_reg_dicom_sub {
  left:calc(23 * var(--u)); top:calc(48 * var(--u)); font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#626c79;
}
/* DICOM 불러오기 버튼: 166×40 파란 버튼. Streamlit 파일 선택 칸을 버튼 모양으로 바꿉니다(누르면 파일 선택 창). */
.st-key-pacs_reg_upload {
  right:calc(23 * var(--u)); top:calc(21 * var(--u)); width:calc(166 * var(--u)) !important; height:calc(40 * var(--u));
}
.st-key-pacs_reg_upload [data-testid="stFileUploader"], .st-key-pacs_reg_upload [data-testid="stFileUploader"] > section,
.st-key-pacs_reg_upload [data-testid="stFileUploaderDropzone"] { width:100% !important; height:calc(40 * var(--u)) !important; }
.st-key-pacs_reg_upload [data-testid="stFileUploaderDropzone"] {
  position:relative; display:block !important; min-height:0 !important; padding:0 !important; margin:0 !important;
  border:0 !important; border-radius:calc(7 * var(--u)) !important; background:#3266c3 !important; overflow:hidden; cursor:pointer;
}
.st-key-pacs_reg_upload [data-testid="stFileUploaderDropzone"]:hover { background:#2a57a8 !important; }
.st-key-pacs_reg_upload [data-testid="stFileUploaderDropzone"]::after {
  content:'+  DICOM 불러오기'; position:absolute; inset:0; display:flex; align-items:center; justify-content:center;
  white-space:pre; pointer-events:none; color:#fff;
  font-size:calc(13 * var(--u)); line-height:calc(20 * var(--u)); font-weight:700;
}
.st-key-pacs_reg_upload [data-testid="stFileUploaderDropzoneInstructions"] { display:none !important; }
.st-key-pacs_reg_upload [data-testid="stFileUploaderDropzone"] [data-testid="stBaseButton-secondary"],
.st-key-pacs_reg_upload [data-testid="stFileUploaderDropzone"] button {
  position:absolute !important; inset:0; width:100% !important; height:100% !important; min-height:0 !important;
  margin:0 !important; padding:0 !important; border:0 !important; opacity:0; cursor:pointer;
}
.st-key-pacs_reg_upload [data-testid="stFileUploaderFile"], .st-key-pacs_reg_upload [data-testid="stFileUploaderPagination"],
.st-key-pacs_reg_upload [data-testid="stFileUploaderDeleteBtn"] { display:none !important; }
/* 등록 담당자가 아니면 비활성(검사 등록 버튼의 비활성 색과 같음) */
.st-key-pacs_reg_upload [data-testid="stFileUploaderDropzone"][aria-disabled="true"] { background:#dfe7f2 !important; cursor:not-allowed; }
.st-key-pacs_reg_upload [data-testid="stFileUploaderDropzone"][aria-disabled="true"]::after { color:#687c98; }

/* 파일 표 머리줄: 위 87, 높이 40. 칸 위치는 표 너비에 대한 비율(디자인 1170 기준 24, 510, 660, 850, 1020) */
.st-key-pacs_reg_thead {
  left:0; right:0; top:calc(87 * var(--u)); height:calc(40 * var(--u)); width:auto !important;
  background:#f4f6f9; border-top:1px solid #dee3e9; border-bottom:1px solid #dee3e9;
}
[class*="st-key-pacs_reg_th_"] {
  top:calc(10 * var(--u)); font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:700; color:#626c79;
}
.st-key-pacs_reg_th_file, [class*="st-key-pacs_reg_cfile_"], [class*="st-key-pacs_reg_cseries_"] { left:calc(24 * var(--u)); }
.st-key-pacs_reg_th_mod, [class*="st-key-pacs_reg_cmod_"] { left:43.59%; }
.st-key-pacs_reg_th_date, [class*="st-key-pacs_reg_cdate_"] { left:56.41%; }
.st-key-pacs_reg_th_count, [class*="st-key-pacs_reg_ccount_"] { left:72.65%; }
.st-key-pacs_reg_th_status, [class*="st-key-pacs_reg_cstatus_"] { left:87.18%; }

/* 파일 줄: 머리줄 아래부터 카드 끝까지, 넘치면 스크롤 */
.st-key-pacs_reg_rows {
  left:0; right:0; top:calc(127 * var(--u)); bottom:0; width:auto !important;
  display:block !important; overflow-y:auto !important; overflow-x:hidden;
  scrollbar-width:thin; scrollbar-color:#cfd6df transparent;
}
[class*="st-key-pacs_reg_row_"] {
  position:relative; flex:0 0 auto; height:calc(52 * var(--u)); border-bottom:1px solid #eef1f5; background:#fff;
}
[class*="st-key-pacs_reg_row_"]:hover { background:#fafbfc; }
[class*="st-key-pacs_reg_cfile_"] {
  top:calc(8 * var(--u)); right:58%; overflow:hidden;
  font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); font-weight:500; color:#1d1d1d;
}
[class*="st-key-pacs_reg_cseries_"] {
  top:calc(27 * var(--u)); right:58%; overflow:hidden; font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#626c79;
}
:is([class*="st-key-pacs_reg_cfile_"], [class*="st-key-pacs_reg_cseries_"])
  :is([data-testid="stElementContainer"], [data-testid="stMarkdown"], [data-testid="stMarkdownContainer"], p) {
  width:100% !important; overflow:hidden; text-overflow:ellipsis;
}
[class*="st-key-pacs_reg_cmod_"], [class*="st-key-pacs_reg_cdate_"], [class*="st-key-pacs_reg_ccount_"] {
  top:calc(17 * var(--u)); font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#333; font-variant-numeric:tabular-nums;
}
/* 파일 상태 배지: key에 상태 종류(ok / bad / wait / saved)가 들어갑니다. saved는 수정 화면의 기존 영상(등록됨). */
[class*="st-key-pacs_reg_cstatus_"] {
  top:calc(14 * var(--u)); height:calc(24 * var(--u)); display:flex !important; flex-direction:column; justify-content:center;
  padding:0 calc(10 * var(--u)); border-radius:calc(12 * var(--u));
  font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); font-weight:700;
}
[class*="st-key-pacs_reg_cstatus_ok_"] { background:#edf3fc; color:#3266c3; }
[class*="st-key-pacs_reg_cstatus_bad_"] { background:#fdeeee; color:#c43c3c; }
[class*="st-key-pacs_reg_cstatus_wait_"] { background:#f4f6f9; color:#626c79; }
[class*="st-key-pacs_reg_cstatus_saved_"] { background:#f2f2f2; color:#444; }
/* 줄 오른쪽 끝 삭제(×) 버튼: 28×28 */
[class*="st-key-pacs_reg_remove_"] {
  position:absolute !important; right:calc(16 * var(--u)); top:calc(12 * var(--u));
  width:calc(28 * var(--u)) !important; height:calc(28 * var(--u));
}
[class*="st-key-pacs_reg_remove_"] :is([data-testid="stElementContainer"], [data-testid="stButton"],
  [data-testid="stButton"] > div, [data-testid="stTooltipIcon"], [data-testid="stTooltipHoverTarget"], button) {
  width:100% !important; height:100% !important; min-height:0 !important;
}
[class*="st-key-pacs_reg_remove_"] button {
  padding:0 !important; border:0 !important; border-radius:calc(6 * var(--u)) !important; box-shadow:none !important;
  background:transparent @ICON_REMOVE@ center / calc(16 * var(--u)) no-repeat !important;
}
[class*="st-key-pacs_reg_remove_"] button:hover:enabled { background-color:#f1f3f6 !important; }
[class*="st-key-pacs_reg_remove_"] button p { font-size:0 !important; }

/* 빈 상태: (23, 145) 높이 194, 글자는 가운데 정렬 */
.st-key-pacs_reg_empty {
  left:calc(23 * var(--u)); right:calc(23 * var(--u)); top:calc(145 * var(--u)); height:calc(194 * var(--u));
  width:auto !important; background:#fff; border-radius:calc(7 * var(--u)); text-align:center;
}
.st-key-pacs_reg_empty_title {
  position:absolute !important; left:0; right:0; top:calc(53 * var(--u));
  font-size:calc(15 * var(--u)); line-height:calc(23 * var(--u)); font-weight:700; color:#1d1d1d;
}
.st-key-pacs_reg_empty_line1 {
  position:absolute !important; left:0; right:0; top:calc(106 * var(--u));
  font-size:calc(12 * var(--u)); line-height:calc(18 * var(--u)); color:#626c79;
}
.st-key-pacs_reg_empty_line2 {
  position:absolute !important; left:0; right:0; top:calc(124 * var(--u)); white-space:pre;
  font-size:calc(11 * var(--u)); line-height:calc(17 * var(--u)); color:#626c79;
}

/* ── 하단: 구분선(y 802) + 취소(88×42) · 검사 등록(수정 화면은 "수정 저장", 120×42) 버튼 ── */
.st-key-pacs_reg_main::after {
  content:''; position:absolute; left:calc(32 * var(--u)); right:calc(32 * var(--u)); top:calc(802 * var(--u));
  height:1px; background:#dee3e9;
}
.st-key-pacs_reg_cancel { right:calc(160 * var(--u)); top:calc(817 * var(--u)); width:calc(88 * var(--u)) !important; height:calc(42 * var(--u)); }
.st-key-pacs_reg_submit { right:calc(33 * var(--u)); top:calc(817 * var(--u)); width:calc(120 * var(--u)) !important; height:calc(42 * var(--u)); }
:is(.st-key-pacs_reg_cancel, .st-key-pacs_reg_submit) :is([data-testid="stElementContainer"], [data-testid="stButton"],
  [data-testid="stButton"] > div, [data-testid="stTooltipIcon"], [data-testid="stTooltipHoverTarget"], button) {
  width:100% !important; height:100% !important; min-height:0 !important;
}
:is(.st-key-pacs_reg_cancel, .st-key-pacs_reg_submit) button {
  padding:0 !important; border-radius:calc(7 * var(--u)) !important; box-shadow:none !important;
}
:is(.st-key-pacs_reg_cancel, .st-key-pacs_reg_submit) button p {
  font-size:calc(13 * var(--u)) !important; line-height:calc(20 * var(--u)) !important; font-weight:700 !important;
}
.st-key-pacs_reg_cancel button { background:#fff !important; border:1px solid #dee3e9 !important; }
.st-key-pacs_reg_cancel button p { color:#626c79 !important; }
.st-key-pacs_reg_cancel button:hover { background:#f4f6f9 !important; }
.st-key-pacs_reg_submit button { background:#3266c3 !important; border:0 !important; }
.st-key-pacs_reg_submit button p { color:#fff !important; }
.st-key-pacs_reg_submit button:hover:enabled { background:#2a57a8 !important; }
.st-key-pacs_reg_submit button:disabled { background:#dfe7f2 !important; cursor:not-allowed; opacity:1 !important; }
.st-key-pacs_reg_submit button:disabled p { color:#687c98 !important; }
</style>
"""

_STYLE = (
    _STYLE.replace("@ICON_LIST@", _css_icon(_ICON_LIST))
    .replace("@ICON_REGISTER@", _css_icon(_ICON_REGISTER))
    .replace("@ICON_REVIEW@", _css_icon(_ICON_REVIEW))
    .replace("@ICON_SHIELD@", _css_icon(_ICON_SHIELD))
    .replace("@ICON_CHEVRON_RIGHT@", _css_icon(_ICON_CHEVRON_RIGHT))
    .replace("@ICON_LOGOUT@", _css_icon(_ICON_LOGOUT))
    .replace("@ICON_AVATAR@", _css_icon(_ICON_AVATAR))
    .replace("@ICON_REMOVE@", _css_icon(_ICON_REMOVE))
)


# ── 이벤트 처리 (버튼 콜백) ──────────────────────────────────────────────────

def _emit(kind: str, target: str | None = None, draft: CaseDraft | None = None) -> None:
    st.session_state[_EVENT_KEY] = RegisterEvent(kind=kind, target=target, draft=draft)


def _form_key(name: str) -> str:
    # 입력칸을 비우기 위해, 칸의 key에 번호를 붙여 새 칸으로 다시 만듭니다(검사 목록의 검색 칸과 같은 방식).
    return f"_pacs_reg_{name}_{st.session_state.get(_FORM_VERSION_KEY, 0)}"


def _upload_key() -> str:
    return f"_pacs_reg_upload_{st.session_state.get(_UPLOAD_VERSION_KEY, 0)}"


def _on_upload() -> None:
    """파일 선택 창에서 고른 파일을 목록에 더하고, 파일 선택 칸은 비웁니다(다음에 또 고를 수 있게)."""
    state = st.session_state
    picked = state.get(_upload_key()) or []
    current = list(_files())
    known = {(item.file_name, len(item.data)) for item in current}
    duplicates = []
    for uploaded in picked:
        data = uploaded.getvalue()
        if (uploaded.name, len(data)) in known:
            duplicates.append(uploaded.name)
            continue
        known.add((uploaded.name, len(data)))
        file_id = state.get(_NEXT_FILE_ID_KEY, 1)
        state[_NEXT_FILE_ID_KEY] = file_id + 1
        current.append(read_dicom(uploaded.name, data, file_id))
    state[_FILES_KEY] = check_same_patient(tuple(current), _patient_reference())
    state[_UPLOAD_VERSION_KEY] = state.get(_UPLOAD_VERSION_KEY, 0) + 1
    if duplicates:
        state[_NOTICE_KEY] = MSG_DUPLICATE_FILE.format(names=", ".join(duplicates))


def _remove_file(file_id: int) -> None:
    st.session_state[_FILES_KEY] = check_same_patient(tuple(f for f in _files() if f.file_id != file_id), _patient_reference())


def _on_cancel() -> None:
    """입력한 내용을 지우고 검사 목록으로 돌아갑니다."""
    reset_state()
    _emit(EVENT_NAVIGATE, TARGET_CASE_LIST)


def _on_submit(exam_key: str) -> None:
    files = _files()
    exam_name = str(st.session_state.get(exam_key) or "")
    # 버튼이 눌린 시점의 값으로 한 번 더 확인합니다(화면 표시 후 값이 바뀌었을 수 있음).
    user = st.session_state.get(_SUBMIT_USER_KEY)
    edit = _editing()
    if user is None or not summarize(user, files, exam_name, edit).can_submit:
        return
    _emit(EVENT_UPDATE if edit else EVENT_SUBMIT, draft=build_draft(user, files, exam_name, edit))


# ── 화면 그리기 ──────────────────────────────────────────────────────────────

# Markdown 기호를 글자 그대로 보이게 하기 위해 앞에 \를 붙일 문자들입니다(검사 목록 화면과 같음).
_MARKDOWN_SYMBOLS = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~:$=&^])")


def _md(text: object) -> str:
    return _MARKDOWN_SYMBOLS.sub(r"\\\1", str(text))


def _text(key: str, body: object, align: str = "left") -> None:
    """글자 한 덩어리를 표시합니다. 글꼴·색·위치는 key 이름으로 위의 CSS가 정합니다."""
    with st.container(key=key):
        st.markdown("  \n".join(_md(line) for line in str(body).split("\n")), text_alignment=align)


def _render_sidebar() -> None:
    with st.container(key="pacs_reg_sidebar"):
        with st.container(key="pacs_reg_brand"):
            _text("pacs_reg_brand_name", "PACS")
            _text("pacs_reg_brand_sub", "의료영상 관리 시스템")
        _text("pacs_reg_workspace", "WORKSPACE")
        with st.container(key="pacs_reg_menu_list"):
            st.button(
                "검사 목록", key="pacs_reg_nav_list", width="stretch",
                on_click=_emit, args=(EVENT_NAVIGATE, TARGET_CASE_LIST),
            )
        with st.container(key="pacs_reg_menu_register"):
            st.button("검사 등록", key="pacs_reg_nav_register", width="stretch")
        with st.container(key="pacs_reg_menu_review"):
            st.button(
                "검토 배정", key="pacs_reg_nav_review", width="stretch",
                on_click=_emit, args=(EVENT_NAVIGATE, TARGET_REVIEW_ASSIGN),
            )
        with st.container(key="pacs_reg_server"):
            _text("pacs_reg_server_title", "서버 연결 정상")
            _text("pacs_reg_server_sub", "DICOM 서비스 운영 중")
        _text("pacs_reg_version", "PACS  /  v1.1")


@st.fragment(run_every=1)
def _render_clock() -> None:
    """상단 바의 실시간 시계. 1초마다 이 부분만 다시 그립니다."""
    _text("pacs_reg_clock", f"{datetime.now(KST):%Y. %m. %d. %H:%M:%S}")


def _render_header(user: CurrentUser, editing: bool = False) -> None:
    with st.container(key="pacs_reg_header"):
        _text("pacs_reg_crumb_home", "워크스페이스")
        _text("pacs_reg_crumb_page", "검사 수정" if editing else "검사 등록")
        with st.container(key="pacs_reg_user"):
            _text("pacs_reg_user_role", role_label(user))
            _text("pacs_reg_user_name", user.name or USER_NAME_FALLBACK)
        _render_clock()
        with st.container(key="pacs_reg_logout"):
            st.button("로그아웃", key="pacs_reg_logout_button", on_click=_emit, args=(EVENT_LOGOUT,))


def _render_title(summary: FormSummary, editing: bool = False) -> None:
    with st.container(key="pacs_reg_title"):
        with st.container(key="pacs_reg_title_text"):
            st.title("검사 수정" if editing else "검사 등록", anchor=False)
        _text(
            "pacs_reg_subtitle",
            "등록된 검사 정보를 확인하고 수정하세요." if editing else "환자 정보를 확인하고 DICOM 영상을 등록하세요.",
        )
    badge_kind = {
        BADGE_NEW: "new", BADGE_DRAFT: "draft", BADGE_CHECK: "check", BADGE_READY: "ready",
        BADGE_EDIT: "draft", BADGE_EDIT_READY: "ready",
    }[summary.badge]
    with st.container(key=f"pacs_reg_badge_{badge_kind}"):
        st.markdown(_md(summary.badge), text_alignment="center")


def _render_value_field(name: str, label: str, value: str, placeholder: str) -> None:
    """자동 입력 칸(읽기 전용). 값이 있으면 key가 value_set_, 없으면 value_empty_가 되어 글자색이 바뀝니다."""
    with st.container(key=f"pacs_reg_field_{name}"):
        _text(f"pacs_reg_label_{name}", label)
        _text(f"pacs_reg_value_{'set' if value else 'empty'}_{name}", value or placeholder)


def _render_info(user: CurrentUser, patient: DicomFile | None, editing: bool = False) -> str:
    """환자 및 검사 정보 카드를 그리고, 검사명 입력칸의 key를 돌려줍니다."""
    exam_key = _form_key("exam_name")
    with st.container(key="pacs_reg_info"):
        _text("pacs_reg_info_title", "환자 및 검사 정보")
        _text("pacs_reg_required", "* 필수 항목")
        auto = "영상에서 자동 입력"
        age = f"{patient.patient_age} 세" if patient and patient.patient_age is not None else ""
        _render_value_field("name", "환자 이름 *", patient.patient_name if patient else "", auto)
        _render_value_field("pid", "환자번호 *", patient.patient_id if patient else "", auto)
        _render_value_field("sex", "성별 *", patient.patient_sex if patient else "", "—")
        _render_value_field("age", "나이 *", age, "— 세")
        with st.container(key="pacs_reg_field_exam"):
            _text("pacs_reg_label_exam", "검사명 *")
            st.text_input(
                "검사명",
                key=exam_key,
                placeholder="검사명을 입력하세요",
                max_chars=MAX_EXAM_NAME_CHARS,
                disabled=not is_registrar(user),
                label_visibility="collapsed",
            )
        _render_value_field("modality", "검사 장비 *", patient.modality if patient else "", auto)
        _text(
            "pacs_reg_help",
            "등록된 검사의 환자 정보입니다. 검사명을 고치거나 같은 환자의 영상을 추가·삭제할 수 있습니다."
            if editing else
            "DICOM을 불러오면 환자 정보와 검사 장비가 자동으로 채워집니다. 등록 전 환자번호를 확인해 주세요.",
        )
    return exam_key


def _status_kind(status: str) -> str:
    if status == FILE_OK:
        return "ok"
    if status == FILE_SAVED:
        return "saved"
    if status == FILE_PENDING:
        return "wait"
    return "bad"


def _render_file_row(item: DicomFile, can_edit: bool) -> None:
    n = item.file_id
    with st.container(key=f"pacs_reg_row_{n}"):
        _text(f"pacs_reg_cfile_{n}", item.file_name)
        _text(f"pacs_reg_cseries_{n}", item.series or "-")
        _text(f"pacs_reg_cmod_{n}", item.modality or "-")
        _text(f"pacs_reg_cdate_{n}", f"{item.study_date:%Y.%m.%d}" if item.study_date else "-")
        _text(f"pacs_reg_ccount_{n}", item.image_count if item.image_count is not None else "-")
        with st.container(key=f"pacs_reg_cstatus_{_status_kind(item.status)}_{n}"):
            st.markdown(_md(item.status), text_alignment="center")
        with st.container(key=f"pacs_reg_remove_{n}"):
            st.button(
                f"{item.file_name} 빼기", key=f"pacs_reg_drop_{n}", disabled=not can_edit,
                help=MSG_PENDING_READER if item.status == FILE_PENDING else None,
                on_click=_remove_file, args=(n,),
            )


def _render_dicom(user: CurrentUser, files: tuple[DicomFile, ...]) -> None:
    can_edit = is_registrar(user)
    with st.container(key="pacs_reg_dicom"):
        _text("pacs_reg_dicom_title", "DICOM 영상")
        _text("pacs_reg_count", len(files), align="center")
        _text("pacs_reg_dicom_sub", "동일 환자의 영상을 불러온 후 검사 정보를 확인하세요.")
        with st.container(key="pacs_reg_upload"):
            st.file_uploader(
                "DICOM 불러오기",
                type=list(DICOM_EXTENSIONS),
                accept_multiple_files=True,
                key=_upload_key(),
                on_change=_on_upload,
                disabled=not can_edit,
                label_visibility="collapsed",
            )
        with st.container(key="pacs_reg_thead"):
            for name, label in (
                ("file", "파일명 / 시리즈"), ("mod", "검사 장비"), ("date", "검사일"), ("count", "영상 수"), ("status", "상태"),
            ):
                _text(f"pacs_reg_th_{name}", label)
        if not files:
            with st.container(key="pacs_reg_empty"):
                _text("pacs_reg_empty_title", "DICOM 영상을 불러오세요", align="center")
                _text(
                    "pacs_reg_empty_line1",
                    "상단의 불러오기 버튼으로 파일을 선택하세요. 여러 파일을 한 번에 추가할 수 있습니다.",
                    align="center",
                )
                _text(
                    "pacs_reg_empty_line2",
                    "지원 형식: DICOM (.dcm)  ·  등록 전 영상의 환자 정보가 일치하는지 확인해 주세요.",
                    align="center",
                )
            return
        with st.container(key="pacs_reg_rows"):
            for item in files:
                _render_file_row(item, can_edit)


def _render_footer(user: CurrentUser, summary: FormSummary, exam_key: str, editing: bool = False) -> None:
    st.session_state[_SUBMIT_USER_KEY] = user
    with st.container(key="pacs_reg_cancel"):
        st.button("취소", key="pacs_reg_cancel_button", width="stretch", on_click=_on_cancel)
    with st.container(key="pacs_reg_submit"):
        st.button(
            "수정 저장" if editing else "검사 등록",
            key="pacs_reg_submit_button",
            width="stretch",
            disabled=not summary.can_submit,
            help=summary.block_reason,
            on_click=_on_submit,
            args=(exam_key,),
        )


def render(user: CurrentUser) -> RegisterEvent | None:
    """검사 등록(또는 수정) 화면을 그립니다. 화면 이동·등록·수정 저장 버튼이 눌리면 RegisterEvent를 돌려줍니다."""
    st.html(_STYLE)

    notice = st.session_state.pop(_NOTICE_KEY, None)
    if notice:
        st.toast(notice)

    files = _files()
    exam_name = str(st.session_state.get(_form_key("exam_name")) or "")
    edit = _editing()
    editing = edit is not None
    summary = summarize(user, files, exam_name, edit)

    with st.container(key="pacs_reg_stage"):
        _render_sidebar()
        _render_header(user, editing)
        with st.container(key="pacs_reg_main"):
            _render_title(summary, editing)
            exam_key = _render_info(user, summary.patient, editing)
            _render_dicom(user, files)
            _render_footer(user, summary, exam_key, editing)

    return st.session_state.pop(_EVENT_KEY, None)


# ── 단독 실행 미리보기 ───────────────────────────────────────────────────────

def _preview_user() -> CurrentUser:
    """미리보기 사용자. 주소 끝에 ?role=REVIEWER처럼 붙이면 그 역할로 봅니다(없거나 잘못된 값이면 등록 담당자)."""
    role = _clean(st.query_params.get("role")).upper()
    if role not in ROLE_LABELS:
        role = ROLE_REGISTRAR
    return current_user_from_profile({"uid": f"preview-{role.lower()}", "role": role, "name": "김의사"})


if __name__ == "__main__":
    # 단독 실행 시 샘플 사용자로 화면을 미리 봅니다. 화면 이동·저장은 app.py에서 처리할 예정이라 안내만 띄웁니다.
    st.set_page_config(page_title="PACS 검사 등록", layout="wide")
    event = render(_preview_user())
    if event is not None and event.kind == EVENT_LOGOUT:
        reset_state()
        st.session_state[_NOTICE_KEY] = "입력한 내용을 지웠습니다. 로그인 화면 이동은 app.py 연결 후 동작합니다."
        st.rerun()
    elif event is not None and event.kind in (EVENT_SUBMIT, EVENT_UPDATE) and event.draft is not None:
        draft = event.draft
        action = "수정할" if event.kind == EVENT_UPDATE else "등록할"
        st.toast(f"{action} 내용: {draft.patient_name} · {draft.exam_name} · 영상 {len(draft.files)}개 (저장은 아직 연결 전)")
    elif event is not None:
        targets = {TARGET_CASE_LIST: "검사 목록", TARGET_REVIEW_ASSIGN: "검토 배정"}
        st.toast(f"{targets.get(event.target or '', '해당')} 화면 이동은 app.py 연결 후 동작합니다.")
