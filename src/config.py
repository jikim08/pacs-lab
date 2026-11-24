# src/config.py
import os
from pathlib import Path

from dotenv import load_dotenv


# 프로젝트 최상위 경로
BASE_DIR = Path(__file__).resolve().parent.parent

# .env 읽기. 이미 설정된 환경변수는 덮어쓰지 않음
load_dotenv(BASE_DIR / ".env")


# Firebase 설정
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID", "")
FIREBASE_WEB_API_KEY = os.getenv("FIREBASE_WEB_API_KEY", "")
FIREBASE_STORAGE_BUCKET = os.getenv("FIREBASE_STORAGE_BUCKET", "")

# 상대 경로는 프로젝트 최상위 기준으로 해석
_credentials = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")

FIREBASE_CREDENTIALS_PATH = (
    (BASE_DIR / _credentials).resolve()
    if _credentials
    else None
)


# 이미지 업로드 설정
ALLOWED_IMAGE_EXTENSIONS = ("png", "jpg", "jpeg")
MAX_IMAGE_SIZE_MB = 20
MAX_IMAGE_SIZE_BYTES = MAX_IMAGE_SIZE_MB * 1024 * 1024

# 스토리지 경로
_storage_root = Path(os.getenv("FILE_STORAGE_ROOT", "data/storage"))

FILE_STORAGE_ROOT = (
    _storage_root
    if _storage_root.is_absolute()
    else BASE_DIR / _storage_root
).resolve()