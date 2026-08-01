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
DEFAULT_STT_MODEL = "nova-3"
DEFAULT_TTS_MODEL = "aura-2-thalia-en"


class VoicePipeline:
    """
    Orchestrates speech transcription, agent reasoning, and text-to-speech.

    * **STT** — Deepgram ``/v1/listen`` (Nova) with diarization → Clinician/Patient lines.
    * **Agent** — OpenAI-compatible chat via ``OPENAI_*`` (can point at Fireworks).
    * **TTS** — Deepgram ``/v1/speak`` (Aura); optional if the key is missing.
    """

    def __init__(self) -> None:
        self.deepgram_api_key = (
            os.environ.get("DEEPGRAM_API_KEY") or os.environ.get("DG_API_KEY") or None
        )
        self.stt_model = os.environ.get("DEEPGRAM_STT_MODEL", DEFAULT_STT_MODEL)
        self.tts_model = os.environ.get("DEEPGRAM_TTS_MODEL", DEFAULT_TTS_MODEL)
        self.diarize_model = os.environ.get("DEEPGRAM_DIARIZE_MODEL", "latest")

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
        is_mp3 = (
            audio_bytes.startswith(b"ID3")
            or audio_bytes.startswith(b"\xff\xfb")
            or audio_bytes.startswith(b"\xff\xf3")
            or audio_bytes.startswith(b"\xff\xf2")
        )
        return "audio/mpeg" if is_mp3 else "audio/wav"

    @staticmethod
    def _speaker_label(speaker: int) -> str:
        """Map Deepgram speaker indices onto the UI's Clinician/Patient labels."""
        return "Clinician" if speaker % 2 == 0 else "Patient"

    @classmethod
    def _format_diarized_transcript(cls, payload: dict) -> str:
        """Build ``Clinician:`` / ``Patient:`` lines from Deepgram utterances or words."""
        results = payload.get("results") or {}
        utterances = results.get("utterances") or []
        if utterances:
            lines: list[str] = []
            for utt in utterances:
                text = (utt.get("transcript") or "").strip()
                if not text:
                    continue
                label = cls._speaker_label(int(utt.get("speaker") or 0))
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
            speaker = int(word.get("speaker") or 0)
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

    async def transcribe_audio(self, audio_bytes: bytes) -> str:
        """Transcribe audio into a diarized ``Clinician:`` / ``Patient:`` transcript via Deepgram Nova."""
        self._require_deepgram_key()
        if not audio_bytes:
            return ""

        params = {
            "model": self.stt_model,
            "diarize_model": self.diarize_model,
            "utterances": "true",
            "smart_format": "true",
            "punctuate": "true",
        }
        headers = {
            **self._auth_headers,
            "Content-Type": self._audio_content_type(audio_bytes),
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
            "model": self.tts_model,
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
