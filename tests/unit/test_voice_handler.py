from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "services"
    / "transcription"
    / "backend"
    / "voice_handler.py"
)


def _voice_pipeline(monkeypatch):
    agent = ModuleType("agent")
    agent.graph = object()
    monkeypatch.setitem(sys.modules, "agent", agent)
    spec = importlib.util.spec_from_file_location("test_voice_handler_module", MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.VoicePipeline


def test_diarization_keeps_speaker_indices_neutral(monkeypatch):
    pipeline = _voice_pipeline(monkeypatch)
    transcript = pipeline._format_diarized_transcript(
        {
            "results": {
                "utterances": [
                    {"speaker": 0, "transcript": "First turn."},
                    {"speaker": 1, "transcript": "Second turn."},
                ]
            }
        }
    )

    assert transcript == "Speaker 0: First turn.\nSpeaker 1: Second turn."
    assert "Clinician" not in transcript
    assert "Patient" not in transcript


def test_audio_container_detection_does_not_relabel_webm_as_wav(monkeypatch):
    pipeline = _voice_pipeline(monkeypatch)

    assert pipeline._audio_content_type(b"\x1a\x45\xdf\xa3webm") == "audio/webm"
    assert pipeline._audio_content_type(b"RIFF\x00\x00\x00\x00WAVE") == "audio/wav"
    assert pipeline._normalize_audio_content_type("audio/webm;codecs=opus") == "audio/webm"
