import { useCallback, useEffect, useState } from 'react';
import { apiGet, ApiError } from '@/lib/api';
import type { ClinicalSnapshot } from '@/lib/types';

export interface UseSnapshotResult {
  snapshot: ClinicalSnapshot | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

export function useSnapshot(chartSubjectId: string | null): UseSnapshotResult {
  const [snapshot, setSnapshot] = useState<ClinicalSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!chartSubjectId) {
      setSnapshot(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<ClinicalSnapshot>(`/api/patients/${chartSubjectId}/snapshot`);
      setSnapshot(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : (err as Error).message);
    } finally {
      setLoading(false);
    }
  }, [chartSubjectId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return { snapshot, loading, error, refresh };
}
