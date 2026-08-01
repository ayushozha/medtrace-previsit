import base64
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# The dev script runs this from services/transcription/backend, so a bare load_dotenv()
# looks for a .env in *that* directory and silently misses the repo-root one where every
# other service reads its config. Load the repo root explicitly, then any local override.
_REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_REPO_ROOT / ".env", override=True)
load_dotenv(_REPO_ROOT / ".env.local", override=True)
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

from copilotkit import LangGraphAGUIAgent  # noqa: E402
from ag_ui_langgraph import add_langgraph_fastapi_endpoint  # noqa: E402
from agent import graph  # noqa: E402
from dashboard_agent import dashboard_graph  # noqa: E402
from voice_handler import VoicePipeline  # noqa: E402
from database import save_session, get_all_sessions, update_session_report  # noqa: E402
from datetime import datetime  # noqa: E402
import uuid  # noqa: E402

DATA_REPORTS_DIR = os.path.join(os.path.dirname(__file__), "data", "reports")

app = FastAPI(title="Predictive State Updates Agent Backend")

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Session document co-editor (unchanged)
add_langgraph_fastapi_endpoint(
    app=app,
    agent=LangGraphAGUIAgent(
        name="predictive_state_updates",
        description="Predictive State Updates LangGraph Agent",
        graph=graph,
    ),
    path="/",
)

# Patient-dashboard checklist collaboration (additive; does not alter session agent)
add_langgraph_fastapi_endpoint(
    app=app,
    agent=LangGraphAGUIAgent(
        name="dashboard_clinical",
        description="Patient dashboard checklist collaboration agent",
        graph=dashboard_graph,
    ),
    path="/dashboard",
)

class SaveSessionRequest(BaseModel):
    audio_base64: str
    duration: str

@app.get("/api/sessions")
async def get_sessions_endpoint():
    """
    Fetch all saved voice sessions from SQLite.
    """
    return get_all_sessions()

@app.post("/api/sessions")
async def save_session_endpoint(request: SaveSessionRequest):
    """
    Transcribes binary WAV audio, generates a report using LangGraph, saves to SQLite, and returns the session.
    """
    try:
        # Decode base64 audio
        audio_data = base64.b64decode(request.audio_base64.split(",")[-1])

        # Transcribe using Deepgram Nova (+ diarization)
        pipeline = VoicePipeline()
        transcript = await pipeline.transcribe_audio(audio_data)

        if not transcript or len(transcript.strip()) < 2:
            transcript = "Could not transcribe audio clearly."

        session_id = str(uuid.uuid4())

        # Generate report using co-editor LangGraph agent.
        # Each recording is its own conversation: the default shared "voice_session" thread
        # accumulates state across every upload, and a run that dies between a tool_call and
        # its tool response leaves a checkpoint the model rejects ("tool_call_ids did not have
        # response messages") — wedging every later recording until the process restarts.
        agent_result = await pipeline.run_agent_pipeline(
            text=f"Please write a structured report based on this transcribed input: {transcript}",
            document="",
            thread_id=f"session_{session_id}",
        )

        timestamp = datetime.now().isoformat()

        # Persist to SQLite
        save_session(
            session_id=session_id,
            timestamp=timestamp,
            duration=request.duration,
            transcript=transcript,
            report=agent_result["document"],
            audio_base64=request.audio_base64
        )

        return {
            "id": session_id,
            "timestamp": timestamp,
            "duration": request.duration,
            "transcript": transcript,
            "report": agent_result["document"],
            "audio_base64": request.audio_base64
        }
    except Exception as e:
        print(f"Error saving session: {e}")
        return {"error": str(e)}

class GenerateReportRequest(BaseModel):
    session_id: str
    transcript: str = ""
    current_report_text: str = ""
    regenerate: bool = Field(
        default=True,
        description="If True and transcript is non-empty, runs the agent to draft/refine before saving.",
    )


class VoiceChatRequest(BaseModel):
    text: str
    document: str | None = None
    thread_id: str | None = "voice_session"

