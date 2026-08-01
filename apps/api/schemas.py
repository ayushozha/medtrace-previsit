"""Pydantic schemas shared between the FastAPI routers and the React frontend.

Field names are snake_case throughout — the web client mirrors them verbatim in
``src/lib/types.ts``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DocumentKind = Literal["clinical_pdf", "radiology_note", "conversation_note"]
RiskLevel = Literal["High", "Medium", "Low"]
LabStatus = Literal["High", "Normal", "Low", "Borderline"]
TrendDirection = Literal["Worsening", "Improving", "Stable"]
DocumentStatus = Literal["Processed", "Processing"]


class PatientOut(BaseModel):
    """Patient directory row + chart detail header."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="chart_subjects.id (uuid)")
    zep_user_id: str
    name: str = Field(..., description="display name")
    age: int = 0
    sex: Literal["M", "F", "O"] = "O"
    dob: str | None = None
    primary_doctor: str | None = None
    last_visit: str | None = None
    last_updated: str | None = None
    document_count: int = 0
    conditions: int = 0
    risk: RiskLevel = "Low"
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreatePatientIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    zep_user_id: str
    display_name: str
    age: int | None = None
    sex: Literal["M", "F", "O"] | None = None
    dob: str | None = None
    primary_doctor: str | None = None
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)


class DocumentOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    doc_id: str
    filename: str
    document_kind: DocumentKind
    extract_mode: str | None = None
    episode_count: int = 0
    storage_url: str | None = None
    storage_key: str | None = None
    storage_bucket: str | None = None
    uploaded_at: str
    status: DocumentStatus = "Processed"
    review_status: str = "Needs review"


class IngestResult(BaseModel):
    document: DocumentOut
    episode_ids: list[str] = Field(default_factory=list)


class ChatMessageOut(BaseModel):
    id: str
    role: Literal["user", "assistant", "system"]
    content: str
    created_at: str | None = None
    name: str | None = None


class ChatThreadOut(BaseModel):
    id: str = Field(..., description="chat_sessions.id")
    zep_thread_id: str
    title: str | None = None
    created_at: str
    updated_at: str


class CreateThreadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None


class SendMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_input: str
    deep: bool = False


class SendMessageOut(BaseModel):
    user: ChatMessageOut
    assistant: ChatMessageOut


class TimelineEvent(BaseModel):
    date: str
    events: list[str]


class LabTrendOut(BaseModel):
    test: str
    latest: str
    previous: str | None = None
    status: LabStatus = "Normal"
    trend: TrendDirection = "Stable"
    date: str | None = None
    range: str | None = None
    source: str | None = None


class ConditionOut(BaseModel):
    name: str
    status: str = "Active"
    first_seen: str | None = None
    last_mentioned: str | None = None


class MedicationOut(BaseModel):
    name: str
    dose: str | None = None
    frequency: str | None = None
    status: Literal["Active", "Previous"] = "Active"
    start: str | None = None
    end: str | None = None


class AllergyOut(BaseModel):
    allergen: str
    reaction: str | None = None
    source: str | None = None


class AbnormalFindingOut(BaseModel):
    test: str
    value: str
    status: str
    source: str | None = None


class AlertOut(BaseModel):
    message: str
    priority: RiskLevel
    type: str
    evidence: str | None = None


class InsightOut(BaseModel):
    title: str
    detail: str
    evidence: list[str] = Field(default_factory=list)
    priority: RiskLevel = "Medium"


class ClinicalSnapshotOut(BaseModel):
    """Aggregated dashboard payload used by ``DashboardHome``."""

    patient: PatientOut
    insights: list[InsightOut] = Field(default_factory=list)
    active_conditions: list[ConditionOut] = Field(default_factory=list)
    current_medications: list[MedicationOut] = Field(default_factory=list)
    allergies: list[AllergyOut] = Field(default_factory=list)
    recent_abnormal: list[AbnormalFindingOut] = Field(default_factory=list)
    risk_alerts: list[AlertOut] = Field(default_factory=list)
    lab_trends: list[LabTrendOut] = Field(default_factory=list)
    timeline: list[TimelineEvent] = Field(default_factory=list)
    documents: list[DocumentOut] = Field(default_factory=list)
    doctor_checklist: list[str] = Field(default_factory=list)


# ---- Imaging (DICOM studies, segmentation, draft reports) --------------------

ReportSource = Literal["mock", "medgemma", "qwen-vl"]


class RoiPrompt(BaseModel):
    """Region of interest as fractions of image width/height (0–1)."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)


class StudyOut(BaseModel):
    id: str
    patient_name: str = "Uploaded Study"
    patient_detail: str = "DICOM metadata pending"
    modality: str = "DICOM"
    body_part: str = "Unspecified"
    series: str = "Uploaded series"
    #: Frame count. >1 means a volume the viewer can scroll through.
    slices: int = 1
    uploaded_file_name: str
    is_dicom: bool = True
    #: 8-bit thumbnail for the study list / no-WebGL fallback.
    preview_url: str | None = None
    #: Original DICOM (first slice), loaded client-side by Cornerstone3D.
    dicom_url: str | None = None
    #: Every slice of the series, already in anatomical order. Empty for a single file.
    slice_urls: list[str] = Field(default_factory=list)
    #: True when the series carries ImagePositionPatient/Orientation/PixelSpacing on every
    #: slice — the precondition for building a volume and reslicing it (MPR).
    has_volume_geometry: bool = False


class SegmentationRequest(BaseModel):
    prompt: RoiPrompt


class SegmentationOut(BaseModel):
    id: str
    label: str
    confidence: float
    volume_ml: float
    # `mock` means no model ran — the box is the caller's own prompt echoed back.
    source: Literal["medsam2", "mock"]
    box: RoiPrompt
    overlay_url: str | None = None


class ReportRequest(BaseModel):
    modality: str
    body_part: str
    segmentations: list[dict[str, Any]] = Field(default_factory=list)


class ReportOut(BaseModel):
    summary: str
    findings: str
    impression: str
    recommendation: str
    confidence: float
    source: ReportSource
