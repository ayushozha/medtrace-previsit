"""
Voice handler for real-time speech-to-speech interaction.
"""

import os
from functools import cached_property

from openai import OpenAI

from agent import graph


class VoicePipeline:
    """
    Orchestrates real-time speech transcription, agent reasoning, and text-to-speech generation.

    Each capability needs a different credential, so the clients are built lazily:
    transcription uses Gemini, the agent uses an OpenAI-compatible chat endpoint, and only
    text-to-speech requires a real OpenAI key. Constructing the OpenAI clients eagerly used
    to raise "Missing credentials" for every request — including Gemini-only transcription,
    which needs no OpenAI key at all.
    """

    def __init__(self):
        self.api_key = os.environ.get("OPENAI_API_KEY") or None
        self.base_url = os.environ.get("OPENAI_BASE_URL") or None

    def _require_openai_key(self) -> str:
        if not self.api_key:
            raise RuntimeError(
                "Text-to-speech needs OPENAI_API_KEY in the repo .env "
                "(transcription uses GEMINI_API_KEY; the agent can use any "
                "OpenAI-compatible endpoint via OPENAI_BASE_URL)."
            )
        return self.api_key

    @cached_property
    def client(self) -> OpenAI:
        """Configured endpoint — honours OPENAI_BASE_URL for OpenAI-compatible providers."""
        return OpenAI(api_key=self._require_openai_key(), base_url=self.base_url)

    @cached_property
    def fallback_client(self) -> OpenAI:
        """Audio endpoints are OpenAI-only, so fall back to api.openai.com."""
        return OpenAI(api_key=self._require_openai_key(), base_url="https://api.openai.com/v1")

    DIARIZE_INSTRUCTION = (
        "You are an expert medical transcriptionist. Transcribe the provided audio between a "
        "clinician and a patient. Segment the conversation accurately into alternating speaker "
        "turns, labelling each line clearly as either 'Clinician: [speech]' or "
        "'Patient: [speech]' based on who is speaking (speaker diarization). Do not add any "
        "other commentary, introductions, or summaries. Return only the diarized transcription."
    )

    async def transcribe_audio(self, audio_bytes: bytes) -> str:
        """Transcribe audio into a diarized ``Clinician:`` / ``Patient:`` transcript.

        Two providers, whichever is configured:

        * **Gemini** (``GEMINI_API_KEY``) — one call does transcription *and* diarization.
        * **OpenAI** (``OPENAI_API_KEY``) — Whisper transcribes, then the chat model labels
          the speaker turns, since Whisper does not diarize.
        """
        gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if gemini_key:
            return await self._transcribe_gemini(audio_bytes, gemini_key)
        if self.api_key:
            return await self._transcribe_openai(audio_bytes)
        raise ValueError(
            "No transcription provider configured. Set GEMINI_API_KEY (Gemini) or "
            "OPENAI_API_KEY (Whisper) in the repo .env."
        )

    async def _transcribe_gemini(self, audio_bytes: bytes, gemini_key: str) -> str:
        import base64
        import httpx

        is_mp3 = (
            audio_bytes.startswith(b"ID3")
            or audio_bytes.startswith(b"\xff\xfb")
            or audio_bytes.startswith(b"\xff\xf3")
            or audio_bytes.startswith(b"\xff\xf2")
        )
        mime_type = "audio/mp3" if is_mp3 else "audio/wav"

        try:
            base64_data = base64.b64encode(audio_bytes).decode("utf-8")
            url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"gemini-2.5-flash:generateContent?key={gemini_key}"
            )
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"inlineData": {"mimeType": mime_type, "data": base64_data}},
                            {"text": self.DIARIZE_INSTRUCTION},
                        ]
                    }
                ]
            }
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=60.0)
                response.raise_for_status()
                transcript = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                print(f"[STT] Gemini transcription successful:\n{transcript}")
                return transcript
        except Exception as e:
            print(f"[STT] Gemini transcription failed: {e}")
            raise

    async def _transcribe_openai(self, audio_bytes: bytes) -> str:
        """Whisper for the words, then the chat model for the speaker labels."""
        import asyncio

        is_mp3 = (
            audio_bytes.startswith(b"ID3")
            or audio_bytes.startswith(b"\xff\xfb")
            or audio_bytes.startswith(b"\xff\xf3")
            or audio_bytes.startswith(b"\xff\xf2")
        )
        filename = "audio.mp3" if is_mp3 else "audio.wav"
        stt_model = os.environ.get("OPENAI_TRANSCRIBE_MODEL", "whisper-1")
        chat_model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

        def _run() -> str:
            # Audio endpoints live on OpenAI proper, so use the direct client.
            raw = self.fallback_client.audio.transcriptions.create(
                model=stt_model,
                file=(filename, audio_bytes),
                response_format="text",
            )
            text = (raw if isinstance(raw, str) else getattr(raw, "text", "")).strip()
            if not text:
                return ""

            labelled = self.fallback_client.chat.completions.create(
                model=chat_model,
                temperature=0,
                messages=[
                    {"role": "system", "content": self.DIARIZE_INSTRUCTION},
                    {
                        "role": "user",
                        "content": (
                            "Label the speaker turns in this consultation transcript. "
                            "Return only the labelled lines.\n\n" + text
                        ),
                    },
                ],
            )
            return (labelled.choices[0].message.content or text).strip()

        try:
            transcript = await asyncio.to_thread(_run)
            print(f"[STT] Whisper transcription successful:\n{transcript}")
            return transcript
        except Exception as e:
            print(f"[STT] Whisper transcription failed: {e}")
            raise

    async def generate_speech(self, text: str) -> bytes:
        """
        Converts text response to speech audio bytes using OpenAI TTS.
        """
        if not self.api_key:
            # Speech output is optional; the transcript and report still work without it.
            print("[TTS] OPENAI_API_KEY not set — skipping speech synthesis.")
            return b""
        try:
            response = self.client.audio.speech.create(
                model="tts-1",
                voice="alloy",
                input=text,
                response_format="mp3"
            )
            return response.content
        except Exception as e:
            print(f"[TTS] Custom endpoint failed, trying fallback: {e}")
            try:
                response = self.fallback_client.audio.speech.create(
                    model="tts-1",
                    voice="alloy",
                    input=text,
                    response_format="mp3"
                )
                return response.content
            except Exception as fallback_err:
                print(f"[TTS] Fallback failed too: {fallback_err}. Returning empty speech content.")
                return b""

    async def run_agent_pipeline(self, text: str, document: str = None, thread_id: str = "voice_session") -> dict:
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
        result_state = await graph.ainvoke({
            "messages": messages,
            "tools": [],
            "document": document
        }, config=config)

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
            "document": new_document
        }
