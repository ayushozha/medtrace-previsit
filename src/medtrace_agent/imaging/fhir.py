"""DICOM header to FHIR R4 ImagingStudy mapping.

Pixel data is deliberately not decoded here.  The canonical ImagingStudy records every
DICOM instance while a patient-scoped Binary/DocumentReference stores a representative
source object.  The viewer's complete series remains in the local demo imaging store.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import uuid


DICOM_CODE_SYSTEM = "http://dicom.nema.org/resources/ontology/DCM"
OID_CODE_SYSTEM = "urn:ietf:rfc:3986"


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _uid(value: Any, fallback_seed: str) -> str:
    value_text = _text(value)
    if value_text:
        return value_text
    # FHIR ImagingStudy requires series/instance UIDs.  Synthetic fixtures occasionally
    # omit one, so generate a stable OID-shaped UUID value rather than dropping the file.
    return f"2.25.{uuid.uuid5(uuid.NAMESPACE_URL, fallback_seed).int}"


def _dicom_started(dataset: Any) -> str | None:
    raw_date = _text(getattr(dataset, "StudyDate", None))
    if len(raw_date) != 8 or not raw_date.isdigit():
        return None
    raw_time = "".join(ch for ch in _text(getattr(dataset, "StudyTime", None)) if ch.isdigit())
    raw_time = (raw_time + "000000")[:6]
    try:
        parsed = datetime.strptime(raw_date + raw_time, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return parsed.isoformat().replace("+00:00", "Z")


def read_dicom_headers(paths: list[Path]) -> list[tuple[Path, Any]]:
    """Read valid DICOM headers only, preserving the supplied path order."""
    try:
        import pydicom
    except ImportError as exc:
        raise RuntimeError("DICOM FHIR mapping needs pydicom: pip install -e '.[imaging]'") from exc

    headers: list[tuple[Path, Any]] = []
    for path in paths:
        try:
            headers.append((path, pydicom.dcmread(path, stop_before_pixels=True)))
        except Exception:
            continue
    return headers


def build_imaging_study(
    *,
    paths: list[Path],
    patient_id: str,
    patient_display: str,
    study_id: str,
    identifier_system: str,
    synthetic: bool = False,
    tag_system: str | None = None,
) -> tuple[dict[str, Any], Path, dict[str, Any]]:
    """Build one valid R4 ImagingStudy and return it with its representative file.

    DICOM PatientID/PatientName are treated as source metadata, never as the canonical
    identity.  ``subject`` always points at the already-resolved Medplum Patient.
    """
    headers = read_dicom_headers(paths)
    if not headers:
        raise ValueError("No valid DICOM headers were found for the imaging study.")

    first_path, first = headers[0]
    study_uid = _uid(getattr(first, "StudyInstanceUID", None), f"{study_id}:study")
    grouped: dict[str, list[tuple[Path, Any]]] = defaultdict(list)
    for path, dataset in headers:
        series_uid = _uid(
            getattr(dataset, "SeriesInstanceUID", None),
            f"{study_id}:series:{_text(getattr(dataset, 'SeriesNumber', None), path.parent.name)}",
        )
        grouped[series_uid].append((path, dataset))

    series_resources: list[dict[str, Any]] = []
    modalities: set[str] = set()
    for series_index, (series_uid, entries) in enumerate(sorted(grouped.items()), start=1):
        series_first = entries[0][1]
        modality = _text(getattr(series_first, "Modality", None), "OT").upper()
        modalities.add(modality)
        instances: list[dict[str, Any]] = []
        for instance_index, (path, dataset) in enumerate(entries, start=1):
            instance_uid = _uid(
                getattr(dataset, "SOPInstanceUID", None),
                f"{study_id}:instance:{path.name}:{instance_index}",
            )
            sop_class_uid = _uid(
                getattr(dataset, "SOPClassUID", None),
                f"{study_id}:sop-class:{modality}",
            )
            raw_number = getattr(dataset, "InstanceNumber", None)
            try:
                number = int(raw_number) if raw_number is not None else instance_index
            except (TypeError, ValueError):
                number = instance_index
            instance: dict[str, Any] = {
                "uid": instance_uid,
                "sopClass": {"system": OID_CODE_SYSTEM, "code": f"urn:oid:{sop_class_uid}"},
                "number": max(1, number),
            }
            title = _text(getattr(dataset, "ImageComments", None)) or path.name
            if title:
                instance["title"] = title[:200]
            instances.append(instance)

        raw_series_number = getattr(series_first, "SeriesNumber", None)
        try:
            series_number = int(raw_series_number) if raw_series_number is not None else series_index
        except (TypeError, ValueError):
            series_number = series_index
        series: dict[str, Any] = {
            "uid": series_uid,
            "number": max(1, series_number),
            "modality": {"system": DICOM_CODE_SYSTEM, "code": modality},
            "description": _text(getattr(series_first, "SeriesDescription", None), "DICOM series")[:200],
            "numberOfInstances": len(instances),
            "instance": instances,
        }
        body_site = _text(getattr(series_first, "BodyPartExamined", None))
        if body_site:
            series["bodySite"] = {"system": DICOM_CODE_SYSTEM, "code": body_site.upper(), "display": body_site.title()}
        series_started = _dicom_started(series_first)
        if series_started:
            series["started"] = series_started
        series_resources.append(series)

    identifier = [
        {"system": identifier_system, "value": study_id},
        {"system": "urn:dicom:uid", "value": f"urn:oid:{study_uid}"},
    ]
    accession = _text(getattr(first, "AccessionNumber", None))
    if accession:
        identifier.append({"system": f"{identifier_system}/accession", "value": accession})

    resource: dict[str, Any] = {
        "resourceType": "ImagingStudy",
        "identifier": identifier,
        "status": "available",
        "subject": {"reference": f"Patient/{patient_id}", "display": patient_display},
        "description": _text(
            getattr(first, "StudyDescription", None),
            _text(getattr(first, "SeriesDescription", None), "DICOM imaging study"),
        )[:200],
        "numberOfSeries": len(series_resources),
        "numberOfInstances": len(headers),
        "series": series_resources,
        "note": [{"text": "DICOM identity was mapped to the canonical Medplum Patient by the MedTrace backend."}],
    }
    started = _dicom_started(first)
    if started:
        resource["started"] = started
    if synthetic and tag_system:
        resource["meta"] = {
            "tag": [
                {"system": tag_system, "code": "synthetic"},
                {"system": tag_system, "code": "dicom-metadata"},
            ]
        }

    source_metadata = {
        "dicom_patient_name": _text(getattr(first, "PatientName", None)),
        "dicom_patient_id": _text(getattr(first, "PatientID", None)),
        "study_uid": study_uid,
        "modalities": sorted(modalities),
        "body_part": _text(getattr(first, "BodyPartExamined", None)),
        "series_description": _text(getattr(first, "SeriesDescription", None), "DICOM series"),
    }
    return resource, first_path, source_metadata
