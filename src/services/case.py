'''케이스 등록 규칙 처리 및 repositories에 등록 요청'''
from src.repositories.case import save_case
from src.repositories.patient import patient_exists
from src.repositories.department import department_exists

def create_case(
    patient_id: str,
    department_id: str,
    exam_type: str,
    exam_info: str,
    created_by: str,
) -> str:
    # 필수 항목 조사
    required_fields = {
        "환자" : patient_id,
        "진료과" : department_id,
        "검사 종류" : exam_type,
        "등록자" : created_by
    }
    #필수 항목 미포함 시 ValueError
    for name, value in required_fields.items():
        if not value or not value.strip():
            raise ValueError(f"{name} 정보가 필요합니다.")
    
    # 앞뒤 공백을 제거한 ID로 조회
    # 환자 및 진료과 존재 여부 확인
    patient_id = patient_id.strip()
    department_id = department_id.strip()

    if not patient_exists(patient_id):
        raise ValueError("등록되지 않은 환자입니다.")

    if not department_exists(department_id):
        raise ValueError("등록되지 않은 진료과입니다.")
    
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