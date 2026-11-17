"""Firestore 영상 파일 메타데이터 관리."""

from src.models import FileInfo


def get_file_info(file_id: str) -> FileInfo | None:
    """파일 정보를 조회한다.

    문서 ID를 file_id 필드에 포함한다.
    없으면 None을 반환한다.
    """
    raise NotImplementedError


def list_case_files(
    case_id: str,
    kind: str | None = None,
) -> list[FileInfo]:
    """케이스에 연결된 파일 정보를 조회한다.

    kind는 None, ORIGINAL, RESULT 중 하나이다.
    None이면 원본과 결과를 모두 조회한다.
    결과가 없으면 빈 리스트를 반환한다.
    """
    raise NotImplementedError


def save_file_info(file_info: FileInfo) -> None:
    """지정된 file_id로 파일 정보를 신규 저장한다.

    같은 ID가 이미 존재하면 ConflictError를 발생시킨다.
    """
    raise NotImplementedError