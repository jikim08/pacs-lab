"""FILE_STORAGE_ROOT 아래의 실제 파일 관리."""


def save_file(
    case_id: str,
    file_id: str,
    file_bytes: bytes,
    extension: str,
) -> str:
    """파일을 저장하고 저장 루트 기준 상대 경로를 반환한다.

    extension 예: png, jpg — 점 제외
    반환 예: cases/{case_id}/files/{file_id}.png

    ID와 확장자를 검사하고 저장 루트 밖 접근을 차단한다.
    기존 파일을 덮어쓰지 않는다.

    잘못된 입력: ValidationError
    파일 중복: ConflictError
    저장 실패: StorageError
    """
    raise NotImplementedError


def read_file(storage_path: str) -> bytes:
    """상대 경로에 해당하는 파일을 바이트로 읽는다.

    저장 루트 밖 접근을 차단한다.

    잘못된 경로: ValidationError
    파일 없음: NotFoundError
    읽기 실패: StorageError
    """
    raise NotImplementedError


def delete_file(storage_path: str) -> None:
    """파일을 삭제한다.

    저장 실패 후 정리 작업에도 사용한다.
    저장 루트 밖 접근을 차단한다.
    이미 없는 파일은 정상 종료한다.

    잘못된 경로: ValidationError
    삭제 실패: StorageError
    """
    raise NotImplementedError