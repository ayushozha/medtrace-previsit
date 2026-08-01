from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


SCRIPT_PATH = Path(__file__).parents[2] / "scripts" / "finetune_medgemma_colab.py"
SPEC = importlib.util.spec_from_file_location("finetune_medgemma_colab", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_group_split_membership_is_deterministic() -> None:
    first = MODULE.stable_eval_membership("patient-123", fraction=0.2, seed=42)
    second = MODULE.stable_eval_membership("patient-123", fraction=0.2, seed=42)

    assert first is second


def test_report_schema_requires_all_keys_and_bounded_confidence() -> None:
    valid = (
        '{"summary":"s","findings":"f","impression":"i",'
        '"recommendation":"r","confidence":0.75}'
    )
    missing_key = '{"summary":"s","findings":"f","impression":"i","confidence":0.75}'
    invalid_confidence = (
        '{"summary":"s","findings":"f","impression":"i",'
        '"recommendation":"r","confidence":2}'
    )

    assert MODULE.report_schema_valid(valid)
    assert not MODULE.report_schema_valid(missing_key)
    assert not MODULE.report_schema_valid(invalid_confidence)


def test_report_json_can_be_extracted_from_fenced_output() -> None:
    text = (
        "```json\n"
        '{"summary":"s","findings":"f","impression":"i",'
        '"recommendation":"r","confidence":0.5}'
        "\n```"
    )

    parsed = MODULE.parse_json_object(text)

    assert parsed is not None
    assert parsed["confidence"] == 0.5


def test_normalized_exact_match_ignores_case_and_whitespace() -> None:
    assert MODULE.normalized_exact_match("No acute finding", "  no  ACUTE finding ")
