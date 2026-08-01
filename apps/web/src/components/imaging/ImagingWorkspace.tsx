import { useCallback, useEffect, useState } from 'react';
import type { ImagingStatus, RoiBox, Segmentation, Study, StudyUpload } from '@/lib/types';
import {
  fetchImagingStatus,
  fetchStudies,
  requestReport,
  requestSegmentation,
  reviewReport,
  uploadStudy,
} from '@/lib/imagingApi';
import { usePatients } from '@/hooks/usePatients';
import { PatientModeSwitcher } from '@/components/PatientVisitNav';
import { StudyPanel } from './StudyPanel';
import { ViewerWorkspace } from './ViewerWorkspace';
import type { VoiRange } from './DicomViewport';
import { DecisionPanel } from './DecisionPanel';
import { FeedbackDialog } from './FeedbackDialog';
import { DEFAULT_ROI, isDicomFile } from './roi';

const EMPTY_STUDY: Study = {
  id: 'NO-DICOM',
  patient_id: '',
  fhir_imaging_study_id: '',
  patient_name: 'No DICOM loaded',
  patient_detail: 'Upload a study',
  modality: 'DICOM',
  body_part: 'Study',
  timestamp: 'Waiting',
  series: 'None',
  slices: 0,
  uploaded_file_name: '',
  is_dicom: false,
  preview_url: null,
  dicom_url: null,
  status: 'ready',
  reviewDecision: 'unreviewed',
  segmentations: [],
  report: {
    summary: 'Awaiting DICOM upload',
    findings: 'Upload a DICOM file to render the image and generate a Fireworks VL draft report.',
    impression: 'No imaging study is loaded.',
    recommendation: 'Use the Upload DICOM control on the left panel.',
    confidence: 0,
    source: 'mock',
  },
};

const MOCK_STATUS: ImagingStatus = { provider: 'mock', fireworks_configured: false, model: null };

function toStudy(upload: StudyUpload): Study {
  return {
    ...upload,
    timestamp: upload.uploaded_at?.slice(0, 10) || 'Medplum',
    status: 'ready',
    reviewDecision: upload.review_decision ?? 'unreviewed',
    reviewNote: upload.review_note ?? undefined,
    segmentations: [],
    report:
      upload.report ??
      {
        summary: 'Awaiting AI review',
        findings: 'Generate a preliminary report after reviewing the DICOM series.',
        impression: 'Pending AI draft and clinician review.',
        recommendation: 'Select an ROI if a suspicious region is present.',
        confidence: 0,
        source: 'mock',
      },
  };
}

