"""Deepgram prerecorded transcription with word-backed speaker diarization."""

from __future__ import annotations

import os
from typing import Any

import httpx

from medtrace_agent.integrations.sponsor_error import SponsorIntegrationError

_DEFAULT_API_URL = "https://api.deepgram.com/v1/listen"
_DIARIZE_MODELS = {"latest", "v1", "v2"}
_MAX_AUDIO_SECONDS = 60.0
_MAX_UTTERANCES = 80
_MAX_TRANSCRIPT_CHARS = 20_000


def configuration_status() -> dict[str, object]:
    required = ("DEEPGRAM_API_KEY", "DEEPGRAM_MODEL", "DEEPGRAM_DIARIZE_MODEL")
    missing = [name for name in required if not (os.environ.get(name) or "").strip()]
    diarizer = (os.environ.get("DEEPGRAM_DIARIZE_MODEL") or "").strip().lower()
    if diarizer and diarizer not in _DIARIZE_MODELS:
        missing.append("DEEPGRAM_DIARIZE_MODEL must be latest, v1, or v2")
    return {"configured": not missing, "missing": missing}


def _required(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise SponsorIntegrationError(
            "deepgram", f"{name} is required for the real Deepgram transcription path.", status_code=503
        )
    return value


def _words_to_utterances(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group Deepgram words by speaker without guessing speaker roles."""
    groups: list[dict[str, Any]] = []
    for word in words:
        if "speaker" not in word:
            continue
        speaker = int(word.get("speaker") or 0)
        token = str(word.get("punctuated_word") or word.get("word") or "").strip()
        if not token:
            continue
        if not groups or groups[-1]["speaker"] != speaker:
            groups.append(
                {
                    "speaker": speaker,
                    "start": float(word.get("start") or 0),
                    "end": float(word.get("end") or 0),
                    "confidence": float(word.get("speaker_confidence") or word.get("confidence") or 0),
                    "tokens": [token],
                }
            )
        else:
            groups[-1]["tokens"].append(token)
            groups[-1]["end"] = float(word.get("end") or groups[-1]["end"])
    return [
        {
            "speaker": group["speaker"],
            "start": group["start"],
            "end": group["end"],
            "confidence": group["confidence"],
            "transcript": " ".join(group.pop("tokens")),
        }
        for group in groups
    ]


async def transcribe_audio(audio: bytes, *, content_type: str) -> dict[str, Any]:
    """Call Deepgram and return exact structured utterances plus request provenance."""
    api_key = _required("DEEPGRAM_API_KEY")
    model = _required("DEEPGRAM_MODEL")
    diarizer = _required("DEEPGRAM_DIARIZE_MODEL")
    if diarizer.lower() not in _DIARIZE_MODELS:
        raise SponsorIntegrationError(
            "deepgram",
            "DEEPGRAM_DIARIZE_MODEL must be latest, v1, or v2.",
            status_code=503,
        )
    params: dict[str, str] = {
        "model": model,
        "diarize_model": diarizer,
        "utterances": "true",
        "punctuate": "true",
        "smart_format": "true",
    }
    language = (os.environ.get("DEEPGRAM_LANGUAGE") or "").strip()
    if language:
        params["language"] = language
    if (os.environ.get("DEEPGRAM_MIP_OPT_OUT") or "").strip().lower() in {"1", "true", "yes", "on"}:
        params["mip_opt_out"] = "true"

    try:
        timeout = float(os.environ.get("DEEPGRAM_TIMEOUT_SECONDS") or "90")
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                _DEFAULT_API_URL,
                params=params,
                headers={"Authorization": f"Token {api_key}", "Content-Type": content_type},
                content=audio,
            )
    except (httpx.HTTPError, ValueError) as exc:
        raise SponsorIntegrationError("deepgram", f"Deepgram request failed: {exc}") from exc

    if not response.is_success:
        raise SponsorIntegrationError(
            "deepgram", f"Deepgram rejected the audio ({response.status_code})."
        )
    try:
        payload = response.json()
        results = payload.get("results") or {}
        utterances = results.get("utterances") or []
        if not utterances:
            channels = results.get("channels") or []
            alternatives = channels[0].get("alternatives") if channels else []
            words = alternatives[0].get("words") if alternatives else []
            utterances = _words_to_utterances(words or [])
    except (TypeError, ValueError, KeyError) as exc:
        raise SponsorIntegrationError("deepgram", "Deepgram returned an unreadable transcript payload.") from exc

    normalized = []
    for index, utterance in enumerate(utterances):
        if not isinstance(utterance, dict) or "speaker" not in utterance:
            continue
        text = str(utterance.get("transcript") or "").strip()
        if not text:
            continue
        normalized.append(
            {
                "id": f"dg-{index + 1}",
                "speaker": int(utterance["speaker"]),
                "start": float(utterance.get("start") or 0),
                "end": float(utterance.get("end") or 0),
                "text": text,
                "confidence": (
                    float(utterance["confidence"]) if utterance.get("confidence") is not None else None
                ),
            }
        )
    if not normalized:
        raise SponsorIntegrationError("deepgram", "Deepgram returned no diarized speech.")
    metadata = payload.get("metadata") if isinstance(payload, dict) else {}
    try:
        duration = float((metadata or {}).get("duration") or normalized[-1]["end"] or 0)
    except (TypeError, ValueError) as exc:
        raise SponsorIntegrationError("deepgram", "Deepgram returned an invalid audio duration.") from exc
    if duration > _MAX_AUDIO_SECONDS:
        raise SponsorIntegrationError(
            "deepgram",
            "The synthetic demo recording must be 60 seconds or shorter.",
            status_code=413,
        )
    if len(normalized) > _MAX_UTTERANCES or sum(len(item["text"]) for item in normalized) > _MAX_TRANSCRIPT_CHARS:
        raise SponsorIntegrationError(
            "deepgram", "Deepgram transcript exceeds the bounded demo evidence limit."
        )
    return {
        "request_id": str((metadata or {}).get("request_id") or ""),
        "model": model,
        "duration_seconds": duration,
        "utterances": normalized,
    }
