# 의료영상 처리 및 검토 업무 관리 시스템

## Firestore DB 설계서 v1.3 — `src/models.py` 기준 정합화

### 0. v1.3 변경 사항 (2026-11-19)

v1.2 설계와 이미 작성된 코드(`src/models.py`)가 다른 부분을 **코드 기준으로 맞추었다.** 관련 논의: GitHub Issue #10

| 항목 | v1.2 | v1.3 |
| --- | --- | --- |
| 케이스 상태값 | `REGISTERED` / `ASSIGNED` / `IN_REVIEW` / `COMPLETED` | `검토 대기` / `검토 중` / `검토 완료` |
| 담당자 배정 표시 | `ASSIGNED` 상태값 | 상태값 없음. `case_assignees`의 `assigned_{case_id}` 문서 존재 여부로 판단 |
| 케이스 검사 정보 | `exam_at` | `exam_info`(부가 정보) 필드 추가, `exam_at` 유지 |
| 환자 성별 | `gender` | `sex` |
| 처리 상태값 | `PROCESSING` | `RUNNING` |
| 파일 종류 | `ORIGINAL` / `PROCESSED` | `ORIGINAL` / `RESULT` |
| `files` 필드 | `file_kind` + `metadata` Map + `roi_analyses` Array | 평평한 필드(`kind`, `original_name`, `content_type`, `size_bytes`, `uploaded_by` 등) |
| ROI 분석 기록 | `files.roi_analyses` | `processing_results`의 `roi`·`statistics` |
| 사용자 역할 | `ADMIN` / `REVIEWER` | `REGISTRAR` 추가 정의(권한 규칙 미정) |
| 파일 저장소 | Firebase Storage | 1단계 로컬 `data/storage/` → 2단계 Firebase Storage |
| 환자·진료과 Repository | `case.py`, `user.py` | `patient.py`, `department.py` |

---

### 1. 설계 개요

**데이터 저장 방식**

| 시스템 | 역할 |
| --- | --- |
| Firebase Authentication | 로그인 및 사용자 인증 |
| Cloud Firestore | 사용자, 케이스, 영상처리 결과, 검토 기록 저장 |
| 파일 저장소 | 원본 및 처리 결과 이미지 파일 저장 (1단계: 로컬 `data/storage/`, 2단계: Firebase Storage) |

**파일 저장소 단계별 계획**

| 단계 | 저장 위치 | 시점 |
| --- | --- | --- |
| 1단계 | `FILE_STORAGE_ROOT`(기본값 `data/storage/`) 아래 로컬 파일 | 현재 ~ 주요 기능 구현 완료 |
| 2단계 | Firebase Storage | 주요 기능 구현 후 전환 |

- 저장소 접근은 `repositories/file_storage.py`의 `save_file`·`read_file`·`delete_file`로만 수행한다.
- 2단계 전환 시 `file_storage.py` 내부만 교체하고 함수 시그니처와 `storage_path` 형식은 유지한다.
- `data/storage/`는 Git에 커밋하지 않는다(`.gitignore` 등록).

**기본 설계 원칙**

1. Firestore 최상위 컬렉션은 8개로 유지한다.
2. 새로운 컬렉션이나 서브컬렉션을 추가하지 않는다.
3. 기존 관계형 DB의 참조 관계는 문서 ID 필드로 표현한다.
4. 검토 기록은 `cases.review_records` Array로, ROI 분석 결과는 `processing_results`의 Map 필드로 관리한다.
5. 데이터 읽기·쓰기는 기존 Repository 구조에서 수행한다.
6. 참조 무결성과 사용자 권한 검사는 Service에서 처리한다.
7. 필드 이름과 값은 `src/models.py`의 정의를 따른다.

---

### 2. Firestore 컬렉션 구성

| 컬렉션 | 문서 ID | 주요 필드 | 담당 Repository |
| --- | --- | --- | --- |
| `users` | Firebase UID | name, role, department_id, last_login_at, created_at, updated_at | `user.py` |
| `departments` | 진료과 ID | name | `department.py` |
| `patients` | 환자 ID | name, birth_date, sex, created_at, updated_at | `patient.py` |
| `cases` | 케이스 ID | patient_id, department_id, exam_type, exam_info, exam_at, created_by, status, memo, review_records, created_at, updated_at | `case.py`, `review.py` |
| `case_assignees` | 접근 권한별 고유 ID | case_id, user_uid, access_type, assigned_at | `review.py` |
| `process_types` | 알고리즘 ID | name, description | `processing.py` |
| `processing_results` | 처리 결과 ID | case_id, process_type_id, performed_by, input_file_id, parameters, status, roi, statistics, created_at, started_at, completed_at | `processing.py` |
| `files` | 파일 ID | case_id, processing_result_id, kind, original_name, storage_path, content_type, size_bytes, uploaded_by, created_at | `image.py` |

