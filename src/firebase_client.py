# src/firebase_client.py
import firebase_admin
from firebase_admin import credentials, firestore

from src.config import FIREBASE_CREDENTIALS_PATH


def get_db():
    try:
        app = firebase_admin.get_app()
    except ValueError:
        if FIREBASE_CREDENTIALS_PATH is None:
            raise ValueError(
                "GOOGLE_APPLICATION_CREDENTIALS 설정이 필요합니다."
            )

        credential = credentials.Certificate(
            str(FIREBASE_CREDENTIALS_PATH)
        )
        app = firebase_admin.initialize_app(credential)

    return firestore.client(app=app)