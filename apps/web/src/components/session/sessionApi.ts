/**
 * Transcription-service client.
 *
 * This service is deliberately separate from the main API: it speaks the LangGraph AG-UI
 * protocol behind the CopilotKit runtime and has its own port. `npm run dev:transcription`
 * starts it on 8010 — the old frontend hard-coded 8000, which is the main API.
 */

const RAW_BASE = import.meta.env.VITE_TRANSCRIPTION_API_URL ?? 'http://localhost:8010';
export const TRANSCRIPTION_API_BASE = RAW_BASE.replace(/\/$/, '');

export interface SessionRecord {
  id: string;
  timestamp: string;
  duration: string;
  transcript: string;
  report: string;
  audio_base64: string;
}

async function detailOf(res: Response, fallback: string): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: unknown; error?: unknown };
    const raw = data.detail ?? data.error;
    if (typeof raw === 'string') return raw;
  } catch {
    // Non-JSON body — fall through to the generic message.
  }
  return fallback;
}

export async function listSessions(signal?: AbortSignal): Promise<SessionRecord[]> {
  const res = await fetch(`${TRANSCRIPTION_API_BASE}/api/sessions`, { signal });
  if (!res.ok) throw new Error(await detailOf(res, `Failed to load sessions (${res.status})`));
  const data = await res.json();
  return Array.isArray(data) ? (data as SessionRecord[]) : [];
}

export async function createSession(
  audioBase64: string,
  duration: string,
): Promise<SessionRecord> {
  const res = await fetch(`${TRANSCRIPTION_API_BASE}/api/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ audio_base64: audioBase64, duration }),
  });
  if (!res.ok) throw new Error(await detailOf(res, `Transcription failed (${res.status})`));
  const session = (await res.json()) as SessionRecord & { error?: string };
  if (session.error) throw new Error(session.error);
  return session;
}

export interface GenerateReportResponse {
  report?: string;
  database_updated?: boolean;
  filename?: string;
  regenerated?: boolean;
}

export async function generateReport(body: {
  session_id: string;
  transcript: string;
  current_report_text: string;
  regenerate: boolean;
}): Promise<GenerateReportResponse> {
  const res = await fetch(`${TRANSCRIPTION_API_BASE}/api/sessions/generate-report`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await detailOf(res, 'Failed to generate report'));
  return (await res.json()) as GenerateReportResponse;
}