위 Repository 이름은 모두 `src/repositories/`를 기준으로 한다.

### 3. 컬렉션별 세부 설계

#### 3.1 `users`

**문서 ID:** Firebase Authentication UID

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| name | String | 사용자 이름 |
| role | String | `ADMIN`, `REVIEWER`, `REGISTRAR` |
| department_id | String / Null | 소속 진료과 ID |
| last_login_at | Timestamp / Null | 마지막 로그인 |
| created_at | Timestamp | 생성 시간 |
| updated_at | Timestamp | 수정 시간 |

조회 전용 권한은 전역 역할이 아니라 케이스별 접근 권한으로 관리한다.

`REGISTRAR`는 `src/models.py`의 `UserRole`에 정의되어 있어 유지한다. 권한 규칙은 미정이다(8절).

#### 3.2 `departments`

**문서 ID:** 진료과 ID

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| name | String | 진료과 이름 |

#### 3.3 `patients`

**문서 ID:** 환자 ID

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| name | String | 모의 환자 이름 |
| birth_date | String | 생년월일, YYYY-MM-DD |
| sex | String | 성별 (허용 값 미정) |
| created_at | Timestamp | 등록 시간 |
| updated_at | Timestamp | 수정 시간 |

#### 3.4 `cases`

**문서 ID:** 케이스 ID

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| patient_id | String | 환자 ID |
| department_id | String | 담당 진료과 ID |
| exam_type | String | 검사 종류 |
| exam_info | String | 검사 부가 정보 |
| exam_at | Timestamp | 검사 일시 (정렬·기간 필터에 사용) |
| created_by | String | 케이스 등록자 UID |
| status | String | 현재 검토 상태 |
| memo | String | 기존 공용 메모(선택) |
| review_records | Array of Map | 검토 메모와 리포트 기록 |
| created_at | Timestamp | 등록 시간 |
| updated_at | Timestamp | 수정 시간 |

**상태값** (`src/models.py`의 `CaseStatus`)

| 값 | 의미 |
| --- | --- |
| 검토 대기 | 케이스 접수. 등록 시 초기 상태 |
| 검토 중 | 담당자가 검토를 시작함 |
| 검토 완료 | 검토 완료 |

담당자 배정은 별도 상태값으로 표현하지 않는다. `case_assignees`의 `assigned_{case_id}` 문서가 있으면 배정된 케이스로 판단한다.

예: "검토 대기 목록(REV-01)" = `status == "검토 대기"`이고 담당자 배정 문서가 있는 케이스

**검토 기록 구조**

```
{
  "review_records": [
    {
      "record_id": "REV001",
      "type": "MEMO",
      "content": "원본 영상 검토 완료",
      "author_uid": "UID002",
      "created_at": "2026-11-17T10:00:00+09:00"
    }
  ]
}
```

실제 날짜 데이터는 Firestore Timestamp로 저장한다.

검토 메모와 리포트는 `review_records`를 기준으로 관리한다. 기존 `memo` 필드는 필요할 때만 최신 요약용으로 사용하며 중복 기록을 피한다.

#### 3.5 `case_assignees`

**문서 ID:** 케이스와 사용자 접근 유형을 구분하는 고유 ID

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| case_id | String | 케이스 ID |
| user_uid | String | 사용자 UID |
| access_type | String | ASSIGNED 또는 READ_ONLY |
| assigned_at | Timestamp | 배정·권한 부여 시간 |

**접근 유형**

- `ASSIGNED`: 검토 담당자로 배정
- `READ_ONLY`: 조회만 허용

`access_type`의 `ASSIGNED`는 케이스별 접근 유형이며, 케이스 상태값이 아니다.

케이스당 검토 담당자를 한 명으로 제한할 경우, 담당자 배정 문서는 케이스별로 고정된 ID를 사용한다.

예시:

```
case_assignees/
  assigned_CASE001
  readonly_CASE001_UID003
```

담당자 배정·변경은 기존 담당자와 새로운 담당자의 상태가 일관되도록 트랜잭션으로 처리한다.

#### 3.6 `process_types`

**문서 ID:** 알고리즘 식별자

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| name | String | 알고리즘 이름 |
| description | String | 알고리즘 설명 |

명암 개선, 노이즈 제거, 경계 검출 등 3종 이상의 영상처리 알고리즘을 등록한다.

