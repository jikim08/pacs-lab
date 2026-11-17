# src/exceptions.py
"""서비스에서 사용하는 공통 예외."""


class AppError(Exception):
    """애플리케이션 공통 오류."""


class NotFoundError(AppError):
    """요청한 데이터가 없는 경우."""


class PermissionDeniedError(AppError):
    """작업 권한이 없는 경우."""


class AuthenticationError(AppError):
    """로그인이 필요하거나 인증에 실패한 경우."""


class ValidationError(AppError):
    """입력값이나 파일, ROI, 파라미터가 올바르지 않은 경우."""


class ConflictError(AppError):
    """중복 요청이나 현재 상태에서 허용되지 않는 작업인 경우."""


class StorageError(AppError):
    """파일 또는 DB 저장과 조회에 실패한 경우."""