@app.post("/api/sessions/generate-report")
async def generate_report_endpoint(request: GenerateReportRequest):
    """
    Optionally regenerates the clinical report from the transcript via LangGraph, then writes plain text
    to data/reports/ and updates the session row in SQLite when the session id exists.
    """
    try:
        report_body = request.current_report_text or ""
        did_regenerate = False

        if (
            request.regenerate
            and request.transcript.strip()
        ):
            pipeline = VoicePipeline()
            thread_id = f"export_{request.session_id}_{uuid.uuid4().hex[:12]}"
            agent_result = await pipeline.run_agent_pipeline(
                text=(
                    "Please write a structured clinical report in markdown based on this consultation "
                    "transcript. Use the current document as a starting draft when it is non-empty; "
                    "expand or correct it from the transcript as needed.\n\n---\n\n"
                    f"{request.transcript}"
                ),
                document=request.current_report_text or "",
                thread_id=thread_id,
            )
            report_body = agent_result.get("document") or report_body
            did_regenerate = True

        os.makedirs(DATA_REPORTS_DIR, exist_ok=True)
        safe_id = "".join(c for c in request.session_id if c.isalnum() or c in "-_")[:96]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_id}_{ts}.txt"
        filepath = os.path.join(DATA_REPORTS_DIR, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(report_body)

        db_ok = update_session_report(request.session_id, report_body)

        return {
            "ok": True,
            "filename": filename,
            "relative_path": f"data/reports/{filename}",
            "report": report_body,
            "database_updated": db_ok,
            "regenerated": did_regenerate,
        }
    except Exception as e:
        print(f"Error generate-report: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/voice-chat")
async def voice_chat_endpoint(request: VoiceChatRequest):
    """
    HTTP POST endpoint for text-based voice agent requests.
    """
    pipeline = VoicePipeline()
    agent_result = await pipeline.run_agent_pipeline(
        text=request.text,
        document=request.document,
        thread_id=request.thread_id
    )
    return {
        "verbal_response": agent_result["verbal_response"],
        "document": agent_result["document"]
    }

@app.websocket("/ws/voice")
async def websocket_voice_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for real-time voice-to-voice interaction.
    """
    await websocket.accept()
    print("[VoiceWS] Connection accepted.")
    pipeline = VoicePipeline()

    try:
        while True:
            # Receive raw binary audio bytes from the client
            audio_bytes = await websocket.receive_bytes()
            print(f"[VoiceWS] Received audio chunk: {len(audio_bytes)} bytes.")

            # 1. Transcribe the audio chunk
            transcribed_text = await pipeline.transcribe_audio(audio_bytes)
            print(f"[VoiceWS] Transcribed: '{transcribed_text}'")

            if not transcribed_text or len(transcribed_text.strip()) < 2:
                await websocket.send_json({"type": "silent", "message": "No voice detected."})
                continue

            # 2. Run through the LangGraph co-editor agent
            agent_result = await pipeline.run_agent_pipeline(transcribed_text)
            verbal_response = agent_result["verbal_response"]
            updated_document = agent_result["document"]
            print(f"[VoiceWS] Agent Response: '{verbal_response}'")

            # 3. Generate speech audio bytes
            speech_bytes = await pipeline.generate_speech(verbal_response)
            audio_base64 = base64.b64encode(speech_bytes).decode("utf-8")

            # 4. Send combined response to the client
            await websocket.send_json({
                "type": "response",
                "user_text": transcribed_text,
                "agent_text": verbal_response,
                "document": updated_document,
                "audio": audio_base64
            })
    except WebSocketDisconnect:
        print("[VoiceWS] Connection disconnected.")
    except Exception as e:
        print(f"[VoiceWS] Error in voice pipeline: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass

if __name__ == "__main__":
    import uvicorn
    # 8010 matches npm run dev:transcription; the old 8000 default collided with other services.
    port = int(os.environ.get("PORT", 8010))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
