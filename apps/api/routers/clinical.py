"""Derived clinical views (timeline, labs, conditions, medications, alerts, insights).

All data is fetched from Zep — no SQL mirror.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, status

from apps.api.dependencies import RequireInsforgeDep
from apps.api.schemas import (
    AbnormalFindingOut,
    AlertOut,
    AllergyOut,
    ConditionOut,
    InsightOut,
    LabTrendOut,
    MedicationOut,
    TimelineEvent,
)
from medtrace_agent.insforge_api import get_chart_subject
from medtrace_agent.zep.graph import (
    list_fact_edges,
    list_recent_episodes,
    search_ontology_edges,
    search_ontology_nodes,
)

router = APIRouter(prefix="/api/patients", tags=["clinical"])


_LAB_HINT_PATTERNS = [
    r"\b(HbA1c|A1C)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*%?",
    r"\b(LDL|HDL|Cholesterol|Triglycerides)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(mg/dL)?",
    r"\b(Creatinine|BUN|Glucose)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)\s*(mg/dL)?",
    r"\b(Blood\s*pressure|BP)\s*[:=]?\s*([0-9]{2,3}/[0-9]{2,3})",
]


def _get_chart_or_404(chart_subject_id: str) -> dict[str, Any]:
    chart = get_chart_subject(chart_subject_id=chart_subject_id)
    if not chart:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found.")
    return chart


def _short_date(value: Any) -> str:
    s = str(value or "")
    if "T" in s:
        s = s.split("T", 1)[0]
    return s


def _month_label(value: Any) -> str:
    s = _short_date(value)
    if not s or len(s) < 7:
        return s or "Unknown"
    try:
        from datetime import date

        d = date.fromisoformat(s[:10])
        return d.strftime("%b %Y")
    except Exception:
        return s[:7]


# ---- internal builders (also reused by patients.snapshot) -------------------


def _timeline(zep_user_id: str) -> list[TimelineEvent]:
    if not zep_user_id:
        return []
    rows = list_recent_episodes(zep_user_id, lastn=40, truncate_chars=240)
    if not rows:
        return []
    grouped: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        date_label = _month_label(row.get("created_at"))
        text = str(row.get("content") or "").strip()
        if not text:
            continue
        snippet = text[:140] + ("…" if len(text) > 140 else "")
        if snippet not in grouped[date_label]:
            grouped[date_label].append(snippet)

    def _sort_key(label: str) -> str:
        try:
            from datetime import datetime

            return datetime.strptime(label, "%b %Y").strftime("%Y-%m")
        except Exception:
            return label

    return [
        TimelineEvent(date=label, events=events[:5])
        for label, events in sorted(grouped.items(), key=lambda kv: _sort_key(kv[0]))
    ]


def _conditions(zep_user_id: str) -> list[ConditionOut]:
    if not zep_user_id:
        return []
    rows = search_ontology_nodes(
        zep_user_id, "active conditions diagnoses", node_labels=["Condition"], limit=15
    )
    if not rows:
        return []
    out: list[ConditionOut] = []
    for row in rows:
        name = str(row.get("node_name") or "").strip()
        if not name:
            continue
        out.append(
            ConditionOut(
                name=name,
                status="Active",
                first_seen=None,
                last_mentioned=None,
            )
        )
    return out[:10]


def _medications(zep_user_id: str) -> list[MedicationOut]:
    if not zep_user_id:
        return []
    rows = search_ontology_nodes(
        zep_user_id, "current medications prescriptions", node_labels=["Medication"], limit=15
    )
    if not rows:
        return []
    out: list[MedicationOut] = []
    for row in rows:
        name = str(row.get("node_name") or "").strip()
        if not name:
            continue
        summary = str(row.get("summary") or "")
        dose = _first_match(r"([0-9]+\s*(?:mg|mcg|g|units?))", summary)
        freq = _first_match(
            r"(once daily|twice daily|three times daily|qd|bid|tid|qhs|prn)",
            summary,
            ignore_case=True,
        )
        status_label: str = "Previous" if "stopped" in summary.lower() else "Active"
        out.append(
            MedicationOut(
                name=name,
                dose=dose,
                frequency=freq,
                status=status_label,
            )
        )
    return out[:10]


def _allergies(zep_user_id: str) -> list[AllergyOut]:
    if not zep_user_id:
        return []
    rows = search_ontology_nodes(
        zep_user_id, "allergies adverse reactions", node_labels=["Allergy"], limit=10
    )
    if not rows:
        return []
    out: list[AllergyOut] = []
    for row in rows:
        name = str(row.get("node_name") or "").strip()
        if not name:
            continue
        summary = str(row.get("summary") or "")
        reaction = _first_match(r"(rash|anaphylaxis|hives|swelling|nausea|itching)", summary, ignore_case=True)
        out.append(
            AllergyOut(
                allergen=name,
                reaction=reaction or "Documented reaction",
                source="Zep memory",
            )
        )
    return out[:5]


def _recent_abnormal(zep_user_id: str) -> list[AbnormalFindingOut]:
    """Heuristic: skim recent episodes for known lab patterns and flag obvious abnormals.

    For each test we keep only the *latest* abnormal value seen, where "latest"
    means: episodes are sorted newest-first, and within an episode (which is
    typically chronological clinical text) we walk matches in reverse so the
    last documented value wins.
    """
    if not zep_user_id:
        return []
    rows = list_recent_episodes(zep_user_id, lastn=25, truncate_chars=None)
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True)
    by_test: dict[str, AbnormalFindingOut] = {}
    for row in rows:
        text = str(row.get("content") or "")
        episode_label = f"Episode {_short_date(row.get('created_at'))}"
        for pat in _LAB_HINT_PATTERNS:
            matches = list(re.finditer(pat, text, flags=re.IGNORECASE))
            for m in reversed(matches):
                test = m.group(1).strip()
                value = m.group(2).strip()
                key = test.lower()
                if key in by_test:
                    continue
                if not _looks_abnormal(test, value):
                    continue
                by_test[key] = AbnormalFindingOut(
                    test=test,
                    value=value,
                    status="elevated",
                    source=episode_label,
                )
    # Cap at 6, keeping insertion (newest-first) order.
    return list(by_test.values())[:6]


def _alerts(zep_user_id: str) -> list[AlertOut]:
    if not zep_user_id:
        return []
    rows = search_ontology_edges(
        zep_user_id,
        "risk allergy abnormal trend",
        edge_types=["HAS_ALLERGY", "HAS_CONDITION", "HAS_OBSERVATION"],
        limit=10,
    )
    if not rows:
        return []
    out: list[AlertOut] = []
    for row in rows:
        fact = str(row.get("fact") or "").strip()
        if not fact:
            continue
        edge_type = str(row.get("edge_type") or "")
        if "ALLERGY" in edge_type:
            priority: str = "High"
            kind = "allergy"
        elif "OBSERVATION" in edge_type:
            priority = "Medium"
            kind = "value_trend"
        else:
            priority = "Low"
            kind = "condition"
        out.append(
            AlertOut(
                message=fact[:160],
                priority=priority,  # type: ignore[arg-type]
                type=kind,
                evidence=str(row.get("valid_at") or "Zep edge"),
            )
        )
    return out[:6]


def _labs(zep_user_id: str) -> list[LabTrendOut]:
    """Aggregate the two most-recent values per lab test from recent episodes."""
    if not zep_user_id:
        return []
    rows = list_recent_episodes(zep_user_id, lastn=40, truncate_chars=None)
    if not rows:
        return []
    rows = sorted(rows, key=lambda r: str(r.get("created_at") or ""), reverse=True)
    series: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in rows:
        text = str(row.get("content") or "")
        date_label = _month_label(row.get("created_at"))
        for pat in _LAB_HINT_PATTERNS:
            for m in re.finditer(pat, text, flags=re.IGNORECASE):
                test = m.group(1).strip()
                value = m.group(2).strip()
                series[test.lower()].append((date_label, f"{value}"))
    out: list[LabTrendOut] = []
    for test_lower, vals in series.items():
        if not vals:
            continue
        # `vals` are gathered from episodes (newest-first) but matches inside
        # any single episode are in document order (chronological), so the last
        # appended entry is the most recent value the parser saw.
        latest_label, latest_val = vals[-1]
        previous_val = vals[-2][1] if len(vals) > 1 else None
        status_label = "High" if _looks_abnormal(test_lower, latest_val) else "Normal"
        trend = _trend(latest_val, previous_val)
        out.append(
            LabTrendOut(
                test=test_lower.upper() if test_lower in ("ldl", "hdl", "bp") else test_lower.title(),
                latest=_format_lab_value(test_lower, latest_val),
                previous=_format_lab_value(test_lower, previous_val) if previous_val else None,
                status=status_label,  # type: ignore[arg-type]
                trend=trend,  # type: ignore[arg-type]
                date=latest_label,
                range=_normal_range(test_lower),
                source="Zep recent episodes",
            )
        )
    out.sort(key=lambda x: 0 if x.status == "High" else 1)
    return out[:6]


def _insights(zep_user_id: str, meta: dict[str, Any]) -> list[InsightOut]:
    """Use cached metadata insights when present, otherwise derive a short list from alerts/labs."""
    cached = meta.get("insights")
    if isinstance(cached, list) and cached:
        out: list[InsightOut] = []
        for c in cached[:6]:
            if not isinstance(c, dict):
                continue
            try:
                out.append(InsightOut.model_validate(c))
            except Exception:
                continue
        if out:
            return out
    alerts = _alerts(zep_user_id)
    labs = _labs(zep_user_id)
    derived: list[InsightOut] = []
    for a in alerts[:2]:
        derived.append(
            InsightOut(
                title=a.type.replace("_", " ").title(),
                detail=a.message,
                evidence=[a.evidence or ""],
                priority=a.priority,
            )
        )
    for lab in labs[:2]:
        if lab.status != "High":
            continue
        derived.append(
            InsightOut(
                title=f"{lab.test} trending {lab.trend.lower()}",
                detail=(
                    f"Latest {lab.test} = {lab.latest}"
                    + (f" (previous {lab.previous})" if lab.previous else "")
                ),
                evidence=[lab.source or ""],
                priority="High" if lab.trend == "Worsening" else "Medium",
            )
        )
    return derived[:3]


# ---- formatting helpers -----------------------------------------------------


def _first_match(pattern: str, text: str, *, ignore_case: bool = False) -> str | None:
    flags = re.IGNORECASE if ignore_case else 0
    m = re.search(pattern, text, flags=flags)
    return m.group(1) if m else None


def _looks_abnormal(test: str, value: str) -> bool:
    t = test.lower()
    try:
        if "/" in value and ("bp" in t or "blood" in t):
            sys_str, dia_str = value.split("/", 1)
            return int(sys_str) > 130 or int(dia_str) > 80
        v = float(value)
    except Exception:
        return False
    if "a1c" in t:
        return v >= 6.5
    if "ldl" in t:
        return v >= 130
    if "creatinine" in t:
        return v >= 1.3
    if "glucose" in t:
        return v >= 126
    return False


def _trend(latest: str, previous: str | None) -> str:
    if not previous:
        return "Stable"
    try:
        if "/" in latest and "/" in previous:
            l_sys = int(latest.split("/", 1)[0])
            p_sys = int(previous.split("/", 1)[0])
            return "Worsening" if l_sys > p_sys else "Improving" if l_sys < p_sys else "Stable"
        a = float(latest)
        b = float(previous)
    except Exception:
        return "Stable"
    if abs(a - b) / max(b, 1.0) < 0.05:
        return "Stable"
    return "Worsening" if a > b else "Improving"


def _format_lab_value(test: str, value: str | None) -> str:
    if not value:
        return ""
    t = test.lower()
    if "a1c" in t:
        return f"{value}%"
    if "/" in value and ("bp" in t or "blood" in t):
        return value
    if any(k in t for k in ("ldl", "hdl", "cholesterol", "creatinine", "glucose")):
        return f"{value} mg/dL"
    return value


def _normal_range(test: str) -> str | None:
    t = test.lower()
    if "a1c" in t:
        return "< 5.7%"
    if "ldl" in t:
        return "< 100 mg/dL"
    if "creatinine" in t:
        return "0.7-1.3 mg/dL"
    if "glucose" in t:
        return "70-99 mg/dL"
    if "bp" in t or "blood" in t:
        return "< 130/80"
    return None


# ---- routes -----------------------------------------------------------------


@router.get(
    "/{chart_subject_id}/timeline",
    response_model=list[TimelineEvent],
    dependencies=[RequireInsforgeDep],
)
def get_timeline(chart_subject_id: str) -> list[TimelineEvent]:
    chart = _get_chart_or_404(chart_subject_id)
    return _timeline(str(chart.get("zep_user_id") or ""))


@router.get(
    "/{chart_subject_id}/labs",
    response_model=list[LabTrendOut],
    dependencies=[RequireInsforgeDep],
)
def get_labs(chart_subject_id: str) -> list[LabTrendOut]:
    chart = _get_chart_or_404(chart_subject_id)
    return _labs(str(chart.get("zep_user_id") or ""))


@router.get(
    "/{chart_subject_id}/conditions",
    response_model=list[ConditionOut],
    dependencies=[RequireInsforgeDep],
)
def get_conditions(chart_subject_id: str) -> list[ConditionOut]:
    chart = _get_chart_or_404(chart_subject_id)
    return _conditions(str(chart.get("zep_user_id") or ""))


@router.get(
    "/{chart_subject_id}/medications",
    response_model=list[MedicationOut],
    dependencies=[RequireInsforgeDep],
)
def get_medications(chart_subject_id: str) -> list[MedicationOut]:
    chart = _get_chart_or_404(chart_subject_id)
    return _medications(str(chart.get("zep_user_id") or ""))


@router.get(
    "/{chart_subject_id}/allergies",
    response_model=list[AllergyOut],
    dependencies=[RequireInsforgeDep],
)
def get_allergies(chart_subject_id: str) -> list[AllergyOut]:
    chart = _get_chart_or_404(chart_subject_id)
    return _allergies(str(chart.get("zep_user_id") or ""))


@router.get(
    "/{chart_subject_id}/abnormal",
    response_model=list[AbnormalFindingOut],
    dependencies=[RequireInsforgeDep],
)
def get_abnormal(chart_subject_id: str) -> list[AbnormalFindingOut]:
    chart = _get_chart_or_404(chart_subject_id)
    return _recent_abnormal(str(chart.get("zep_user_id") or ""))


@router.get(
    "/{chart_subject_id}/alerts",
    response_model=list[AlertOut],
    dependencies=[RequireInsforgeDep],
)
def get_alerts(chart_subject_id: str) -> list[AlertOut]:
    chart = _get_chart_or_404(chart_subject_id)
    return _alerts(str(chart.get("zep_user_id") or ""))


@router.get(
    "/{chart_subject_id}/insights",
    response_model=list[InsightOut],
    dependencies=[RequireInsforgeDep],
)
def get_insights(chart_subject_id: str) -> list[InsightOut]:
    chart = _get_chart_or_404(chart_subject_id)
    meta = chart.get("metadata") if isinstance(chart.get("metadata"), dict) else {}
    return _insights(str(chart.get("zep_user_id") or ""), meta or {})


# Suppress unused-fact-edges import (keeps the import close to usage if extended later).
__all__ = [
    "router",
    "list_fact_edges",
    "_alerts",
    "_allergies",
    "_conditions",
    "_insights",
    "_labs",
    "_medications",
    "_recent_abnormal",
    "_timeline",
]
