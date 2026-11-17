# PACS 프로젝트 개발 안내

이 문서는 현재 합의한 기본 구조, 초기 설정, 데이터 모델과 4인 개발 분담을 정리한다. 기존 UI는 구성된 것으로 가정하며, 각 기능의 Python 로직을 구현한 뒤 연결한다. 실제 저장소 전체의 구현 완료 여부를 점검한 문서는 아니다.

## 1. 구현 구성

| 구분 | 사용 기술과 역할 |
|---|---|
| 화면 및 앱 실행 | Python + Streamlit |
| 로그인 | Firebase Authentication |
| 업무 데이터 | Cloud Firestore |
| 원본 영상과 처리 결과 파일 | JCloud 영구 저장 공간 |
| 배포 | JCloud |

Streamlit 화면에서 Python 서비스 함수를 직접 호출한다. 별도의 HTTP API 서버와 Controller 계층은 만들지 않는다. 서버 측 처리는 Streamlit 앱 안에서 수행한다.

- UI: 입력과 화면 표시, 서비스 호출, 결과 및 오류 안내
- Service: 입력과 권한 및 상태 검사, 처리와 저장 순서 관리
- Repository: Firestore 문서 저장과 조회
- Processing: 이미지 디코딩, 알고리즘, ROI 계산
- File storage: 실제 파일 저장과 읽기 및 삭제

## 2. 기본 폴더 구조

```text
pacs-project/
├── app.py
├── src/
│   ├── __init__.py
│   ├── config.py
│   ├── firebase_client.py
│   ├── models.py
│   ├── exceptions.py
│   ├── ui/
│   │   ├── login.py
│   │   ├── case_list.py
│   │   ├── case_register.py
│   │   ├── case_detail.py
│   │   ├── admin.py
│   │   └── components/
│   │       ├── sidebar.py
│   │       └── image_viewer.py
│   ├── auth/
│   │   ├── firebase_auth.py
│   │   └── session.py
│   ├── services/
│   │   ├── access_policy.py
│   │   ├── case.py
│   │   ├── image.py
│   │   ├── processing.py
│   │   └── review.py
│   ├── processing/
│   │   ├── image_io.py
│   │   ├── algorithms.py
│   │   └── roi.py
│   └── repositories/
│       ├── user.py
│       ├── patient.py
│       ├── department.py
│       ├── case.py
│       ├── image.py
│       ├── processing.py
│       ├── review.py
│       └── file_storage.py
├── assets/
│   └── style.css
├── data/
│   └── samples/
│       ├── images/
│       ├── cases.json
│       ├── patients.json
│       ├── departments.json
│       └── README.md
├── tests/
├── docs/
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

- 各 Python 패키지에 `__init__.py`를 둔다. 위 구조에서는 반복 표기를 생략했다.
- 기존 UI의 세부 파일 배치가 다르면 동작하는 UI를 기준으로 유지한다. 위 구조에 맞추기 위한 패널 분리는 하지 않는다.
- `data/samples/`의 JSON 파일은 필요한 시점에 준비하는 시연 자료이며, 앱의 실제 DB가 아니다.
- `scripts/seed_demo_data.py`는 시연 데이터 일괄 등록이 필요할 때 추가한다.
- `.streamlit/config.toml`은 선택 사항으로 현재 필수 초기 파일에서 제외한다.
- 프리셋과 팀 특화 기능은 범위를 확정한 후 추가한다. 특화 기능 1개 이상은 최종 구현 범위에 포함해야 한다.
- 테스트 파일은 기능 구현 후 통합 단계에서 작성하기로 한다. 개발 중에는 각 기능의 직접 실행 확인을 진행한다.

## 3. 공통 초기 파일

| 파일 | 역할과 현재 기준 |
|---|---|
| `requirements.txt` | 공통 라이브러리 목록 |
| `.gitignore` | 비밀정보, 가상환경, 생성 파일 제외 |
| `.env.example` | 설정 항목 예시. 실제 값은 넣지 않음 |
| `src/config.py` | 환경변수, 파일 저장 경로, 업로드 제한 관리 |
| `src/firebase_client.py` | Firebase 초기화, `get_db()`로 Firestore 객체 반환 |
| `src/models.py` | 팀이 주고받는 딕셔너리의 필드와 자료형 정의 |
| `src/exceptions.py` | 공통 오류 유형 정의. 현재 작성한 파일을 사용 |

Firebase 연결 코드는 `config.py`에서 `firebase_client.py`로 분리했다. DB 연결을 사용하는 파일은 다음과 같이 가져온다.

```python
from src.firebase_client import get_db
```

### 실행 환경

`models.py`의 `NotRequired` 사용을 기준으로 Python 3.11 이상을 사용한다. 공통 Python 세부 버전도 팀에서 맞춘다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# .env의 실제 값과 서비스 계정 키 파일을 준비한 뒤 실행
python -m streamlit run app.py
```

