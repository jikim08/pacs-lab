"""Verify the Firestore repository contract without using project credentials."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

from google.api_core.exceptions import AlreadyExists, ServiceUnavailable

from src.exception import ConflictError, StorageError, ValidationError
from src.models import ProcessingResult
from src.repositories import processing


NOW = datetime(2026, 11, 24, tzinfo=timezone.utc)


def _result(result_id: str, *, file_id: str = "F01", created_at: datetime = NOW) -> ProcessingResult:
    return {
        "processing_result_id": result_id, "case_id": "C01", "processing_type_id": "CLAHE",
        "algorithm_version": "1", "parameters": {"clip_limit": 2.0, "tile_grid_size": 8},
        "performed_by": "reviewer", "input_file_id": file_id, "status": "COMPLETED",
        "roi": {"x": 3, "y": 7, "width": 2, "height": 2},
        "statistics": {"mean": 80.0, "variance": 0.0, "histogram": [4], "bin_edges": [0.0, 256.0]},
        "error_message": None, "created_at": created_at, "started_at": created_at,
        "completed_at": created_at,
    }


def _snapshot(result: ProcessingResult) -> Mock:
    data = dict(result)
    result_id = data.pop("processing_result_id")
    document = Mock(id=result_id, exists=True)
    document.to_dict.return_value = data
    return document


class ProcessingRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.get_db = self.enterContext(patch.object(processing, "get_db"))
        self.collection = self.get_db.return_value.collection.return_value
        self.document = self.collection.document.return_value

    def test_saves_and_reads_metadata_by_document_id(self):
        result = _result("R01")
        processing.save_processing_result(result)
        self.get_db.return_value.collection.assert_called_once_with("processing_results")
        self.collection.document.assert_called_once_with("R01")
        data = dict(result)
        del data["processing_result_id"]
        self.document.create.assert_called_once_with(data)

        self.document.get.return_value = _snapshot(result)
        self.assertEqual(processing.get_processing_result("C01", "R01"), result)
        self.assertIsNone(processing.get_processing_result("C02", "R01"))
        self.document.get.return_value = Mock(exists=False)
        self.assertIsNone(processing.get_processing_result("C01", "R99"))

    def test_duplicate_id_returns_conflict_and_never_overwrites(self):
        self.document.create.side_effect = AlreadyExists("duplicate")
        with self.assertRaises(ConflictError):
            processing.save_processing_result(_result("R01"))
        self.document.set.assert_not_called()

    def test_lists_latest_first_and_scopes_query_to_case_and_optional_file(self):
        old = _result("R01")
        new = _result("R02", created_at=NOW + timedelta(minutes=1))
        other = _result("R03", file_id="F02")
        by_case = self.collection.where.return_value
        by_case.stream.return_value = iter(map(_snapshot, (old, other, new)))
        by_case.where.return_value.stream.return_value = iter(map(_snapshot, (old, new)))

        self.assertEqual(processing.list_processing_results("C01"), [new, other, old])
        self.assertEqual(processing.list_processing_results("C01", input_file_id="F01"), [new, old])
        case_filter = self.collection.where.call_args.kwargs["filter"]
        file_filter = by_case.where.call_args.kwargs["filter"]
        self.assertEqual((case_filter.field_path, case_filter.op_string, case_filter.value), ("case_id", "==", "C01"))
        self.assertEqual((file_filter.field_path, file_filter.op_string, file_filter.value), ("input_file_id", "==", "F01"))

    def test_database_failure_is_reported_as_storage_error(self):
        self.document.create.side_effect = ServiceUnavailable("unavailable")
        self.document.get.side_effect = ServiceUnavailable("unavailable")
        self.collection.where.return_value.stream.side_effect = ServiceUnavailable("unavailable")
        with self.assertRaises(StorageError):
            processing.save_processing_result(_result("R01"))
        with self.assertRaises(StorageError):
            processing.get_processing_result("C01", "R01")
        with self.assertRaises(StorageError):
            processing.list_processing_results("C01")

    def test_invalid_document_id_fails_before_database_access(self):
        with self.assertRaises(ValidationError):
            processing.save_processing_result(_result("R/01"))
        self.get_db.assert_not_called()
