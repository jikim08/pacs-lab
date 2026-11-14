'''	Firebase 연결 정보, 파일 저장 경로, 업로드 제한 등 설정 관리'''
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, firestore

# Firebase 연결 준비 및 Firestore 사용 객체 반환
def get_db():
    # 이미 초기화되어있으면 기존 app 사용
    try:
        app = firebase_admin.get_app()
    # 아닌 경우 인증 파일을 이용해 Firebase 초기화
    except ValueError:
        key_path = (
            Path(__file__).resolve().parent.parent / "service-account.json"
        )
        credential = credentials.Certificate(str(key_path))
        app = firebase_admin.initialize_app(credential)

    return firestore.client(app=app)