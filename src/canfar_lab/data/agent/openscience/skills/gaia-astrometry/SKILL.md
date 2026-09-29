---
name: gaia-astrometry
description: Gaia DR3 astrometry and photometry done right — quality cuts (RUWE, parallax_over_error), the parallax zero-point, distances from Bailer-Jones rather than 1/parallax, proper-motion epoch propagation, and colour–magnitude diagrams. Use for stellar samples, distances, kinematics, cluster membership, or when positions from another epoch must be compared with Gaia.
summary: "Gaia DR3 quality cuts, parallax zero-point, distances and epoch propagation."
category: astronomy
metadata:
  maintainer: canfar-lab
---

# Gaia DR3

## Query (ESA archive via `astroai_tap_query`, `service: gaia`)

```sql
SELECT TOP 2000 g.source_id, g.ra, g.dec, g.parallax, g.parallax_error,
       g.parallax_over_error, g.pmra, g.pmdec, g.ruwe, g.phot_g_mean_mag, g.bp_rp,
       g.phot_bp_rp_excess_factor, g.radial_velocity, g.nu_eff_used_in_astrometry, g.pseudocolour, g.ecl_lat,
       g.astrometric_params_solved, d.r_med_geo, d.r_lo_geo, d.r_hi_geo
FROM gaiadr3.gaia_source AS g
JOIN external.gaiaedr3_distance AS d USING (source_id)
WHERE 1 = CONTAINS(POINT('ICRS', g.ra, g.dec), CIRCLE('ICRS', 56.75, 24.12, 0.5))
  AND g.parallax_over_error > 10 AND g.ruwe < 1.4
```

Units: `ra`/`dec` degrees at epoch **J2016.0** (`ref_epoch`), `parallax` and errors in
**mas**, `pmra` (already × cos δ) and `pmdec` in **mas/yr**, `radial_velocity` km/s,
magnitudes Vega, `r_*_geo` in **pc**.

## Quality

- `ruwe < 1.4`: larger values flag a poor single-star astrometric fit (binaries,
  extended or blended sources).
- `parallax_over_error > 5–10` before treating a parallax as a distance.
- `astrometric_params_solved`: 31 = 5-parameter, 95 = 6-parameter, 3 = position only
  (no parallax or proper motion).
- Photometry near bright neighbours or in crowded fields: check
  `phot_bp_rp_excess_factor` (or its colour-corrected form C*, Riello et al. 2021)
  before trusting `bp_rp`.

## Parallax zero-point

DR3 parallaxes are too small by typically 0.02–0.06 mas depending on magnitude, colour
and ecliptic latitude (Lindegren et al. 2021). Correct them for 5/6-parameter solutions
(`gaiadr3-zeropoint` package):

```python
import numpy as np
import astropy.units as u
from zero_point import zpt
zpt.load_tables()
z = zpt.get_zpt(t["phot_g_mean_mag"], t["nu_eff_used_in_astrometry"], t["pseudocolour"],
                t["ecl_lat"], t["astrometric_params_solved"])  # mas, usually negative
plx_corr = (np.asarray(t["parallax"]) - z) * u.mas   # corrected parallaxes grow
```

The correction is calibrated for 6 < G < 21, 1.1 < ν_eff < 1.9 (5-parameter solutions)
and 1.24 < pseudocolour < 1.72 (6-parameter solutions). Outside that range the package
clamps to the edge value and warns; with `_warnings=False` it returns NaN instead, which
propagates silently into `Distance` and `SkyCoord` — so keep warnings on, flag those
sources and say so. The correction matters most for distant stars (at a 0.1 mas
parallax it is a 20–60 % effect).

## Distances

- Use `r_med_geo` (geometric prior) or `r_med_photogeo` with their 16/84 % bounds
  from `external.gaiaedr3_distance` (Bailer-Jones et al. 2021), not `1000/parallax`,
  unless `parallax_over_error` is large (> 10), where both agree.
- Never invert negative or low-S/N parallaxes. For population work, model the parallaxes
  directly instead of converting each one.

## Other epochs

Gaia positions are at J2016.0. Before matching to 2MASS (~J2000), SDSS or old plates,
or predicting today's position, propagate high-proper-motion stars:

```python
import astropy.units as u
from astropy.coordinates import SkyCoord, Distance
from astropy.time import Time

c = SkyCoord(ra=t["ra"], dec=t["dec"], distance=Distance(parallax=plx_corr),
             pm_ra_cosdec=t["pmra"], pm_dec=t["pmdec"], obstime=Time(2016.0, format="jyear"))
c_2000 = c.apply_space_motion(new_obstime=Time(2000.0, format="jyear"))
```

`Distance(parallax=…)` raises on negative parallaxes: if the `parallax_over_error` cut is
loosened, use `distance=t["r_med_geo"] * u.pc` instead.

Check: Barnard's star moves ~10.4″/yr, about 166″ between J2000 and J2016.

## Colour–magnitude diagram

Absolute magnitude `M_G = G + 5 log10(parallax_mas) − 10` (i.e. `G − 5 log10(d_pc) + 5`);
extinction is not included (see the photometric-calibration skill). Plot `bp_rp` vs `M_G`
with the y axis inverted.
