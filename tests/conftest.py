"""Test muhiti: har bir test sessiyasi ALOHIDA vaqtinchalik DATA_DIR bilan
ishlaydi — haqiqiy backend/data/ dagi ma'lumotlarga hech qachon tegmaydi.

Muhim: muhit o'zgaruvchilari ``app`` import qilinishidan OLDIN o'rnatiladi
(config.py ularni import paytida o'qiydi; load_dotenv mavjud qiymatlarni
almashtirmaydi).
"""
from __future__ import annotations

import io
import math
import os
import struct
import sys
import tempfile
import wave
from pathlib import Path

import pytest

_TMP = tempfile.mkdtemp(prefix="sadaf-test-")
os.environ.update(
    {
        "DATA_DIR": _TMP,
        "RECORDINGS_DIR": str(Path(_TMP) / "recordings"),
        "JWT_SECRET": "test-secret-0123456789abcdef0123456789abcdef",
        "ADMIN_LOGIN": "boss",
        "ADMIN_PASSWORD": "boss12345",
        "ADMIN_NAME": "Bosh Menejer",
        "GROQ_API_KEY": "",
        "XAI_API_KEY": "test-xai-key-SECRET",
        "XAI_MODEL": "test-grok-model",
        "XAI_STT_MODEL": "",
        "XAI_STT_LANGUAGE": "",
        "CALL_MAX_UPLOAD_MB": "5",
    }
)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402


@pytest.fixture(scope="session")
def client() -> TestClient:
    with TestClient(main.app) as c:
        yield c


def login(client: TestClient, login_name: str, password: str) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"login": login_name, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def make_wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
            for i in range(int(seconds * rate))
        )
        wf.writeframes(frames)
    return buf.getvalue()
