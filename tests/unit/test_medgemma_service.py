from __future__ import annotations

from medtrace_agent.imaging.model_adapters.medgemma import MedGemmaService


def test_status_treats_partial_fireworks_config_as_unconfigured(monkeypatch):
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-key")
    monkeypatch.delenv("FIREWORKS_VL_MODEL", raising=False)
    monkeypatch.delenv("MEDGEMMA_ENDPOINT", raising=False)
    monkeypatch.delenv("MEDGEMMA_MODEL_ID", raising=False)

    assert MedGemmaService().status() == {
        "provider": "mock",
        "fireworks_configured": False,
        "model": None,
    }


def test_status_reports_http_fallback(monkeypatch):
    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
    monkeypatch.setenv("MEDGEMMA_ENDPOINT", "http://127.0.0.1:9000")

    status = MedGemmaService().status()

    assert status["provider"] == "http"
    assert status["fireworks_configured"] is False