#### 3.7 `processing_results`

**문서 ID:** 처리 실행 ID

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| case_id | String | 케이스 ID |
| process_type_id | String | 알고리즘 ID |
| performed_by | String | 수행자 UID |
| input_file_id | String | 입력 이미지 ID (ROI 분석 대상 파일) |
| parameters | Map | 실행 설정값 |
| status | String | 처리 상태 |
| roi | Map / Null | ROI 영역. Null이면 전체 이미지 |
| statistics | Map / Null | ROI 분석 통계 |
| created_at | Timestamp | 기록 생성 시간 |
| started_at | Timestamp / Null | 실행 시작 시간 |
| completed_at | Timestamp / Null | 완료 시간 |

**파라미터 예시**

```
{
  "parameters": {
    "kernel_size": 5,
    "sigma": 1.5
  }
}
```

**ROI 분석 기록** (`src/models.py`의 `ROI`, `ROIStatistics`)

```
{
  "roi": {
    "x": 100,
    "y": 150,
    "width": 200,
    "height": 200
  },
  "statistics": {
    "mean": 128.5,
    "variance": 32.4,
    "histogram": [12, 40, 85, ...],
    "bin_edges": [0.0, 16.0, 32.0, ...]
  }
}
```

- ROI 좌표는 화면 확대 좌표가 아닌 원본 이미지의 픽셀 좌표를 사용한다.
- ROI 분석 대상 파일은 `input_file_id`로 연결한다.
- 분석자는 `performed_by`, 분석 시간은 `completed_at`으로 확인한다.

처리 상태는 `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`를 사용한다(`ProcessingStatus`).

각 처리 실행은 별도 문서로 저장하여 이전 실행 결과를 보존한다.

#### 3.8 `files`

**문서 ID:** 파일 ID

필드 구조는 `src/models.py`의 `FileInfo`를 따른다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| case_id | String | 케이스 ID |
| processing_result_id | String / Null | 원본이면 Null, 결과 파일이면 처리 결과 ID |
| kind | String | `ORIGINAL` 또는 `RESULT` |
| original_name | String | 업로드 당시 파일명 |
| storage_path | String | 파일 저장소 기준 상대 경로 |
| content_type | String | MIME 타입 (예: image/png) |
| size_bytes | Number | 파일 크기 |
| uploaded_by | String | 업로드한 사용자 UID |
| created_at | Timestamp | 등록 시간 |

**문서 예시**

```
{
  "case_id": "CASE001",
  "processing_result_id": null,
  "kind": "ORIGINAL",
  "original_name": "sample.png",
  "storage_path": "cases/CASE001/files/FILE001.png",
  "content_type": "image/png",
  "size_bytes": 245760,
  "uploaded_by": "UID001",
  "created_at": "2026-11-17T09:00:00+09:00"
}
```

`storage_path` 형식은 `cases/{case_id}/files/{file_id}.{ext}`이며, 실제 경로는 `FILE_STORAGE_ROOT`와 결합해 만든다. 2단계(Firebase Storage)에서도 같은 형식을 사용하는 것을 기본안으로 한다.

이미지 가로·세로 크기(`width`, `height`) 저장 여부는 미정이다(8절). 저장하지 않으면 필요할 때 이미지를 디코딩해 구한다.

히스토그램은 저장된 이미지와 ROI 범위를 사용해 다시 계산하는 것을 기본으로 한다.

---

### 4. 데이터 관계 및 검증 규칙

| 데이터 관계 | 검증 사항 |
| --- | --- |
| 사용자 → 진료과 | 존재하는 진료과 ID인지 확인 |
| 케이스 → 환자 | 존재하는 모의 환자인지 확인 |
| 케이스 → 사용자 | 등록자가 유효한 사용자인지 확인 |
| 케이스 담당자 → 사용자·케이스 | 두 문서의 존재와 접근 권한 확인 |
| 처리 결과 → 입력 파일 | 입력 파일이 실제로 해당 케이스에 속하는지 확인 |
| 결과 파일 → 처리 결과 | 결과 파일과 실행 기록이 동일한 케이스에 속하는지 확인 |
| ROI 분석(처리 결과) → 입력 파일 | `input_file_id` 파일과 해당 케이스의 일치 여부, ROI 범위가 이미지 크기 안인지 확인 |

Firestore에는 관계형 DB의 외래키 제약조건이 없으므로 이러한 검사는 Service 계층에서 처리한다.

### 5. 기존 Repository와 데이터 연결