/** DICOM upload → ROI segmentation → draft report → doctor review, all in one screen. */
export function ImagingWorkspace({ patientId }: { patientId?: string }) {
  const { patients } = usePatients();
  const [studies, setStudies] = useState<Study[]>([]);
  const [selectedPatientId, setSelectedPatientId] = useState(patientId ?? '');
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [activeStudyId, setActiveStudyId] = useState<string | null>(null);
  const [segmentVisible, setSegmentVisible] = useState(true);
  const [zoom, setZoom] = useState(100);
  // Window/level and slice belong to the study being viewed, so they reset when it changes.
  const [voi, setVoi] = useState<VoiRange | null>(null);
  const [sliceIndex, setSliceIndex] = useState(0);
  const [layout, setLayout] = useState<'stack' | 'mpr'>('stack');
  const [imagingStatus, setImagingStatus] = useState<ImagingStatus>(MOCK_STATUS);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const patientLocked = Boolean(patientId);
  const patientName =
    patients.find((p) => p.id === (patientId ?? selectedPatientId))?.name ?? 'Patient';

  const study = studies.find((s) => s.id === activeStudyId) ?? studies[0] ?? EMPTY_STUDY;
  const hasLoadedStudy = study.id !== EMPTY_STUDY.id;

  useEffect(() => {
    if (patientId) setSelectedPatientId(patientId);
  }, [patientId]);

  useEffect(() => {
    const controller = new AbortController();
    fetchImagingStatus(controller.signal)
      .then(setImagingStatus)
      .catch(() => setImagingStatus(MOCK_STATUS));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (patientLocked) return;
    if (!selectedPatientId && patients.length > 0) setSelectedPatientId(patients[0].id);
  }, [patients, patientLocked, selectedPatientId]);

  useEffect(() => {
    if (!selectedPatientId) {
      setStudies([]);
      setActiveStudyId(null);
      return;
    }
    const controller = new AbortController();
    fetchStudies(controller.signal, selectedPatientId)
      .then((rows) => {
        const canonical = rows.map(toStudy);
        setStudies(canonical);
        setActiveStudyId(canonical[0]?.id ?? null);
        setVoi(null);
        setSliceIndex(0);
        setLayout('stack');
      })
      .catch((error) => setUploadError(error instanceof Error ? error.message : 'Could not load Medplum studies.'));
    return () => controller.abort();
  }, [selectedPatientId]);

  const updateStudy = useCallback(
    (studyId: string, updater: (study: Study) => Study) => {
      setStudies((current) => current.map((s) => (s.id === studyId ? updater(s) : s)));
    },
    [],
  );

  const handleFiles = useCallback(async (files: File[]) => {
    // One study per drop: a DICOM series is many files that belong to a single volume,
    // so they are uploaded together rather than creating one study per slice.
    const dicoms = files.filter(isDicomFile);
    if (dicoms.length > 0 && selectedPatientId) {
      try {
        setUploadError(null);
        const uploaded = await uploadStudy(dicoms, selectedPatientId);
        const next = toStudy(uploaded);

        setStudies((current) => [next, ...current.filter((s) => s.id !== next.id)]);
        setActiveStudyId(next.id);
        setSegmentVisible(true);
        setVoi(null);
        setSliceIndex(0);
        setLayout('stack');
      } catch (error) {
        setUploadError(error instanceof Error ? error.message : 'DICOM upload failed.');
      }
    }
  }, [selectedPatientId]);

  const runSegmentation = useCallback(
    async (prompt: RoiBox = DEFAULT_ROI) => {
      const studyId = study.id;
      updateStudy(studyId, (s) => ({ ...s, status: 'segmenting' }));
      setSegmentVisible(true);

      try {
        // The box carries the slice it was drawn on (so a volumetric model can propagate
        // from it) and the viewer's window/level — MedSAM2 normalizes intensities to that
        // window, so segmenting "what the clinician sees" is what keeps masks tight.
        const window = voi
          ? {
              window_lower: voi.windowCenter - voi.windowWidth / 2,
              window_upper: voi.windowCenter + voi.windowWidth / 2,
            }
          : {};
        const segmentation = await requestSegmentation(studyId, {
          ...prompt,
          slice_index: sliceIndex,
          ...window,
        });
        updateStudy(studyId, (s) => ({
          ...s,
          status: 'ready',
          // Keep only the latest segmentation so each ROI analysis starts fresh.
          segmentations: [{ ...segmentation, box: segmentation.box ?? prompt }],
        }));
      } catch {
        const fallback: Segmentation = {
          id: `seg-${Date.now()}`,
          label: 'Prompted ROI (offline)',
          confidence: 0.79,
          volume_ml: Math.round(prompt.width * prompt.height * 1200) / 10,
          source: 'mock',
          box: prompt,
        };
        updateStudy(studyId, (s) => ({ ...s, status: 'ready', segmentations: [fallback] }));
      }
    },
    [sliceIndex, voi, study.id, updateStudy],
  );

  const runReport = useCallback(async () => {
    const studyId = study.id;
    updateStudy(studyId, (s) => ({ ...s, status: 'reporting' }));

    try {
      const report = await requestReport(studyId, {
        modality: study.modality,
        body_part: study.body_part,
        segmentations: study.segmentations,
      });
      updateStudy(studyId, (s) => ({ ...s, status: 'ready', report }));
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Report service is unavailable.';
      updateStudy(studyId, (s) => ({
        ...s,
        status: 'ready',
        report: {
          summary: 'Fireworks VL draft unavailable',
          findings:
            s.segmentations.length > 0
              ? `AI draft based on ${s.segmentations.length} segmentation ROI(s). ${message}`
              : message,
          impression:
            'Preliminary decision support only. No autonomous diagnosis should be made from this draft.',
          recommendation: imagingStatus.fireworks_configured
            ? 'Fireworks VL appears configured. Restart the API if the key changed, then try Generate again.'
            : 'Set FIREWORKS_API_KEY in the repo .env, restart the API, then generate the report again.',
          confidence: s.segmentations.length > 0 ? 0.72 : 0.38,
          source: 'fireworks-vl',
        },
      }));
    }
  }, [imagingStatus.fireworks_configured, study.body_part, study.id, study.modality, study.segmentations, updateStudy]);

  return (
    <div className="bg-[#05070b] text-slate-100">
      {patientId ? (
        <PatientModeSwitcher
          patientId={patientId}
          patientName={patientName}
          active="imaging"
          tone="dark"
        />
      ) : null}
      <div
        className={
          patientId
            ? 'h-[calc(100vh-3.5rem-3rem)] overflow-hidden'
            : 'h-[calc(100vh-3.5rem)] overflow-hidden'
        }
      >
        <div className="grid h-full grid-cols-[280px_minmax(0,1fr)_390px] overflow-hidden max-xl:grid-cols-[240px_minmax(0,1fr)_360px] max-lg:grid-cols-1 max-lg:overflow-y-auto">
          <StudyPanel
            activeStudyId={study.id}
            studies={studies}
            patients={patients}
            selectedPatientId={selectedPatientId}
            patientLocked={patientLocked}
            uploadError={uploadError}
            onFiles={handleFiles}
            onPatientChange={setSelectedPatientId}
            onSelectStudy={(id) => {
              setActiveStudyId(id);
              setVoi(null);
              setSliceIndex(0);
              setLayout('stack');
            }}
          />

          <ViewerWorkspace
            voi={voi}
            sliceIndex={sliceIndex}
            layout={layout}
            segmentVisible={segmentVisible}
            study={study}
            zoom={zoom}
            canRunSegmentation={hasLoadedStudy}
            onVoiChange={setVoi}
            onVoiLoaded={({ defaultVoi }) => setVoi(defaultVoi)}
            onSliceChange={setSliceIndex}
            onLayoutChange={setLayout}
            onRunSegmentation={runSegmentation}
            onSegmentVisibleChange={setSegmentVisible}
            onZoomChange={setZoom}
            onFiles={handleFiles}
          />

          <DecisionPanel
            imagingStatus={imagingStatus}
            study={study}
            canRunReport={hasLoadedStudy}
            onAccept={() => {
              reviewReport(study.id, 'accepted')
                .then(() => updateStudy(study.id, (s) => ({ ...s, reviewDecision: 'accepted', reviewNote: undefined })))
                .catch((error) => setUploadError(error instanceof Error ? error.message : 'Could not save review.'));
            }}
            onNeedsCorrection={() => {
              setFeedbackOpen(true);
            }}
            onRunReport={runReport}
          />
        </div>
      </div>

      <FeedbackDialog
        open={feedbackOpen}
        study={study}
        onOpenChange={setFeedbackOpen}
        onSave={(note) => {
          reviewReport(study.id, 'needs-correction', note)
            .then(() =>
              updateStudy(study.id, (s) => ({
                ...s,
                reviewDecision: 'needs-correction',
                reviewNote: note || undefined,
              })),
            )
            .catch((error) => setUploadError(error instanceof Error ? error.message : 'Could not save review.'));
        }}
      />
    </div>
  );
}
