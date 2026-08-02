# Sample DICOM data

Real, de-identified DICOM studies for exercising the imaging feature (DICOM upload →
volume assembly → MedSAM2 3D segmentation → MPR / 3D volume viewing). These are the
datasets used to validate the volumetric segmentation pipeline end-to-end.

## `lidc_chest_ct.zip` (~71 MB, not committed)

Download locally into `samples/dicom/` (gitignored `*.zip`). Single-series chest CT:
**250 axial slices, 512×512, 0.703 mm in-plane, 1.25 mm slice thickness**, GE Medical
Systems, uncompressed (Implicit VR Little Endian). Full per-slice geometry
(ImagePositionPatient / ImageOrientationPatient / PixelSpacing), so it drives MPR and
the 3D volume viewport.

- **Source:** [Saga IT DICOM samples](https://saga-it.com/dicom/samples) →
  `ct-chest-lidc-idri`, derived from the public **LIDC-IDRI** collection on The Cancer
  Imaging Archive (TCIA).
- **License / use:** de-identified per HIPAA Safe Harbor; provided for testing,
  development, and education.

### Use it

```bash
# Place the zip at samples/dicom/lidc_chest_ct.zip, then:
unzip samples/dicom/lidc_chest_ct.zip -d /tmp/lidc

# Option A — select a synthetic patient at http://localhost:3000/patients,
#   open that patient's Imaging tab, then upload every .dcm in the series.

# Option B — straight to the API (npm run dev:api must be running):
curl -s -X POST http://127.0.0.1:8001/api/studies \
  -F patient_id=<synthetic-medplum-patient-id> \
  $(for f in /tmp/lidc/**/*.dcm; do printf -- '-F files=@%s ' "$f"; done)
# → returns { id, slices: 250, has_volume_geometry: true, ... }
```

Then draw an ROI and run MedSAM2, or switch to **MPR 3D** for the axial/sagittal/coronal
reconstructions plus the volume-rendered 3D viewport.

## Notes

- **Compressed studies need pixel decoders.** This series is uncompressed, but most
  clinical DICOM is JPEG-Lossless / JPEG-LS / JPEG 2000. The `imaging` extra installs
  `pylibjpeg*` so server-side volume assembly can decode those (`pip install -e ".[imaging]"`).
- The repo-wide `.gitignore` rule `*.dcm` intentionally excludes loose DICOM files (runtime
  uploads under `data/studies/`), so sample data is committed as a `.zip` instead. The zip
  is ~71 MB — if you'd rather not carry that in git history, track it with Git LFS or keep
  it out of the commit and download it on demand from the source link above.
