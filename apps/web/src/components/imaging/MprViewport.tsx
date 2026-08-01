import { useEffect, useRef, useState } from 'react';
import {
  Enums,
  RenderingEngine,
  setVolumesForViewports,
  volumeLoader,
  type Types,
} from '@cornerstonejs/core';
import { Crosshair } from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  addCrosshairs,
  attachViewportTools,
  attachVolume3dTools,
  initCornerstone,
  setCrosshairsActive,
} from '@/lib/cornerstone';
import {
  clearSegmentationLabelmaps,
  showSegmentationLabelmap,
} from '@/lib/segmentationLabelmap';
import type { Segmentation } from '@/lib/types';
import type { VoiRange } from './DicomViewport';

const ENGINE_ID = 'medtrace-mpr';
const VOLUME_ID = 'cornerstoneStreamingImageVolume:medtrace';
const MPR_TOOLS_ID = 'medtrace-mpr-tools';
const VOLUME_3D_ID = 'mpr-3d';
const VOLUME_3D_TOOLS_ID = 'medtrace-mpr-3d-tools';

const PLANES = [
  { id: 'mpr-axial', label: 'Axial', orientation: Enums.OrientationAxis.AXIAL },
  { id: 'mpr-sagittal', label: 'Sagittal', orientation: Enums.OrientationAxis.SAGITTAL },
  { id: 'mpr-coronal', label: 'Coronal', orientation: Enums.OrientationAxis.CORONAL },
] as const;

// Reference-line colors: each plane's line is drawn in the *other* two viewports.
const CROSSHAIR_COLORS: Record<string, string> = {
  'mpr-axial': 'rgb(255, 190, 60)',
  'mpr-sagittal': 'rgb(120, 220, 130)',
  'mpr-coronal': 'rgb(120, 180, 255)',
};

interface MprViewportProps {
  /** Ordered slice URLs for the series — a volume needs the whole stack. */
  imageIds: string[];
  voi: VoiRange | null;
  /** Picks the 3D transfer-function preset (CT vs MR). */
  modality?: string;
  /** Latest volumetric segmentation, shown as a labelmap over all three planes. */
  segmentation?: Segmentation | null;
  segmentVisible?: boolean;
  onError: (message: string) => void;
}

/**
 * Multiplanar reconstruction + 3D volume rendering: one GPU volume, resliced into three
 * orthogonal views, plus a volume-rendered fourth viewport.
 *
 * The slices are composed into a single 3D texture using each file's
 * `ImagePositionPatient` / `ImageOrientationPatient` / `PixelSpacing`, so sagittal and
 * coronal are genuine reconstructions through the voxel grid rather than separate images.
 * All four viewports share that one texture — this is why four views cost roughly the
 * memory of one.
 */
