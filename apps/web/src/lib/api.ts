/**
 * Tiny typed wrapper around the FastAPI service at VITE_API_BASE_URL.
 * Centralises error shaping so hooks can render friendly messages.
 */

// Empty by default: `/api` and `/data` are proxied to the API by Vite, so the browser stays
// same-origin. Set VITE_API_BASE_URL only when the API is genuinely on another origin.
const RAW_BASE = import.meta.env.VITE_API_BASE_URL ?? '';
const API_BASE_URL = RAW_BASE.replace(/\/$/, '');

/** Backoff for an unreachable API — mostly the backend still booting behind the proxy. */
const RETRY_DELAYS_MS = [250, 750, 1500];

export class ApiError extends Error {
  status: number;
  details?: unknown;

  constructor(message: string, status: number, details?: unknown) {
    super(message);
    this.status = status;
    this.details = details;
  }
}

async function parseError(res: Response): Promise<ApiError> {
  let detail: string | undefined;
  let payload: unknown;
  try {
    payload = await res.json();
    if (payload && typeof payload === 'object' && 'detail' in payload) {
      const d = (payload as { detail?: unknown }).detail;
      detail = typeof d === 'string' ? d : JSON.stringify(d);
    }
  } catch {
    try {
      detail = await res.text();
    } catch {
      detail = undefined;
    }
  }
  return new ApiError(detail || `Request failed (${res.status})`, res.status, payload);
}

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError';
}

/**
 * Did the request die before reaching the API?
 *
 * Cross-origin, an unreachable API throws at the connection level. Behind the Vite proxy it
 * instead answers `500 text/plain` with an empty body, which has to be told apart from a
 * genuine API 500 — those carry a JSON `detail` from uvicorn.
 */
async function isUnreachable(res: Response): Promise<boolean> {
  if (res.status === 502 || res.status === 503 || res.status === 504) return true;
  if (res.status !== 500) return false;
  return (await res.clone().text()).trim().length === 0;
}

/**
 * `fetch` a GET, retrying while the API looks unreachable.
 *
 * The dashboard mounts in ~200 ms but the API needs a second or two to start listening, so
 * the first request after `npm run dev` would otherwise fail for no real reason.
 */
async function fetchWithRetry(url: string, init: RequestInit): Promise<Response> {
  for (let attempt = 0; ; attempt += 1) {
    let res: Response | undefined;
    try {
      res = await fetch(url, init);
    } catch (err) {
      if (isAbort(err)) throw err;
    }

    if (res && !(await isUnreachable(res))) return res;
    if (attempt >= RETRY_DELAYS_MS.length) {
      throw new ApiError(
        `Cannot reach the API${API_BASE_URL ? ` at ${API_BASE_URL}` : ''} — is it running?`,
        res?.status ?? 0,
      );
    }
    await new Promise((resolve) => setTimeout(resolve, RETRY_DELAYS_MS[attempt]));
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const url = path.startsWith('http') ? path : `${API_BASE_URL}${path}`;
  // Retry reads only: a write that failed at the connection level may still have landed.
  const res =
    (init.method ?? 'GET').toUpperCase() === 'GET'
      ? await fetchWithRetry(url, init)
      : await fetch(url, init);
  if (!res.ok) {
    throw await parseError(res);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export function apiGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>(path, { method: 'GET', signal });
}

export function apiPost<TResp, TBody = unknown>(
  path: string,
  body?: TBody,
  signal?: AbortSignal,
): Promise<TResp> {
  return request<TResp>(path, {
    method: 'POST',
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal,
  });
}

export async function apiUpload<TResp>(
  path: string,
  file: File,
  fields: Record<string, string> = {},
  signal?: AbortSignal,
): Promise<TResp> {
  const fd = new FormData();
  fd.append('file', file, file.name);
  for (const [k, v] of Object.entries(fields)) {
    fd.append(k, v);
  }
  const res = await fetch(`${API_BASE_URL}${path}`, {
    method: 'POST',
    body: fd,
    signal,
  });
  if (!res.ok) {
    throw await parseError(res);
  }
  return (await res.json()) as TResp;
}

/**
 * POST several files under a repeated field — the shape FastAPI's `list[UploadFile]` reads.
 *
 * Separate from `apiUpload`, which sends a single `file` plus form fields; a DICOM series
 * is many files and no extra fields.
 */
export async function uploadFiles<TResp>(
  path: string,
  files: File[],
  signal?: AbortSignal,
  field = 'files',
): Promise<TResp> {
  const fd = new FormData();
  for (const file of files) {
    fd.append(field, file, file.name);
  }
  const res = await fetch(`${API_BASE_URL}${path}`, {method: 'POST', body: fd, signal});
  if (!res.ok) {
    throw await parseError(res);
  }
  return (await res.json()) as TResp;
}

export const API_BASE = API_BASE_URL;
