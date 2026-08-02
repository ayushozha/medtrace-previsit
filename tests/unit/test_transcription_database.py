from __future__ import annotations

import importlib.util
import asyncio
from pathlib import Path
import sys

from fastapi.testclient import TestClient


MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "services"
    / "transcription"
    / "backend"
    / "database.py"
)
MAIN_PATH = MODULE_PATH.parent / "main.py"


def _database(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_DB_PATH", str(tmp_path / "sessions.db"))
    spec = importlib.util.spec_from_file_location("test_transcription_database", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _service():
    backend = str(MAIN_PATH.parent)
    if backend not in sys.path:
        sys.path.insert(0, backend)
    spec = importlib.util.spec_from_file_location("tested_transcription_main", MAIN_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_session_lookup_and_report_update_are_patient_scoped(tmp_path, monkeypatch):
    database = _database(tmp_path, monkeypatch)
    database.save_session(
        "session-1",
        "2026-08-01T12:00:00",
        "1:30",
        "Patient transcript",
        "Draft",
        "audio",
        "patient-a",
    )

    assert database.get_session("session-1", "patient-b") is None
    assert database.get_all_sessions("patient-b") == []
    assert not database.update_session_report("session-1", "patient-b", "Wrong patient")

    session = database.get_session("session-1", "patient-a")
    assert session is not None
    assert session["timestamp"] == "2026-08-01T12:00:00"
    assert session["duration"] == "1:30"
    assert [row["id"] for row in database.get_all_sessions("patient-a")] == ["session-1"]
    assert database.update_session_report("session-1", "patient-a", "Validated")
    assert database.get_session("session-1", "patient-a")["report"] == "Validated"


def test_canonical_failure_prevents_derived_session_cache(monkeypatch) -> None:
    service = _service()
    cache_writes: list[str] = []

    class Pipeline:
        async def transcribe_audio(self, *_args):
            return "Patient transcript"

        async def run_agent_pipeline(self, **_kwargs):
            return {"document": "Draft"}

    async def fail_write(**_kwargs):
        raise RuntimeError("canonical unavailable")

    monkeypatch.setattr(service, "VoicePipeline", Pipeline)
    monkeypatch.setattr(service, "persist_canonical_consultation", fail_write)
    monkeypatch.setattr(service, "save_session", lambda **_kwargs: cache_writes.append("written"))

    response = TestClient(service.app).post(
        "/api/sessions",
        json={
            "patient_id": "patient-a",
            "duration": "0:05",
            "audio_base64": "data:audio/webm;base64,YQ==",
        },
    )

    assert response.json() == {"error": "canonical unavailable"}
    assert cache_writes == []


def test_session_endpoint_returns_multiple_canonical_rows(monkeypatch) -> None:
    service = _service()
    rows = [{"id": "session-2"}, {"id": "session-1"}]

    async def history(_patient_id):
        return rows

    monkeypatch.setattr(service, "fetch_canonical_consultations", history)
    response = TestClient(service.app).get("/api/sessions", params={"patient_id": "patient-a"})

    assert response.status_code == 200
    assert response.json() == rows


def test_report_canonical_failure_prevents_file_and_sqlite_writes(tmp_path, monkeypatch) -> None:
    service = _service()
    report_dir = tmp_path / "reports"
    cache_writes: list[str] = []

    async def history(_patient_id):
        return [{
            "id": "session-1",
            "patient_id": "patient-a",
            "timestamp": "2026-08-01T12:00:00",
            "duration": "0:05",
            "transcript": "Transcript",
        }]

    async def fail_write(**_kwargs):
        raise RuntimeError("canonical unavailable")

    monkeypatch.setattr(service, "DATA_REPORTS_DIR", str(report_dir))
    monkeypatch.setattr(service, "fetch_canonical_consultations", history)
    monkeypatch.setattr(service, "persist_canonical_consultation", fail_write)
    monkeypatch.setattr(service, "update_session_report", lambda *_args: cache_writes.append("written"))
    response = TestClient(service.app, raise_server_exceptions=False).post(
        "/api/sessions/generate-report",
        json={
            "session_id": "session-1",
            "patient_id": "patient-a",
            "current_report_text": "Reviewed",
            "regenerate": False,
        },
    )

    assert response.status_code == 500
    assert not report_dir.exists()
    assert cache_writes == []


def test_voice_websocket_rejects_untrusted_origins() -> None:
    service = _service()

    class Socket:
        headers = {"origin": "https://attacker.example"}

        def __init__(self):
            self.closed = None

        async def close(self, **kwargs):
            self.closed = kwargs

        async def accept(self):  # pragma: no cover
            raise AssertionError("Untrusted websocket must not be accepted")

    socket = Socket()
    asyncio.run(service.websocket_voice_endpoint(socket))

    assert socket.closed["code"] == 1008