export function MprViewport({
  imageIds,
  voi,
  modality,
  segmentation,
  segmentVisible = true,
  onError,
}: MprViewportProps) {
  const refs = useRef<(HTMLDivElement | null)[]>([]);
  const engineRef = useRef<RenderingEngine | null>(null);
  const [ready, setReady] = useState(false);
  const [crosshairs, setCrosshairs] = useState(false);
  const [sliceInfo, setSliceInfo] = useState<Record<string, { index: number; total: number }>>({});

  useEffect(() => {
    let cancelled = false;
    const elements = refs.current.slice(0, PLANES.length + 1);
    if (elements.some((el) => !el)) return;

    (async () => {
      try {
        await initCornerstone();
        if (cancelled) return;

        const engine = new RenderingEngine(ENGINE_ID);
        engineRef.current = engine;

        engine.setViewports([
          ...PLANES.map((plane, i) => ({
            viewportId: plane.id,
            type: Enums.ViewportType.ORTHOGRAPHIC,
            element: elements[i] as HTMLDivElement,
            defaultOptions: {
              orientation: plane.orientation,
              background: [0, 0, 0] as Types.Point3,
            },
          })),
          {
            viewportId: VOLUME_3D_ID,
            type: Enums.ViewportType.VOLUME_3D,
            element: elements[PLANES.length] as HTMLDivElement,
            defaultOptions: {
              orientation: Enums.OrientationAxis.CORONAL,
              background: [0.016, 0.027, 0.043] as Types.Point3,
            },
          },
        ]);

        // Decodes every slice and builds the voxel grid; this is the expensive step.
        const volume = await volumeLoader.createAndCacheVolume(VOLUME_ID, { imageIds });
        await (volume as unknown as { load: () => Promise<void> }).load();
        if (cancelled) return;

        await setVolumesForViewports(
          engine,
          [{ volumeId: VOLUME_ID }],
          [...PLANES.map((p) => p.id), VOLUME_3D_ID],
        );

        // Wheel scroll / window-level / zoom / pan on the planes; rotate on the 3D view.
        attachViewportTools(MPR_TOOLS_ID, ENGINE_ID, PLANES.map((p) => p.id));
        addCrosshairs(MPR_TOOLS_ID, CROSSHAIR_COLORS);
        attachVolume3dTools(VOLUME_3D_TOOLS_ID, ENGINE_ID, VOLUME_3D_ID);

        // The preset replaces plain slice sampling with a transfer function, which is
        // what makes the fourth viewport read as a rendered body rather than a slab.
        try {
          const vp3d = engine.getViewport(VOLUME_3D_ID) as Types.IVolumeViewport;
          vp3d.setProperties({ preset: modality === 'MR' ? 'MR-Default' : 'CT-Bone' });
        } catch {
          // Preset unavailable: the 3D viewport still renders, just without the styling.
        }

        engine.renderViewports([...PLANES.map((p) => p.id), VOLUME_3D_ID]);

        if (!cancelled) setReady(true);
      } catch (err) {
        if (!cancelled) {
          onError(err instanceof Error ? err.message : 'Could not reconstruct this volume.');
        }
      }
    })();

    return () => {
      cancelled = true;
      clearSegmentationLabelmaps();
      try {
        engineRef.current?.destroy();
      } catch {
        // Already torn down by a fast route change.
      }
      engineRef.current = null;
      setReady(false);
      setCrosshairs(false);
      setSliceInfo({});
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [imageIds.join('|')]);

  // Per-pane slice counter, fed by the scroll events each viewport emits.
  useEffect(() => {
    if (!ready) return;
    const elements = refs.current.slice(0, PLANES.length);
    const handler = (event: Event) => {
      const detail = (event as CustomEvent<{
        imageIndex: number;
        numberOfSlices: number;
        viewportId: string;
      }>).detail;
      if (!detail?.viewportId) return;
      setSliceInfo((current) => ({
        ...current,
        [detail.viewportId]: { index: detail.imageIndex, total: detail.numberOfSlices },
      }));
    };
    for (const el of elements) el?.addEventListener(Enums.Events.VOLUME_NEW_IMAGE, handler);
    return () => {
      for (const el of elements) el?.removeEventListener(Enums.Events.VOLUME_NEW_IMAGE, handler);
    };
  }, [ready]);

  // The volumetric mask reslices with the image: one labelmap volume shared by the
  // planes (and, best-effort, the 3D view). Cleared and rebuilt when the segmentation
  // changes so a stale mask never lingers over a new result.
  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    (async () => {
      clearSegmentationLabelmaps();
      if (!segmentation?.mask_url || !segmentVisible) {
        engineRef.current?.renderViewports(PLANES.map((p) => p.id));
        return;
      }
      try {
        const shown = await showSegmentationLabelmap({
          seg: segmentation,
          sourceVolumeId: VOLUME_ID,
          sourceImageIds: imageIds,
          viewportIds: PLANES.map((p) => p.id),
        });
        if (cancelled || !shown) return;
        // Labelmap on the volume-rendered view is the flakiest part of the stack —
        // attempted separately so a failure cannot take the plane overlays down.
        try {
          await showSegmentationLabelmap({
            seg: segmentation,
            sourceVolumeId: VOLUME_ID,
            sourceImageIds: imageIds,
            viewportIds: [VOLUME_3D_ID],
          });
        } catch {
          // 3D labelmap unsupported for this volume; the MPR overlays still shown.
        }
        const renderAll = () =>
          engineRef.current?.renderViewports([...PLANES.map((p) => p.id), VOLUME_3D_ID]);
        renderAll();
        // The labelmap actor is wired up asynchronously after the representation is
        // registered; without a follow-up render the mask stays invisible until the
        // next interaction repaints the viewports.
        setTimeout(() => {
          if (!cancelled) renderAll();
        }, 300);
      } catch (err) {
        console.warn('Labelmap display failed:', err);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, segmentation?.id, segmentation?.mask_url, segmentVisible]);

  // Window/level applies to the shared volume, so all three planes stay consistent.
  // The 3D viewport is excluded: its preset owns the transfer function.
  useEffect(() => {
    const engine = engineRef.current;
    if (!ready || !engine || !voi) return;
    const voiRange = {
      lower: voi.windowCenter - voi.windowWidth / 2,
      upper: voi.windowCenter + voi.windowWidth / 2,
    };
    for (const plane of PLANES) {
      const vp = engine.getViewport(plane.id) as Types.IVolumeViewport | undefined;
      vp?.setProperties({ voiRange }, VOLUME_ID);
    }
    engine.renderViewports(PLANES.map((p) => p.id));
  }, [ready, voi]);

  const toggleCrosshairs = () => {
    const next = !crosshairs;
    setCrosshairs(next);
    setCrosshairsActive(MPR_TOOLS_ID, next);
    engineRef.current?.renderViewports(PLANES.map((p) => p.id));
  };

  return (
    <div className="relative grid h-full grid-cols-2 grid-rows-2 gap-1 bg-black p-1">
      {PLANES.map((plane, i) => (
        <div key={plane.id} className="relative min-h-0 overflow-hidden rounded border border-slate-800">
          <div
            ref={(el) => {
              refs.current[i] = el;
            }}
            onContextMenu={(e) => e.preventDefault()}
            className="absolute inset-0 h-full w-full"
          />
          <span
            className="pointer-events-none absolute left-2 top-2 rounded bg-black/60 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em]"
            style={{ color: CROSSHAIR_COLORS[plane.id] }}
          >
            {plane.label}
          </span>
          {sliceInfo[plane.id] && (
            <span className="pointer-events-none absolute bottom-2 right-2 rounded bg-black/60 px-2 py-0.5 text-[10px] tabular-nums text-slate-300">
              {sliceInfo[plane.id].index + 1} / {sliceInfo[plane.id].total}
            </span>
          )}
        </div>
      ))}

      <div className="relative min-h-0 overflow-hidden rounded border border-slate-800">
        <div
          ref={(el) => {
            refs.current[PLANES.length] = el;
          }}
          onContextMenu={(e) => e.preventDefault()}
          className="absolute inset-0 h-full w-full"
        />
        <span className="pointer-events-none absolute left-2 top-2 rounded bg-black/60 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-cyan-100">
          3D volume
        </span>
        {ready && (
          <span className="pointer-events-none absolute bottom-2 right-2 rounded bg-black/60 px-2 py-0.5 text-[10px] text-slate-400">
            drag to rotate
          </span>
        )}
        {!ready && (
          <p className="absolute inset-0 grid place-items-center text-xs text-slate-500">
            Building volume…
          </p>
        )}
      </div>

      <button
        className={cn(
          'absolute right-3 top-3 inline-flex h-8 items-center gap-2 rounded-md border px-2.5 text-xs font-medium transition',
          crosshairs
            ? 'border-cyan-300/40 bg-cyan-400/10 text-cyan-100'
            : 'border-slate-700 bg-slate-950/80 text-slate-400',
        )}
        type="button"
        disabled={!ready}
        title={
          crosshairs
            ? 'Crosshairs on: left-drag moves the linked cursor. Turn off to restore window/level drag.'
            : 'Link the three planes with a shared crosshair cursor (left-drag)'
        }
        onClick={toggleCrosshairs}
      >
        <Crosshair className="h-3.5 w-3.5" />
        Crosshairs
      </button>
    </div>
  );
}
