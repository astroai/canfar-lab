---
name: photometry-aperture
description: Aperture photometry on astronomical images with photutils — source detection, background subtraction, aperture/annulus sums, zero points and magnitudes with uncertainties. Use when the user wants fluxes, magnitudes, light curves from images, or a source catalogue from an image.
summary: "Source detection, background and aperture photometry with photutils."
category: astronomy
metadata:
  maintainer: canfar-lab
---

# Aperture photometry

## Steps

1. **Background**: estimate and subtract a 2D background.

   ```python
   from astropy.stats import SigmaClip
   from photutils.background import Background2D, MedianBackground

   bkg = Background2D(data, box_size=64, filter_size=3,
                      sigma_clip=SigmaClip(sigma=3.0), bkg_estimator=MedianBackground())
   sub = data - bkg.background
   ```

2. **Detect sources** (or use catalogue positions via the WCS):

   ```python
   from photutils.detection import DAOStarFinder
   from astropy.stats import sigma_clipped_stats

   mean, median, std = sigma_clipped_stats(sub, sigma=3.0)
   sources = DAOStarFinder(fwhm=fwhm_pix, threshold=5 * std)(sub)
   ```

   Estimate `fwhm_pix` from the header seeing (arcsec) / pixel scale (arcsec/pix).

3. **Measure** with an aperture ~1–1.5 × FWHM radius and a local annulus:

   ```python
   import numpy as np
   from photutils.aperture import CircularAperture, CircularAnnulus, ApertureStats, aperture_photometry

   pos = np.transpose([sources["xcentroid"], sources["ycentroid"]])
   ap = CircularAperture(pos, r=1.5 * fwhm_pix)
   ann = CircularAnnulus(pos, r_in=3 * fwhm_pix, r_out=5 * fwhm_pix)
   local = ApertureStats(data, ann).median          # per-source local background
   phot = aperture_photometry(data, ap, error=err)
   flux = phot["aperture_sum"] - local * ap.area
   ```

   `err` should combine background noise and Poisson noise:
   `err = np.sqrt(bkg.background_rms**2 + np.clip(sub, 0, None) / gain)` (data in ADU,
   `gain` in e-/ADU).

4. **Calibrate**: `mag = -2.5 * log10(flux / exptime) + ZP` when the zero point is per
   second, or without `exptime` when it is per image — check the header (`PHOTZP`,
   `MAGZP`, `EXPTIME`). Magnitude error ≈ 1.0857 × flux_err / flux.

## Sanity checks

- Compare a few bright unsaturated stars against Gaia or Pan-STARRS magnitudes (via
  `astroai_tap_query`); the median offset should be ≲ 0.05 mag after colour terms.
- Flag saturated sources (peak near `SATURATE`) and sources near edges/masked pixels.
- Aperture corrections matter for small apertures: measure a curve of growth on isolated
  bright stars.
