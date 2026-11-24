"""FILE_STORAGE_ROOT 아래의 실제 파일 관리."""
import re
from pathlib import Path

from src.config import FILE_STORAGE_ROOT,ALLOWED_IMAGE_EXTENSIONS
from src.exception import NotFoundError, StorageError, ConflictError, ValidationError

# 영문, 숫자, 밑줄(_), 하이픈(-)만 허용하는 ID 규칙
_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")

def _validate_id(value: str, name: str) -> None:
    """ID가 허용된 문자로만 구성되었는지 확인한다."""
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise ValidationError(f"{name} 형식이 올바르지 않습니다.")

def _resolve_path(storage_path: str) -> Path:
    """상대 경로를 저장 루트 안의 실제 경로로 바꾼다."""
    # 비어있거나 문자열이 아님
    if not isinstance(storage_path, str) or not storage_path:
        raise ValidationError("저장 경로가 필요합니다.")

    # 형식의 문제 있을 때
    if "\\" in storage_path or Path(storage_path).is_absolute():
        raise ValidationError("저장 경로 형식이 올바르지 않습니다.")

    # 실제 경로 계산
    path = (FILE_STORAGE_ROOT / storage_path).resolve()

    # 계산한 경로가 저장 루트 밖으로 나갔는가 검사.
    if path == FILE_STORAGE_ROOT or not path.is_relative_to(FILE_STORAGE_ROOT):
        raise ValidationError("저장소 밖의 경로에는 접근할 수 없습니다.")

    return path


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
    _validate_id(case_id, "case_id")
    _validate_id(file_id, "file_id")

    if not isinstance(extension, str) or extension.lower() not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValidationError("지원하지 않는 파일 확장자입니다.")

    if not isinstance(file_bytes, bytes) or not file_bytes:
        raise ValidationError("저장할 파일 내용이 비어 있습니다.")

    storage_path = f"cases/{case_id}/files/{file_id}.{extension.lower()}"
    path = _resolve_path(storage_path)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as file:
            file.write(file_bytes)
    except FileExistsError as error:
        raise ConflictError("같은 ID의 파일이 이미 존재합니다.") from error
    except OSError as error:
        # 쓰다가 실패해 남은 불완전한 파일 정리
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise StorageError("파일을 저장하는 중 오류가 발생했습니다.") from error

    return storage_path


def read_file(storage_path: str) -> bytes:
    """상대 경로에 해당하는 파일을 바이트로 읽는다.

    저장 루트 밖 접근을 차단한다.

    잘못된 경로: ValidationError
    파일 없음: NotFoundError
    읽기 실패: StorageError
    """
    path = _resolve_path(storage_path)

    try:
        return path.read_bytes()
    except FileNotFoundError as error:
        raise NotFoundError("저장된 파일을 찾을 수 없습니다.") from error
    except OSError as error:
        raise StorageError("파일을 읽는 중 오류가 발생했습니다.") from error


def delete_file(storage_path: str) -> None:
    """파일을 삭제한다.

    저장 실패 후 정리 작업에도 사용한다.
    저장 루트 밖 접근을 차단한다.
    이미 없는 파일은 정상 종료한다.

    잘못된 경로: ValidationError
    삭제 실패: StorageError
    """
    path = _resolve_path(storage_path)

    try:
        path.unlink(missing_ok=True)
    except OSError as error:
        raise StorageError("파일을 삭제하는 중 오류가 발생했습니다.") from error