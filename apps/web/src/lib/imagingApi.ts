/**
 * Imaging endpoints (`/api/studies`, `/api/health`) on the shared typed client.
 * Replaces the bespoke fetch wrappers the standalone radiology app used to carry.
 */

import { API_BASE, apiGet, apiPost, uploadFiles } from './api';
import type { ImagingStatus, ReportSource, RoiBox, Segmentation, StudyUpload } from './types';

/** Server-relative asset paths (previews, mask overlays) need the API origin. */
export function absoluteAssetUrl(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  return url.startsWith('/') ? `${API_BASE}${url}` : url;
}

/**
 * Upload a study: one DICOM file, or a whole series.
 *
 * The route takes a repeated `files` field — a real CT/MR study is a folder of single-frame
 * slices, and the volume/MPR path needs all of them.
 */
export async function uploadStudy(
  files: File[],
  patientId: string,
  signal?: AbortSignal,
): Promise<StudyUpload> {
  const study = await uploadFiles<StudyUpload>('/api/studies', files, { patient_id: patientId }, signal);
  return {
    ...study,
    preview_url: absoluteAssetUrl(study.preview_url) ?? null,
    dicom_url: absoluteAssetUrl(study.dicom_url) ?? null,
    slice_urls: (study.slice_urls ?? []).map((u) => absoluteAssetUrl(u) ?? u),
  };
}

export async function fetchStudies(
  signal?: AbortSignal,
  patientId?: string,
): Promise<StudyUpload[]> {
  const query = patientId ? `?${new URLSearchParams({ patient_id: patientId })}` : '';
  const studies = await apiGet<StudyUpload[]>(`/api/studies${query}`, signal);
  return studies.map((study) => ({
    ...study,
    preview_url: absoluteAssetUrl(study.preview_url) ?? null,
    dicom_url: absoluteAssetUrl(study.dicom_url) ?? null,
    slice_urls: (study.slice_urls ?? []).map((u) => absoluteAssetUrl(u) ?? u),
  }));
}

export async function requestSegmentation(
  studyId: string,
  prompt: RoiBox,
  signal?: AbortSignal,
): Promise<Segmentation> {
  const seg = await apiPost<Segmentation>(
    `/api/studies/${studyId}/segmentations/medsam2`,
    { prompt },
    signal,
  );
  return {
    ...seg,
    overlay_url: absoluteAssetUrl(seg.overlay_url) ?? null,
    mask_url: absoluteAssetUrl(seg.mask_url) ?? null,
    slice_overlay_urls: (seg.slice_overlay_urls ?? []).map((u) => absoluteAssetUrl(u) ?? null),
  };
}

export function requestReport(
  studyId: string,
  body: { modality: string; body_part: string; segmentations: unknown[] },
  signal?: AbortSignal,
): Promise<{
  summary: string;
  findings: string;
  impression: string;
  recommendation: string;
  confidence: number;
  source: ReportSource;
}> {
  return apiPost(`/api/studies/${studyId}/reports/qwen-vl`, body, signal);
}

export function reviewReport(
  studyId: string,
  decision: 'accepted' | 'needs-correction',
  note?: string,
): Promise<{
  decision: 'accepted' | 'needs-correction';
  note?: string | null;
  fhir_diagnostic_report_id: string;
  fhir_task_id: string;
}> {
  return apiPost(`/api/studies/${studyId}/reports/review`, { decision, note: note || null });
}

/** Report-provider status, read from the `imaging` block of the shared health route. */
export async function fetchImagingStatus(signal?: AbortSignal): Promise<ImagingStatus> {
  const health = await apiGet<{ imaging: ImagingStatus }>('/api/health', signal);
  return health.imaging;
}
