"""
Voice handler for real-time speech-to-speech interaction.

STT and TTS go through Deepgram (Nova + Aura). The report agent still uses an
OpenAI-compatible chat endpoint via ``agent.graph``.
"""

from __future__ import annotations

import os
from functools import cached_property

import httpx

from agent import graph

DEEPGRAM_BASE = "https://api.deepgram.com"


def _required_model(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for the Deepgram voice pipeline.")
    return value


class VoicePipeline:
    """
    Orchestrates speech transcription, agent reasoning, and text-to-speech.

    * **STT** — Deepgram ``/v1/listen`` (Nova) with neutral speaker-number diarization.
    * **Agent** — OpenAI-compatible chat via ``OPENAI_*`` (can point at Fireworks).
    * **TTS** — Deepgram ``/v1/speak`` (Aura); optional if the key is missing.
    """

    def __init__(self) -> None:
        self.deepgram_api_key = (
            os.environ.get("DEEPGRAM_API_KEY") or os.environ.get("DG_API_KEY") or None
        )

    def _require_deepgram_key(self) -> str:
        if not self.deepgram_api_key:
            raise RuntimeError(
                "DEEPGRAM_API_KEY is required for voice transcription/TTS. "
                "Get a key at https://console.deepgram.com/"
            )
        return self.deepgram_api_key

    @cached_property
    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Token {self._require_deepgram_key()}"}

    @staticmethod
    def _audio_content_type(audio_bytes: bytes) -> str:
        """Detect common browser/upload containers without relabeling them as WAV."""
        if audio_bytes.startswith(b"\x1a\x45\xdf\xa3"):
            return "audio/webm"
        if audio_bytes.startswith(b"OggS"):
            return "audio/ogg"
        if audio_bytes.startswith(b"fLaC"):
            return "audio/flac"
        if len(audio_bytes) >= 12 and audio_bytes.startswith(b"RIFF") and audio_bytes[8:12] == b"WAVE":
            return "audio/wav"
        if len(audio_bytes) >= 8 and audio_bytes[4:8] == b"ftyp":
            return "audio/mp4"
        if (
            audio_bytes.startswith(b"ID3")
            or audio_bytes.startswith(b"\xff\xfb")
            or audio_bytes.startswith(b"\xff\xf3")
            or audio_bytes.startswith(b"\xff\xf2")
        ):
            return "audio/mpeg"
        return "application/octet-stream"

    @staticmethod
    def _normalize_audio_content_type(content_type: str | None) -> str | None:
        if not content_type:
            return None
        media_type = content_type.partition(";")[0].strip().lower()
        return media_type if media_type.startswith("audio/") else None

    @staticmethod
    def _speaker_label(speaker: int | None) -> str:
        """Keep diarization neutral; a speaker index does not establish a clinical role."""
        return f"Speaker {speaker}" if speaker is not None and speaker >= 0 else "Speaker"

    @classmethod
    def _format_diarized_transcript(cls, payload: dict) -> str:
        """Build neutral ``Speaker N:`` lines from Deepgram utterances or words."""
        results = payload.get("results") or {}
        utterances = results.get("utterances") or []
        if utterances:
            lines: list[str] = []
            for utt in utterances:
                text = (utt.get("transcript") or "").strip()
                if not text:
                    continue
                raw_speaker = utt.get("speaker")
                label = cls._speaker_label(int(raw_speaker) if raw_speaker is not None else None)
                lines.append(f"{label}: {text}")
            if lines:
                return "\n".join(lines)

        # Fallback: group consecutive words by speaker.
        channels = results.get("channels") or []
        if not channels:
            return ""
        alternatives = (channels[0] or {}).get("alternatives") or []
        if not alternatives:
            return ""
        words = alternatives[0].get("words") or []
        if not words:
            return (alternatives[0].get("transcript") or "").strip()

        lines = []
        current_speaker: int | None = None
        current_words: list[str] = []
        for word in words:
            raw_speaker = word.get("speaker")
            speaker = int(raw_speaker) if raw_speaker is not None else -1
            token = (word.get("punctuated_word") or word.get("word") or "").strip()
            if not token:
                continue
            if current_speaker is None:
                current_speaker = speaker
            if speaker != current_speaker:
                if current_words:
                    label = cls._speaker_label(current_speaker)
                    lines.append(f"{label}: {' '.join(current_words)}")
                current_speaker = speaker
                current_words = [token]
            else:
                current_words.append(token)
        if current_words and current_speaker is not None:
            label = cls._speaker_label(current_speaker)
            lines.append(f"{label}: {' '.join(current_words)}")
        return "\n".join(lines)

    async def transcribe_audio(self, audio_bytes: bytes, content_type: str | None = None) -> str:
        """Transcribe audio into a neutral diarized transcript via Deepgram Nova."""
        self._require_deepgram_key()
        if not audio_bytes:
            return ""

        params = {
            "model": _required_model("DEEPGRAM_MODEL"),
            "diarize_model": _required_model("DEEPGRAM_DIARIZE_MODEL"),
            "utterances": "true",
            "smart_format": "true",
            "punctuate": "true",
        }
        headers = {
            **self._auth_headers,
            "Content-Type": self._normalize_audio_content_type(content_type)
            or self._audio_content_type(audio_bytes),
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{DEEPGRAM_BASE}/v1/listen",
                    params=params,
                    headers=headers,
                    content=audio_bytes,
                )
                response.raise_for_status()
                payload = response.json()
            transcript = self._format_diarized_transcript(payload)
            print(f"[STT] Deepgram transcription successful:\n{transcript}")
            return transcript
        except Exception as e:
            print(f"[STT] Deepgram transcription failed: {e}")
            raise

    async def generate_speech(self, text: str) -> bytes:
        """Convert text to speech audio bytes using Deepgram Aura TTS."""
        text = (text or "").strip()
        if not text:
            return b""
        if not self.deepgram_api_key:
            print("[TTS] DEEPGRAM_API_KEY not set — skipping speech synthesis.")
            return b""

        params = {
            "model": _required_model("DEEPGRAM_TTS_MODEL"),
            "encoding": "mp3",
        }
        headers = {
            **self._auth_headers,
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{DEEPGRAM_BASE}/v1/speak",
                    params=params,
                    headers=headers,
                    json={"text": text},
                )
                response.raise_for_status()
                return response.content
        except Exception as e:
            print(f"[TTS] Deepgram speech synthesis failed: {e}. Returning empty speech content.")
            return b""

    async def run_agent_pipeline(
        self,
        text: str,
        document: str = None,
        thread_id: str = "voice_session",
    ) -> dict:
        """
        Passes user input through the compiled co-editor LangGraph graph and returns the verbal response and document updates.
        """
        # Configure thread for checkpointer memory Saver
        config = {"configurable": {"thread_id": thread_id}}

        # Load current state from the graph checkpointer
        state = await graph.aget_state(config)
        messages = list(state.values.get("messages", [])) if state.values else []

        # Prioritize live document text from frontend editor
        if document is None:
            document = state.values.get("document", "") if state.values else ""

        # Append new user message
        from langchain_core.messages import HumanMessage

        messages.append(HumanMessage(content=text))

        # Execute agent graph
        result_state = await graph.ainvoke(
            {
                "messages": messages,
                "tools": [],
                "document": document,
            },
            config=config,
        )

        # Extract verbal response from the newly generated assistant messages
        verbal_response = "I have updated the document."
        new_document = result_state.get("document", document)

        # Read the content of the last assistant message
        for msg in reversed(result_state.get("messages", [])):
            is_ai = False
            content = ""
            if isinstance(msg, dict):
                is_ai = msg.get("role") == "assistant" or msg.get("type") == "ai"
                content = msg.get("content", "")
            else:
                is_ai = getattr(msg, "type", None) == "ai" or getattr(msg, "role", None) == "assistant"
                content = getattr(msg, "content", "")

            if is_ai and content:
                verbal_response = content
                break

        return {
            "verbal_response": verbal_response,
            "document": new_document,
        }
