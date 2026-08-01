import { useCallback, useEffect, useState } from 'react';
import { apiGet, apiPost, ApiError } from '@/lib/api';
import type { CreatePatientPayload, Patient } from '@/lib/types';

export interface UsePatientsResult {
  patients: Patient[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  createPatient: (payload: CreatePatientPayload) => Promise<Patient>;
}

export function usePatients(): UsePatientsResult {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await apiGet<Patient[]>('/api/patients');
      setPatients(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : (err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const createPatient = useCallback(async (payload: CreatePatientPayload) => {
    const created = await apiPost<Patient, CreatePatientPayload>('/api/patients', payload);
    setPatients((prev) => [created, ...prev.filter((p) => p.id !== created.id)]);
    return created;
  }, []);

  return { patients, loading, error, refresh, createPatient };
}
