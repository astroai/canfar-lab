---
name: catalog-crossmatch
description: Cross-match astronomical source lists and catalogues (Gaia, 2MASS, AllWISE, Pan-STARRS, SIMBAD, your own detections) with astropy or the CDS XMatch service, choosing radii from astrometric errors, handling proper motion between epochs, and measuring the false-match rate. Use whenever two lists of sky positions must be paired.
summary: "Cross-match catalogues with astropy or CDS XMatch and measure false matches."
category: astronomy
metadata:
  maintainer: canfar-lab
---

# Cross-matching catalogues

## Choose the method

- Both lists local and moderate (≲10⁶ rows): astropy.
- Your list against a large VizieR catalogue: CDS XMatch (server side, no download of the
  big catalogue).
- Many-to-many or cluster fields: `search_around_sky`, then resolve duplicates yourself.

## astropy

```python
import numpy as np
import astropy.units as u
from astropy.coordinates import SkyCoord

c1 = SkyCoord(ra1, dec1, unit="deg")
c2 = SkyCoord(ra2, dec2, unit="deg")
idx, d2d, _ = c1.match_to_catalog_sky(c2)        # nearest neighbour in c2 for each c1
ok = d2d < 1.0 * u.arcsec
pairs = list(zip(np.flatnonzero(ok), idx[ok]))

i1, i2, sep, _ = c2.search_around_sky(c1, 2 * u.arcsec)  # all pairs within radius
```

`match_to_catalog_sky` is one-directional: several c1 sources can claim the same c2
source. Keep the closest (or require the match to be mutual) when a one-to-one match is
needed.

## CDS XMatch

```python
from astroquery.xmatch import XMatch
m = XMatch.query(cat1=my_table, cat2="vizier:I/355/gaiadr3",
                 max_distance=2 * u.arcsec, colRA1="ra", colDec1="dec")
# m["angDist"] is the separation in arcsec; returns every pair within max_distance
```

VizieR identifiers: Gaia DR3 `I/355/gaiadr3`, 2MASS PSC `II/246/out`, AllWISE
`II/328/allwise`, Pan-STARRS1 DR1 `II/349/ps1`; SIMBAD is `simbad`.

## Radius

- Start from the combined positional uncertainty: σ = √(σ₁² + σ₂²); a radius of 3–5σ keeps
  most true matches. Typical σ: Gaia ≪ 0.01″, 2MASS ~0.1″, AllWISE ~0.2–0.5″, ground-based
  detections 0.1–0.3″, low-resolution radio/IR up to arcseconds.
- Plot the separation histogram: true matches pile up at small separation, chance
  matches grow ∝ r. Put the radius where the peak has fallen to the background.

## Epochs and proper motion

Gaia positions are J2016.0; 2MASS observed ~1997–2001; SDSS ~2000–2008. A star moving
100 mas/yr shifts 1.6″ in 16 years. Propagate Gaia to the other catalogue's epoch with
`SkyCoord.apply_space_motion` (see gaia-astrometry) before matching high-proper-motion
or nearby samples, or widen the radius and then check.

## False matches (always measure)

Shift one list by an offset much larger than the radius (e.g. 1′ in declination) and
rematch; the number of matches is the chance-match count:

```python
radius = 2 * u.arcsec                                   # the radius used for the real match
c1_shift = c1.spherical_offsets_by(0 * u.arcmin, 1 * u.arcmin)
_, d2s, _ = c1_shift.match_to_catalog_sky(c2)
n_false = (d2s < radius).sum()
```

Analytic check: expected chance matches ≈ N₁ · Σ₂ · π r² (Σ₂ = surface density of list 2).
Report matches, chance matches and the resulting contamination fraction.
