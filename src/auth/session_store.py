"""새로고침 후 복원을 위한 서버 메모리 세션 저장소."""

import secrets
import time
from threading import RLock

import streamlit as st


SESSION_LIFETIME = 8 * 60 * 60  # 로그인 유지 시간: 8시간


@st.cache_resource
def _get_store():
    return {}, RLock()

def create_session(data: dict) -> str:
    store, lock = _get_store()
    session_id = secrets.token_urlsafe(32)
    now = time.time()

    with lock:
        # 새 로그인 시 만료된 세션도 정리합니다.
        expired_ids = [
            key
            for key, record in store.items()
            if record["valid_until"] <= now
        ]
        for key in expired_ids:
            del store[key]

        store[session_id] = {
            "data": data.copy(),
            "valid_until": now + SESSION_LIFETIME,
        }

    return session_id

def read_session(session_id: str) -> dict | None:
    store, lock = _get_store()

    with lock:
        record = store.get(session_id)

        if record is None:
            return None

        if time.time() >= record["valid_until"]:
            del store[session_id]
            return None

        return record["data"].copy()

def update_session(session_id: str, data: dict) -> bool:
    store, lock = _get_store()

    with lock:
        record = store.get(session_id)

        if record is None:
            return False

        if time.time() >= record["valid_until"]:
            del store[session_id]
            return False

        record["data"] = data.copy()
        return True


def delete_session(session_id: str) -> None:
    store, lock = _get_store()

    with lock:
        store.pop(session_id, None)