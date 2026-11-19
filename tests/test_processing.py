"""Verification of image conversion and the three whole-image algorithms."""

import unittest
from io import BytesIO

import numpy as np
from PIL import Image

from src.exception import ValidationError
from src.processing.image_io import decode_image, encode_png


class ImageProcessingTest(unittest.TestCase):
    def test_png_round_trip_preserves_pixels(self):
        original = np.array([[0, 17, 255], [42, 100, 128]], dtype=np.uint8)
        decoded = decode_image(encode_png(original))
        np.testing.assert_array_equal(decoded, original)
        self.assertTrue(decoded.flags.writeable)

    def test_jpeg_is_grayscale_and_gif_is_rejected(self):
        source = Image.new("RGB", (32, 24), (70, 70, 70))
        with BytesIO() as buffer:
            source.save(buffer, format="JPEG")
            image = decode_image(buffer.getvalue())
        self.assertEqual(image.shape, (24, 32))
        self.assertEqual(image.dtype, np.uint8)
        np.testing.assert_array_equal(image, np.full((24, 32), 70, dtype=np.uint8))

        with BytesIO() as buffer:
            source.save(buffer, format="GIF")
            with self.assertRaises(ValidationError):
                decode_image(buffer.getvalue())

    def test_16_bit_png_maps_through_the_intensity_range(self):
        source = np.array([[0, 256, 32768, 65535]], dtype=np.uint16)
        with BytesIO() as buffer:
            Image.fromarray(source).save(buffer, format="PNG")
            image = decode_image(buffer.getvalue())
        np.testing.assert_array_equal(image, [[0, 1, 128, 255]])

    def test_rejects_truncated_png(self):
        png = encode_png(np.zeros((12, 12), dtype=np.uint8))
        with self.assertRaises(ValidationError):
            decode_image(png[: len(png) // 2])
