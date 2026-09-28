---
name: photometric-calibration
description: Put instrumental magnitudes on a standard system — zero points and colour terms from reference catalogues (Pan-STARRS1, SDSS, Gaia), AB vs Vega, flux densities in Jy, and Galactic extinction corrections with SFD/Schlafly & Finkbeiner maps and modern extinction curves. Use for calibrating CFHT/Gemini/HST images, comparing photometry across surveys, building SEDs, or correcting colours for dust.
summary: "Zero points, colour terms, AB/Vega and Galactic extinction corrections."
category: astronomy
metadata:
  maintainer: canfar-lab
---

# Photometric calibration

## Systems and units

- AB: `m_AB = −2.5 log10(f_ν / 3631 Jy)`; astropy: `(flux).to(u.ABmag)` (1 mJy = 16.4 AB).
- Pan-STARRS1 grizy and SDSS ugriz are (close to) AB; SDSS u and z are offset
  (u_AB ≈ u_SDSS − 0.04, z_AB ≈ z_SDSS + 0.02). Gaia G/BP/RP and 2MASS JHKs are Vega.
- Never assume two filters with the same name are the same: CFHT MegaCam, SDSS and PS1
  g/r/i differ; use published transformations (colour terms) between systems.

## Zero point from field stars

```python
import numpy as np
from astropy.stats import sigma_clipped_stats

m_inst = -2.5 * np.log10(counts / exptime)              # counts: background-subtracted
d = m_ref - m_inst                                      # matched, unsaturated stars
_, zp, zp_std = sigma_clipped_stats(d, sigma=3)
zp_err = zp_std / np.sqrt(np.sum(np.isfinite(d)))
# with a colour term: d = zp + k * colour_ref  → fit a line (np.polyfit or astropy.modeling)
```

- Use stars that are unsaturated in the image and well above the noise in the
  reference catalogue; exclude flagged/extended sources and close pairs.
- Cross-match first (catalog-crossmatch skill), in the same aperture/PSF scheme you apply
  to science targets; apply the aperture correction if apertures are finite.
- Check: residuals vs colour (colour term), vs position (flat-field/illumination
  errors), vs magnitude (non-linearity, background errors). Report ZP ± error and N stars.
- Reference catalogues: PS1 DR1 (`II/349/ps1`, δ > −30°), SDSS (`astroquery.sdss`),
  Gaia DR3 synthetic photometry for other systems.

## Galactic extinction

E(B−V) toward a position (IRSA dust service):

```python
from astroquery.ipac.irsa.irsa_dust import IrsaDust
t = IrsaDust.get_query_table(coord, section="ebv")
ebv = t["ext SandF mean"][0]        # Schlafly & Finkbeiner 2011 = 0.86 × SFD ("ext SFD mean")
```

A_λ from a curve (dust_extinction; Gordon et al. 2023 `G23` is valid 0.0912–32 µm):

```python
import astropy.units as u
from dust_extinction.parameter_averages import G23
ext = G23(Rv=3.1)
A_V = 3.1 * ebv
A_lambda = ext(wavelength) * A_V            # ext() returns A(λ)/A(V); wavelength with units
m_corrected = m_observed - A_lambda
```

- For broad bands use the effective wavelength for the source's SED, or integrate over the
  bandpass for very red/blue sources or large A_V.
- SFD maps are unreliable at low Galactic latitude (|b| ≲ 5°) and toward dense clouds;
  there, derive extinction from the stars themselves (colour excesses) and say so.
- Only correct for foreground Galactic dust by default; host-galaxy or circumstellar dust
  is a separate, model-dependent step.

## Sanity checks

- A_B/A_V ≈ 1.32 and A_K/A_V ≈ 0.10 for R_V = 3.1.
- Calibrated magnitudes of a few catalogued stars in the field should reproduce the
  catalogue to within the zero-point error.
