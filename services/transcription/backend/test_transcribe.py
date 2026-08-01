import os
import asyncio
from dotenv import load_dotenv

# Load backend environment variables
env_path = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(env_path)

from voice_handler import VoicePipeline

async def main():
    print("--- Starting Transcription Test ---")
    print(f"GEMINI_API_KEY set: {bool(os.environ.get('GEMINI_API_KEY'))}")
    print(f"GOOGLE_API_KEY set: {bool(os.environ.get('GOOGLE_API_KEY'))}")

    # Path to the test audio file
    audio_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../turn_01_patient.mp3"))
    if not os.path.exists(audio_path):
        print(f"Error: Test audio file not found at {audio_path}")
        return

    print(f"Loading test audio file from: {audio_path}")
    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    print(f"Loaded {len(audio_bytes)} bytes of audio data.")
    print("Sending to Gemini 2.5 Flash for transcription and speaker diarization...")

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
