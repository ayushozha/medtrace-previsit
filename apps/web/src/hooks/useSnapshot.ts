import { useCallback, useEffect, useState } from 'react';
import { apiGet, ApiError } from '@/lib/api';
import type { ClinicalSnapshot } from '@/lib/types';

export interface UseSnapshotResult {
  snapshot: ClinicalSnapshot | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

interface SnapshotState {
  patientId: string | null;
  value: ClinicalSnapshot | null;
  loading: boolean;
  error: string | null;
}

export function useSnapshot(chartSubjectId: string | null): UseSnapshotResult {
  const [state, setState] = useState<SnapshotState>({
    patientId: null,
    value: null,
    loading: false,
    error: null,
  });

  const fetchSnapshot = useCallback(async (signal?: AbortSignal) => {
    if (!chartSubjectId) {
      setState({ patientId: null, value: null, loading: false, error: null });
      return;
    }
    setState({ patientId: chartSubjectId, value: null, loading: true, error: null });
    try {
      const data = await apiGet<ClinicalSnapshot>(
        `/api/patients/${chartSubjectId}/snapshot`,
        signal,
      );
      if (!signal?.aborted) {
        setState({ patientId: chartSubjectId, value: data, loading: false, error: null });
      }
    } catch (err) {
      if (!signal?.aborted) {
        setState({
          patientId: chartSubjectId,
          value: null,
          loading: false,
          error: err instanceof ApiError ? err.message : (err as Error).message,
        });
      }
    }
  }, [chartSubjectId]);

  const refresh = useCallback(() => fetchSnapshot(), [fetchSnapshot]);

  useEffect(() => {
    const controller = new AbortController();
    void fetchSnapshot(controller.signal);
    return () => controller.abort();
  }, [fetchSnapshot]);

  // Effects run after paint. Hide state owned by the previous patient immediately during render.
  const belongsToPatient = state.patientId === chartSubjectId;

  return {
    snapshot: belongsToPatient ? state.value : null,
    loading: Boolean(chartSubjectId) && (!belongsToPatient || state.loading),
    error: belongsToPatient ? state.error : null,
    refresh,
  };
}
