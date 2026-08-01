import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "sessions.db")

def init_db():
    """
    Initializes the SQLite database and creates the sessions table if it doesn't exist.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            timestamp TEXT,
            duration TEXT,
            transcript TEXT,
            report TEXT,
            audio_base64 TEXT,
            patient_id TEXT
        )
    """)
    columns = {row[1] for row in cursor.execute("PRAGMA table_info(sessions)").fetchall()}
    if "patient_id" not in columns:
        cursor.execute("ALTER TABLE sessions ADD COLUMN patient_id TEXT")
    conn.commit()
    conn.close()

def save_session(session_id: str, timestamp: str, duration: str, transcript: str, report: str, audio_base64: str, patient_id: str):
    """
    Saves a new recording session to the database.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO sessions (id, timestamp, duration, transcript, report, audio_base64, patient_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (session_id, timestamp, duration, transcript, report, audio_base64, patient_id))
    conn.commit()
    conn.close()

def get_all_sessions(patient_id: str | None = None):
    """
    Fetches all saved sessions from the database ordered by timestamp descending.
    """
    conn = sqlite3.connect(DB_PATH)
    # Return as dictionaries
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if patient_id:
        cursor.execute(
            "SELECT id, timestamp, duration, transcript, report, audio_base64, patient_id FROM sessions WHERE patient_id = ? ORDER BY timestamp DESC",
            (patient_id,),
        )
    else:
        cursor.execute("SELECT id, timestamp, duration, transcript, report, audio_base64, patient_id FROM sessions ORDER BY timestamp DESC")
    rows = cursor.fetchall()
    sessions = []
    for row in rows:
        sessions.append({
            "id": row["id"],
            "timestamp": row["timestamp"],
            "duration": row["duration"],
            "transcript": row["transcript"],
            "report": row["report"],
            "audio_base64": row["audio_base64"],
            "patient_id": row["patient_id"] or ""
        })
    conn.close()
    return sessions

def update_session_report(session_id: str, report: str) -> bool:
    """
    Updates the report column for an existing session. Returns True if a row was updated.
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE sessions SET report = ? WHERE id = ?",
        (report, session_id),
    )
    conn.commit()
    updated = cursor.rowcount > 0
    conn.close()
    return updated

# Auto-initialize database on import
init_db()
