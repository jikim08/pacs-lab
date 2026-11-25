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
    """Decode single-frame DICOM, PNG or JPEG into writable grayscale pixels."""
    if not isinstance(file_bytes, bytes) or not file_bytes:
        raise ValidationError("영상 파일이 비어 있거나 올바르지 않습니다.")
    if len(file_bytes) > MAX_IMAGE_SIZE_BYTES:
        raise ValidationError("영상 파일이 허용 크기를 초과했습니다.")

    raster_signature = file_bytes.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"))
    if not raster_signature and file_bytes[128:132] == b"DICM":
        return _decode_dicom(file_bytes)

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
        raise ValidationError("지원하지 않는 형식이거나 손상된 영상입니다. DICOM, PNG 또는 JPEG를 선택해 주세요.") from error


def _decode_dicom(file_bytes: bytes) -> np.ndarray:
    """Decode a Part 10 monochrome image for the app's 8-bit processing pipeline."""
    try:
        from pydicom import dcmread
        from pydicom.errors import InvalidDicomError
    except ImportError as error:
        raise ValidationError("DICOM을 읽으려면 pydicom이 필요합니다.") from error

    try:
        dataset = dcmread(BytesIO(file_bytes))
        rows, columns = int(dataset.Rows), int(dataset.Columns)
        frames = int(dataset.get("NumberOfFrames", 1))
        samples = int(dataset.SamplesPerPixel)
        photometric = dataset.PhotometricInterpretation
        bits_allocated = int(dataset.BitsAllocated)
        bits_stored = int(dataset.BitsStored)
        representation = int(dataset.PixelRepresentation)
    except (InvalidDicomError, AttributeError, EOFError, OSError, TypeError, ValueError) as error:
        raise ValidationError("DICOM 영상의 헤더를 읽을 수 없습니다.") from error

    if rows <= 0 or columns <= 0:
        raise ValidationError("DICOM 영상 크기가 올바르지 않습니다.")
    pixel_limit = Image.MAX_IMAGE_PIXELS
    if pixel_limit is not None and rows * columns > pixel_limit:
        raise ValidationError("영상의 픽셀 수가 허용 범위를 초과했습니다.")
    if frames != 1 or samples != 1 or photometric not in ("MONOCHROME1", "MONOCHROME2"):
        raise ValidationError("한 장으로 된 회색조 DICOM 영상을 선택해 주세요.")
    if bits_allocated not in (8, 16) or not 1 <= bits_stored <= bits_allocated or representation not in (0, 1):
        raise ValidationError("지원하지 않는 DICOM 픽셀 형식입니다.")

    try:
        pixels = dataset.pixel_array
    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as error:
        raise ValidationError("DICOM 픽셀을 읽을 수 없습니다. 파일 형식과 압축 방식을 확인해 주세요.") from error
    if pixels.shape != (rows, columns):
        raise ValidationError("DICOM 픽셀 크기가 헤더의 영상 크기와 일치하지 않습니다.")

    if bits_stored == 8 and representation == 0:
        result = pixels.astype(np.uint8, copy=True)
    else:
        # Map the declared stored-value range to 8 bits, consistently across images.
        minimum = -(1 << (bits_stored - 1)) if representation else 0
        value_range = (1 << bits_stored) - 1
        values = pixels.astype(np.int32)
        values.clip(minimum, minimum + value_range, out=values)
        values -= minimum
        values *= 255
        values //= value_range
        result = values.astype(np.uint8)
    if photometric == "MONOCHROME1":
        np.subtract(255, result, out=result)
    return result


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
