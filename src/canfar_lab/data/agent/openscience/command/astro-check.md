---
description: Audit current results for astronomy-specific correctness (units, frames, systems, known values)
---
Audit the current results for astronomy correctness. Focus (optional): $ARGUMENTS

Do not change files; report findings. For each result, script or table in scope, check:

1. Units: every quantity carries a unit; conversions (mag ↔ flux, Jy ↔ erg s⁻¹ cm⁻² Hz⁻¹, K ↔ Jy/beam, wavelength in m vs Å vs µm, ObsCore em_min/em_max in metres) are right.
2. Coordinates and time: frame (ICRS, FK5, Galactic) and epoch stated; proper motion propagated when epochs differ; time scale (UTC, TDB, BJD) and format (MJD, JD) explicit and consistent.
3. Photometry: magnitude system (AB or Vega) stated and consistent; zero points and extinction corrections applied once, with their source.
4. Statistics: uncertainties propagated; fits report parameters, errors and goodness of fit; selection cuts (S/N, RUWE, flags) justified; periodogram peaks checked for aliases.
5. Reference checks: at least one number compared with an independent value (catalogue, literature via astroai_ads_search, a known object, a physical limit). Run the comparison when it is cheap.
6. Provenance and persistence: inputs identified (DIDs, table names, query text); results that exist only on scratch.

Report a table of findings (severity, location, evidence, suggested fix), then what was verified as correct.
