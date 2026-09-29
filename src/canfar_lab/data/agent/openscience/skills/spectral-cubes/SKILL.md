---
name: spectral-cubes
description: Analyse radio/sub-mm and IFU data cubes with spectral-cube and radio-beam — velocity axes and conventions, spectral slabs, moment maps, line widths, beams, K ↔ Jy/beam conversion, and JCMT HARP/ALMA/VLA specifics. Use for position–position–velocity cubes, molecular line maps (CO, HCN, …), HI data, or IFU cubes.
summary: "Velocity axes, moment maps, beams and K to Jy/beam with spectral-cube."
category: astronomy
metadata:
  maintainer: canfar-lab
---

# Spectral cubes

## Load and set the velocity axis

```python
import astropy.units as u
from spectral_cube import SpectralCube

cube = SpectralCube.read("cube.fits")
print(cube)                      # shape, units, spectral axis, beam
vcube = cube.with_spectral_unit(u.km / u.s, velocity_convention="radio",
                                rest_value=345.79599 * u.GHz)   # CO(3–2)
```

- Always state the velocity convention (radio for mm/sub-mm, optical for many
  optical/IFU data) and the reference frame (`SPECSYS`: LSRK, BARYCENT).
- Rest frequencies: CO(3–2) 345.79599 GHz, ¹³CO(3–2) 330.58797 GHz, C¹⁸O(3–2)
  329.33055 GHz, HI 1.420405752 GHz; look others up (Splatalogue:
  `astroquery.splatalogue`) rather than recalling them.

## Moments

```python
slab = vcube.spectral_slab(-5 * u.km / u.s, 25 * u.km / u.s)   # just the line
mask = slab > 3 * rms                                            # rms from line-free channels
m0 = slab.with_mask(mask).moment(order=0)       # K km/s (integrated intensity)
m1 = slab.with_mask(mask).moment(order=1)       # km/s (intensity-weighted velocity)
sigma = slab.with_mask(mask).linewidth_sigma()  # km/s; FWHM = 2.3548 σ for a Gaussian
m0.write("mom0.fits")
```

- Estimate the rms from line-free channels (`vcube.spectral_slab(...)` away from the line,
  then `.std(axis=0)` or `mad_std`).
- Moment 1 and 2 are meaningless where there is no signal: mask at ≥3σ (or with a smoothed
  mask) before computing them. Moment 0 noise ≈ rms · Δv · √N_channels.

## Beams and units

```python
from radio_beam import Beam
beam = cube.beam                                  # from BMAJ/BMIN/BPA
k_per_jy = beam.jtok(345.79599 * u.GHz)           # K per Jy/beam (Rayleigh–Jeans), at the line
kcube = cube.to(u.K)                              # Jy/beam → K using the beam
```

- Check: a 14″ beam at 345.8 GHz gives 0.0521 K per Jy/beam.
- CASA/ALMA/VLA cubes often carry one beam per channel (`cube.beams`), where `cube.beam`
  fails: convolve to `cube.beams.common_beam()` with `convolve_to` first.
- Smooth cubes to a common beam before comparing lines or ratioing maps:
  `cube.convolve_to(Beam(20 * u.arcsec))`; regrid with `reproject` or
  `cube.spectral_interpolate` for the velocity axis.

## Instrument notes

- JCMT HARP/ʻŪʻū cubes (CADC `JCMT` collection) are in antenna temperature T_A*;
  convert to main-beam temperature with T_mb = T_A* / η_mb, taking η_mb for the
  frequency and epoch from the JCMT documentation; say which scale results are on.
- Big cubes: `SpectralCube.read(..., use_dask=True)` and work on slabs/subcubes
  (`cube.subcube(xlo=…, xhi=…)`) to stay within session memory.
