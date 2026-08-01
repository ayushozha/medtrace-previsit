"""Pydantic schemas shared between the FastAPI routers and the React frontend.

Field names are snake_case throughout — the web client mirrors them verbatim in
``src/lib/types.ts``.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DocumentKind = Literal["clinical_pdf", "radiology_note", "conversation_note"]
RiskLevel = Literal["High", "Medium", "Low"]
LabStatus = Literal["High", "Normal", "Low", "Borderline"]
TrendDirection = Literal["Worsening", "Improving", "Stable"]
DocumentStatus = Literal["Processed", "Processing", "Failed"]
VerificationStatus = Literal["verified", "unverified"]


class PatientOut(BaseModel):
    """Patient directory row + chart detail header."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="Canonical Medplum Patient.id")
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
    processing_error: str | None = None


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
    id: str = Field(..., description="Canonical Medplum Communication header id")
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
    request_id: str | None = Field(default=None, min_length=8, max_length=128)


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
    verification_status: VerificationStatus | None = None
    source_document_id: str | None = None


class ConditionOut(BaseModel):
    name: str
    status: str = "Active"
    first_seen: str | None = None
    last_mentioned: str | None = None
    verification_status: VerificationStatus | None = None
    source_document_id: str | None = None


class MedicationOut(BaseModel):
    name: str
    dose: str | None = None
    frequency: str | None = None
    status: Literal["Active", "Previous"] = "Active"
    start: str | None = None
    end: str | None = None
    verification_status: VerificationStatus | None = None
    source_document_id: str | None = None


class AllergyOut(BaseModel):
    allergen: str
    reaction: str | None = None
    source: str | None = None
    verification_status: VerificationStatus | None = None
    source_document_id: str | None = None


class AbnormalFindingOut(BaseModel):
    test: str
    value: str
    status: str
    source: str | None = None
    verification_status: VerificationStatus | None = None
    source_document_id: str | None = None


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


# ---- YC Medplum hackathon demo ---------------------------------------------


class DemoProviderStatus(BaseModel):
    configured: bool
    missing: list[str] = Field(default_factory=list)


class DemoStatusOut(BaseModel):
    demo_patient_id: str | None = None
    deepgram: DemoProviderStatus
    moss: DemoProviderStatus
    openai: DemoProviderStatus
    medplum: DemoProviderStatus
    stedi: DemoProviderStatus
    workflow: DemoProviderStatus


class TranscriptUtteranceOut(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    speaker: int = Field(ge=0, le=20)
    start: float = Field(ge=0, le=60)
    end: float = Field(ge=0, le=60)
    text: str = Field(min_length=1, max_length=2_000)
    confidence: float | None = None


class RetrievalEvidenceOut(BaseModel):
    id: str
    text: str
    score: float
    source: str


class MossRetrievalOut(BaseModel):
    index_name: str
    query: str
    time_taken_ms: int | None = None
    evidence: list[RetrievalEvidenceOut] = Field(default_factory=list)
    persisted: bool = False


DemoChangeKind = Literal["medication_adherence", "allergy_confirmation", "follow_up"]
DemoText = Annotated[str, Field(min_length=1, max_length=1_000)]


class ProposedChangeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: DemoChangeKind
    title: str = Field(min_length=1, max_length=200)
    clinical_subject: str = Field(min_length=1, max_length=200)
    proposed_value: str = Field(min_length=1, max_length=1_000)
    evidence_utterance_id: str = Field(min_length=1, max_length=64)
    evidence_quote: str = Field(min_length=1, max_length=1_000)
    clinician_note: str = Field(max_length=1_000)


class PrevisitDraftOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2_000)
    proposed_changes: list[ProposedChangeOut] = Field(max_length=10)
    unresolved_questions: list[DemoText] = Field(max_length=10)
    clinician_verification: list[DemoText] = Field(max_length=10)
    recommended_visit: bool
    recommended_service: str = Field(max_length=200)


class DemoCheckinOut(BaseModel):
    checkin_id: str
    patient_id: str
    deepgram_request_id: str
    deepgram_model: str
    openai_response_id: str
    patient_speaker: int = Field(ge=0, le=20)
    checkin_token: str
    utterances: list[TranscriptUtteranceOut] = Field(max_length=80)
    moss: MossRetrievalOut
    draft: PrevisitDraftOut
    write_status: Literal["not_written"] = "not_written"


class DemoConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkin_id: str = Field(min_length=1, max_length=64)
    deepgram_request_id: str = Field(min_length=1, max_length=128)
    openai_response_id: str = Field(min_length=1, max_length=128)
    patient_speaker: int = Field(ge=0, le=20)
    checkin_token: str = Field(min_length=1, max_length=2_048)
    utterances: list[TranscriptUtteranceOut] = Field(min_length=1, max_length=80)
    source_draft: PrevisitDraftOut
    draft: PrevisitDraftOut
    approved: bool


class FhirValidationOut(BaseModel):
    resource_type: str
    valid: bool
    notices: list[str] = Field(default_factory=list)


class FhirResourceOut(BaseModel):
    resource_type: str | None = None
    resource_id: str | None = None
    version_id: str | None = None
    location: str | None = None
    status: str
    checkin_id: str | None = None


class DemoConfirmOut(BaseModel):
    checkin_id: str
    approved: bool
    validation_status: Literal["passed"]
    validations: list[FhirValidationOut]
    resources: list[FhirResourceOut]
    document_id: str


class DemoEligibilityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checkin_id: str


class EligibilityBenefitOut(BaseModel):
    code: str
    name: str
    benefit_amount: float | None = None
    benefit_percent: float | None = None
    coverage_level_code: str | None = None
    in_plan_network_indicator_code: str | None = None
    time_qualifier_code: str | None = None
    service_type_codes: list[str] = Field(default_factory=list)
    additional_information: list[Any] = Field(default_factory=list)


class DemoEligibilityOut(BaseModel):
    checkin_id: str
    transaction_id: str
    trace_id: str
    application_mode: Literal["test"]
    coverage_active: bool | None = None
    plan_status: list[dict[str, Any]] = Field(default_factory=list)
    benefits: list[EligibilityBenefitOut] = Field(default_factory=list)
    patient_responsibility_summary: str
    disclaimer: str
    medplum_resource: FhirResourceOut


class DemoReadinessOut(BaseModel):
    checkin_id: str
    patient_id: str
    approved_at: str
    clinician_name: str
    what_changed: list[ProposedChangeOut] = Field(default_factory=list)
    clinician_verification: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    utterances: list[TranscriptUtteranceOut] = Field(default_factory=list)
    resources: list[FhirResourceOut] = Field(default_factory=list)
    validation_status: Literal["passed"]
    eligibility: dict[str, Any] | None = None


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
