import { useEffect, useRef, useState } from 'react';
import { Enums, RenderingEngine, type Types } from '@cornerstonejs/core';
import { buildImageIds, initCornerstone } from '@/lib/cornerstone';

const RENDERING_ENGINE_ID = 'medtrace-imaging';
const VIEWPORT_ID = 'medtrace-viewport';

export interface VoiRange {
  windowCenter: number;
  windowWidth: number;
}

interface DicomViewportProps {
  dicomUrl: string;
  sliceUrls?: string[];
  /** Frame count; >1 renders as a scrollable stack. */
  frames: number;
  sliceIndex: number;
  /** null = use the window/level baked into the DICOM. */
  voi: VoiRange | null;
  zoom: number;
  onLoaded: (info: { defaultVoi: VoiRange | null }) => void;
  onError: (message: string) => void;
}

/**
 * GPU-rendered DICOM viewport.
 *
 * A Cornerstone StackViewport holds the decoded frames; window/level is applied as a
 * shader property (`voiRange`) on the original pixel values — not a CSS filter over a
 * pre-flattened image.
 */
export function DicomViewport({
  dicomUrl,
  sliceUrls,
  frames,
  sliceIndex,
  voi,
  zoom,
  onLoaded,
  onError,
}: DicomViewportProps) {
  const elementRef = useRef<HTMLDivElement>(null);
  const engineRef = useRef<RenderingEngine | null>(null);
  const viewportRef = useRef<Types.IStackViewport | null>(null);
  const [ready, setReady] = useState(false);

  // Create the engine + load the stack. Re-runs only when the study changes.
  useEffect(() => {
    let cancelled = false;
    const element = elementRef.current;
    if (!element) return;

    (async () => {
      try {
        await initCornerstone();
        if (cancelled) return;

        const engine = new RenderingEngine(RENDERING_ENGINE_ID);
        engineRef.current = engine;
        engine.enableElement({
          viewportId: VIEWPORT_ID,
          type: Enums.ViewportType.STACK,
          element,
          defaultOptions: { background: [0, 0, 0] as Types.Point3 },
        });

        const viewport = engine.getViewport(VIEWPORT_ID) as Types.IStackViewport;
        viewportRef.current = viewport;

        await viewport.setStack(buildImageIds(dicomUrl, frames, sliceUrls), 0);
        viewport.render();
        if (cancelled) return;

        // Surface the file's own window/level so the UI sliders start where the
        // radiologist's defaults are, not at an arbitrary midpoint.
        const props = viewport.getProperties();
        const range = props.voiRange;
        onLoaded({
          defaultVoi: range
            ? {
                windowCenter: (range.upper + range.lower) / 2,
                windowWidth: range.upper - range.lower,
              }
            : null,
        });
        setReady(true);
      } catch (err) {
        if (!cancelled) {
          onError(err instanceof Error ? err.message : 'Could not render this DICOM.');
        }
      }
    })();

    return () => {
      cancelled = true;
      try {
        engineRef.current?.destroy();
      } catch {
        // Engine may already be torn down by a fast route change.
      }
      engineRef.current = null;
      viewportRef.current = null;
      setReady(false);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dicomUrl, frames]);

  // Slice scrubbing.
  useEffect(() => {
    const viewport = viewportRef.current;
    if (!ready || !viewport || frames <= 1) return;
    const clamped = Math.max(0, Math.min(frames - 1, sliceIndex));
    if (viewport.getCurrentImageIdIndex() === clamped) return;
    void viewport.setImageIdIndex(clamped);
  }, [ready, sliceIndex, frames]);

  // Real window/level: a shader range over the source pixel values.
  useEffect(() => {
    const viewport = viewportRef.current;
    if (!ready || !viewport || !voi) return;
    viewport.setProperties({
      voiRange: {
        lower: voi.windowCenter - voi.windowWidth / 2,
        upper: voi.windowCenter + voi.windowWidth / 2,
      },
    });
    viewport.render();
  }, [ready, voi]);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!ready || !viewport) return;
    viewport.setZoom(zoom / 100);
    viewport.render();
  }, [ready, zoom]);

  return (
    <div
      ref={elementRef}
      // Cornerstone attaches its own canvas here and handles its own context menu.
      onContextMenu={(e) => e.preventDefault()}
      className="absolute inset-0 h-full w-full"
    />
  );
}
