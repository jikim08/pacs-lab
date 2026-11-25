'''원본 영상 업로드·조회·다운로드 흐름 조정.

권한 확인, 파일 크기·확장자 검증, 저장소 저장과 files 문서 저장, 실패 시 정리를 담당한다.
이미지 디코딩·손상 확인·크기 추출은 processing/image_io.py를 호출한다.
'''
from datetime import datetime, timezone
from pathlib import PurePath
from uuid import uuid4

from src.config import ALLOWED_IMAGE_EXTENSIONS, MAX_IMAGE_SIZE_BYTES
from src.exception import NotFoundError, StorageError, ValidationError
from src.models import Case, FileInfo
from src.processing.image_io import decode_image
from src.repositories import case as cases, file_storage, image as images
from src.services import access_policy


# 확장자별 MIME 형식 (files.content_type에 저장)
_CONTENT_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "dcm": "application/dicom",
}


def _get_case(case_id: str) -> Case:
    """케이스를 조회한다. 없으면 NotFoundError."""
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValidationError("케이스 ID가 필요합니다.")

    case = cases.get_case(case_id.strip())
    if case is None:
        raise NotFoundError("존재하지 않는 케이스입니다.")
    return case


def _get_extension(original_name: str) -> str:
    """파일 이름에서 점을 뺀 소문자 확장자를 꺼내고 허용 여부를 검사한다."""
    if not isinstance(original_name, str) or not original_name.strip():
        raise ValidationError("파일 이름이 필요합니다.")

    extension = PurePath(original_name.strip()).suffix.lower()[1:]
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValidationError("DICOM, PNG 또는 JPEG 영상만 업로드할 수 있습니다.")
    return extension


def upload_original_image(
    uid: str,
    case_id: str,
    original_name: str,
    file_bytes: bytes,
) -> FileInfo:
    """원본 영상을 저장소에 저장하고 files 문서를 등록한다.

    권한 없음: PermissionError (access_policy)
    케이스 없음: NotFoundError
    형식·크기·손상 오류: ValidationError
    저장 실패: StorageError (메타데이터 저장 실패 시 저장한 파일을 정리)
    """
    access_policy.require_case_registration(uid)
    case = _get_case(case_id)
    extension = _get_extension(original_name)

    if not isinstance(file_bytes, bytes) or not file_bytes:
        raise ValidationError("업로드할 파일이 비어 있습니다.")
    if len(file_bytes) > MAX_IMAGE_SIZE_BYTES:
        raise ValidationError("영상 파일이 허용 크기를 초과했습니다.")

    # 실제로 열리는 영상인지 확인 (손상·위장 파일 거부)
    decode_image(file_bytes)

    file_id = uuid4().hex
    storage_path = file_storage.save_file(case["case_id"], file_id, file_bytes, extension)

    file_info: FileInfo = {
        "file_id": file_id,
        "case_id": case["case_id"],
        "processing_result_id": None,
        "kind": "ORIGINAL",
        "original_name": original_name.strip(),
        "storage_path": storage_path,
        "content_type": _CONTENT_TYPES[extension],
        "size_bytes": len(file_bytes),
        "uploaded_by": uid,
        "created_at": datetime.now(timezone.utc),
    }

    try:
        images.save_file_info(file_info)
    except Exception:
        # 메타데이터 저장 실패 → 디스크에 남은 파일 정리 후 원래 오류 전달
        try:
            file_storage.delete_file(storage_path)
        except StorageError:
            pass
        raise

    return file_info

def _require_case_read(uid: str, case_id: str) -> Case:
    """사용자의 조회 권한을 검사하고 케이스를 조회한다.

    현재는 로그인 여부만 검사한다 (케이스별 권한은 require_case_access 구현 후 연결).
    """
    # TODO: require_case_access 구현 후 아래로 교체 (케이스별 ASSIGNED/READ_ONLY 권한 검사)
    #   case = _get_case(case_id)
    #   access_policy.require_case_access(uid, case, cases.get_case_assignment(case["case_id"]))
    access_policy.require_case_read(uid)
    return _get_case(case_id)


def list_images(
    uid: str,
    case_id: str,
    kind: str | None = None,
) -> list[FileInfo]:
    """케이스의 영상 목록을 조회한다.

    kind: None(전체), ORIGINAL(원본), RESULT(처리 결과)
    권한 없음: PermissionError (access_policy)
    케이스 없음: NotFoundError
    """
    case = _require_case_read(uid, case_id)
    return images.list_case_files(case["case_id"], kind)


def get_image(uid: str, file_id: str) -> FileInfo:
    """영상 1건의 정보를 조회한다.

    영상이 속한 케이스의 접근 권한을 검사한다.
    영상 없음: NotFoundError
    권한 없음: PermissionError (access_policy)
    """
    file_info = images.get_file_info(file_id)
    if file_info is None:
        raise NotFoundError("존재하지 않는 영상입니다.")

    _require_case_read(uid, file_info["case_id"])
    return file_info