"""공통 데이터 구조와 역할 및 상태 정의."""

from datetime import datetime
from typing import Literal, NotRequired, TypedDict


# ── 역할과 상태 ──────────────────────────────────────

UserRole = Literal["REGISTRAR", "REVIEWER", "ADMIN"]

CaseStatus = Literal["검토 대기", "검토 중", "검토 완료"]

ProcessingStatus = Literal["PENDING", "RUNNING", "COMPLETED", "FAILED"]

FileKind = Literal["ORIGINAL", "RESULT"]

ReviewKind = Literal["MEMO", "REPORT"]

HistoryKind = Literal["ASSIGNMENT_CHANGED", "STATUS_CHANGED"]


# ── 사용자 / 진료과 / 환자 ───────────────────────────

class User(TypedDict):
    # Firestore 문서 ID도 Firebase UID를 사용
    uid: str
    name: str
    role: UserRole
    department_id: str | None
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class Department(TypedDict):
    department_id: str
    name: str


class Patient(TypedDict):
    patient_id: str
    name: str
    birth_date: str              # YYYY-MM-DD
    sex: str                    # 허용 값은 팀에서 확정
    created_at: datetime
    updated_at: datetime


# ── 케이스 ───────────────────────────────────────────

class CaseCreateData(TypedDict):
    """현재 등록 서비스가 Repository에 전달하는 데이터."""

    patient_id: str
    department_id: str
    exam_type: str
    exam_info: str
    created_by: str              # 사용자 UID
    status: CaseStatus

    # 기존 등록 코드에서도 사용할 수 있도록 선택 항목으로 정의
    exam_at: NotRequired[datetime | None]
    registration_note: NotRequired[str]


class Case(CaseCreateData):
    """Repository에서 반환하는 저장된 케이스."""

    case_id: str
    created_at: datetime
    updated_at: datetime


class CaseAssignment(TypedDict):
    # Firestore 문서 ID는 case_id: 케이스당 현재 담당자 1명
    case_id: str
    user_id: str                 # 배정받은 사용자 UID
    assigned_by: str            # 배정한 사용자 UID
    assigned_at: datetime


class CaseDetail(Case):
    """상세 화면용 데이터. 케이스 문서에 그대로 저장하지 않음."""

    patient: Patient | None
    department: Department | None


# ── 이미지 처리 ──────────────────────────────────────

class ROI(TypedDict):
    # 원본 이미지의 픽셀 좌표 기준
    x: int
    y: int
    width: int
    height: int


class ROIStatistics(TypedDict):
    mean: float
    variance: float
    histogram: list[int]         # 각 구간의 픽셀 개수
    bin_edges: list[float]       # 히스토그램 구간 경계


# 알고리즘마다 파라미터 이름이 달라 공통 값 형식만 정의
ParameterValue = str | int | float | bool | None
ProcessingParameters = dict[str, ParameterValue]


class ProcessingType(TypedDict):
    processing_type_id: str
    name: str
    description: str
    version: str


class ProcessingResult(TypedDict):
    processing_result_id: str
    case_id: str
    processing_type_id: str

    # 실행 이후 처리 종류 정보가 바뀌어도 재현 가능하도록 보존
    algorithm_version: str
    parameters: ProcessingParameters

    performed_by: str           # 사용자 UID
    input_file_id: str
    status: ProcessingStatus

    roi: ROI | None             # None이면 전체 이미지
    statistics: ROIStatistics | None
    error_message: str | None

    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


# ── 원본 / 결과 파일 ─────────────────────────────────

class FileInfo(TypedDict):
    file_id: str
    case_id: str

    # 원본 파일이면 None, 결과 파일이면 해당 처리 결과 ID
    processing_result_id: str | None
    kind: FileKind

    original_name: str
    storage_path: str           # FILE_STORAGE_ROOT 기준 상대 경로
    content_type: str           # 예: image/png
    size_bytes: int

    uploaded_by: str            # 사용자 UID
    created_at: datetime


# ── 검토 기록 / 업무 변경 이력 ────────────────────────

class ReviewRecord(TypedDict):
    review_id: str
    case_id: str
    kind: ReviewKind
    content: str
    author_id: str              # 사용자 UID

    # 특정 영상이나 처리 결과에 연결하지 않으면 None
    file_id: str | None
    processing_result_id: str | None

    created_at: datetime
    updated_at: datetime


class CaseHistory(TypedDict):
    history_id: str
    case_id: str
    kind: HistoryKind

    # 상태 변경이면 상태명, 담당자 변경이면 사용자 UID
    # 최초 배정의 이전 값은 None
    before_value: str | None
    after_value: str | None

    changed_by: str             # 사용자 UID
    created_at: datetime