"""Read the team's accepted image formats and encode grayscale results."""

from io import BytesIO

import numpy as np
from PIL import Image, UnidentifiedImageError

from src.config import MAX_IMAGE_SIZE_BYTES, MAX_IMAGE_SIZE_MB
from src.exception import ValidationError


def validate_grayscale_image(image: np.ndarray) -> None:
    """Require a nonempty, two-dimensional array of unsigned 8-bit pixels."""
    if (
        not isinstance(image, np.ndarray)
        or image.ndim != 2
        or image.size == 0
        or image.dtype != np.uint8
    ):
        raise ValidationError("영상은 비어 있지 않은 8비트 회색조 배열이어야 합니다.")


def decode_image(file_bytes: bytes) -> np.ndarray:
    """Decode PNG or JPEG bytes into a writable uint8 array (height, width)."""
    if not isinstance(file_bytes, bytes) or not file_bytes:
        raise ValidationError("영상 파일이 비어 있거나 올바르지 않습니다.")
    if len(file_bytes) > MAX_IMAGE_SIZE_BYTES:
        raise ValidationError("영상 파일이 허용 크기를 초과했습니다.")

    try:
        with Image.open(BytesIO(file_bytes), formats=("PNG", "JPEG")) as source:
            pixel_limit = Image.MAX_IMAGE_PIXELS
            if pixel_limit is not None and source.width * source.height > pixel_limit:
                raise ValidationError("영상의 픽셀 수가 허용 범위를 초과했습니다.")
            if getattr(source, "n_frames", 1) != 1:
                raise ValidationError("한 장으로 된 PNG 또는 JPEG 영상을 선택해 주세요.")
            source.verify()

        # Pillow requires reopening the image to decode pixels after verify().
        with Image.open(BytesIO(file_bytes), formats=("PNG", "JPEG")) as source:
            source.load()
            if source.format == "PNG" and (
                source.mode == "I" or source.mode.startswith("I;16")
            ):
                return (np.asarray(source, dtype=np.uint16) >> 8).astype(np.uint8)
            return np.array(source.convert("L"), dtype=np.uint8)
    except Image.DecompressionBombError as error:
        raise ValidationError("영상의 픽셀 수가 허용 범위를 초과했습니다.") from error
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as error:
        raise ValidationError("지원하지 않는 형식이거나 손상된 영상입니다. PNG 또는 JPEG를 선택해 주세요.") from error


def encode_png(image: np.ndarray) -> bytes:
    """Return PNG bytes that fit the image reader's file size limit."""
    validate_grayscale_image(image)
    try:
        with BytesIO() as output:
            Image.fromarray(image).save(output, format="PNG")
            encoded = output.getvalue()
    except (OSError, ValueError) as error:
        raise ValidationError("영상 결과를 PNG로 변환할 수 없습니다.") from error
    if len(encoded) > MAX_IMAGE_SIZE_BYTES:
        raise ValidationError(f"처리 결과가 저장 가능한 크기({MAX_IMAGE_SIZE_MB} MiB)를 초과했어요.")
    return encoded