가상환경 활성화 명령은 macOS/Linux 기준이다. `.env`가 이미 있으면 복사하지 않고 기존 파일을 사용한다.

현재 라이브러리 목록:

```text
streamlit
firebase-admin
requests
python-dotenv
numpy
opencv-python-headless
Pillow
```

버전은 공통 환경에서 실행을 확인한 뒤 확인된 버전으로 고정한다. OpenCV는 서버용 `opencv-python-headless`를 사용한다.

### 환경변수

```dotenv
FIREBASE_PROJECT_ID=your-project-id
FIREBASE_API_KEY=your-web-api-key
GOOGLE_APPLICATION_CREDENTIALS=credentials/service-account.json
FILE_STORAGE_ROOT=data/storage
```

- `.env.example`은 Git으로 공유하고 실제 값은 각자의 `.env`에 입력한다.
- 인증키 경로는 실제 파일 위치와 일치해야 한다. 키를 최상위에 두었다면 `service-account.json`으로 설정한다.
- `.env`, 서비스 계정 키, 토큰은 Git에 올리지 않는다. 최상위 키를 사용한다면 해당 파일명도 `.gitignore`에 추가한다.
- Firebase Storage는 사용하지 않으므로 `FIREBASE_STORAGE_BUCKET`은 필요하지 않다.
- Firebase 필수 설정 누락은 연결 또는 인증 기능을 실행할 때 명확한 오류로 안내한다.

### 파일 저장 경로

개발 중에는 각자 PC의 `data/storage/`에 파일을 저장한다. 배포 시 `FILE_STORAGE_ROOT`를 JCloud에서 확인한 영구 저장 경로로 설정한다. 특정 JCloud 마운트 경로는 아직 확정하지 않았다.

```text
data/storage/
└── cases/
    └── {case_id}/
        └── files/
            └── {file_id}.png
```

위 경로는 공통으로 사용할 저장 규칙안이다. 원본과 결과는 서로 다른 파일 ID를 사용하고, 파일 종류는 메타데이터로 구분한다. 원본을 덮어쓰지 않는다.

- Firestore의 `storage_path`에는 `cases/.../files/...png` 같은 상대 경로를 기록한다.
- 실제 경로는 `FILE_STORAGE_ROOT`와 상대 경로를 결합해 만든다.
- 저장 경로는 앱에서 생성한다. 입력된 경로가 저장 루트 밖을 가리키지 않도록 검사한다.
- `data/storage/`는 `.gitignore`에 등록한다.
- 로컬 개발자의 파일은 서로 자동 공유되지 않는다. Firestore에 기록이 있어도 해당 PC에 파일이 없으면 읽을 수 없으므로 개발 데이터와 파일의 출처를 구분한다.

## 4. 데이터 모델

`TypedDict`를 사용해 기존 딕셔너리 코드를 유지한다. 타입 선언 자체가 실행 시 입력 검증이나 DB 생성을 수행하지는 않는다.

| 모델 | 주요 내용 | 담당 Repository |
|---|---|---|
| `User` | Firebase UID, 이름, 역할, 소속 과, 로그인 및 생성과 수정 시간 | `user.py` |
| `Department` | 진료과 ID와 이름 | `department.py` |
| `Patient` | 모의 환자 ID, 이름, 생년월일, 성별, 생성과 수정 시간 | `patient.py` |
| `CaseCreateData` | 등록 서비스가 저장 요청할 케이스 데이터 | `case.py` |
| `Case` | 케이스 ID와 생성 및 수정 시간이 포함된 케이스 | `case.py` |
| `CaseDetail` | 케이스에 환자와 진료과 정보를 붙인 화면용 조회 결과 | `case.py` 및 서비스 조합 |
| `CaseAssignment` | 케이스의 현재 담당자, 배정자, 배정 시간 | `case.py` |
| `ProcessingType` | 처리 종류 ID, 이름, 설명, 버전 | `processing.py` |
| `ProcessingResult` | 입력 파일, 알고리즘 버전, 설정, ROI, 통계, 실행 상태와 시간, 실패 사유 | `processing.py` |
| `FileInfo` | 원본과 결과 파일의 메타데이터, 상대 경로, 종류, 등록자 | `image.py` |
| `ReviewRecord` | 검토 메모 또는 요약 리포트 | `review.py` |
| `CaseHistory` | 담당자와 상태 변경 전후 값, 변경자, 시간 | `review.py` |

