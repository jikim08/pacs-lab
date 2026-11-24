'''케이스 등록·조회 규칙 처리 및 repositories에 요청'''
from src.repositories.case import save_case, list_cases, get_case
from src.repositories.patient import patient_exists, get_patient
from src.repositories.department import department_exists, get_department
from src.services.access_policy import require_case_registration, require_case_read
from src.exception import NotFoundError, ValidationError

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