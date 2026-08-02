import os
import asyncio
from pathlib import Path

from dotenv import load_dotenv

# Prefer repo-root .env (same as main.py), then a local backend override.
_REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_REPO_ROOT / ".env", override=True)
load_dotenv(_REPO_ROOT / ".env.local", override=True)
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

from voice_handler import VoicePipeline


async def main():
    print("--- Starting Transcription Test ---")
    print(f"DEEPGRAM_API_KEY set: {bool(os.environ.get('DEEPGRAM_API_KEY') or os.environ.get('DG_API_KEY'))}")

    # Path to the test audio file
    audio_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../turn_01_patient.mp3"))
    if not os.path.exists(audio_path):
        print(f"Error: Test audio file not found at {audio_path}")
        return

    print(f"Loading test audio file from: {audio_path}")
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    print(f"Loaded {len(audio_bytes)} bytes of audio data.")
    print("Sending to Deepgram Nova for transcription and speaker diarization...")

    pipeline = VoicePipeline()
    try:
        transcript = await pipeline.transcribe_audio(audio_bytes)
        print("\n--- TRANSCRIPTION RESULT ---")
        print(transcript)
        print("----------------------------")
    except Exception as e:
        print(f"Transcription failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
