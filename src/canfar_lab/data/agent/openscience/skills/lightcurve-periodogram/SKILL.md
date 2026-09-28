---
name: lightcurve-periodogram
description: Time-series analysis of astronomical light curves — Lomb-Scargle and box least squares periodograms, phase folding, and period uncertainties. Use for variable stars, rotation periods, eclipsing binaries, exoplanet transits (TESS, Kepler, ZTF, Gaia epoch photometry).
summary: "Lomb-Scargle and BLS periods, phase folding and period errors for light curves."
category: astronomy
metadata:
  maintainer: canfar-lab
---

# Light curves and periods

## Prepare

- Put times in a single scale and reference: BJD_TDB for TESS/Kepler (`TIME` column is
  BTJD = BJD − 2457000), MJD for ZTF. Never mix scales.
- Remove flagged cadences (`QUALITY != 0`), NaNs, and obvious outliers (sigma-clip the
  flux, not the time).
- Normalise each sector/season separately before combining.

## Lomb-Scargle (sinusoidal variability)

```python
import numpy as np
from astropy.timeseries import LombScargle

ls = LombScargle(t, y, dy)
freq, power = ls.autopower(minimum_frequency=1 / 30, maximum_frequency=24, samples_per_peak=10)
best = freq[np.argmax(power)]
period = 1 / best
fap = ls.false_alarm_probability(power.max())
```

- Units follow `t`: with `t` in days, frequencies are 1/day.
- Check aliases: `1/P ± 1/day` (ground-based), `1/P ± 1/13.7 d` (TESS orbit), and
  `2P` for eclipsing binaries (Lomb-Scargle often finds half the true period).

## Box least squares (transits, eclipses)

```python
from astropy.timeseries import BoxLeastSquares
bls = BoxLeastSquares(t, y, dy)
res = bls.autopower(0.1)                     # duration in days
P = res.period[np.argmax(res.power)]
```

## Uncertainty and validation

- Period uncertainty ≈ P² · σ_f where σ_f is the peak width (or bootstrap).
- Phase-fold (`(t - t0) / P % 1`) and plot; a correct period gives a clean folded curve.
- Validate the pipeline on a known variable first (e.g. a catalogued RR Lyrae from
  VizieR/Gaia DR3 `vari_rrlyrae`) and require agreement within 1%.
- TESS/Kepler data: download light curves from MAST (`astroai_tap_query` with
  `service: mast` to find products, or `lightkurve` when installed).