계산용 공통 타입은 `ROI`, `ROIStatistics`, `ProcessingParameters`를 사용한다.

### 공통 기준

- 사용자 ID는 Firebase UID로 통일한다. `created_by`, `user_id`, `author_id` 등의 사용자 참조 값도 UID를 의미한다.
- 조회 결과는 Firestore 문서 ID를 `case_id`, `file_id` 등의 모델 필드에 포함한다.
- 시간은 UTC 기준의 시간대 정보가 있는 `datetime`으로 통일한다. Firestore 서버 시간은 저장 시 사용하고, 조회 모델에는 실제 시간값을 반환한다.
- 생년월일은 `YYYY-MM-DD` 문자열로 표현한다.
- ROI는 화면 확대 좌표가 아닌 원본 이미지의 픽셀 좌표를 사용한다.
- 원본 파일의 `processing_result_id`는 `None`이다. 결과 파일에는 해당 처리 실행 ID를 기록한다.
- 처리 결과의 `input_file_id`는 입력으로 사용한 파일을 참조한다.
- 별도의 `images` 데이터와 `files` 데이터를 중복 관리하지 않는다. 영상 메타데이터는 `FileInfo`로 관리한다.
- 케이스당 현재 담당자는 1명으로 잡고, 변경 내역은 `CaseHistory`에 남긴다.
- 케이스의 `registration_note`는 접수 전달사항이다. 검토 메모와 리포트는 `ReviewRecord`로 구분한다.

모델 초안의 역할은 `REGISTRAR`, `REVIEWER`, `ADMIN`, 케이스 상태는 `검토 대기`, `검토 중`, `검토 완료`이다. 기존 인증과 권한 코드의 역할 값과 일치하는지 분기 전에 확인한다. 성별 허용 값, 완료 후 수정과 재검토 정책, Firestore 컬렉션 이름은 별도 합의가 필요하다.

## 5. 공통 예외

| 예외 | 발생 상황 |
|---|---|
| `AppError` | 공통 애플리케이션 오류의 부모 타입 |
| `AuthenticationError` | 인증 실패 또는 로그인 필요 |
| `PermissionDeniedError` | 작업 권한 없음 |
| `NotFoundError` | 요청한 데이터 없음 |
| `ValidationError` | 파일, ROI, 파라미터 등 입력 오류 |
| `ConflictError` | 현재 상태에서 허용되지 않는 작업이나 중복 |
| `StorageError` | 파일 또는 DB 입출력 실패 |

서비스에서 구체적인 메시지와 함께 발생시키고 UI에서 받아 안내한다. 현재 코드의 `ValueError`, `LookupError`, `PermissionError`는 자동으로 바뀌지 않는다. 담당 기능을 수정할 때 공통 예외로 전환하고 UI 처리도 맞춘다. 예상하지 못한 내부 오류를 그대로 사용자에게 출력하지 않는다.

## 6. 4인 담당 범위

UI 구현은 완료된 것으로 가정한다. 기능 개발 중에는 각자 아래 파일을 수정하고, UI와의 최종 연결은 1번 담당자가 진행한다.

| 담당 | 기능 | 수정 담당 파일 |
|---|---|---|
| 1번: 지후 | 인증, 권한, 공통 설정과 모델, 최종 연결 | `auth/*`, `services/access_policy.py`, `repositories/user.py`, `config.py`, `firebase_client.py`, `models.py`, `exceptions.py` |
| 2번 | 케이스, 환자와 진료과 조회, 영상 파일 관리 | `services/case.py`, `services/image.py`, `repositories/case.py`, `repositories/patient.py`, `repositories/department.py`, `repositories/image.py`, `repositories/file_storage.py` |
| 3번 | 알고리즘 3종 이상, ROI, 처리 결과와 이력 | `services/processing.py`, `processing/image_io.py`, `processing/algorithms.py`, `processing/roi.py`, `repositories/processing.py` |
| 4번 | 담당자 배정, 상태 전환, 메모와 리포트, 검토 이력 | `services/review.py`, `repositories/review.py` |

