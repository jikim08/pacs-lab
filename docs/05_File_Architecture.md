pacs-lab/
├── app.py                       # 앱 실행, 로그인 확인, 화면 전환
│
├── src/
│   ├── config.py                # 환경변수, 지원 형식, 업로드 제한
│   ├── models.py                # 공통 데이터 구조, 사용자 역할, 케이스 상태
│   ├── exceptions.py            # 공통 예외
│   ├── firebase_client.py       # Firebase 초기화, Firestore와 Storage 연결
│   │
│   ├── ui/
│   │   ├── login.py             # 로그인 화면
│   │   ├── case_list.py         # 케이스 목록, 검색, 필터
│   │   ├── case_register.py     # 케이스 등록, 영상 첨부
│   │   ├── case_detail.py       # 영상 처리, 결과 비교, 메모, 리포트, 이력
│   │   ├── admin.py             # 담당자 배정, 진행 현황 관리
│   │   └── components/
│   │       ├── sidebar.py       # 공통 메뉴, 사용자 정보, 로그아웃
│   │       └── image_viewer.py  # 영상 표시, 확대와 축소, ROI 선택 
│   │
│   ├── auth/
│   │   ├── firebase_auth.py     # 로그인 요청, 토큰 검증과 갱신
│   │   └── session.py           # Streamlit 로그인 상태 유지와 정리
│   │
│   ├── services/
│   │   ├── access_policy.py     # 역할과 담당 범위에 따른 권한 검사
│   │   ├── case.py              # 케이스 등록, 검색, 조회, 수정
│   │   ├── image.py             # 영상 업로드·조회·다운로드 흐름, 권한·크기·확장자 검증, 실패 시 정리
│   │   ├── processing.py        # 처리 실행, 결과와 처리 이력 저장
│   │   └── review.py            # 담당자 배정, 상태 변경, 메모와 리포트
│   │
│   ├── processing/
│   │   ├── image_io.py          # 이미지 디코딩, 손상 확인, 가로세로 크기 추출, 인코딩
│   │   ├── algorithms.py        # 영상처리 알고리즘 3종 이상
│   │   └── roi.py               # ROI 검사와 추출, 평균, 분산, 히스토그램
│   │
│   └── repositories/
│       ├── user.py              # 사용자 정보와 역할 조회
│       ├── case.py              # 케이스 데이터 저장과 조회
│       ├── patient.py           # 환자 존재 여부 확인과 조회
│       ├── department.py        # 진료과 존재 여부 확인과 조회
│       ├── image.py             # 원본·결과 파일 정보 저장과 조회
│       ├── processing.py        # 처리 설정, 결과 정보와 실행 이력, ROI 분석 기록
│       ├── review.py            # 메모, 리포트, 상태 변경 이력
│       └── file_storage.py      # 파일 저장·읽기·삭제 (1단계 로컬 data/storage → 2단계 Firebase Storage)
│
├── assets/
│   └── style.css                # 화면 스타일
│
├── data/
│   ├── storage/                 # 1단계 로컬 파일 저장소 (Git 제외)
│   └── samples/
│       ├── images/              # 공개 샘플 영상
│       ├── cases.json           # 시연용 모의 케이스
│       └── README.md            # 샘플 출처와 이용 조건
│
├── scripts/
│   └── seed_demo_data.py        # 시연 데이터를 Firebase에 등록
│
├── tests/
│   ├── fixtures/                # 테스트용 작은 이미지와 데이터
│   ├── test_access_policy.py    # 역할과 담당 범위 검사
│   ├── test_review.py           # 상태 전환과 변경 조건
│   ├── test_processing.py       # 알고리즘과 ROI 계산
│   └── test_input_validation.py # 잘못된 파일, ROI, 파라미터 검사
│
├── docs/
│   ├── design.md               # 요구사항, 구조, DB, 권한, 상태 전이
│   ├── verification.md         # 검증 시나리오와 실제 결과
│   ├── deployment.md           # JCloud 배포, 재시작, 복구
│   └── contributions.md        # 개인별 기여와 코드 및 외부 자료 활용
│
├── .streamlit/
│   └── config.toml             # 테마와 서버 실행 설정
│
├── .env.example                # 환경변수 예시
├── .gitignore
├── requirements.txt
└── README.md                   # 프로젝트 소개, 설치, 실행, 테스트