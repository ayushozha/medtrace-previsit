/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Medtrace API (clinical + imaging). Defaults to http://localhost:8001. */
  readonly VITE_API_BASE_URL?: string;
  /** Transcription service, a separate process. Defaults to http://localhost:8010. */
  readonly VITE_TRANSCRIPTION_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
