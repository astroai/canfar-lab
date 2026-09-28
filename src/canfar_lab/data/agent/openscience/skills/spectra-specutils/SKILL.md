---
name: spectra-specutils
description: Load, inspect and analyse 1D astronomical spectra with specutils — units, continuum normalisation, line fitting, equivalent widths and radial velocities. Use for ESPaDOnS/GMOS/SDSS/JWST spectra, emission or absorption line measurements, or redshift estimates.
summary: "1D spectra with specutils: continuum, lines, equivalent widths, velocities."
category: astronomy
metadata:
  maintainer: canfar-lab
---

# 1D spectra with specutils

## Load with units

```python
import astropy.units as u
from specutils import Spectrum1D   # specutils >= 2: `Spectrum`

spec = Spectrum1D.read(path)        # many archive formats are auto-detected
print(spec.spectral_axis.unit, spec.flux.unit)
```

If the format is unknown, build it explicitly from a table or FITS columns:
`Spectrum1D(flux=flux * u.Unit("erg / (s cm2 AA)"), spectral_axis=wave * u.AA)`.
Check whether wavelengths are **air or vacuum** and whether the spectrum is already
heliocentric/barycentric corrected (header keywords).

## Continuum and lines

```python
from specutils.fitting import fit_generic_continuum, fit_lines
from specutils.manipulation import extract_region
from specutils import SpectralRegion
from specutils.analysis import equivalent_width, centroid
from astropy.modeling import models

cont = fit_generic_continuum(spec)
norm = spec / cont(spec.spectral_axis)
region = SpectralRegion(6555 * u.AA, 6572 * u.AA)          # around H-alpha
ew = equivalent_width(norm, regions=region)                  # negative = emission
line = extract_region(norm - 1, region)
g = fit_lines(line, models.Gaussian1D(amplitude=0.5, mean=6563 * u.AA, stddev=1 * u.AA))
```

## Velocities

```python
from astropy.constants import c
lam0 = 6562.80 * u.AA                     # air wavelength of H-alpha
v = ((g.mean.quantity - lam0) / lam0 * c).to(u.km / u.s)
```

For many lines, cross-correlate against a template instead of fitting lines one by one.

## Sanity checks

- A redshift from one line is ambiguous: confirm with a second line.
- Equivalent widths are in wavelength units and the sign convention must be stated.
- Report the spectral resolution (R) and S/N per pixel near the line you measure.
