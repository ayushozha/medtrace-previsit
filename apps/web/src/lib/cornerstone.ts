/**
 * Cornerstone3D bootstrap.
 *
 * The imaging viewport renders the *original* DICOM in the browser on the GPU
 * (Cornerstone3D → vtk.js → WebGL) rather than displaying a server-rendered PNG. That is
 * what makes real window/level possible: the shader works on the full 12/16-bit pixel
 * data, so re-windowing recovers detail an 8-bit preview has already thrown away.
 *
 * Init is idempotent and lazy — the imaging route is the only caller, and it must not run
 * during SSR-less module import because it registers Web Workers.
 */

import { init as coreInit } from '@cornerstonejs/core';
import dicomImageLoader from '@cornerstonejs/dicom-image-loader';
import {
  Enums as ToolEnums,
  PanTool,
  StackScrollTool,
  ToolGroupManager,
  WindowLevelTool,
  ZoomTool,
  addTool,
  init as toolsInit,
} from '@cornerstonejs/tools';

let ready: Promise<void> | null = null;

export function initCornerstone(): Promise<void> {
  ready ??= (async () => {
    await coreInit();
    // Registers the wadouri:/wadors: image loaders and their decode workers.
    //
    // `useLegacyMetadataProvider` picks the classic `loadImage` path (dataSetCacheManager →
    // loadImageFromDataSet). The v5.6 default registers `loadImageFromNaturalizedMetadata`,
    // which reads frames from a COMPRESSED_FRAME_DATA module and therefore throws
    // "no pixel data in NATURALIZED" for uncompressed transfer syntaxes — which is most
    // plain DICOM, including everything we store from the upload route.
    dicomImageLoader.init({ useLegacyMetadataProvider: true });

    // Interaction lives in a separate package; without it the viewports render but do not
    // respond to the mouse at all — no scrolling through slices, no drag window/level.
    toolsInit();
    addTool(StackScrollTool);
    addTool(WindowLevelTool);
    addTool(ZoomTool);
    addTool(PanTool);
  })();
  return ready;
}

/**
 * Bind the standard radiology mouse bindings to a set of viewports.
 *
 * Wheel scrolls through slices, left-drag adjusts window/level, right-drag zooms and
 * middle-drag pans — the conventions a radiologist expects from any PACS viewer.
 */
export function attachViewportTools(
  toolGroupId: string,
  renderingEngineId: string,
  viewportIds: string[],
): void {
  // Recreate per mount: a stale group holds destroyed viewport ids.
  ToolGroupManager.destroyToolGroup(toolGroupId);
  const group = ToolGroupManager.createToolGroup(toolGroupId);
  if (!group) return;

  for (const tool of [StackScrollTool, WindowLevelTool, ZoomTool, PanTool]) {
    group.addTool(tool.toolName);
  }
  group.setToolActive(StackScrollTool.toolName, {
    bindings: [{ mouseButton: ToolEnums.MouseBindings.Wheel }],
  });
  group.setToolActive(WindowLevelTool.toolName, {
    bindings: [{ mouseButton: ToolEnums.MouseBindings.Primary }],
  });
  group.setToolActive(ZoomTool.toolName, {
    bindings: [{ mouseButton: ToolEnums.MouseBindings.Secondary }],
  });
  group.setToolActive(PanTool.toolName, {
    bindings: [{ mouseButton: ToolEnums.MouseBindings.Auxiliary }],
  });

  for (const viewportId of viewportIds) {
    group.addViewport(viewportId, renderingEngineId);
  }
}

/**
 * Build the Cornerstone imageIds for a study.
 *
 * `wadouri:` fetches a plain DICOM file over HTTP — no PACS or DICOMweb server needed,
 * which is why the API's `/data` static mount is enough. Multi-frame studies address each
 * frame with `?frame=N`, giving one imageId per slice.
 *
 * **`frame` is 1-based.** `parseImageId` computes `pixelDataFrame = frame - 1`, so a
 * 0-based value yields frame index -1 and the loader reports "no pixel data".
 */
export function buildImageIds(
  dicomUrl: string,
  frames: number,
  sliceUrls?: string[],
): string[] {
  // A series: one imageId per file, already in anatomical order from the API.
  if (sliceUrls && sliceUrls.length > 1) return sliceUrls.map((u) => `wadouri:${u}`);
  if (frames <= 1) return [`wadouri:${dicomUrl}`];
  // A single multi-frame file: address each frame of the same file.
  return Array.from({ length: frames }, (_, i) => `wadouri:${dicomUrl}?frame=${i + 1}`);
}
