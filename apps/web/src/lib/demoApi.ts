import { apiGet, apiPost, apiUpload } from '@/lib/api';

export interface ProviderStatus {
  configured: boolean;
  missing: string[];
}

export interface DemoStatus {
  demo_patient_id: string | null;
  deepgram: ProviderStatus;
  moss: ProviderStatus;
  openai: ProviderStatus;
  medplum: ProviderStatus;
  stedi: ProviderStatus;
  workflow: ProviderStatus;
}

export interface TranscriptUtterance {
  id: string;
  speaker: number;
  start: number;
  end: number;
  text: string;
  confidence: number | null;
}

export type DemoChangeKind = 'medication_adherence' | 'allergy_confirmation' | 'follow_up';

export interface ProposedChange {
  kind: DemoChangeKind;
  title: string;
  clinical_subject: string;
  proposed_value: string;
  evidence_utterance_id: string;
  evidence_quote: string;
  clinician_note: string;
}

export interface PrevisitDraft {
  summary: string;
  proposed_changes: ProposedChange[];
  unresolved_questions: string[];
  clinician_verification: string[];
  recommended_visit: boolean;
  recommended_service: string;
}

export interface RetrievalEvidence {
  id: string;
  text: string;
  score: number;
  source: string;
}

export interface ImagingEvidence {
  diagnostic_report_id: string;
  imaging_study_ids: string[];
  issued: string | null;
  summary: string;
  reviewer_id: string | null;
  reviewer_name: string | null;
  reviewed_at: string | null;
  report_version_id: string | null;
}

export interface DemoCheckin {
  checkin_id: string;
  patient_id: string;
  deepgram_request_id: string;
  deepgram_model: string;
  openai_response_id: string;
  patient_speaker: number;
  checkin_token: string;
  utterances: TranscriptUtterance[];
  moss: {
    index_name: string;
    query: string;
    time_taken_ms: number | null;
    evidence: RetrievalEvidence[];
    persisted: false;
  };
  imaging_evidence: ImagingEvidence[];
  openai_model: string;
  draft: PrevisitDraft;
  write_status: 'not_written';
}

export interface FhirResource {
  resource_type: string | null;
  resource_id: string | null;
  version_id: string | null;
  location: string | null;
  status: string;
  checkin_id?: string | null;
}

export interface DemoConfirmation {
  checkin_id: string;
  approved: true;
  validation_status: 'passed';
  validations: Array<{ resource_type: string; valid: true; notices: string[] }>;
  resources: FhirResource[];
  document_id: string;
}

export interface EligibilityBenefit {
  code: string;
  name: string;
  benefit_amount: number | null;
  benefit_percent: number | null;
  coverage_level_code: string | null;
  in_plan_network_indicator_code: string | null;
  time_qualifier_code: string | null;
  service_type_codes: string[];
  additional_information: unknown[];
}

export interface DemoEligibility {
  checkin_id: string;
  transaction_id: string;
  trace_id: string;
  application_mode: 'test';
  coverage_active: boolean | null;
  plan_status: Array<Record<string, unknown>>;
  benefits: EligibilityBenefit[];
  patient_responsibility_summary: string;
  disclaimer: string;
  medplum_resource: FhirResource;
}

export interface DemoReadiness {
  checkin_id: string;
  patient_id: string;
  approved_at: string;
  clinician_name: string;
  what_changed: ProposedChange[];
  clinician_verification: string[];
  unresolved_questions: string[];
  utterances: TranscriptUtterance[];
  resources: FhirResource[];
  validations: Array<{ resource_type: string; valid: true; notices: string[] }>;
  imaging_evidence: ImagingEvidence[];
  deepgram_request_id: string;
  openai_response_id: string;
  openai_model: string;
  document_id: string;
  validation_status: 'passed';
  eligibility: DemoEligibility | null;
}

export const getDemoStatus = (signal?: AbortSignal) =>
  apiGet<DemoStatus>('/api/demo/status', signal);

const demoHeaders = (accessToken: string) => ({ 'X-MedTrace-Demo-Token': accessToken });

export const createDemoCheckin = (
  patientId: string,
  audio: File,
  durationSeconds: number,
  patientSpeaker: number,
  accessToken: string,
) =>
  apiUpload<DemoCheckin>(`/api/demo/patients/${patientId}/checkins`, audio, {
    duration_seconds: durationSeconds.toFixed(1),
    patient_speaker: patientSpeaker.toString(),
  }, undefined, demoHeaders(accessToken));

export const confirmDemoCheckin = (
  patientId: string,
  checkin: DemoCheckin,
  draft: PrevisitDraft,
  accessToken: string,
) =>
  apiPost<DemoConfirmation>(`/api/demo/patients/${patientId}/checkins/confirm`, {
    checkin_id: checkin.checkin_id,
    deepgram_request_id: checkin.deepgram_request_id,
    openai_response_id: checkin.openai_response_id,
    patient_speaker: checkin.patient_speaker,
    checkin_token: checkin.checkin_token,
    utterances: checkin.utterances,
    source_draft: checkin.draft,
    draft,
    approved: true,
  }, undefined, demoHeaders(accessToken));

export const checkDemoEligibility = (patientId: string, checkinId: string, accessToken: string) =>
  apiPost<DemoEligibility>(`/api/demo/patients/${patientId}/eligibility`, {
    checkin_id: checkinId,
  }, undefined, demoHeaders(accessToken));

export const getDemoReadiness = (patientId: string, accessToken: string, signal?: AbortSignal) =>
  apiGet<DemoReadiness>(`/api/demo/patients/${patientId}/readiness`, signal, demoHeaders(accessToken));
