"""Verification of image conversion and whole-image processing."""

import unittest
from io import BytesIO
from unittest.mock import patch

import numpy as np
from PIL import Image

from src.exception import ValidationError
from src.processing.algorithm import apply_canny, apply_clahe, apply_median
from src.processing.image_io import decode_image, encode_png
from src.processing.roi import analyze_roi
from src.services.processing import run_processing


class ImageProcessingTest(unittest.TestCase):
    def test_png_round_trip_preserves_pixels(self):
        original = np.array([[0, 17, 255], [42, 100, 128]], dtype=np.uint8)
        decoded = decode_image(encode_png(original))
        np.testing.assert_array_equal(decoded, original)
        self.assertTrue(decoded.flags.writeable)

    def test_result_png_can_be_read_at_the_limit_and_is_rejected_above_it(self):
        original = np.array([[0, 17, 255], [42, 100, 128]], dtype=np.uint8)
        encoded_size = len(encode_png(original))

        with patch("src.processing.image_io.MAX_IMAGE_SIZE_BYTES", encoded_size):
            result = encode_png(original)
            self.assertEqual(len(result), encoded_size)
            np.testing.assert_array_equal(decode_image(result), original)

        with patch("src.processing.image_io.MAX_IMAGE_SIZE_BYTES", encoded_size - 1):
            with self.assertRaisesRegex(ValidationError, "저장 가능한 크기"):
                encode_png(original)

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


class CLAHETest(unittest.TestCase):
    def test_settings_change_contrast_without_overwriting_source(self):
        source = np.random.default_rng(1234).integers(40, 90, size=(64, 64), dtype=np.uint8)
        before = source.copy()
        standard = apply_clahe(source)
        adjusted = apply_clahe(source, clip_limit=6, tile_grid_size=4)

        self.assertEqual(standard.shape, source.shape)
        self.assertEqual(standard.dtype, np.uint8)
        self.assertFalse(np.array_equal(standard, adjusted))
        np.testing.assert_array_equal(source, before)

    def test_rejects_invalid_contrast_settings(self):
        source = np.zeros((16, 16), dtype=np.uint8)
        for settings in ({"clip_limit": float("nan")}, {"tile_grid_size": 8.0}):
            with self.subTest(settings=settings):
                with self.assertRaises(ValidationError):
                    apply_clahe(source, **settings)


class MedianTest(unittest.TestCase):
    def test_larger_kernel_removes_a_bright_patch(self):
        source = np.full((15, 15), 40, dtype=np.uint8)
        source[6:9, 6:9] = 255
        before = source.copy()

        self.assertEqual(apply_median(source, kernel_size=3)[7, 7], 255)
        self.assertEqual(apply_median(source, kernel_size=5)[7, 7], 40)
        np.testing.assert_array_equal(source, before)

    def test_rejects_even_kernel(self):
        source = np.zeros((16, 16), dtype=np.uint8)
        with self.assertRaises(ValidationError):
            apply_median(source, kernel_size=4)


class CannyTest(unittest.TestCase):
    def test_thresholds_control_detection_of_a_faint_edge(self):
        source = np.full((32, 32), 50, dtype=np.uint8)
        source[:, 16:] = 65
        before = source.copy()

        detected = apply_canny(source, low_threshold=10, high_threshold=20)
        filtered = apply_canny(source, low_threshold=50, high_threshold=100)
        self.assertGreater(np.count_nonzero(detected), 0)
        self.assertEqual(np.count_nonzero(filtered), 0)
        self.assertTrue(set(np.unique(detected)).issubset({0, 255}))
        np.testing.assert_array_equal(source, before)

    def test_rejects_equal_thresholds(self):
        source = np.zeros((16, 16), dtype=np.uint8)
        with self.assertRaises(ValidationError):
            apply_canny(source, low_threshold=30, high_threshold=30)


class ProcessingServiceTest(unittest.TestCase):
    def test_runs_each_algorithm_with_the_selected_settings(self):
        original = np.random.default_rng(42).integers(0, 256, size=(64, 64), dtype=np.uint8)
        before = original.copy()
        for name, function, parameters in (
            ("CLAHE", apply_clahe, {"clip_limit": 5.0, "tile_grid_size": 4}),
            ("Median", apply_median, {"kernel_size": 7}),
            ("Canny", apply_canny, {"low_threshold": 50, "high_threshold": 100}),
        ):
            with self.subTest(algorithm=name):
                result = run_processing(original, name, parameters)
                np.testing.assert_array_equal(result, function(before, **parameters))
                self.assertFalse(np.shares_memory(result, original))
        np.testing.assert_array_equal(original, before)

    def test_invalid_algorithm_or_settings_raise_validation_error(self):
        original = np.zeros((16, 16), dtype=np.uint8)
        for name, parameters in (
            ("Unknown", {}),
            ("CLAHE", {"kernel_size": 3}),
            ("Canny", {"low_threshold": 30, "high_threshold": 30}),
        ):
            with self.subTest(algorithm=name, parameters=parameters):
                with self.assertRaises(ValidationError):
                    run_processing(original, name, parameters)


class ROIAnalysisTest(unittest.TestCase):
    def test_uses_original_coordinates_and_only_the_selected_pixels(self):
        image = np.array(
            [[99, 99, 99, 99], [99, 0, 8, 99], [99, 16, 255, 99]],
            dtype=np.uint8,
        )
        roi = {"x": 1, "y": 1, "width": 2, "height": 2}
        stats = analyze_roi(image, roi)
        self.assertAlmostEqual(stats["mean"], 69.75)
        self.assertAlmostEqual(stats["variance"], 11471.1875)
        self.assertEqual(len(stats["histogram"]), 32)
        self.assertEqual(sum(stats["histogram"]), 4)
        self.assertEqual([stats["histogram"][index] for index in (0, 1, 2, 31)], [1] * 4)
        self.assertEqual(stats["bin_edges"][-1], 256.0)

    def test_bottom_right_pixel_is_valid_and_one_step_past_it_is_not(self):
        image = np.array([[1, 2, 3], [4, 5, 6]], dtype=np.uint8)
        roi = {"x": 2, "y": 1, "width": 1, "height": 1}
        self.assertEqual(analyze_roi(image, roi)["mean"], 6.0)

        for invalid in ({"width": 2}, {"height": 0}, {"x": -1}, {"x": 2.0}, {"y": True}):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    analyze_roi(image, {**roi, **invalid})
