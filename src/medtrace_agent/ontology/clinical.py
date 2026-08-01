"""
Clinical hackathon reference ontology (Patient ~ Zep User) and Zep Cloud projection.

Reference entities: Patient(User), Doctor, Encounter, ClinicalNote, Document, Fact,
Condition, Symptom, Medication, Allergy, labs/vitals, imaging, Insight, SuggestedTask, Evidence.

Zep limits custom types to 10 entities + 10 edges per project; Evidence is modeled as
fields on ClinicalFact; Symptom/Insight/SuggestedTask are deferred or folded into facts.
"""

from __future__ import annotations

import logging
import os

from pydantic import Field

from zep_cloud import EntityEdgeSourceTarget
from zep_cloud.external_clients.ontology import (
    EdgeModel,
    EntityBoolean,
    EntityInt,
    EntityModel,
    EntityText,
)

from medtrace_agent.zep.memory import get_zep_client

_logger = logging.getLogger(__name__)

# For graph.search filters (custom type names match EntityModel class registrations).
ONTOLOGY_NODE_LABELS: list[str] = [
    "Doctor",
    "Encounter",
    "ClinicalNote",
    "ClinicalDocument",
    "ClinicalFact",
    "Condition",
    "Medication",
    "Allergy",
    "ClinicalObservation",
    "ImagingRecord",
]

ONTOLOGY_EDGE_TYPES: list[str] = [
    "HAS_ENCOUNTER",
    "HAS_DOCUMENT",
    "HAS_CONDITION",
    "HAS_MEDICATION",
    "HAS_ALLERGY",
    "HAS_OBSERVATION",
    "HAS_IMAGING",
    "ENCOUNTER_HAS_NOTE",
    "CONTAINS_FACT",
    "FACT_APPROVED_BY",
]


class Doctor(EntityModel):
    """A licensed clinician mentioned or responsible for documentation (not the Zep User patient)."""

    specialty_text: EntityText = Field(
        default=None,
        description="Clinical specialty if stated (e.g. Cardiology). Leave empty if unknown.",
    )
    credential_text: EntityText = Field(
        default=None,
        description="Degrees or credentials when explicitly written (e.g. MD, DO). Do not invent.",
    )
    department_text: EntityText = Field(
        default=None,
        description="Hospital department or service line if documented.",
    )


class Encounter(EntityModel):
    """A single healthcare encounter: visit, admission, ED stay, or telehealth session."""

    encounter_kind_text: EntityText = Field(
        default=None,
        description="One of outpatient, inpatient, ED, observation, telehealth, unknown — only if supported by text.",
    )
    encounter_date_text: EntityText = Field(
        default=None,
        description="Documented encounter date or approximate timeframe as written; never fabricate precision.",
    )
    facility_text: EntityText = Field(
        default=None,
        description="Facility or clinic name if stated.",
    )
    chief_complaint_text: EntityText = Field(
        default=None,
        description="Reason for visit or presenting complaint when explicitly documented.",
    )


class ClinicalNote(EntityModel):
    """A narrative clinical note tied to care (progress note, H&P, discharge summary excerpt)."""

    note_type_text: EntityText = Field(
        default=None,
        description="Note category when identifiable (H&P, discharge summary, consult). Otherwise unknown.",
    )
    authored_date_text: EntityText = Field(
        default=None,
        description="Authored date if explicitly present in the note header.",
    )
    note_excerpt_text: EntityText = Field(
        default=None,
        description="Short excerpt capturing the note gist; avoid copying entire documents.",
    )


class ClinicalDocument(EntityModel):
    """An uploaded or referenced clinical document artifact (e.g. prior PDF medical history)."""

    doc_title_text: EntityText = Field(
        default=None,
        description="Human title of the document if given (e.g. Discharge Summary 2024-01-02).",
    )
    doc_kind_text: EntityText = Field(
        default=None,
        description="High-level kind: discharge_summary, referral, imaging_report, lab_pdf, other.",
    )
    source_uri_text: EntityText = Field(
        default=None,
        description="Filename or external reference string when provided by ingest metadata.",
    )


class ClinicalFact(EntityModel):
    """One atomic, verifiable clinical assertion extracted from text; must support provenance without a separate Evidence node."""

    assertion_text: EntityText = Field(
        default=None,
        description="Single concise clinical statement (who/when/what). Split compound statements into separate facts.",
    )
    certainty_text: EntityText = Field(
        default=None,
        description="Documented certainty: affirmed_by_patient, documented_in_record, inferred, unknown.",
    )
    negation_flag: EntityBoolean = Field(
        default=None,
        description="True if the assertion is negated (e.g. denies chest pain). False if affirmed; null if unclear.",
    )
    clinical_date_text: EntityText = Field(
        default=None,
        description="Clinical time anchor from source text (onset, documented date). Do not invent dates.",
    )
    evidence_quote: EntityText = Field(
        default=None,
        description="Short verbatim quote from source supporting this assertion (EvidenceSpan surrogate).",
    )
    source_document_label: EntityText = Field(
        default=None,
        description="Which document or chunk this came from (filename or ingest label).",
    )
    chunk_index: EntityInt = Field(
        default=None,
        description="Zero-based chunk index from PDF ingestion when available.",
    )
    page_hint_text: EntityText = Field(
        default=None,
        description="Page number hint only when explicitly stated in surrounding text.",
    )


