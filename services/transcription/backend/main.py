import base64
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
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
from chart_router_agent import chart_router_graph  # noqa: E402
from clinical_memory_agent import clinical_memory_graph  # noqa: E402
from dashboard_agent import dashboard_graph  # noqa: E402
from voice_handler import VoicePipeline  # noqa: E402
from database import save_session, update_session_report  # noqa: E402
from datetime import datetime  # noqa: E402
import uuid  # noqa: E402
import httpx  # noqa: E402

DATA_REPORTS_DIR = os.path.join(os.path.dirname(__file__), "data", "reports")
MAX_AUDIO_BYTES = 25 * 1024 * 1024

app = FastAPI(title="Predictive State Updates Agent Backend")

# Browser access is local by default. Deployments can provide a comma-separated allowlist.
cors_origins = [
    origin.strip()
    for origin in os.environ.get(
        "TRANSCRIPTION_CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
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

# Clinical memory (Zep/FHIR Q&A via apps/api) — specialist used by chart_router
add_langgraph_fastapi_endpoint(
    app=app,
    agent=LangGraphAGUIAgent(
        name="clinical_memory",
        description="Patient chart clinical memory Q&A (Zep + FHIR via MedTrace API)",
        graph=clinical_memory_graph,
    ),
    path="/memory",
)

# Auto-router supervisor for the patient-chart CopilotKit chat
add_langgraph_fastapi_endpoint(
    app=app,
    agent=LangGraphAGUIAgent(
        name="chart_router",
        description="Routes chart chat to collab UI updates or clinical memory",
        graph=chart_router_graph,
    ),
    path="/router",
)

class SaveSessionRequest(BaseModel):
    audio_base64: str = Field(max_length=35_000_000)
    duration: str
    patient_id: str = Field(min_length=1)

@app.get("/api/sessions")
async def get_sessions_endpoint(patient_id: str):
    """Fetch canonical voice-session history reconstructed from Medplum."""
    return await fetch_canonical_consultations(patient_id)


async def fetch_canonical_consultations(patient_id: str) -> list[dict]:
    base_url = (os.environ.get("MEDTRACE_API_BASE_URL") or "http://127.0.0.1:8001").rstrip("/")
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.get(f"{base_url}/api/patients/{patient_id}/consultations")
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(detail or f"Canonical Medplum read failed ({response.status_code})."),
        )
    payload = response.json()
    return payload if isinstance(payload, list) else []


async def persist_canonical_consultation(
    *,
    patient_id: str,
    session_id: str,
    timestamp: str,
    duration: str,
    transcript: str,
    report: str,
    audio_base64: str | None = None,
) -> dict:
    base_url = (os.environ.get("MEDTRACE_API_BASE_URL") or "http://127.0.0.1:8001").rstrip("/")
    payload = {
        "consultation_id": session_id,
        "recorded_at": timestamp,
        "duration": duration,
        "transcript": transcript,
        "report": report,
        "audio_base64": audio_base64,
    }
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{base_url}/api/patients/{patient_id}/consultations",
            json=payload,
        )
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        raise RuntimeError(str(detail or f"Canonical Medplum write failed ({response.status_code})."))
    return response.json()