위 파일 경로는 `src/` 기준이다. `app.py`, `src/ui/*`, `assets/style.css`, 공통 의존성 및 설정 파일은 1번 담당자가 변경을 조율한다. 다른 담당 파일의 수정이 필요하면 해당 담당자에게 요청한다.

담당자 배정은 검토 업무 흐름이므로 `services/review.py`에서 처리한다. 배정 가능 여부를 판단하는 것은 4번 담당, 케이스와 배정 정보를 저장하는 함수는 2번 담당이다. 단순 저장 함수가 담당자 역할이나 검토 완료 조건 같은 업무 규칙을 판단하지 않는다.

## 7. 먼저 맞출 함수 규격

모든 함수의 내부 구현을 먼저 끝낼 필요는 없다. 다른 담당자가 호출하는 함수부터 이름, 인자, 반환값, 실패 시 예외를 맞춘다. 아래는 우선 합의할 항목이며, 미확정 함수명을 이미 구현된 것으로 보지 않는다.

| 제공 담당 | 위치 | 먼저 합의할 내용 |
|---|---|---|
| 1번 | `auth/session.py` | 현재 사용자 반환 형식과 비로그인 처리 |
| 1번 | `services/access_policy.py` | 등록, 목록 조회, 특정 케이스 조회, 처리, 검토, 배정 권한 검사 |
| 1번 | `repositories/user.py` | UID로 사용자 조회, 배정 가능한 검토자 목록 |
| 2번 | `repositories/case.py` | 케이스 조회, 현재 담당자 조회, 담당자와 상태 변경 저장 |
| 2번 | `repositories/image.py` | 파일 메타데이터 저장과 조회 |
| 2번 | `repositories/file_storage.py` | 파일 읽기, 저장, 삭제의 인자와 반환 경로 |
| 3번 | `processing/image_io.py` | 파일 바이트 검사와 이미지 배열 변환 |

### 기존 케이스 서비스 규격

현재 공유된 함수는 다음 규격을 사용한다.

```python
create_case(
    patient_id: str,
    department_id: str,
    exam_type: str,
    exam_info: str,
    created_by: str,
) -> str

get_case_list(uid: str, limit: int = 50) -> list[Case]

get_case_detail(uid: str, case_id: str) -> CaseDetail
```

- 등록 결과는 생성된 케이스 ID를 반환한다.
- 목록이 없으면 빈 리스트를 반환한다.
- 상세 조회 시 케이스가 없으면 공통 예외 `NotFoundError`로 통일한다. 기존 구현의 `LookupError` 전환이 필요하다.
- 참조한 환자나 진료과를 찾을 수 없으면 상세 결과의 해당 항목은 `None`이다.
- 타입 이름은 `src.models`에서 가져오고 기존 딕셔너리 반환 구조를 유지한다.
- 현재 권한 함수는 UID 방식이므로 `User` 객체 방식과 혼용하지 않는다. UID는 토큰 검증을 거친 세션 사용자에서 가져온다.
- 특정 케이스의 접근 권한은 사용자 역할뿐 아니라 담당 범위를 검사해야 한다. 목록도 조회 가능한 범위로 제한한다. 현재 UID만 검사하는 함수에 이 범위 제한이 구현되었는지는 별도 확인한다.

### 이미지와 파일 전달 기준

- 저장소 입출력: `bytes`
- 이미지 계산: NumPy 배열
- ROI: `ROI` 딕셔너리
- 통계: Python 기본 `float`, `int`, `list`로 변환해 Firestore에 저장
- 배열의 색상 순서, 회색조 변환, 자료형은 2번과 3번이 먼저 합의한다.

미구현 함수는 임시 성공값을 반환하지 않고 명확히 표시한다.

```python
def get_case(case_id: str) -> Case | None:
    """Repository 조회. 케이스가 없으면 None 반환."""
    raise NotImplementedError
```

Repository의 조회 결과가 `None`이면 서비스에서 존재 여부를 판단해 `NotFoundError`를 발생시키는 방식으로 구분할 수 있다.

## 8. 개발 순서와 연결 시 주의점

1. 공통 모델, DB 필드와 컬렉션 이름, 함수 규격을 맞춘다.
2. 공통 설정과 Firebase 연결을 준비하고 PR로 `develop`에 반영한다.
3. 각자 최신 `develop`에서 담당 작업 브랜치를 만든다.
4. 인증, 데이터 접근, 샘플 이미지 계산, 검토 규칙을 병렬로 구현한다.
5. 2번의 파일과 케이스 접근 함수가 준비되면 처리 및 검토 기능에 연결한다.
6. 구현된 서비스를 기존 UI에 연결하고 전체 업무 흐름을 확인한다.
7. 통합 단계에서 자동화 테스트와 권한 및 예외 시나리오를 확인한다.

