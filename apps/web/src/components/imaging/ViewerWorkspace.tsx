import { type DragEvent, type MouseEvent, useRef, useState } from 'react';
import { Box, Brain, Layers, Loader2, Moon, Square, Sun, Upload, ZoomIn, ZoomOut } from 'lucide-react';
import { cn } from '@/lib/utils';
import type { RoiBox, Segmentation, Study } from '@/lib/types';
import { DEFAULT_ROI, clamp01, isUsableRoi, normalizeRoi } from './roi';
import { DicomViewport, type VoiRange } from './DicomViewport';
import { MprViewport } from './MprViewport';
import { buildImageIds } from '@/lib/cornerstone';

// Slider bounds wide enough for CT Hounsfield units (-1024..3071) and MR signal values.
const VOI_LIMITS = { centerMin: -1024, centerMax: 3071, widthMin: 1, widthMax: 4096 };

function ViewportOverlay({
  study,
  voi,
  sliceIndex,
  gpuRendered,
}: {
  study: Study;
  voi: VoiRange | null;
  sliceIndex: number;
  gpuRendered: boolean;
}) {
  return (
    <>
      <div className="pointer-events-none absolute inset-x-0 top-0 flex items-center justify-between p-4 text-xs text-slate-400">
        <span>{study.uploaded_file_name ?? study.id}</span>
        {/* Live values from the viewport, not a placeholder string. */}
        <span className="tabular-nums">
          {voi ? `W: ${Math.round(voi.windowWidth)}  L: ${Math.round(voi.windowCenter)}` : '—'}
        </span>
      </div>
      <div className="pointer-events-none absolute bottom-4 left-4 rounded border border-slate-700/80 bg-black/45 px-3 py-2 text-xs text-slate-300">
        {study.series || 'Series'} • Slice {Math.min(sliceIndex + 1, study.slices)} / {study.slices}
      </div>
      <div className="pointer-events-none absolute bottom-4 right-4 rounded border border-slate-700/80 bg-black/45 px-3 py-2 text-xs text-slate-300">
        {gpuRendered ? 'DICOM · GPU rendered' : study.is_dicom ? 'DICOM · preview' : 'Preview mode'}
      </div>
    </>
  );
}

function RoiBoxOverlay({ box, label, className }: { box: RoiBox; label: string; className: string }) {
  const corner =
    'absolute h-2.5 w-2.5 rounded-full border border-white/80 bg-cyan-300/95 shadow-[0_0_12px_rgba(34,211,238,0.7)]';
  return (
    <div
      className={cn('pointer-events-none absolute border-2', className)}
      style={{
        left: `${box.x * 100}%`,
        top: `${box.y * 100}%`,
        width: `${box.width * 100}%`,
        height: `${box.height * 100}%`,
      }}
    >
      <div className="absolute inset-0 border border-white/25" />
      <span className={cn(corner, '-left-1 -top-1')} />
      <span className={cn(corner, '-right-1 -top-1')} />
      <span className={cn(corner, '-bottom-1 -left-1')} />
      <span className={cn(corner, '-bottom-1 -right-1')} />
      <span className="absolute left-2 top-2 whitespace-nowrap rounded-sm border border-cyan-300/45 bg-[#041724]/90 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-cyan-100">
        {label}
      </span>
    </div>
  );
}

function SegmentationOverlay({
  segmentations,
  zoom,
}: {
  segmentations: Segmentation[];
  zoom: number;
}) {
  return (
    <>
      {segmentations.map((segmentation, index) =>
        segmentation.overlay_url ? (
          <img
            key={`${segmentation.id}-${index}`}
            className="pointer-events-none absolute inset-0 h-full w-full object-contain transition-transform duration-200"
            src={segmentation.overlay_url}
            alt={`Segmentation overlay ${index + 1}`}
            style={{ transform: `scale(${zoom / 100})` }}
          />
        ) : (
          <RoiBoxOverlay
            key={`${segmentation.id}-${index}`}
            box={segmentation.box}
            // A mock result is just the drawn ROI echoed back — never present it as a model mask.
            label={
              segmentation.source === 'mock'
                ? 'ROI only — no model'
                : `MedSAM2 mask ${index + 1}`
            }
            className={
              segmentation.source === 'mock'
                ? 'border-amber-300/80 bg-amber-300/5'
                : 'border-cyan-300/90 bg-cyan-300/10 shadow-[0_0_30px_rgba(103,232,249,0.35)]'
            }
          />
        ),
      )}
    </>
  );
}

