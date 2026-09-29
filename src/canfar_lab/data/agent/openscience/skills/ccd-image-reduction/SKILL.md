---
name: ccd-image-reduction
description: Reduce raw optical/near-IR CCD frames with ccdproc — overscan, bias, dark, flat, gain, cosmic rays, and combining — and know when archive data is already calibrated (CFHT Elixir, MegaPipe, Gemini/HST pipeline products). Use when the user has raw frames (bias/dark/flat/science), asks to calibrate or stack images, or sees instrumental artefacts.
summary: "Overscan, bias, dark, flat and cosmic-ray reduction with ccdproc."
category: astronomy
metadata:
  maintainer: canfar-lab
---

# CCD reduction with ccdproc

## First: do you need to reduce at all?

- CADC `calib_level` 2/3 products are already calibrated. CFHT MegaCam `…p.fits.fz` files
  are Elixir-processed (bias, flat, astrometry/photometry keywords); `…o.fits.fz` are raw.
  MegaPipe provides calibrated stacks. HST/JWST and Gemini offer pipeline products.
- Reduce yourself only from raw frames, with calibrations from the same night/run,
  binning, readout mode and (for flats) filter.

## Organise

```python
from ccdproc import ImageFileCollection
ic = ImageFileCollection("raw/", keywords=["imagetyp", "filter", "exptime", "object"])
bias_files = ic.files_filtered(imagetyp="BIAS", include_path=True)
```

Keyword names and values vary by instrument: print `ic.summary` and check the headers
before filtering. Multi-extension files (one HDU per CCD) need `CCDData.read(f, hdu=n)`
per extension, with calibrations per extension.

## Pipeline

```python
import numpy as np
import astropy.units as u
import ccdproc
from astropy.nddata import CCDData

def prep(ccd):  # overscan and trim sections come from BIASSEC/DATASEC or the manual
    ccd = ccdproc.subtract_overscan(ccd, fits_section="[2049:2080, :]", median=True, overscan_axis=1)
    return ccdproc.trim_image(ccd, fits_section="[1:2048, :]")

read = lambda f: prep(CCDData.read(f, unit="adu"))
master_bias = ccdproc.combine([read(f) for f in bias_files], method="median", sigma_clip=True)

darks = [ccdproc.subtract_bias(read(f), master_bias) for f in dark_files]
master_dark = ccdproc.combine(darks, method="median", sigma_clip=True)

def debias_dark(ccd):
    ccd = ccdproc.subtract_bias(ccd, master_bias)
    return ccdproc.subtract_dark(ccd, master_dark, exposure_time="exptime",
                                 exposure_unit=u.s, scale=True)

flats = [debias_dark(read(f)) for f in flat_files]          # one filter at a time
master_flat = ccdproc.combine(flats, method="median", sigma_clip=True,
                              scale=lambda a: 1 / np.median(a))  # normalise each flat

sci = ccdproc.flat_correct(debias_dark(read(sci_file)), master_flat)
sci = ccdproc.gain_correct(sci, gain * u.electron / u.adu)  # gain from header/manual
sci = ccdproc.cosmicray_lacosmic(sci, sigclip=5, readnoise=readnoise_e, gain_apply=False)
sci.write("reduced/sci_001.fits", overwrite=True)
```

- Skip the dark step when dark current is negligible for the exposure (most optical
  CCDs); keep it for near-IR arrays and long exposures.
- `cosmicray_lacosmic` sets `mask` on the cleaned pixels; propagate masks into photometry.
- Combine science frames only after registration (reproject/WCS alignment) and with
  per-frame scaling to a common sky/zero point.

## Checks

- Master bias: mean near the overscan-corrected zero, noise ≈ 1.25 · (readnoise/gain)/√N
  ADU for a median combine (readnoise/√N for a mean).
- Master flat: normalised to ~1, no stars (use a dithered twilight/dome set with
  sigma-clipping), dust donuts expected.
- Reduced science: flat sky across the chip (compare corner medians); sky level in e⁻
  consistent with exposure time and filter; count bad/masked pixels.
- Keep raw data read-only; write products to a new directory with a log of the steps.
