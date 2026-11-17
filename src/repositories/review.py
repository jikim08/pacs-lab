"""Firestore 검토 기록과 업무 변경 이력 관리."""

from src.models import CaseHistory, ReviewRecord


def save_review_record(record: ReviewRecord) -> None:
    """메모 또는 리포트를 신규 저장한다.

    같은 review_id가 이미 존재하면 ConflictError를 발생시킨다.
    """
    raise NotImplementedError


def list_review_records(
    case_id: str,
) -> list[ReviewRecord]:
    """케이스의 검토 기록을 작성 시간 오름차순으로 반환한다.

    결과가 없으면 빈 리스트를 반환한다.
    """
    raise NotImplementedError


def save_case_history(history: CaseHistory) -> None:
    """담당자 또는 상태 변경 이력을 신규 저장한다.

    같은 history_id가 이미 존재하면 ConflictError를 발생시킨다.
    """
    raise NotImplementedError


def list_case_history(case_id: str) -> list[CaseHistory]:
    """케이스의 변경 이력을 시간 오름차순으로 반환한다.

    결과가 없으면 빈 리스트를 반환한다.
    """
    raise NotImplementedError