class Condition(EntityModel):
    """A diagnosis, problem, or chronic condition attributed to the patient."""

    condition_label_text: EntityText = Field(
        default=None,
        description="Canonical disease or problem label as written (e.g. Type 2 diabetes mellitus).",
    )
    clinical_status_text: EntityText = Field(
        default=None,
        description="active, resolved, remission, ruled_out, unknown — only if supported.",
    )
    onset_text: EntityText = Field(
        default=None,
        description="Onset timing or duration phrase copied from chart when present.",
    )


class Medication(EntityModel):
    """A medication or vaccine relevant to the patient."""

    drug_name_text: EntityText = Field(
        default=None,
        description="Drug name as documented (generic or brand). Do not normalize unless chart does.",
    )
    dose_sig_text: EntityText = Field(
        default=None,
        description="Dose and frequency signature when documented (e.g. metformin 500 mg BID).",
    )
    route_text: EntityText = Field(
        default=None,
        description="Route of administration if stated (PO, IV, IM).",
    )


class Allergy(EntityModel):
    """A patient allergy or adverse reaction."""

    allergen_text: EntityText = Field(
        default=None,
        description="Substance or drug class provoking allergy.",
    )
    reaction_text: EntityText = Field(
        default=None,
        description="Reaction description when documented (rash, anaphylaxis).",
    )
    severity_text: EntityText = Field(
        default=None,
        description="Severity wording when documented; otherwise unknown.",
    )


class ClinicalObservation(EntityModel):
    """A laboratory result or vital sign measurement."""

    observation_kind_text: EntityText = Field(
        default=None,
        description="lab or vital — discriminates labs vs vitals within one type.",
    )
    analyte_text: EntityText = Field(
        default=None,
        description="Lab test name or vital name (e.g. hemoglobin, BP systolic).",
    )
    value_text: EntityText = Field(
        default=None,
        description="Numeric or categorical value as written.",
    )
    unit_text: EntityText = Field(
        default=None,
        description="Units when stated.",
    )


class ImagingRecord(EntityModel):
    """Imaging study, radiology report, or key imaging finding rolled into one node for Zep MVP."""

    modality_text: EntityText = Field(
        default=None,
        description="CT, MRI, XR, US, etc., when documented.",
    )
    body_site_text: EntityText = Field(
        default=None,
        description="Anatomic region when documented.",
    )
    impression_text: EntityText = Field(
        default=None,
        description="Impression or conclusion phrase when stated.",
    )
    technique_text: EntityText = Field(
        default=None,
        description="Technique or protocol hints when documented.",
    )


class HasEncounter(EdgeModel):
    """Links the Zep User patient to an Encounter."""

    panel_role_text: EntityText = Field(
        default=None,
        description="Optional role of patient in encounter if documented (attending, admitted).",
    )


class HasDocument(EdgeModel):
    """Links the patient User to a ClinicalDocument artifact."""

    ingestion_note_text: EntityText = Field(
        default=None,
        description="Optional note such as uploaded_pdf when sourced from ingest.",
    )


class HasCondition(EdgeModel):
    """Patient User has or had a Condition."""

    rank_text: EntityText = Field(
        default=None,
        description="Problem list rank or chronic vs acute if documented.",
    )


class HasMedication(EdgeModel):
    """Patient User medication relationship; captures regimen timing intent."""

    therapy_status_text: EntityText = Field(
        default=None,
        description="active, completed, held, discontinued_as_documented, unknown.",
    )
    valid_from_text: EntityText = Field(
        default=None,
        description="Start or prescription date phrase from chart when present.",
    )
    valid_to_text: EntityText = Field(
        default=None,
        description="End or DC date phrase when present.",
    )


class HasAllergy(EdgeModel):
    """Patient User allergy linkage."""

    verified_flag: EntityBoolean = Field(
        default=None,
        description="True if chart explicitly verifies allergy; False if unverified; null unknown.",
    )


class HasObservation(EdgeModel):
    """Patient User has a lab or vital observation."""

    collected_at_text: EntityText = Field(
        default=None,
        description="Collection or measurement time phrase when documented.",
    )


class HasImaging(EdgeModel):
    """Patient User linked to an imaging record."""

    study_date_text: EntityText = Field(
        default=None,
        description="Study date phrase when documented.",
    )


class EncounterHasNote(EdgeModel):
    """Encounter contains or references a ClinicalNote."""

    note_sequence_text: EntityText = Field(
        default=None,
        description="Day-of-admission note numbering if documented.",
    )


class ContainsFact(EdgeModel):
    """Document or note contains or yields an extracted ClinicalFact."""

    extraction_round_text: EntityText = Field(
        default=None,
        description="Optional ingest batch or pass identifier for debugging.",
    )


