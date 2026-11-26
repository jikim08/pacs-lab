'''케이스 등록(CASE-01)·상세 조회(CASE-03) 테스트 — Firestore 없이 실행'''
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from src.exception import NotFoundError, ValidationError
from src.services import case as case_service


KST = timezone(timedelta(hours=9))
VALID = {
    "patient_id": "P1",
    "department_id": "D1",
    "exam_type": "CR",
    "exam_info": "흉부 X-ray",
    "created_by": "uid-registrar",
}


class ServiceTestBase(unittest.TestCase):
    '''services/case가 부르는 권한·Repository 함수를 Mock으로 교체'''

    def mock(self, name, **kwargs):
        patcher = patch.object(case_service, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()


class CreateCaseTest(ServiceTestBase):
    '''create_case: 필수값, 참조 검증, 초기 상태, 검사 일시'''

    def setUp(self):
        self.require = self.mock("require_case_registration")
        self.patient_exists = self.mock("patient_exists", return_value=True)
        self.department_exists = self.mock("department_exists", return_value=True)
        self.save_case = self.mock("save_case", return_value="C1")

    def saved(self):
        '''save_case에 넘긴 데이터'''
        return self.save_case.call_args.args[0]

    def test_saves_trimmed_fields_with_initial_status(self):
        case_id = case_service.create_case(**{**VALID, "exam_type": " CR "})

        self.assertEqual(case_id, "C1")
        self.assertEqual(self.saved()["exam_type"], "CR")
        self.assertEqual(self.saved()["status"], "검토 대기")

    def test_missing_required_field_is_rejected(self):
        for field in ["patient_id", "department_id", "exam_type"]:
            for blank in ["", "   "]:
                with self.subTest(field=field, value=blank):
                    with self.assertRaises(ValidationError):
                        case_service.create_case(**{**VALID, field: blank})

        self.save_case.assert_not_called()

    def test_unknown_patient_or_department_is_rejected(self):
        self.patient_exists.return_value = False
        with self.assertRaises(ValidationError):
            case_service.create_case(**VALID)

        self.patient_exists.return_value = True
        self.department_exists.return_value = False
        with self.assertRaises(ValidationError):
            case_service.create_case(**VALID)

        self.save_case.assert_not_called()

    def test_permission_error_stops_registration(self):
        self.require.side_effect = PermissionError("등록 권한 없음")

        with self.assertRaises(PermissionError):
            case_service.create_case(**VALID)

        self.save_case.assert_not_called()

    def test_exam_at_is_optional_and_saved_as_none(self):
        case_service.create_case(**VALID)

        self.assertIn("exam_at", self.saved())
        self.assertIsNone(self.saved()["exam_at"])

    def test_exam_date_becomes_kst_midnight(self):
        case_service.create_case(**VALID, exam_at=date(2026, 11, 20))

        self.assertEqual(self.saved()["exam_at"], datetime(2026, 11, 20, tzinfo=KST))

    def test_exam_datetime_with_timezone_is_kept(self):
        exam_at = datetime(2026, 11, 20, 5, 30, tzinfo=timezone.utc)

        case_service.create_case(**VALID, exam_at=exam_at)

        self.assertEqual(self.saved()["exam_at"], exam_at)

    def test_today_is_allowed(self):
        today = datetime.now(KST).date()

        case_service.create_case(**VALID, exam_at=today)

        self.save_case.assert_called_once()

    def test_invalid_exam_at_is_rejected_before_db_lookup(self):
        invalid = {
            "시간대 없음": datetime(2026, 11, 20, 9, 0),
            "미래 날짜": date(2999, 1, 1),
            "미래 시각": datetime.now(timezone.utc) + timedelta(hours=1),
            "문자열": "2026-11-20",
            "숫자": 20261120,
        }
        for name, value in invalid.items():
            with self.subTest(name):
                with self.assertRaises(ValidationError):
                    case_service.create_case(**VALID, exam_at=value)

        self.patient_exists.assert_not_called()
        self.save_case.assert_not_called()


class GetCaseDetailFilesTest(ServiceTestBase):
    '''get_case_detail: 영상 목록(files) 연결'''

    def setUp(self):
        self.mock("require_case_read")
        self.mock(
            "get_case",
            side_effect=lambda c: {"case_id": "C1", "patient_id": "P1", "department_id": "D1"} if c == "C1" else None,
        )
        self.mock("get_patient", return_value={"patient_id": "P1"})
        self.mock("get_department", return_value={"department_id": "D1"})
        self.files = [
            {"file_id": "F1", "case_id": "C1", "kind": "ORIGINAL"},
            {"file_id": "F2", "case_id": "C1", "kind": "RESULT"},
        ]
        self.list_case_files = self.mock("list_case_files", return_value=self.files)

    def test_includes_original_and_result_files(self):
        detail = case_service.get_case_detail("uid", "C1")

        self.assertEqual(detail["files"], self.files)
        self.list_case_files.assert_called_once_with("C1")

    def test_uses_stored_case_id_not_raw_input(self):
        case_service.get_case_detail("uid", "  C1  ")

        self.list_case_files.assert_called_once_with("C1")

    def test_case_without_files_has_empty_list(self):
        self.list_case_files.return_value = []

        self.assertEqual(case_service.get_case_detail("uid", "C1")["files"], [])

    def test_missing_case_does_not_look_up_files(self):
        with self.assertRaises(NotFoundError):
            case_service.get_case_detail("uid", "C9")

        self.list_case_files.assert_not_called()


if __name__ == "__main__":
    unittest.main()