@app.post("/api/sessions")
async def save_session_endpoint(request: SaveSessionRequest):
    """
    Transcribe uploaded audio, generate a report, persist it canonically to
    Medplum, then update the optional local SQLite cache.
    """
    try:
        # Preserve the browser/upload container type carried by the data URL.
        data_url_parts = request.audio_base64.split(",", 1)
        encoded_audio = data_url_parts[-1]
        declared_content_type = None
        if len(data_url_parts) == 2 and data_url_parts[0].startswith("data:"):
            declared_content_type = data_url_parts[0][5:].partition(";")[0]
        audio_data = base64.b64decode(encoded_audio)

        # Transcribe using Deepgram Nova (+ diarization)
        pipeline = VoicePipeline()
        transcript = await pipeline.transcribe_audio(audio_data, declared_content_type)

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

        # Medplum is canonical.  Do not present a SQLite-only session as saved.
        await persist_canonical_consultation(
            patient_id=request.patient_id,
            session_id=session_id,
            timestamp=timestamp,
            duration=request.duration,
            transcript=transcript,
            report=agent_result["document"],
            audio_base64=request.audio_base64,
        )

        # Derived local cache used by the prototype session list.
        try:
            save_session(
                session_id=session_id,
                timestamp=timestamp,
                duration=request.duration,
                transcript=transcript,
                report=agent_result["document"],
                audio_base64=request.audio_base64,
                patient_id=request.patient_id,
            )
        except Exception as cache_error:
            print(f"SQLite session cache write failed after canonical save: {cache_error}")

        return {
            "id": session_id,
            "timestamp": timestamp,
            "duration": request.duration,
            "transcript": transcript,
            "report": agent_result["document"],
            "audio_base64": request.audio_base64,
            "patient_id": request.patient_id,
        }
    except Exception as e:
        print(f"Error saving session: {e}")
        return {"error": str(e)}

class GenerateReportRequest(BaseModel):
    session_id: str
    patient_id: str = Field(min_length=1)
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
    Regenerate a canonical Medplum report, then write the derived local text
    export and update SQLite when a cache row exists.
    """
    try:
        sessions = await fetch_canonical_consultations(request.patient_id)
        session = next((item for item in sessions if item.get("id") == request.session_id), None)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found for this patient.")

        patient_id = session["patient_id"]
        transcript = session["transcript"] or ""
        report_body = request.current_report_text or ""
        did_regenerate = False

        if (
            request.regenerate
            and transcript.strip()
        ):
            pipeline = VoicePipeline()
            thread_id = f"export_{request.session_id}_{uuid.uuid4().hex[:12]}"
            agent_result = await pipeline.run_agent_pipeline(
                text=(
                    "Please write a structured clinical report in markdown based on this consultation "
                    "transcript. Use the current document as a starting draft when it is non-empty; "
                    "expand or correct it from the transcript as needed.\n\n---\n\n"
                    f"{transcript}"
                ),
                document=request.current_report_text or "",
                thread_id=thread_id,
            )
            report_body = agent_result.get("document") or report_body
            did_regenerate = True

        canonical = await persist_canonical_consultation(
            patient_id=patient_id,
            session_id=request.session_id,
            timestamp=session["timestamp"],
            duration=session["duration"],
            transcript=transcript,
            report=report_body,
        )
        os.makedirs(DATA_REPORTS_DIR, exist_ok=True)
        safe_id = "".join(c for c in request.session_id if c.isalnum() or c in "-_")[:96]
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_id}_{ts}.txt"
        filepath = os.path.join(DATA_REPORTS_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(report_body)
        db_ok = update_session_report(request.session_id, patient_id, report_body)

        return {
            "ok": True,
            "filename": filename,
            "relative_path": f"data/reports/{filename}",
            "report": report_body,
            "database_updated": db_ok,
            "regenerated": did_regenerate,
            "medplum_synced": True,
            "encounter_id": canonical.get("encounter_id"),
            "document_ids": canonical.get("document_ids", {}),
        }
    except HTTPException:
        raise
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
    if websocket.headers.get("origin") not in cors_origins:
        await websocket.close(code=1008, reason="Origin not allowed")
        return
    await websocket.accept()
    print("[VoiceWS] Connection accepted.")
    pipeline = VoicePipeline()

    try:
        while True:
            # Receive raw binary audio bytes from the client
            audio_bytes = await websocket.receive_bytes()
            if len(audio_bytes) > MAX_AUDIO_BYTES:
                await websocket.close(code=1009, reason="Audio frame too large")
                return
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
    uvicorn.run(
        "main:app",
        host=os.environ.get("TRANSCRIPTION_HOST", "127.0.0.1"),
        port=port,
        reload=True,
        ws_max_size=MAX_AUDIO_BYTES,
    )
