---
name: cadc-archive-search
description: Find and download observations from the CADC archive (CFHT MegaCam/WIRCam/ESPaDOnS, JCMT SCUBA-2/HARP, Gemini, HST, JWST, VLASS, …) on CANFAR. Use when the user asks what data exists for a target or field, wants archive images or spectra, or needs files downloaded for analysis.
category: astronomy
metadata:
  maintainer: canfar-lab
---

# CADC archive search and download

## Workflow

1. **Resolve the target** with `astroai_resolve_target` (never recall coordinates from
   memory). Note the resolver and object type it returns.
2. **Cone search** with `astroai_cadc_search`:
   - `target` or `ra`/`dec` (ICRS degrees), `radius_deg` (default 0.05).
   - Narrow with `collection` (`CFHT`, `JCMT`, `GEMINI`, `HST`, `JWST`, `VLASS`, …),
     `instrument` (`MegaPrime`, `WIRCam`, `SCUBA-2`, …) and `calib_level`
     (1 raw, 2 calibrated, 3 stacked/products).
   - The search uses `INTERSECTS(s_region, CIRCLE(...))`: wide-field footprints whose
     centre is far from the target still match.
   - Read `by_instrument` for a summary before listing rows to the user.
3. **Refine with ADQL** when needed via `astroai_tap_query` (`service: cadc`), e.g.
   filter on wavelength or date:

   ```sql
   SELECT obs_id, instrument_name, em_min, em_max, t_min, t_exptime, obs_publisher_did
   FROM ivoa.ObsCore
   WHERE obs_collection = 'CFHT' AND instrument_name = 'MegaPrime'
     AND calib_level = 2
     AND 1 = INTERSECTS(s_region, CIRCLE('ICRS', 10.6847, 41.2688, 0.1))
     AND em_min < 6.0e-7 AND em_max > 5.5e-7          -- metres
   ORDER BY t_min DESC
   ```

   - `em_min`/`em_max` are wavelengths in **metres**; `t_min`/`t_max` are **MJD**;
     `t_exptime` is seconds; `s_ra`/`s_dec`/`s_fov` are degrees.
   - Richer metadata (planes, artifacts, file URIs) lives in `caom2.Observation`,
     `caom2.Plane`, `caom2.Artifact`; artifact URIs look like `cadc:CFHT/1234567p.fits.fz`.
4. **Download** with `astroai_cadc_download` into `$SCRATCH` (the default). Pass a
   `cadc:` artifact URI or an `ivo://cadc.nrc.ca/...` publisher DID. Proprietary data
   needs a CADC certificate (`canfar login`).
5. **Report**: number of observations by instrument/filter, date range, total exposure,
   where files were saved, and that scratch is deleted at session end.

## Pitfalls

- Always cap results (`maxrec`, `TOP`): popular fields have thousands of rows.
- The same `obs_id` appears once per plane/calibration level; deduplicate before counting
  "observations".
- `.fits.fz` files are Rice-compressed: `astropy.io.fits` reads them directly (HDU 1).
