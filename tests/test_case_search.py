'''케이스 검색·필터 테스트 (CASE-04, CASE-05) — Firestore 없이 실행'''

# 아래 명령어를 통해 테스트 실행 가능
# .venv/bin/python -m unittest discover -s tests -p "test_case_search.py" -v
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import Mock, patch

from google.api_core.exceptions import ServiceUnavailable

from src.exception import StorageError, ValidationError
from src.repositories import case as case_repository
from src.services import case as case_service


KST = timezone(timedelta(hours=9))


def at(day, hour=0):
    '''2026년 11월 day일 hour시 (한국 시간)'''
    return datetime(2026, 11, day, hour, tzinfo=KST)


def make_snapshot(case_id, **fields):
    '''Firestore 문서 조회 결과 흉내'''
    document = Mock(id=case_id)
    document.to_dict.return_value = fields
    return document


def ids(cases):
    return [case["case_id"] for case in cases]


class SearchCasesRepositoryTest(unittest.TestCase):
    '''repositories/case.search_cases: 조건 조회, 기간, 정렬'''

    def setUp(self):
        patcher = patch.object(case_repository, "get_db")
        self.get_db = patcher.start()
        self.addCleanup(patcher.stop)

        self.collection = self.get_db.return_value.collection.return_value
        documents = [
            make_snapshot("A", status="검토 대기", exam_type="CR", created_at=at(1)),
            make_snapshot("B", status="검토 중", exam_type="CT", created_at=at(3)),
            make_snapshot("C", status="검토 대기", exam_type="CR", created_at=at(5)),
            make_snapshot("NO_TIME", status="검토 대기", exam_type="CR"),
        ]
        # where를 몇 번 붙여도 같은 문서 목록을 돌려주도록 설정
        self.collection.stream.return_value = documents
        self.collection.where.return_value.stream.return_value = documents
        self.collection.where.return_value.where.return_value.stream.return_value = documents

    def test_no_condition_returns_all_newest_first(self):
        self.assertEqual(ids(case_repository.search_cases()), ["C", "B", "A"])
        self.collection.where.assert_not_called()

    def test_status_only(self):
        result = case_repository.search_cases(status="검토 대기")

        self.assertEqual(ids(result), ["C", "A"])
        condition = self.collection.where.call_args.kwargs["filter"]
        self.assertEqual((condition.field_path, condition.value), ("status", "검토 대기"))

    def test_status_and_exam_type_are_both_applied(self):
        result = case_repository.search_cases(status="검토 대기", exam_type="CT")
        self.assertEqual(result, [])

    def test_period_includes_start_and_excludes_end(self):
        result = case_repository.search_cases(created_from=at(1), created_to=at(5))
        self.assertEqual(ids(result), ["B", "A"])

    def test_case_without_created_at_is_excluded(self):
        self.assertNotIn("NO_TIME", ids(case_repository.search_cases()))

    def test_case_id_uses_direct_lookup_and_other_conditions(self):
        case_a = {"case_id": "A", "status": "검토 대기", "exam_type": "CR", "created_at": at(1)}
        with patch.object(case_repository, "get_case", side_effect=lambda c: case_a if c == "A" else None):
            self.assertEqual(ids(case_repository.search_cases(case_id="A")), ["A"])
            self.assertEqual(case_repository.search_cases(case_id="A", status="검토 중"), [])
            self.assertEqual(case_repository.search_cases(case_id="X"), [])

        self.collection.stream.assert_not_called()

    def test_firestore_failure_raises_storage_error(self):
        self.collection.stream.side_effect = ServiceUnavailable("down")

        with self.assertRaises(StorageError):
            case_repository.search_cases()


def fake_require_case_read(uid):
    if uid != "uid-ok":
        raise PermissionError("권한 없음")
    return {"role": "REVIEWER"}


class SearchCaseListServiceTest(unittest.TestCase):
    '''services/case.search_case_list: 권한, 입력 검증, 기간 변환, limit'''

    def setUp(self):
        read_patcher = patch.object(case_service, "require_case_read", side_effect=fake_require_case_read)
        read_patcher.start()
        self.addCleanup(read_patcher.stop)

        search_patcher = patch.object(
            case_service, "search_cases",
            return_value=[{"case_id": f"C{i}"} for i in range(5)],
        )
        self.search_cases = search_patcher.start()
        self.addCleanup(search_patcher.stop)

    def passed(self):
        '''Repository에 넘긴 조건'''
        return self.search_cases.call_args.kwargs

    def test_no_condition_passes_all_none(self):
        case_service.search_case_list("uid-ok")

        self.assertEqual(self.passed(), {
            "case_id": None, "status": None, "exam_type": None,
            "created_from": None, "created_to": None,
        })

    def test_text_conditions_are_trimmed_and_blank_means_no_condition(self):
        case_service.search_case_list("uid-ok", case_id="   ", exam_type=" CR ", status=" 검토 중 ")

        self.assertIsNone(self.passed()["case_id"])
        self.assertEqual(self.passed()["exam_type"], "CR")
        self.assertEqual(self.passed()["status"], "검토 중")

    def test_dates_become_kst_range_including_end_date(self):
        case_service.search_case_list("uid-ok", start_date=date(2026, 11, 1), end_date=date(2026, 11, 3))

        self.assertEqual(self.passed()["created_from"], at(1))
        self.assertEqual(self.passed()["created_to"], at(4))

    def test_same_start_and_end_date_is_one_day(self):
        case_service.search_case_list("uid-ok", start_date=date(2026, 11, 30), end_date=date(2026, 11, 30))

        self.assertEqual(self.passed()["created_from"], at(30))
        self.assertEqual(self.passed()["created_to"], datetime(2026, 12, 1, tzinfo=KST))

    def test_limit_keeps_newest_results(self):
        self.assertEqual(ids(case_service.search_case_list("uid-ok", limit=2)), ["C0", "C1"])

    def test_invalid_conditions_are_rejected_before_search(self):
        invalid = {
            "없는 상태": {"status": "대기"},
            "기간 역전": {"start_date": date(2026, 11, 5), "end_date": date(2026, 11, 1)},
            "시각 포함": {"start_date": datetime(2026, 11, 1)},
            "문자 날짜": {"end_date": "2026-11-01"},
            "ID 형식": {"case_id": "a/b"},
            "검사 종류 숫자": {"exam_type": 3},
            "limit 0": {"limit": 0},
        }
        for name, condition in invalid.items():
            with self.subTest(name):
                with self.assertRaises(ValidationError):
                    case_service.search_case_list("uid-ok", **condition)

        self.search_cases.assert_not_called()

    def test_unauthorized_user_gets_no_data(self):
        with self.assertRaises(PermissionError):
            case_service.search_case_list("uid-unknown")

        self.search_cases.assert_not_called()


if __name__ == "__main__":
    unittest.main()