class FactApprovedBy(EdgeModel):
    """ClinicalFact reviewed or attested by a Doctor when documentation supports it."""

    approval_phrase_text: EntityText = Field(
        default=None,
        description="Short phrase from chart indicating sign-off if present.",
    )


_CLINICAL_ENTITIES: dict[str, type[EntityModel]] = {
    "Doctor": Doctor,
    "Encounter": Encounter,
    "ClinicalNote": ClinicalNote,
    "ClinicalDocument": ClinicalDocument,
    "ClinicalFact": ClinicalFact,
    "Condition": Condition,
    "Medication": Medication,
    "Allergy": Allergy,
    "ClinicalObservation": ClinicalObservation,
    "ImagingRecord": ImagingRecord,
}

_CLINICAL_EDGES: dict[str, tuple[type[EdgeModel], list[EntityEdgeSourceTarget]]] = {
    "HAS_ENCOUNTER": (
        HasEncounter,
        [EntityEdgeSourceTarget(source="User", target="Encounter")],
    ),
    "HAS_DOCUMENT": (
        HasDocument,
        [EntityEdgeSourceTarget(source="User", target="ClinicalDocument")],
    ),
    "HAS_CONDITION": (
        HasCondition,
        [EntityEdgeSourceTarget(source="User", target="Condition")],
    ),
    "HAS_MEDICATION": (
        HasMedication,
        [EntityEdgeSourceTarget(source="User", target="Medication")],
    ),
    "HAS_ALLERGY": (
        HasAllergy,
        [EntityEdgeSourceTarget(source="User", target="Allergy")],
    ),
    "HAS_OBSERVATION": (
        HasObservation,
        [EntityEdgeSourceTarget(source="User", target="ClinicalObservation")],
    ),
    "HAS_IMAGING": (
        HasImaging,
        [EntityEdgeSourceTarget(source="User", target="ImagingRecord")],
    ),
    "ENCOUNTER_HAS_NOTE": (
        EncounterHasNote,
        [EntityEdgeSourceTarget(source="Encounter", target="ClinicalNote")],
    ),
    "CONTAINS_FACT": (
        ContainsFact,
        [
            EntityEdgeSourceTarget(source="ClinicalDocument", target="ClinicalFact"),
            EntityEdgeSourceTarget(source="ClinicalNote", target="ClinicalFact"),
        ],
    ),
    "FACT_APPROVED_BY": (
        FactApprovedBy,
        [EntityEdgeSourceTarget(source="ClinicalFact", target="Doctor")],
    ),
}


def apply_clinical_ontology(
    user_id: str | None = None,
    *,
    scope_to_user: bool = False,
) -> None:
    """
    Register the clinical projection in Zep.

    By default this sets ontology **project-wide** (no ``user_ids``), which matches Zep's
    docs examples and makes custom types show up in the dashboard under project / user
    ontology views. If you pass ``scope_to_user=True`` and a ``user_id``, only that
    user's scope is targeted — the dashboard may still look empty for "project-wide"
    even though extraction uses the schema.
    """
    client = get_zep_client()
    kwargs: dict = {
        "entities": _CLINICAL_ENTITIES,
        "edges": _CLINICAL_EDGES,
    }
    if scope_to_user and user_id:
        kwargs["user_ids"] = [user_id]
    client.graph.set_ontology(**kwargs)


def clinical_ontology_auto_apply_enabled() -> bool:
    """True unless ``AUTO_APPLY_ZEP_ONTOLOGY`` is explicitly switched off."""
    flag = (os.environ.get("AUTO_APPLY_ZEP_ONTOLOGY") or "true").strip().lower()
    return flag not in ("0", "false", "no", "off")


def auto_apply_clinical_ontology(logger: logging.Logger | None = None) -> bool:
    """Register the ontology at startup; log and swallow failures instead of raising.

    The clinical read paths in ``apps/api`` search Zep by the node labels and edge types
    defined here (``Condition``, ``Medication``, ``Allergy``, …). If the ontology is never
    registered those searches return nothing and the dashboard silently renders empty, so
    this runs on every boot. ``graph.set_ontology`` is project-wide and idempotent.

    Returns True when the ontology was applied.
    """
    log = logger or _logger
    if not clinical_ontology_auto_apply_enabled():
        log.info("AUTO_APPLY_ZEP_ONTOLOGY is off; skipping clinical ontology registration.")
        return False
    if not (os.environ.get("ZEP_API_KEY") or "").strip():
        log.warning(
            "ZEP_API_KEY is not set; skipping clinical ontology registration. "
            "Clinical endpoints will return empty results until Zep is configured."
        )
        return False
    try:
        apply_clinical_ontology()
    except Exception as exc:  # noqa: BLE001 — startup must not fail on a Zep hiccup
        log.warning("Clinical ontology registration failed: %s", exc)
        return False
    log.info("Clinical ontology registered (project-wide).")
    return True
