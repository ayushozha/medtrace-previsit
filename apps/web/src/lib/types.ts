// Types mirror apps/api/schemas.py.

export type DocumentKind = 'clinical_pdf' | 'radiology_note' | 'conversation_note' | 'dicom';
export type RiskLevel = 'High' | 'Medium' | 'Low';
export type LabStatus = 'High' | 'Normal' | 'Low' | 'Borderline';
export type TrendDirection = 'Worsening' | 'Improving' | 'Stable';
export type DocumentStatus = 'Processed' | 'Processing' | 'Failed';
export type VerificationStatus = 'verified' | 'unverified';

export interface Patient {
  id: string;
  zep_user_id: string;
  name: string;
  age: number;
  sex: 'M' | 'F' | 'O';
  dob: string | null;
  primary_doctor: string | null;
  last_visit: string | null;
  last_updated: string | null;
  document_count: number;
  conditions: number;
  risk: RiskLevel;
  summary: string | null;
  metadata: Record<string, unknown>;
}

export interface CreatePatientPayload {
  zep_user_id: string;
  display_name: string;
  age?: number;
  sex?: 'M' | 'F' | 'O';
  dob?: string;
  primary_doctor?: string;
  notes?: string;
  tags?: string[];
}

export interface UpdatePatientPayload {
  display_name?: string;
  dob?: string | null;
  sex?: 'M' | 'F' | 'O';
  primary_doctor?: string | null;
}

export interface DocumentRecord {
  doc_id: string;
  filename: string;
  document_kind: DocumentKind;
  extract_mode: string | null;
  episode_count: number;
  storage_url: string | null;
  storage_key: string | null;
  storage_bucket: string | null;
  uploaded_at: string;
  status: DocumentStatus;
  review_status: string;
  processing_error?: string | null;
}

export interface IngestResult {
  document: DocumentRecord;
  episode_ids: string[];
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  created_at: string | null;
  name: string | null;
}

export interface ChatThread {
  id: string;
  zep_thread_id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface SendMessageResult {
  user: ChatMessage;
  assistant: ChatMessage;
}

export interface TimelinePeriod {
  date: string;
  events: string[];
}

export interface LabTrend {
  test: string;
  latest: string;
  previous: string | null;
  status: LabStatus;
  trend: TrendDirection;
  date: string | null;
  range: string | null;
  source: string | null;
  verification_status?: VerificationStatus | null;
  source_document_id?: string | null;
}

export interface ConditionRecord {
  name: string;
  status: string;
  first_seen: string | null;
  last_mentioned: string | null;
  verification_status?: VerificationStatus | null;
  source_document_id?: string | null;
}

export interface MedicationRecord {
  name: string;
  dose: string | null;
  frequency: string | null;
  status: 'Active' | 'Previous';
  start: string | null;
  end: string | null;
  verification_status?: VerificationStatus | null;
  source_document_id?: string | null;
}

export interface AllergyRecord {
  allergen: string;
  reaction: string | null;
  source: string | null;
  verification_status?: VerificationStatus | null;
  source_document_id?: string | null;
}

export interface AbnormalFinding {
  test: string;
  value: string;
  status: string;
  source: string | null;
  verification_status?: VerificationStatus | null;
  source_document_id?: string | null;
}

export interface RiskAlert {
  message: string;
  priority: RiskLevel;
  type: string;
  evidence: string | null;
}

export interface EvidenceInsight {
  title: string;
  detail: string;
  evidence: string[];
  priority: RiskLevel;
}

export interface ClinicalSnapshot {
  patient: Patient;
  insights: EvidenceInsight[];
  active_conditions: ConditionRecord[];
  current_medications: MedicationRecord[];
  allergies: AllergyRecord[];
  recent_abnormal: AbnormalFinding[];
  risk_alerts: RiskAlert[];
  lab_trends: LabTrend[];
  timeline: TimelinePeriod[];
  documents: DocumentRecord[];
  doctor_checklist: string[];
  doctor_checklist_items: ChecklistItemRecord[];
}

export interface ChecklistItemRecord {
  id: string;
  text: string;
  done: boolean;
  agent_note?: string | null;
}

// ---- Imaging (apps/api/routers/studies.py) ----

export type ReportSource = 'mock' | 'medgemma' | 'fireworks-vl' | 'qwen-vl';
export type StudyStatus = 'ready' | 'segmenting' | 'reporting';
export type ReviewDecision = 'unreviewed' | 'accepted' | 'needs-correction';

export interface RoiBox {
  x: number;
  y: number;
  width: number;
  height: number;
  /** Slice the box was drawn on; omitted/null means the middle slice. */
  slice_index?: number | null;
  /** Intensity window (HU) MedSAM2 normalizes with — from the viewer's window/level. */
  window_lower?: number | null;
  window_upper?: number | null;
}

export interface Segmentation {
  id: string;
  label: string;
  confidence: number;
  volume_ml: number;
  /** The API only ever returns `medsam2`; `mock` marks a client-side offline fallback. */
  source: 'medsam2' | 'mock';
  box: RoiBox;
  /** Overlay for the prompt slice (or the whole image on the legacy 2D path). */
  overlay_url?: string | null;
  // Volumetric fields — present only when the study is a multi-slice series.
  prompt_slice?: number | null;
  /** Raw uint8 mask bytes ([depth, rows, cols], C-order) for the Cornerstone labelmap. */
  mask_url?: string | null;
  /** [depth, rows, cols] */
  mask_shape?: number[] | null;
  /** [dz, dy, dx] in mm */
  voxel_spacing_mm?: number[] | null;
  /** Indexed by slice; null where the mask is empty on that slice. */
  slice_overlay_urls?: (string | null)[];
}

export interface DraftReport {
  summary: string;
  findings: string;
  impression: string;
  recommendation: string;
  confidence: number;
  source: ReportSource;
  fhir_diagnostic_report_id?: string | null;
}

/** Server payload from `POST /api/studies`. */
export interface StudyUpload {
  id: string;
  patient_id: string;
  fhir_imaging_study_id: string;
  fhir_document_reference_id?: string | null;
  patient_name: string;
  patient_detail: string;
  modality: string;
  body_part: string;
  series: string;
  slices: number;
  uploaded_file_name: string;
  is_dicom: boolean;
  /** 8-bit thumbnail for the study list and the no-WebGL fallback. */
  preview_url?: string | null;
  /** Original DICOM (first slice), loaded client-side by Cornerstone3D. */
  dicom_url?: string | null;
  /** Every slice of the series in anatomical order; empty for a single file. */
  slice_urls?: string[];
  /** True when every slice carries position/orientation/spacing — the precondition for MPR. */
  has_volume_geometry?: boolean;
  uploaded_at?: string | null;
  review_decision?: ReviewDecision;
  review_note?: string | null;
  report?: DraftReport | null;
}

/** Client-side study: the server payload plus local review state. */
export interface Study extends StudyUpload {
  timestamp: string;
  status: StudyStatus;
  reviewDecision: ReviewDecision;
  /** Free-text note captured when a draft is sent back for correction. */
  reviewNote?: string;
  segmentations: Segmentation[];
  report: DraftReport;
}

/** The `imaging` block of `GET /api/health`. */
export interface ImagingStatus {
  provider: 'mock' | 'fireworks-vl' | 'http' | 'local';
  fireworks_configured: boolean;
  model: string | null;
}
