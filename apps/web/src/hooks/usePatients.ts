import { useCallback, useEffect, useState } from 'react';
import { apiGet, apiPatch, apiPost, ApiError } from '@/lib/api';
import type { CreatePatientPayload, Patient, UpdatePatientPayload } from '@/lib/types';

export interface UsePatientsResult {
  patients: Patient[];
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
  createPatient: (payload: CreatePatientPayload) => Promise<Patient>;
  updatePatient: (patientId: string, payload: UpdatePatientPayload) => Promise<Patient>;
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

  const updatePatient = useCallback(async (patientId: string, payload: UpdatePatientPayload) => {
    const updated = await apiPatch<Patient, UpdatePatientPayload>(`/api/patients/${patientId}`, payload);
    setPatients((prev) => prev.map((patient) => (patient.id === updated.id ? updated : patient)));
    return updated;
  }, []);

  return { patients, loading, error, refresh, createPatient, updatePatient };
}
