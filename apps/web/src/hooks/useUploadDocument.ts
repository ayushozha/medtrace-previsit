import { useCallback, useState } from 'react';
import { apiUpload, ApiError } from '@/lib/api';
import type { DocumentKind, IngestResult } from '@/lib/types';

export interface UseUploadDocumentResult {
  uploading: boolean;
  error: string | null;
  upload: (
    chartSubjectId: string,
    file: File,
    opts?: { documentKind?: DocumentKind; extractMode?: string },
  ) => Promise<IngestResult>;
}

export function useUploadDocument(): UseUploadDocumentResult {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const upload = useCallback(
    async (
      chartSubjectId: string,
      file: File,
      opts?: { documentKind?: DocumentKind; extractMode?: string },
    ): Promise<IngestResult> => {
      setUploading(true);
      setError(null);
      try {
        const inferredKind: DocumentKind =
          opts?.documentKind ??
          (file.name.toLowerCase().endsWith('.pdf')
            ? 'clinical_pdf'
            : file.name.toLowerCase().includes('radiolog')
              ? 'radiology_note'
              : 'conversation_note');
        const result = await apiUpload<IngestResult>(
          `/api/patients/${chartSubjectId}/documents`,
          file,
          {
            document_kind: inferredKind,
            extract_mode: opts?.extractMode ?? 'vlm_png',
          },
        );
        return result;
      } catch (err) {
        const msg = err instanceof ApiError ? err.message : (err as Error).message;
        setError(msg);
        throw err;
      } finally {
        setUploading(false);
      }
    },
    [],
  );

  return { uploading, error, upload };
}
