/**
 * Volumetric segmentation display: raw mask bytes → Cornerstone3D labelmap.
 *
 * The API stores each volumetric mask as `mask.bin` — uint8 `[depth, rows, cols]` in
 * C order, which is byte-for-byte the layout Cornerstone3D uses for a volume's scalar
 * data (x fastest, then y, then z). So the whole file is fetched once and copied into a
 * labelmap volume derived from the study volume; the ortho viewports then reslice the
 * mask exactly like they reslice the image, and the mask stays aligned in all planes.
 */

import { cache, volumeLoader } from '@cornerstonejs/core';
import { Enums as ToolEnums, segmentation } from '@cornerstonejs/tools';
import type { Segmentation } from './types';

const LABELMAP_PREFIX = 'medtrace-labelmap:';
//: Same cyan as the 2D overlays, so the mask reads as one thing across layouts.
const MASK_COLOR: [number, number, number, number] = [34, 211, 238, 255];

/** Labelmap volumes created this session, so teardown can purge them from the cache. */
const createdLabelmaps = new Set<string>();

export async function showSegmentationLabelmap({
  seg,
  sourceVolumeId,
  sourceImageIds,
  viewportIds,
}: {
  seg: Segmentation;
  /** The already-built study volume the labelmap derives its geometry from. */
  sourceVolumeId: string;
  /** The imageIds the study volume was built from, in the API's anatomical order. */
  sourceImageIds: string[];
  viewportIds: string[];
}): Promise<boolean> {
  const shape = seg.mask_shape;
  if (!seg.mask_url || !shape || shape.length !== 3) return false;

  const [depth, rows, cols] = shape;
  const response = await fetch(seg.mask_url);
  if (!response.ok) return false;
  const bytes = new Uint8Array(await response.arrayBuffer());
  if (bytes.length !== depth * rows * cols) {
    console.warn(
      `Segmentation mask has ${bytes.length} bytes, expected ${depth * rows * cols} — skipping labelmap.`,
    );
    return false;
  }

  const sourceVolume = cache.getVolume(sourceVolumeId);
  if (!sourceVolume) return false;
  const [x, y, z] = sourceVolume.dimensions;
  if (x !== cols || y !== rows || z !== depth) {
    console.warn(
      `Mask grid [${depth},${rows},${cols}] does not match volume [${z},${y},${x}] — skipping labelmap.`,
    );
    return false;
  }

  // Cornerstone sorts slices along the scan axis itself and may settle on the opposite
  // direction from the API's ordering; in that case the mask's z axis must be flipped
  // or the labelmap would be mirrored head-to-foot.
  const volumeImageIds: string[] = (sourceVolume as { imageIds?: string[] }).imageIds ?? [];
  const flipped =
    volumeImageIds.length > 1 &&
    sourceImageIds.length === volumeImageIds.length &&
    volumeImageIds[0] === sourceImageIds[sourceImageIds.length - 1];

  let data = bytes;
  if (flipped) {
    data = new Uint8Array(bytes.length);
    const sliceSize = rows * cols;
    for (let d = 0; d < depth; d += 1) {
      data.set(bytes.subarray(d * sliceSize, (d + 1) * sliceSize), (depth - 1 - d) * sliceSize);
    }
  }

  const labelmapId = `${LABELMAP_PREFIX}${seg.id}${flipped ? ':flipped' : ''}`;
  if (!cache.getVolume(labelmapId)) {
    volumeLoader.createAndCacheDerivedLabelmapVolume(sourceVolumeId, { volumeId: labelmapId });
    createdLabelmaps.add(labelmapId);
  }
  const labelmap = cache.getVolume(labelmapId);
  labelmap?.voxelManager?.setCompleteScalarDataArray?.(data);

  if (!segmentation.state.getSegmentation(seg.id)) {
    segmentation.addSegmentations([
      {
        segmentationId: seg.id,
        representation: {
          type: ToolEnums.SegmentationRepresentations.Labelmap,
          data: { volumeId: labelmapId },
        },
      },
    ]);
  }

  await segmentation.addLabelmapRepresentationToViewportMap(
    Object.fromEntries(viewportIds.map((id) => [id, [{ segmentationId: seg.id }]])),
  );

  for (const viewportId of viewportIds) {
    try {
      segmentation.config.color.setSegmentIndexColor(viewportId, seg.id, 1, MASK_COLOR);
    } catch {
      // Default colormap is acceptable; color is cosmetic.
    }
  }
  segmentation.config.style.setStyle(
    { type: ToolEnums.SegmentationRepresentations.Labelmap },
    { fillAlpha: 0.4, outlineWidth: 2 },
  );
  return true;
}

/** Remove every segmentation and purge the labelmap volumes this module created. */
export function clearSegmentationLabelmaps(): void {
  try {
    segmentation.removeAllSegmentationRepresentations();
    segmentation.removeAllSegmentations();
  } catch {
    // Segmentation state may already be gone after an engine teardown.
  }
  for (const volumeId of createdLabelmaps) {
    try {
      cache.removeVolumeLoadObject(volumeId);
    } catch {
      // Not cached (evicted or never fully created).
    }
  }
  createdLabelmaps.clear();
}
