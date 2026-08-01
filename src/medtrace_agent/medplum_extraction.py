"""Validated clinical facts produced by document extraction.

These models are intentionally terminology-neutral.  Text supported by the
source document is preserved; codes are not guessed.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field


class ExtractedCondition(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    source_page: int | None = None


class ExtractedMedication(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    dosage_text: str | None = None
    source_page: int | None = None


class ExtractedAllergy(BaseModel):
    model_config = ConfigDict(extra="ignore")
    substance: str
    reaction: str | None = None
    source_page: int | None = None


class ExtractedObservation(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    value: str | None = None
    unit: str | None = None
    reference_range: str | None = None
    flag: str | None = None
    source_page: int | None = None


class ExtractedClinicalFacts(BaseModel):
    model_config = ConfigDict(extra="ignore")
    conditions: list[ExtractedCondition] = Field(default_factory=list)
    medications: list[ExtractedMedication] = Field(default_factory=list)
    allergies: list[ExtractedAllergy] = Field(default_factory=list)
    observations: list[ExtractedObservation] = Field(default_factory=list)


_DOSE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|mL|units?)\b(?:[^,;]{0,40})?",
    flags=re.IGNORECASE,
)


def facts_from_vlm_pages(pages: list[object]) -> ExtractedClinicalFacts:
    """Convert validated ``PageVLMExtract`` objects without inventing codes."""
    out = ExtractedClinicalFacts()
    seen: set[tuple[str, str]] = set()

    def unique(kind: str, value: str) -> bool:
        key = (kind, value.strip().casefold())
        if not key[1] or key in seen:
            return False
        seen.add(key)
        return True

    for index, page in enumerate(pages, start=1):
        page_number = getattr(page, "page_number", None) or index
        for raw in getattr(page, "diagnoses_or_impressions", []) or []:
            value = str(raw).strip()
            if unique("condition", value):
                out.conditions.append(ExtractedCondition(name=value, source_page=page_number))
        for raw in getattr(page, "medications", []) or []:
            value = str(raw).strip()
            if not unique("medication", value):
                continue
            dose = _DOSE.search(value)
            name = value[: dose.start()].strip(" ,-:") if dose else value
            out.medications.append(
                ExtractedMedication(
                    name=name or value,
                    dosage_text=value if dose else None,
                    source_page=page_number,
                )
            )
        for raw in getattr(page, "allergies", []) or []:
            value = str(raw).strip()
            if unique("allergy", value):
                out.allergies.append(ExtractedAllergy(substance=value, source_page=page_number))
        for lab in getattr(page, "labs", []) or []:
            name = str(getattr(lab, "name", None) or "").strip()
            value = str(getattr(lab, "value", None) or "").strip() or None
            identity = f"{name}|{value}|{page_number}"
            if not name or not unique("observation", identity):
                continue
            out.observations.append(
                ExtractedObservation(
                    name=name,
                    value=value,
                    unit=str(getattr(lab, "unit", None) or "").strip() or None,
                    reference_range=str(getattr(lab, "ref_range", None) or "").strip() or None,
                    flag=str(getattr(lab, "flag", None) or "").strip() or None,
                    source_page=page_number,
                )
            )
    return out


def facts_from_plain_text(text: str) -> ExtractedClinicalFacts:
    """Conservative fallback for embedded-text PDFs.

    Only explicit label/value patterns are accepted.  Narrative diagnosis and
    medication inference remains a VLM responsibility.
    """
    patterns = [
        re.compile(
            r"\b(HbA1c|A1C|LDL|HDL|Creatinine|BUN|Glucose)\s*[:=]?\s*"
            r"([0-9]+(?:\.[0-9]+)?)\s*(%|mg/dL)?",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"\b(Blood\s*pressure|BP)\s*[:=]?\s*([0-9]{2,3}/[0-9]{2,3})",
            flags=re.IGNORECASE,
        ),
    ]
    observations: list[ExtractedObservation] = []
    seen: set[tuple[str, str]] = set()
    for pattern in patterns:
        for match in pattern.finditer(text):
            name, value = match.group(1).strip(), match.group(2).strip()
            key = (name.casefold(), value)
            if key in seen:
                continue
            seen.add(key)
            unit = match.group(3) if match.lastindex and match.lastindex >= 3 else None
            observations.append(ExtractedObservation(name=name, value=value, unit=unit))
    return ExtractedClinicalFacts(observations=observations)