| 파일 | 담당 데이터 | 주요 작업 |
| --- | --- | --- |
| `repositories/user.py` | users | 사용자 조회, 역할 확인·변경 |
| `repositories/department.py` | departments | 진료과 존재 여부 확인, 진료과 조회 |
| `repositories/patient.py` | patients | 환자 존재 여부 확인, 환자 조회 |
| `repositories/case.py` | cases | 케이스 생성, 목록 및 검색 |
| `repositories/image.py` | files | 원본·결과 파일 정보 저장·조회 |
| `repositories/processing.py` | process_types, processing_results | 알고리즘 정보, 처리 결과, ROI 분석 기록(`roi`·`statistics`) 저장·조회 |
| `repositories/review.py` | case_assignees, cases의 상태·검토 기록 | 담당자 배정, 검토 상태, 메모·리포트 저장 |
| `repositories/file_storage.py` | 파일 저장소 (1단계 로컬, 2단계 Firebase Storage) | 파일 저장·읽기·삭제 |

`cases`는 둘 이상의 Repository에서 일부 필드를 수정한다.

따라서 서로 다른 데이터 영역을 덮어쓰지 않도록 **담당 필드만 갱신하는 방식**으로 구현한다.

예를 들어 검토 상태 변경은 `cases.status`와 `cases.updated_at`을 갱신한다. 문서 전체를 재작성하지 않도록 한다.

`files`는 `repositories/image.py`만 접근한다. 영상처리 모듈이 결과 파일을 저장할 때도 `repositories/image.py`와 `repositories/file_storage.py`의 함수를 호출한다.

### 6. 공통 데이터 규칙

1. 사용자 식별자는 Firebase UID를 사용한다.
2. 컬렉션 이름과 필드 이름은 모든 모듈에서 동일하게 사용하며, `src/models.py`의 정의를 따른다.
3. 시간 데이터는 Firestore Timestamp를 사용한다.
4. 케이스 상태와 접근 유형은 정해진 값만 허용한다.
5. 이미지 바이너리는 Firestore에 저장하지 않는다.
6. 원본 파일과 처리 결과 파일을 구분하며, 원본을 처리 결과로 덮어쓰지 않는다.
7. 복잡한 이력 검색 기능은 이번 구현 범위에서 제외한다.
8. 권한 검사는 Service 계층에서 일관되게 수행한다. 파일 다운로드도 케이스 접근 권한을 확인한다.

### 7. 설계의 제한 사항

- `review_records`는 배열로 저장하므로 개별 기록 검색과 무제한 누적에 적합하지 않다.
- 소규모 시연 데이터를 대상으로 기록 수와 크기를 제한한다.
- Firestore 문서별 최대 크기 제한을 고려한다.
- 파일 저장소 저장과 Firestore 문서 저장은 별개의 작업이므로 일부 실패 시 정리 처리가 필요하다.
- 실제 운영 수준의 변경 감사 로그와 복잡한 이력 관리는 구현하지 않는다.

### 8. 미결정 사항

| 항목 | 내용 | 영향 모듈 |
| --- | --- | --- |
| `width`·`height` 저장 | `FileInfo`에 추가할지 여부 | 영상 관리, 뷰어, ROI 검증 |
| 원본 ROI 단독 분석 기록 | 영상처리 없이 ROI만 분석하는 경우 `processing_results` 기록 방식 | 영상처리 |
| 처리 관련 필드명 | 설계 `process_type_id` vs `models.py`의 `processing_type_id`, `algorithm_version`, `error_message`, `ProcessingType.version` | 영상처리 |
| `REGISTRAR` 권한 | 역할별 허용 기능 | 인증·권한 |
| `sex` 허용 값 | 환자 성별 값 목록 | 케이스 |
| Firebase Storage 전환 | 전환 시점과 버킷 경로 규칙 | 영상 관리 |

### 9. 최종 설계 요약

**Firestore 컬렉션: 8개**

- users
- departments
- patients
- cases
- case_assignees
- process_types
- processing_results
- files

**추가 컬렉션: 없음**

**파일 저장:** 1단계 로컬 `data/storage/` → 2단계 Firebase Storage

**인증:** Firebase Authentication

**공통 데이터 구조:** `src/models.py`

**데이터 접근:** `src/repositories/`

**업무 규칙과 권한 검사:** `src/services/`

---

**문서 상태:** 팀 합의용 개정안 v1.3

**기준:** 팀 프로젝트 파일 구조 + 기존 Firestore 최소 설계 + `src/models.py`

**설계 원칙:** 기존 기능과 데이터 영역을 유지하면서 구조 변경 최소화