Firestore 상태 변경과 변경 이력은 가능하면 같은 트랜잭션으로 저장한다. 2번과 4번은 트랜잭션 전달 또는 저장 함수 구성을 미리 합의한다. 파일 저장과 Firestore 기록은 하나의 트랜잭션으로 묶이지 않으므로 실패 시 정리 또는 재시도 기준을 서비스에서 처리한다. 연속 클릭이나 Streamlit 재실행으로 의도하지 않은 중복 저장이 발생하지 않도록 한다.

## 9. Git 협업 규칙

### 브랜치

| 브랜치 | 용도 | 분기 기준 | 병합 대상 |
|---|---|---|---|
| `main` | 배포 가능한 안정 버전 | — | — |
| `develop` | 개발 기능 통합 | `main` | `main` |
| `feature/*` | 기능 개발 | `develop` | `develop` |
| `fix/*` | 개발 중 오류 수정 | `develop` | `develop` |
| `hotfix/*` | 배포 버전 긴급 수정 | `main` | `main`, `develop` |

- `main`, `develop`에 직접 푸시하지 않는다. PR로 병합한다.
- 초기에는 별도 `release` 브랜치를 사용하지 않는다.
- 브랜치명은 `종류/이슈번호-영문-작업내용` 형식이다. 예: `feature/12-firebase-login`.
- 기능 작업 PR은 Squash merge, `develop → main` 배포 PR은 merge commit을 사용한다.
- 작성자 외 1명 이상의 승인을 받고 병합한다. 리뷰 후 변경한 코드도 확인받는다.
- 검증 실패와 미해결 충돌이 있으면 병합하지 않는다.
- hotfix는 `develop`에도 반영한다. 병합 완료된 작업 브랜치는 삭제한다.

### 기본 작업 흐름

Issue 등록 → 담당자와 완료 조건 작성 → 작업 브랜치 생성 → 구현과 직접 확인 → 커밋 → PR → 리뷰와 수정 → 병합 → 완료 조건 확인 후 Issue 종료.

```bash
git switch develop
git pull --ff-only origin develop
git switch -c feature/12-firebase-login
```

작업 중 최신 통합 내용을 반영할 때는 해당 작업 브랜치에서 실행한다.

```bash
git fetch origin
git merge origin/develop
```

### 커밋과 PR

커밋 형식은 `type(scope): 작업 내용`이며 scope는 선택 사항이다. 타입은 `feat`, `fix`, `refactor`, `docs`, `test`, `style`, `chore`, `ci`, `revert`를 사용한다. `style`은 공백이나 들여쓰기 등 코드 형식 변경에 사용한다.

```text
feat(case): 케이스 등록 기능 추가
fix(auth): 로그아웃 후 사용자 정보가 남는 오류 수정
chore: 프로젝트 공통 설정과 데이터 모델 구성
```

Issue에는 목표, 담당자, 완료 조건, 선행 작업, 관련 요구사항을 적는다. PR에는 관련 Issue(`Refs #번호`), 변경 내용, 실제 확인한 내용, 추가 설정과 제한 사항을 적는다. 관련 없는 변경은 나눠서 커밋하고 PR 하나에는 하나의 목적을 담는다.

공유 브랜치에 강제 푸시하지 않는다. 다른 팀원이 사용하는 함수나 모델 변경은 먼저 공유한다. 실제 수행한 검증만 기록한다.

## 10. 분기 전 확인할 미확정 사항

- 기존 사용자 역할 값과 모델의 역할 값 일치 여부
- Firestore 컬렉션 이름, 문서 ID와 필드명
- 이미지 배열의 색상 및 자료형 규격과 알고리즘 3종
- 권한 함수의 케이스별 접근 범위와 목록 제한
- 상태 변경 조건, 재배정과 완료 후 수정 정책
- 상태 및 배정 저장과 이력 저장의 원자성
- 팀 특화 기능의 범위와 담당자
- JCloud의 실제 영구 저장 경로와 배포 설정

모델과 폴더 구조는 현재 기준안이다. 변경이 필요하면 담당자와 합의한 뒤 공통 파일과 호출 코드를 함께 수정한다.
