"""Firestore 영상 파일 메타데이터 관리."""
import re
from typing import cast

# firestroe가 던지는 Google 쪽 예외들 처리
from google.api_core.exceptions import Conflict, GoogleAPICallError, RetryError
from google.cloud import firestore
from google.cloud.firestore_v1 import DocumentSnapshot 


from src.exception import ConflictError, StorageError, ValidationError
from src.firebase_client import get_db
from src.models import FileInfo

# 컬렉션 이름을 한 곳에 둠.
_FILES_COLLECTION = "files"

# file_storage.py와 같은 ID 규칙 (file_id가 저장 경로에도 쓰이므로)
_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


def _validate_id(value: str, name: str) -> None:
    """ID가 허용된 문자로만 구성되었는지 확인한다."""
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise ValidationError(f"{name} 형식이 올바르지 않습니다.")

# Firesstore 문서를 FileInfo로 바꿔야. 실제로 file을 가져와서 다룰 수 있기 때문이다.
def _to_file_info(document: DocumentSnapshot) -> FileInfo:
    """Firestore 문서를 FileInfo로 바꾼다. 문서 ID를 file_id로 넣는다."""
    return cast(FileInfo, {**document.to_dict(), "file_id": document.id})

def get_file_info(file_id: str) -> FileInfo | None:
    """파일 정보를 조회한다.

    문서 ID를 file_id 필드에 포함한다.
    없으면 None을 반환한다.
    """
    _validate_id(file_id, "file_id")

    try:
        document = get_db().collection(_FILES_COLLECTION).document(file_id).get()
    except (GoogleAPICallError, RetryError) as error:
        raise StorageError("파일 정보를 조회하지 못했습니다.") from error

    if not document.exists:
        return None

    return _to_file_info(document)


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
    created_at은 전달값과 관계없이 Firestore 서버 시각으로 저장한다.
    """
    # 딕셔너리 복사한 값을 이용. 그렇지 않으면 원본을 수정하게 됨.
    data = dict(file_info)
    file_id = data.pop("file_id", None)
    _validate_id(file_id, "file_id")

    data["created_at"] = firestore.SERVER_TIMESTAMP

    try:
        get_db().collection(_FILES_COLLECTION).document(file_id).create(data)
    except Conflict as error:
        raise ConflictError("같은 ID의 파일 정보가 이미 존재합니다.") from error
    except (GoogleAPICallError, RetryError) as error:
        raise StorageError("파일 정보를 저장하지 못했습니다.") from error