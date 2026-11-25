"""Verify DICOM decoding without real patient data or external files."""

import unittest
from io import BytesIO
from unittest.mock import patch

import numpy as np
from PIL import Image
from pydicom import dcmwrite
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.encaps import encapsulate
from pydicom.uid import ComputedRadiographyImageStorage, ExplicitVRLittleEndian, JPEGBaseline8Bit, generate_uid

from src.exception import ValidationError
from src.processing.image_io import decode_image


def _dataset(pixels: np.ndarray, *, bits_stored: int | None = None) -> Dataset:
    media = FileMetaDataset()
    media.TransferSyntaxUID = ExplicitVRLittleEndian
    media.MediaStorageSOPClassUID = ComputedRadiographyImageStorage
    media.MediaStorageSOPInstanceUID = generate_uid()
    dataset = Dataset()
    dataset.file_meta = media
    dataset.SOPClassUID = media.MediaStorageSOPClassUID
    dataset.SOPInstanceUID = media.MediaStorageSOPInstanceUID
    dataset.Rows, dataset.Columns = pixels.shape
    dataset.SamplesPerPixel = 1
    dataset.PhotometricInterpretation = "MONOCHROME2"
    dataset.BitsAllocated = pixels.dtype.itemsize * 8
    dataset.BitsStored = bits_stored or dataset.BitsAllocated
    dataset.HighBit = dataset.BitsStored - 1
    dataset.PixelRepresentation = int(np.issubdtype(pixels.dtype, np.signedinteger))
    dataset.PixelData = pixels.tobytes()
    return dataset


def _bytes(dataset: Dataset) -> bytes:
    with BytesIO() as buffer:
        dcmwrite(buffer, dataset, enforce_file_format=True)
        return buffer.getvalue()


class DicomImageTest(unittest.TestCase):
    def test_eight_bit_monochrome_preserves_pixels_and_returns_writable_array(self):
        original = np.array([[0, 17, 255], [42, 100, 128]], dtype=np.uint8)
        decoded = decode_image(_bytes(_dataset(original)))
        np.testing.assert_array_equal(decoded, original)
        self.assertEqual(decoded.dtype, np.uint8)
        self.assertTrue(decoded.flags.writeable)

    def test_monochrome1_inverts_the_grayscale_order(self):
        dataset = _dataset(np.array([[0, 17, 255]], dtype=np.uint8))
        dataset.PhotometricInterpretation = "MONOCHROME1"
        np.testing.assert_array_equal(decode_image(_bytes(dataset)), [[255, 238, 0]])

    def test_signed_pixels_are_mapped_using_bits_stored(self):
        dataset = _dataset(np.array([[-2048, 0, 2047]], dtype="<i2"), bits_stored=12)
        np.testing.assert_array_equal(decode_image(_bytes(dataset)), [[0, 127, 255]])

    def test_jpeg_baseline_pixels_decode_from_dicom(self):
        original = np.arange(256, dtype=np.uint8).reshape((16, 16))
        with BytesIO() as buffer:
            Image.fromarray(original).save(buffer, format="JPEG")
            jpeg = buffer.getvalue()
        with Image.open(BytesIO(jpeg)) as image:
            expected = np.asarray(image)

        dataset = _dataset(original)
        dataset.file_meta.TransferSyntaxUID = JPEGBaseline8Bit
        dataset.PixelData = encapsulate([jpeg])
        np.testing.assert_array_equal(decode_image(_bytes(dataset)), expected)

    def test_rejects_multiframe_and_oversized_dimensions(self):
        dataset = _dataset(np.zeros((2, 3), dtype=np.uint8))
        with patch.object(Image, "MAX_IMAGE_PIXELS", 5):
            with self.assertRaisesRegex(ValidationError, "픽셀 수"):
                decode_image(_bytes(dataset))
        dataset.NumberOfFrames = 2
        with self.assertRaisesRegex(ValidationError, "한 장"):
            decode_image(_bytes(dataset))

    def test_jpeg_comment_with_dicom_marker_remains_a_jpeg(self):
        with BytesIO() as buffer:
            Image.new("L", (2, 2), 64).save(buffer, format="JPEG")
            jpeg = buffer.getvalue()
        comment = bytes(122) + b"DICM"
        segment = b"\xff\xfe" + (len(comment) + 2).to_bytes(2, "big") + comment
        jpeg = jpeg[:2] + segment + jpeg[2:]
        self.assertEqual(jpeg[128:132], b"DICM")
        np.testing.assert_array_equal(decode_image(jpeg), np.full((2, 2), 64, dtype=np.uint8))

    def test_dicom_with_missing_header_fields_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "DICOM 영상의 헤더"):
            decode_image(bytes(128) + b"DICM")
