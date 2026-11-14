'''케이스 등록 규칙 처리 및 repositories에 등록 요청'''
from ..repositories.case import save_case

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