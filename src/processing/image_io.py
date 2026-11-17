"""이미지 파일 검사와 배열 변환."""

import numpy as np


def decode_image(file_bytes: bytes) -> np.ndarray:
    """이미지 바이트를 8비트 회색조 배열로 변환한다.

    반환:
        shape: (height, width)
        dtype: uint8
        픽셀 범위: 0~255

    파일 크기, 지원 형식, 디코딩 가능 여부를 검사한다.
    확장자만으로 파일 형식을 판단하지 않는다.
    지원하지 않거나 손상된 이미지는 ValidationError를 발생시킨다.
    """
    raise NotImplementedError


def encode_png(image: np.ndarray) -> bytes:
    """8비트 회색조 배열을 PNG 바이트로 변환한다.

    잘못된 배열이나 인코딩 실패는 ValidationError를 발생시킨다.
    """
    raise NotImplementedError