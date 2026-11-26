'''케이스 등록·조회 규칙 처리 및 repositories에 요청'''
import re
from datetime import date, datetime, time, timedelta, timezone
from typing import get_args

from src.models import CaseStatus
from src.repositories.case import save_case, list_cases, get_case, search_cases
from src.repositories.patient import patient_exists, get_patient
from src.repositories.department import department_exists, get_department
from src.services.access_policy import require_case_registration, require_case_read
from src.exception import NotFoundError, ValidationError


# 한국 시간(UTC+9). 등록일 기간은 한국 날짜 기준
_KST = timezone(timedelta(hours=9))

# 케이스 ID 규칙 (Firestore 자동 ID: 영문·숫자)
_CASE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")

def create_case(
    patient_id: str,
    department_id: str,
    exam_type: str,
    exam_info: str,
    created_by: str,
) -> str:

    # 등록 권한 검사
    created_by = created_by.strip()
    require_case_registration(created_by)

    # 필수 항목 조사
    required_fields = {
        "환자" : patient_id,
        "진료과" : department_id,
        "검사 종류" : exam_type,
        "등록자" : created_by
    }
    
    #필수 항목 미포함 시 ValidationError
    for name, value in required_fields.items():
        if not value or not value.strip():
            raise ValidationError(f"{name} 정보가 필요합니다.")
    
    # 앞뒤 공백을 제거한 ID로 조회
    # 환자 및 진료과 존재 여부 확인
    patient_id = patient_id.strip()
    department_id = department_id.strip()

    if not patient_exists(patient_id):
        raise ValidationError("등록되지 않은 환자입니다.")

    if not department_exists(department_id):
        raise ValidationError("등록되지 않은 진료과입니다.")
    
    case_data = {
        "patient_id": patient_id.strip(),
        "department_id": department_id.strip(),
        "exam_type": exam_type.strip(),
        "exam_info": exam_info.strip(),
        "created_by": created_by.strip(),
        "status": "검토 대기",
    }

    #repositories에 저장 요청
    return save_case(case_data)


# 케이스 목록 조회
# 결과가 없으면 빈 리스트 반환 - 화면에서 빈 목록 안내
def get_case_list(uid: str, limit: int = 50) -> list[dict]:
    require_case_read(uid)

    if limit <= 0:
        raise ValidationError("조회 개수는 1 이상이어야 합니다.")

    return list_cases(limit)


# 케이스 상세 조회
# 없는 케이스는 NotFoundError
# 환자·진료과 정보를 함께 반환, 참조 대상이 없으면 None (화면에서 찾을 수 없음 안내)
# 담당자·영상·처리 결과는 해당 저장 기능 구현 후 연결
def get_case_detail(uid: str, case_id: str) -> dict:
    require_case_read(uid)

    if not case_id or not case_id.strip():
        raise ValidationError("케이스 ID가 필요합니다.")

    case = get_case(case_id.strip())

    if case is None:
        raise NotFoundError("존재하지 않는 케이스입니다.")

    patient_id = case.get("patient_id")
    department_id = case.get("department_id")

    return {
        **case,
        "patient": get_patient(patient_id) if patient_id else None,
        "department": get_department(department_id) if department_id else None,
    }



# 검색어 입력값 정리: 없거나 빈 문자열이면 None(조건 없음)
def _optional_text(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{name} 형식이 올바르지 않습니다.")
    return value.strip() or None


# 한국 시간 기준 그날 0시
def _start_of_day_kst(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=_KST)


# 케이스 검색·필터 (CASE-04, CASE-05)
# 모든 조건은 선택. 여러 조건을 주면 모두 만족하는 케이스만 등록 최신순으로 반환
# 기간은 등록일(created_at) 기준, 한국 날짜로 start_date ~ end_date (양 끝 포함)
def search_case_list(
    uid: str,
    case_id: str | None = None,
    status: str | None = None,
    exam_type: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = 50,
) -> list[dict]:
    require_case_read(uid)

    if limit <= 0:
        raise ValidationError("조회 개수는 1 이상이어야 합니다.")

    case_id = _optional_text(case_id, "케이스 ID")
    if case_id is not None and _CASE_ID_PATTERN.fullmatch(case_id) is None:
        raise ValidationError("케이스 ID 형식이 올바르지 않습니다.")

    status = _optional_text(status, "검토 상태")
    if status is not None and status not in get_args(CaseStatus):
        raise ValidationError("검토 상태는 검토 대기, 검토 중, 검토 완료 중 하나여야 합니다.")

    exam_type = _optional_text(exam_type, "검사 종류")

    for value, name in ((start_date, "시작일"), (end_date, "종료일")):
        if value is not None and (not isinstance(value, date) or isinstance(value, datetime)):
            raise ValidationError(f"{name}은 날짜여야 합니다.")

    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValidationError("시작일은 종료일보다 늦을 수 없습니다.")

    created_from = _start_of_day_kst(start_date) if start_date is not None else None
    created_to = _start_of_day_kst(end_date + timedelta(days=1)) if end_date is not None else None

    cases = search_cases(
        case_id=case_id,
        status=status,
        exam_type=exam_type,
        created_from=created_from,
        created_to=created_to,
    )
    return cases[:limit]