interface ViewerWorkspaceProps {
  study: Study;
  segmentVisible: boolean;
  zoom: number;
  /** Window/level applied by the GPU to the source pixel values; null = the DICOM's own. */
  voi: VoiRange | null;
  sliceIndex: number;
  /** `stack` = one plane with a slice scrubber; `mpr` = axial/sagittal/coronal. */
  layout: 'stack' | 'mpr';
  canRunSegmentation: boolean;
  onSegmentVisibleChange: (visible: boolean) => void;
  onVoiChange: (voi: VoiRange) => void;
  onVoiLoaded: (info: { defaultVoi: VoiRange | null }) => void;
  onSliceChange: (index: number) => void;
  onLayoutChange: (layout: 'stack' | 'mpr') => void;
  onZoomChange: (zoom: number) => void;
  onRunSegmentation: (prompt?: RoiBox) => void;
  onFiles: (files: File[]) => void;
}

export function ViewerWorkspace({
  study,
  segmentVisible,
  zoom,
  voi,
  sliceIndex,
  layout,
  canRunSegmentation,
  onSegmentVisibleChange,
  onVoiChange,
  onVoiLoaded,
  onSliceChange,
  onLayoutChange,
  onZoomChange,
  onRunSegmentation,
  onFiles,
}: ViewerWorkspaceProps) {
  const [renderError, setRenderError] = useState<string | null>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const [draftRoi, setDraftRoi] = useState<RoiBox | null>(null);
  const [roiStart, setRoiStart] = useState<{ x: number; y: number } | null>(null);
  const [isDragging, setIsDragging] = useState(false);

  const getPoint = (event: MouseEvent<HTMLDivElement>) => {
    const rect = viewportRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return {
      x: clamp01((event.clientX - rect.left) / rect.width),
      y: clamp01((event.clientY - rect.top) / rect.height),
    };
  };

  const handleRoiStart = (event: MouseEvent<HTMLDivElement>) => {
    const point = getPoint(event);
    setRoiStart(point);
    setDraftRoi({ x: point.x, y: point.y, width: 0.01, height: 0.01 });
  };

  const handleRoiMove = (event: MouseEvent<HTMLDivElement>) => {
    if (!roiStart) return;
    setDraftRoi(normalizeRoi(roiStart, getPoint(event)));
  };

  const handleRoiEnd = () => {
    if (!draftRoi || !roiStart) return;
    const roi = isUsableRoi(draftRoi) ? draftRoi : null;
    setRoiStart(null);
    setDraftRoi(null);
    if (roi) onRunSegmentation(roi);
  };

  const hasFileDrag = (event: DragEvent<HTMLDivElement>) =>
    Array.from(event.dataTransfer?.types ?? []).includes('Files');

  return (
    <section className="flex min-h-0 flex-col bg-[#05070b]">
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-slate-800 bg-[#090f18] px-4 py-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-white">
            {study.patient_name} / {study.modality} {study.body_part}
          </p>
          <p className="mt-1 truncate text-xs text-slate-500">
            {study.series} • {study.slices} slice{study.slices === 1 ? '' : 's'} •{' '}
            {study.uploaded_file_name ?? study.id}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <button
            className="inline-flex h-9 items-center gap-2 rounded-md border border-slate-700 bg-slate-950 px-3 text-sm font-medium text-slate-200 transition hover:border-cyan-300/50 hover:text-cyan-100 disabled:cursor-wait disabled:opacity-70"
            type="button"
            disabled={study.status === 'segmenting' || !canRunSegmentation}
            title={
              canRunSegmentation
                ? 'Quick test run with a preset ROI. Preferred workflow: draw an ROI directly on the image.'
                : 'Upload a study first to run MedSAM2.'
            }
            onClick={() => onRunSegmentation(DEFAULT_ROI)}
          >
            {study.status === 'segmenting' ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Brain className="h-4 w-4" />
            )}
            Run MedSAM2 (Default ROI)
          </button>
          <button
            className={cn(
              'inline-flex h-9 items-center gap-2 rounded-md border px-3 text-sm font-medium transition',
              segmentVisible
                ? 'border-cyan-300/40 bg-cyan-400/10 text-cyan-100'
                : 'border-slate-700 bg-slate-950 text-slate-400',
            )}
            type="button"
            title={segmentVisible ? 'Hide segmentation masks' : 'Show segmentation masks'}
            onClick={() => onSegmentVisibleChange(!segmentVisible)}
          >
            <Layers className="h-4 w-4" />
            {segmentVisible ? 'Hide Masks' : 'Show Masks'}
          </button>

          {/* MPR needs per-slice geometry; without it sagittal/coronal are undefined. */}
          <button
            className={cn(
              'inline-flex h-9 items-center gap-2 rounded-md border px-3 text-sm font-medium transition disabled:opacity-40',
              layout === 'mpr'
                ? 'border-cyan-300/40 bg-cyan-400/10 text-cyan-100'
                : 'border-slate-700 bg-slate-950 text-slate-400',
            )}
            type="button"
            disabled={!study.has_volume_geometry}
            title={
              study.has_volume_geometry
                ? 'Reconstruct axial, sagittal and coronal planes from the volume'
                : 'This study has no per-slice position/orientation/spacing, so it cannot be resliced'
            }
            onClick={() => onLayoutChange(layout === 'mpr' ? 'stack' : 'mpr')}
          >
            {layout === 'mpr' ? <Square className="h-4 w-4" /> : <Box className="h-4 w-4" />}
            {layout === 'mpr' ? 'Single plane' : 'MPR 3D'}
          </button>
        </div>
      </header>

      <div className="flex shrink-0 flex-wrap items-center gap-x-5 gap-y-2 border-b border-slate-800 bg-[#070b12] px-4 py-2">
        {/* True window/level: these drive a GPU shader range over the DICOM's own pixel
            values, so widening the window recovers detail a flattened PNG would have lost. */}
        <div className="flex min-w-[210px] flex-1 items-center gap-3 text-xs text-slate-300">
          <Moon className="h-4 w-4 shrink-0 text-slate-500" aria-label="Window level (brightness)" />
          <input
            className="h-1.5 w-full cursor-pointer accent-amber-300"
            type="range"
            min={VOI_LIMITS.centerMin}
            max={VOI_LIMITS.centerMax}
            step={1}
            value={voi?.windowCenter ?? 0}
            disabled={!voi}
            aria-label="Window level"
            onChange={(e) =>
              voi && onVoiChange({ ...voi, windowCenter: Number(e.target.value) })
            }
          />
          <span className="w-24 shrink-0 text-right font-medium tabular-nums">
            L {voi ? Math.round(voi.windowCenter) : '—'}
          </span>
        </div>

        <div className="flex min-w-[210px] flex-1 items-center gap-3 text-xs text-slate-300">
          <Sun className="h-4 w-4 shrink-0 text-slate-500" aria-label="Window width (contrast)" />
          <input
            className="h-1.5 w-full cursor-pointer accent-amber-300"
            type="range"
            min={VOI_LIMITS.widthMin}
            max={VOI_LIMITS.widthMax}
            step={1}
            value={voi?.windowWidth ?? 1}
            disabled={!voi}
            aria-label="Window width"
            onChange={(e) =>
              voi && onVoiChange({ ...voi, windowWidth: Math.max(1, Number(e.target.value)) })
            }
          />
          <span className="w-24 shrink-0 text-right font-medium tabular-nums">
            W {voi ? Math.round(voi.windowWidth) : '—'}
          </span>
        </div>

        {study.slices > 1 && layout === 'stack' && (
          <div className="flex min-w-[210px] flex-1 items-center gap-3 text-xs text-slate-300">
            <Layers className="h-4 w-4 shrink-0 text-slate-500" aria-label="Slice" />
            <input
              className="h-1.5 w-full cursor-pointer accent-cyan-400"
              type="range"
              min={0}
              max={study.slices - 1}
              step={1}
              value={sliceIndex}
              aria-label="Slice"
              onChange={(e) => onSliceChange(Number(e.target.value))}
            />
            <span className="w-24 shrink-0 text-right font-medium tabular-nums">
              {sliceIndex + 1} / {study.slices}
            </span>
          </div>
        )}

        <div className="flex min-w-[180px] flex-1 items-center gap-3 text-xs text-slate-300">
          <ZoomOut className="h-4 w-4 shrink-0 text-slate-500" />
          <input
            className="h-1.5 w-full cursor-pointer accent-cyan-400"
            type="range"
            min="70"
            max="180"
            step="5"
            value={zoom}
            aria-label="Image zoom"
            onChange={(event) => onZoomChange(Number(event.target.value))}
          />
          <ZoomIn className="h-4 w-4 shrink-0 text-slate-500" />
          <span className="w-12 shrink-0 text-right font-medium tabular-nums">{zoom}%</span>
        </div>
      </div>

      {layout === 'mpr' ? (
        <div className="min-h-0 flex-1 p-4">
          <MprViewport
            imageIds={buildImageIds(study.dicom_url ?? '', study.slices, study.slice_urls)}
            voi={voi}
            onError={setRenderError}
          />
          {renderError && (
            <p className="mt-2 rounded border border-amber-400/40 bg-amber-950/80 px-3 py-2 text-xs text-amber-100">
              Volume reconstruction failed: {renderError}
            </p>
          )}
        </div>
      ) : (
      <div className="min-h-0 flex-1 p-4">
        <div
          ref={viewportRef}
          className={cn(
            'relative h-full min-h-[520px] cursor-crosshair overflow-hidden rounded-md border bg-black transition',
            isDragging ? 'border-cyan-300 ring-4 ring-cyan-400/20' : 'border-slate-800',
          )}
          onMouseDown={handleRoiStart}
          onMouseMove={handleRoiMove}
          onMouseUp={handleRoiEnd}
          onMouseLeave={() => {
            setRoiStart(null);
            setDraftRoi(null);
          }}
          onDragEnter={(event) => {
            if (!hasFileDrag(event)) return;
            event.preventDefault();
            setIsDragging(true);
          }}
          onDragLeave={(event) => {
            if (!hasFileDrag(event)) return;
            setIsDragging(false);
          }}
          onDragOver={(event) => {
            if (!hasFileDrag(event)) return;
            event.preventDefault();
          }}
          onDrop={(event) => {
            if (!hasFileDrag(event)) return;
            event.preventDefault();
            setIsDragging(false);
            // The dropped files were previously discarded, making this overlay decorative.
            const files = Array.from(event.dataTransfer?.files ?? []);
            if (files.length > 0) onFiles(files);
          }}
        >
          {study.dicom_url && !renderError ? (
            // Rendered in the browser on the GPU from the original DICOM.
            <DicomViewport
              key={study.id}
              dicomUrl={study.dicom_url}
              sliceUrls={study.slice_urls}
              frames={study.slices}
              sliceIndex={sliceIndex}
              voi={voi}
              zoom={zoom}
              onLoaded={onVoiLoaded}
              onError={setRenderError}
            />
          ) : study.preview_url ? (
            // Fallback: the server-rendered thumbnail (no WebGL, or an unreadable DICOM).
            <>
              <img
                className="pointer-events-none absolute inset-0 h-full w-full select-none object-contain transition-transform duration-200"
                src={study.preview_url}
                alt={`${study.modality} ${study.body_part} preview`}
                draggable={false}
                style={{ transform: `scale(${zoom / 100})` }}
              />
              {renderError && (
                <div className="absolute inset-x-4 top-4 rounded border border-amber-400/40 bg-amber-950/80 px-3 py-2 text-xs text-amber-100">
                  GPU rendering unavailable ({renderError}) — showing the server preview, so
                  window/level is fixed.
                </div>
              )}
            </>
          ) : (
            <div className="absolute inset-0 bg-[#02050a]">
              <div
                className="absolute inset-0 opacity-25"
                style={{
                  backgroundImage:
                    'linear-gradient(to right, rgba(148,163,184,0.08) 1px, transparent 1px), linear-gradient(to bottom, rgba(148,163,184,0.08) 1px, transparent 1px)',
                  backgroundSize: '28px 28px',
                  transform: `scale(${zoom / 100})`,
                }}
              />
              <div className="absolute inset-0 grid place-items-center">
                <div className="rounded-md border border-slate-700/70 bg-[#070d17]/90 px-6 py-5 text-center">
                  <p className="text-sm font-semibold text-slate-200">No study loaded</p>
                  <p className="mt-1 text-xs text-slate-400">
                    Upload or drop a DICOM file to start ROI-based segmentation.
                  </p>
                </div>
              </div>
            </div>
          )}

          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_center,transparent_43%,rgba(0,0,0,0.62)_100%)]" />
          <ViewportOverlay
            study={study}
            voi={voi}
            sliceIndex={sliceIndex}
            gpuRendered={Boolean(study.dicom_url) && !renderError}
          />

          {segmentVisible && study.segmentations.length > 0 && (
            <SegmentationOverlay segmentations={study.segmentations} zoom={zoom} />
          )}

          {draftRoi && (
            <RoiBoxOverlay
              box={draftRoi}
              label="ROI prompt"
              className="border-amber-200 bg-amber-300/10 shadow-[0_0_28px_rgba(252,211,77,0.25)]"
            />
          )}

          {isDragging && (
            <div className="absolute inset-0 grid place-items-center bg-cyan-950/45 backdrop-blur-sm">
              <div className="rounded-md border border-cyan-300/50 bg-slate-950/85 px-6 py-5 text-center shadow-[0_0_22px_rgba(59,130,246,0.36)]">
                <Upload className="mx-auto h-7 w-7 text-cyan-200" />
                <p className="mt-2 text-sm font-semibold text-white">Drop DICOM study</p>
                <p className="mt-1 text-xs text-cyan-100/80">Release to upload and render</p>
              </div>
            </div>
          )}
        </div>
      </div>
      )}
    </section>
  );
}
