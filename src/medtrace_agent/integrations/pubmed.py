"""NCBI E-utilities (PubMed) search helper — stdlib only."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
ESUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"


def _common_params() -> dict[str, str]:
    params: dict[str, str] = {
        "tool": "medtrace_clinical_demo",
        "email": os.environ.get("NCBI_EMAIL", "demo@example.local"),
    }
    key = os.environ.get("NCBI_API_KEY", "").strip()
    if key:
        params["api_key"] = key
    return params


def _get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    merged = {**_common_params(), **params}
    q = urllib.parse.urlencode(merged)
    req = urllib.request.Request(f"{url}?{q}", headers={"User-Agent": "medtrace-agent/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw)


def pubmed_search_summaries(query: str, *, max_results: int = 8) -> str:
    """
    Search PubMed and return a compact markdown summary (titles, PMID, source, year).

    Demo-grade only — not medical advice.
    """
    q = (query or "").strip()
    if not q:
        return "Empty PubMed query."

    max_results = max(1, min(max_results, 20))

    try:
        es = _get_json(
            ESEARCH,
            {
                "db": "pubmed",
                "term": q,
                "retmax": str(max_results),
                "retmode": "json",
                "sort": "relevance",
            },
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        return f"PubMed esearch failed: {e}"

    idlist = (es.get("esearchresult") or {}).get("idlist") or []
    if not idlist:
        return f"No PubMed articles found for query: {q!r}"

    try:
        sm = _get_json(
            ESUMMARY,
            {
                "db": "pubmed",
                "id": ",".join(idlist),
                "retmode": "json",
            },
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        return f"PubMed esummary failed: {e}"

    result = sm.get("result") or {}
    lines: list[str] = [
        f"PubMed search ({len(idlist)} hits shown, query={q!r}):",
        "",
    ]

    for pmid in idlist:
        rec = result.get(pmid)
        if not isinstance(rec, dict):
            continue
        title = (rec.get("title") or "").strip() or "(no title)"
        src = (rec.get("source") or "").strip()
        pubdate = (rec.get("pubdate") or rec.get("epubdate") or "").strip()
        authors = rec.get("authors")
        auth_s = ""
        if isinstance(authors, list) and authors:
            names = []
            for a in authors[:3]:
                if isinstance(a, dict) and a.get("name"):
                    names.append(str(a["name"]))
            auth_s = "; ".join(names)
            if len(authors) > 3:
                auth_s += "; et al."
        lines.append(f"- **PMID {pmid}** — {title}")
        meta_parts = [p for p in (pubdate, src, auth_s) if p]
        if meta_parts:
            lines.append(f"  - {' · '.join(meta_parts)}")
        lines.append("")

    lines.append("_Literature only — verify primary sources; demo not peer-reviewed._")
    return "\n".join(lines).strip()
