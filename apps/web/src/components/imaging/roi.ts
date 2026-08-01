import type { RoiBox } from '@/lib/types';

/** Preset ROI for the "run with default ROI" toolbar action. */
export const DEFAULT_ROI: RoiBox = { x: 0.47, y: 0.34, width: 0.16, height: 0.22 };

/** Drags smaller than this in either axis are treated as a click, not an ROI. */
export const MIN_ROI_SIZE = 0.025;

export function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

/** Build a normalised (0–1) box from two drag corners, in any order. */
export function normalizeRoi(
  start: { x: number; y: number },
  end: { x: number; y: number },
): RoiBox {
  return {
    x: clamp01(Math.min(start.x, end.x)),
    y: clamp01(Math.min(start.y, end.y)),
    width: clamp01(Math.abs(end.x - start.x)),
    height: clamp01(Math.abs(end.y - start.y)),
  };
}

export function isUsableRoi(box: RoiBox): boolean {
  return box.width >= MIN_ROI_SIZE && box.height >= MIN_ROI_SIZE;
}

const DICOM_EXTENSIONS = ['.dcm', '.dicom'];

export function isDicomFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return DICOM_EXTENSIONS.some((ext) => name.endsWith(ext)) || file.type === 'application/dicom';
}
