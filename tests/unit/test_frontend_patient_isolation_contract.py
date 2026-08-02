from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_patient_isolation_and_provider_failure_contracts() -> None:
    app = _source("apps/web/src/App.tsx")
    assert "<ImagingWorkspace key={patientId} patientId={patientId}" in app
    assert "<SessionWorkspace key={patientId} patientId={patientId}" in app
    hook = _source("apps/web/src/hooks/useSnapshot.ts")
    assert "state.patientId === chartSubjectId" in hook
    assert "controller.abort()" in hook
    assert "snapshot: belongsToPatient ? state.value : null" in hook
    workspace = _source("apps/web/src/components/imaging/ImagingWorkspace.tsx")
    assert "Prompted ROI (offline)" not in workspace
    assert "AI draft based on" not in workspace
    assert "Segmentation failed:" in workspace
    assert "Report generation failed:" in workspace
    transcription = _source("services/transcription/backend/main.py")
    compose = _source("infra/medplum/docker-compose.yml")
    assert 'os.environ.get("TRANSCRIPTION_HOST", "127.0.0.1")' in transcription
    assert "audio_base64: str = Field(max_length=35_000_000)" in transcription
    assert "MEDPLUM_MAX_JSON_SIZE: 40mb" in compose
