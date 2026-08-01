import { useEffect, useRef, useState } from 'react';
import {
  Enums,
  RenderingEngine,
  setVolumesForViewports,
  volumeLoader,
  type Types,
} from '@cornerstonejs/core';
import { attachViewportTools, initCornerstone } from '@/lib/cornerstone';
import type { VoiRange } from './DicomViewport';

const ENGINE_ID = 'medtrace-mpr';
const VOLUME_ID = 'cornerstoneStreamingImageVolume:medtrace';

const PLANES = [
  { id: 'mpr-axial', label: 'Axial', orientation: Enums.OrientationAxis.AXIAL },
  { id: 'mpr-sagittal', label: 'Sagittal', orientation: Enums.OrientationAxis.SAGITTAL },
  { id: 'mpr-coronal', label: 'Coronal', orientation: Enums.OrientationAxis.CORONAL },
] as const;

interface MprViewportProps {
  /** Ordered slice URLs for the series — a volume needs the whole stack. */
  imageIds: string[];
  voi: VoiRange | null;
  onError: (message: string) => void;
}

/**
 * Multiplanar reconstruction: one GPU volume, resliced into three orthogonal views.
 *
 * The slices are composed into a single 3D texture using each file's
 * `ImagePositionPatient` / `ImageOrientationPatient` / `PixelSpacing`, so sagittal and
 * coronal are genuine reconstructions through the voxel grid rather than separate images.
 * All three viewports share that one texture — this is why three planes cost roughly the
 * memory of one.
 */
export function MprViewport({ imageIds, voi, onError }: MprViewportProps) {
  const refs = useRef<(HTMLDivElement | null)[]>([]);
  const engineRef = useRef<RenderingEngine | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const elements = refs.current.slice(0, PLANES.length);
    if (elements.some((el) => !el)) return;

    (async () => {
      try {
        await initCornerstone();
        if (cancelled) return;

        const engine = new RenderingEngine(ENGINE_ID);
        engineRef.current = engine;

        engine.setViewports(
          PLANES.map((plane, i) => ({
            viewportId: plane.id,
            type: Enums.ViewportType.ORTHOGRAPHIC,
            element: elements[i] as HTMLDivElement,
            defaultOptions: {
              orientation: plane.orientation,
              background: [0, 0, 0] as Types.Point3,
            },
          })),
        );

        // Decodes every slice and builds the voxel grid; this is the expensive step.
        const volume = await volumeLoader.createAndCacheVolume(VOLUME_ID, { imageIds });
        await (volume as unknown as { load: () => Promise<void> }).load();
        if (cancelled) return;

        await setVolumesForViewports(
          engine,
          [{ volumeId: VOLUME_ID }],
          PLANES.map((p) => p.id),
        );
        // Wheel scroll / window-level / zoom / pan on every plane.
        attachViewportTools('medtrace-mpr-tools', ENGINE_ID, PLANES.map((p) => p.id));

        engine.renderViewports(PLANES.map((p) => p.id));



        if (!cancelled) setReady(true);
      } catch (err) {
        if (!cancelled) {
          onError(err instanceof Error ? err.message : 'Could not reconstruct this volume.');
        }
      }
    })();

    return () => {
      cancelled = true;
      try {
        engineRef.current?.destroy();
      } catch {
        // Already torn down by a fast route change.
      }
      engineRef.current = null;
      setReady(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [imageIds.join('|')]);

  // Window/level applies to the shared volume, so all three planes stay consistent.
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

  return (
    <div className="grid h-full grid-cols-2 grid-rows-2 gap-1 bg-black p-1">
      {PLANES.map((plane, i) => (
        <div key={plane.id} className="relative min-h-0 overflow-hidden rounded border border-slate-800">
          <div
            ref={(el) => {
              refs.current[i] = el;
            }}
            onContextMenu={(e) => e.preventDefault()}
            className="absolute inset-0 h-full w-full"
          />
          <span className="pointer-events-none absolute left-2 top-2 rounded bg-black/60 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-cyan-100">
            {plane.label}
          </span>
        </div>
      ))}

      <div className="grid min-h-0 place-items-center rounded border border-slate-800 bg-[#070d17] p-4 text-center">
        {ready ? (
          <div className="text-xs text-slate-400">
            <p className="font-semibold text-slate-200">Volume reconstructed</p>
            <p className="mt-1">
              {imageIds.length} slices · one GPU texture shared by all three planes
            </p>
            <p className="mt-2 text-slate-500">
              Window/level applies to the whole volume, so all three planes stay consistent.
            </p>
            <p className="mt-1 text-slate-600">
              Per-plane scrolling is not wired up yet — each pane shows its mid-volume slice.
            </p>
          </div>
        ) : (
          <p className="text-xs text-slate-500">Building volume…</p>
        )}
      </div>
    </div>
  );
}
