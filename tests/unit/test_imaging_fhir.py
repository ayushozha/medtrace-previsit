from __future__ import annotations

from pathlib import Path

from pydicom.data import get_testdata_file

from medtrace_agent.imaging.fhir import build_imaging_study


def test_dicom_maps_to_patient_linked_imaging_study() -> None:
    sample = Path(get_testdata_file("MR_small.dcm"))
    resource, representative, source = build_imaging_study(
        paths=[sample],
        patient_id="patient-1",
        patient_display="Synthetic Patient",
        study_id="ST-test",
        identifier_system="https://example.test/imaging-study",
        synthetic=True,
        tag_system="https://example.test/tag",
    )

    assert resource["resourceType"] == "ImagingStudy"
    assert resource["status"] == "available"
    assert resource["subject"]["reference"] == "Patient/patient-1"
    assert resource["numberOfSeries"] == 1
    assert resource["numberOfInstances"] == 1
    assert resource["series"][0]["instance"][0]["uid"]
    assert resource["series"][0]["instance"][0]["sopClass"]["code"].startswith("urn:oid:")
    assert representative == sample
    assert source["study_uid"]
