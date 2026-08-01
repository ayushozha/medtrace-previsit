import { useCallback, useEffect, useState } from 'react';
import type { ImagingStatus, RoiBox, Segmentation, Study } from '@/lib/types';
import {
  fetchImagingStatus,
  requestReport,
  requestSegmentation,
  uploadStudy,
} from '@/lib/imagingApi';
import { StudyPanel } from './StudyPanel';
import { ViewerWorkspace } from './ViewerWorkspace';
import type { VoiRange } from './DicomViewport';
import { DecisionPanel } from './DecisionPanel';
import { FeedbackDialog } from './FeedbackDialog';
import { DEFAULT_ROI, isDicomFile } from './roi';

const EMPTY_STUDY: Study = {
  id: 'NO-DICOM',
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
    findings: 'Upload a DICOM file to render the image and generate a Qwen VL draft report.',
    impression: 'No imaging study is loaded.',
    recommendation: 'Use the Upload DICOM control on the left panel.',
    confidence: 0,
    source: 'mock',
  },
};

const MOCK_STATUS: ImagingStatus = { provider: 'mock', nebius_configured: false, model: null };

/** DICOM upload → ROI segmentation → draft report → doctor review, all in one screen. */
export function ImagingWorkspace() {
  const [studies, setStudies] = useState<Study[]>([]);
  const [activeStudyId, setActiveStudyId] = useState<string | null>(null);
  const [segmentVisible, setSegmentVisible] = useState(true);
  const [zoom, setZoom] = useState(100);
  // Window/level and slice belong to the study being viewed, so they reset when it changes.
  const [voi, setVoi] = useState<VoiRange | null>(null);
  const [sliceIndex, setSliceIndex] = useState(0);
  const [layout, setLayout] = useState<'stack' | 'mpr'>('stack');
  const [imagingStatus, setImagingStatus] = useState<ImagingStatus>(MOCK_STATUS);
  const [feedbackOpen, setFeedbackOpen] = useState(false);

  const study = studies.find((s) => s.id === activeStudyId) ?? studies[0] ?? EMPTY_STUDY;
  const hasLoadedStudy = study.id !== EMPTY_STUDY.id;

  useEffect(() => {
    const controller = new AbortController();
    fetchImagingStatus(controller.signal)
      .then(setImagingStatus)
      .catch(() => setImagingStatus(MOCK_STATUS));
    return () => controller.abort();
  }, []);

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
    if (dicoms.length > 0) {
      const file = dicoms[0];

      const fallback: Study = {
        ...EMPTY_STUDY,
        id: `LOCAL-${Date.now()}`,
        patient_name: dicoms.length > 1 ? `${dicoms.length} slices` : file.name,
        patient_detail: 'Local file',
        body_part: 'Unspecified',
        timestamp: 'Just now',
        series: 'DICOM series',
        slices: dicoms.length,
        uploaded_file_name: file.name,
        is_dicom: true,
        report: {
          summary: 'Awaiting AI review',
          findings:
            'The study is loaded locally. Run MedSAM2 segmentation or Qwen VL report generation.',
          impression: 'Pending AI draft and clinician review.',
          recommendation: 'Select an ROI for segmentation if a suspicious region is present.',
          confidence: 0,
          source: 'mock',
        },
      };

      let next = fallback;
      try {
        const uploaded = await uploadStudy(dicoms);
        next = { ...fallback, ...uploaded };
      } catch {
        // Backend unavailable — keep the local placeholder so the viewer still opens.
      }

      setStudies((current) => [next, ...current.filter((s) => s.id !== next.id)]);
      setActiveStudyId(next.id);
      setSegmentVisible(true);
      // New study: drop the previous study's window/level and slice position.
      setVoi(null);
      setSliceIndex(0);
      setLayout('stack');
    }
  }, []);

  const runSegmentation = useCallback(
    async (prompt: RoiBox = DEFAULT_ROI) => {
      const studyId = study.id;
      updateStudy(studyId, (s) => ({ ...s, status: 'segmenting' }));
      setSegmentVisible(true);

      try {
        const segmentation = await requestSegmentation(studyId, prompt);
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
    [study.id, updateStudy],
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
          summary: 'Qwen VL draft unavailable',
          findings:
            s.segmentations.length > 0
              ? `AI draft based on ${s.segmentations.length} segmentation ROI(s). ${message}`
              : message,
          impression:
            'Preliminary decision support only. No autonomous diagnosis should be made from this draft.',
          recommendation: imagingStatus.nebius_configured
            ? 'Qwen VL appears configured. Restart the API if the key changed, then try Generate again.'
            : 'Set NEBIUS_API_KEY in the repo .env, restart the API, then generate the report again.',
          confidence: s.segmentations.length > 0 ? 0.72 : 0.38,
          source: 'qwen-vl',
        },
      }));
    }
  }, [imagingStatus.nebius_configured, study.body_part, study.id, study.modality, study.segmentations, updateStudy]);

  return (
    <div className="h-[calc(100vh-3.5rem)] overflow-hidden bg-[#05070b] text-slate-100">
      <div className="grid h-full grid-cols-[280px_minmax(0,1fr)_390px] overflow-hidden max-xl:grid-cols-[240px_minmax(0,1fr)_360px] max-lg:grid-cols-1 max-lg:overflow-y-auto">
        <StudyPanel
          activeStudyId={study.id}
          studies={studies}
          onFiles={handleFiles}
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
          onAccept={() =>
            updateStudy(study.id, (s) => ({ ...s, reviewDecision: 'accepted', reviewNote: undefined }))
          }
          onNeedsCorrection={() => {
            updateStudy(study.id, (s) => ({ ...s, reviewDecision: 'needs-correction' }));
            setFeedbackOpen(true);
          }}
          onRunReport={runReport}
        />
      </div>

      <FeedbackDialog
        open={feedbackOpen}
        study={study}
        onOpenChange={setFeedbackOpen}
        onSave={(note) => updateStudy(study.id, (s) => ({ ...s, reviewNote: note || undefined }))}
      />
    </div>
  );
}
