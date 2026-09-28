---
description: Survey archive data for a target or field (CADC, Gaia, MAST) and recommend what to use
---
Find what archive data exists for: $ARGUMENTS

1. Resolve the target with astroai_resolve_target (or use the coordinates given). Report the resolved ICRS position and object type.
2. CADC: astroai_cadc_search around the position with a radius suited to the object's size. Summarise by collection, instrument, filter or wavelength range (em_min/em_max are metres), calibration level, date range and total exposure. Load the cadc-archive-search skill for details.
3. Other archives that fit the science: Gaia DR3 for stars and astrometry, SIMBAD/VizieR for catalogued measurements, MAST (astroai_tap_query with service mast) for HST, JWST, TESS. Keep each query small.
4. Present one table of the most useful data sets (archive, instrument/survey, band, depth or exposure, resolution, date, calibration level, public or proprietary) and recommend what to use for the stated goal, with reasons.
5. Do not download anything yet. Offer the exact next step (server-side cutouts with the vo-cutouts-datalink skill, or astroai_cadc_download into $SCRATCH) and the expected data volume.
