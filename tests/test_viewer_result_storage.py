"""Check the saved result workflow using fake case, permission and storage APIs."""

import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import numpy as np

from src.exception import ConflictError, NotFoundError, PermissionDeniedError, StorageError, ValidationError
from src.processing.image_io import encode_png
from src.repositories import processing as records
from src.services import processing


class ResultStorageTest(unittest.TestCase):
    def setUp(self):
        self.pixels = np.arange(64, dtype=np.uint8).reshape((8, 8))
        self.source_png = encode_png(self.pixels)
        self.case = {"case_id": "C01", "status": "검토 중"}
        self.original = {"file_id": "F01", "case_id": "C01", "kind": "ORIGINAL", "storage_path": "original.png"}
        self.output_files = {}
        self.metadata = {}
        self.saved_records = {}
        self.policy = self.enterContext(patch.object(processing, "access_policy"))
        case_repo = self.enterContext(patch.object(processing, "cases"))
        case_repo.get_case.return_value = self.case
        case_repo.get_case_assignment.return_value = {"user_id": "reviewer"}
        image_repo = self.enterContext(patch.object(processing, "images"))
        image_repo.get_file_info.return_value = self.original
        image_repo.list_case_files.side_effect = lambda case_id, kind: list(self.metadata.values())
        self.storage = self.enterContext(patch.object(processing, "file_storage"))
        self.storage.save_file.side_effect = self.write_file
        self.storage.read_file.side_effect = self.read_file
        self.history = self.enterContext(patch.object(processing, "records"))
        self.history.save_processing_result_with_file.side_effect = self.save_metadata
        self.history.get_processing_result.side_effect = lambda case_id, result_id: self.saved_records.get(result_id)
        self.roi = {"x": 0, "y": 0, "width": 4, "height": 4}

    def write_file(self, case_id, result_id, png, extension):
        path = f"cases/{case_id}/files/{result_id}.{extension}"
        if path in self.output_files:
            raise ConflictError("existing file")
        self.output_files[path] = png
        return path

    def read_file(self, path):
        return self.source_png if path == "original.png" else self.output_files[path]

    def save_metadata(self, record, file):
        self.saved_records[record["processing_result_id"]] = record
        self.metadata[file["file_id"]] = file

    def create_preview(self):
        return processing.create_processing_preview("reviewer", "C01", "F01", "Median", {"kernel_size": 3})

    def save_preview(self, preview):
        return processing.save_processing_preview("reviewer", "C01", "F01", preview, self.roi)

    def test_retries_a_partial_save_and_reopens_the_same_png(self):
        preview = self.create_preview()
        self.history.save_processing_result_with_file.side_effect = StorageError("temporary failure")
        with self.assertRaises(StorageError):
            self.save_preview(preview)
        self.assertEqual(len(self.output_files), 1)
        self.assertEqual(self.saved_records, {})

        self.history.save_processing_result_with_file.side_effect = self.save_metadata
        record = self.save_preview(preview)
        self.assertEqual(self.save_preview(preview), record)
        self.assertEqual(len(self.output_files), 1)
        self.assertEqual(len(self.saved_records), 1)
        result_id = record["processing_result_id"]
        self.assertEqual(record["roi"], self.roi)
        self.assertEqual(record["statistics"]["mean"], self.pixels[:4, :4].mean())
        reopened = processing.open_processing_result("reader", "C01", "F01", result_id)
        np.testing.assert_array_equal(reopened.pixels, preview.pixels)
        self.assertEqual(reopened.record["parameters"], {"kernel_size": 3})
        self.policy.require_case_access.assert_called_once()
        self.assertEqual(self.policy.require_case_access.call_args.args[0], "reader")

    def test_does_not_reopen_a_result_for_a_different_original(self):
        record = self.save_preview(self.create_preview())
        self.original["file_id"] = "F02"
        with self.assertRaises(NotFoundError):
            processing.open_processing_result("reader", "C01", "F02", record["processing_result_id"])

    def test_denied_viewer_never_loads_the_result_record(self):
        self.policy.require_case_access.side_effect = PermissionDeniedError("no read access")
        with self.assertRaises(PermissionDeniedError):
            processing.open_processing_result("reader", "C01", "F01", "R01")
        self.history.get_processing_result.assert_not_called()
        self.storage.read_file.assert_not_called()

    def test_save_rechecks_permissions_and_rejects_an_oversize_result_before_writing(self):
        preview = self.create_preview()
        self.policy.require_case_processing.side_effect = PermissionDeniedError("no write access")
        with self.assertRaises(PermissionDeniedError):
            self.save_preview(preview)
        self.policy.require_case_processing.side_effect = None
        with patch("src.processing.image_io.MAX_IMAGE_SIZE_BYTES", 1):
            with self.assertRaises(ValidationError):
                self.save_preview(preview)
        self.storage.save_file.assert_not_called()
        self.history.save_processing_result_with_file.assert_not_called()


class ResultRecordBatchTest(unittest.TestCase):
    def test_output_and_history_use_the_same_batch(self):
        now = datetime(2026, 11, 24, tzinfo=timezone.utc)
        result = {"processing_result_id": "R01", "case_id": "C01", "status": "COMPLETED", "created_at": now}
        file = {"file_id": "F01", "case_id": "C01", "kind": "RESULT", "processing_result_id": "R01"}
        db = Mock()
        result_ref, file_ref = Mock(), Mock()
        refs = {"processing_results": result_ref, "files": file_ref}
        db.collection.side_effect = lambda name: Mock(document=Mock(return_value=refs[name]))
        with patch.object(records, "get_db", return_value=db):
            records.save_processing_result_with_file(result, file)
        batch = db.batch.return_value
        created = [(call.args[0], call.args[1]) for call in batch.create.call_args_list]
        self.assertEqual(created, [
            (result_ref, {"case_id": "C01", "status": "COMPLETED", "created_at": now}),
            (file_ref, {"case_id": "C01", "kind": "RESULT", "processing_result_id": "R01"}),
        ])
        batch.commit.assert_called_once_with()
