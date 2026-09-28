---
name: obscore-tap
description: Query astronomical catalogues and archives with ADQL over IVOA TAP (Gaia, SIMBAD, VizieR, NED, MAST, CADC). Use for catalogue cross-matches, cone searches, sample selection (e.g. stars within 100 pc), or any "look up in a catalogue" request.
summary: "ADQL over TAP for Gaia, SIMBAD, VizieR, NED, MAST and CADC catalogues."
category: astronomy
metadata:
  maintainer: canfar-lab
---

# ADQL over TAP

Use `astroai_tap_query` with `service` one of `cadc`, `gaia`, `simbad`, `vizier`, `ned`,
`mast` (see `astroai_tap_services`), or a TAP base URL. Results come back as columns +
preview rows; big results are saved to a CSV path.

## Patterns

Cone search (all services support the ADQL geometry functions):

```sql
SELECT TOP 100 source_id, ra, dec, parallax, phot_g_mean_mag
FROM gaiadr3.gaia_source
WHERE 1 = CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', 56.75, 24.12, 0.5))
ORDER BY phot_g_mean_mag
```

One object by id (fast, exact):

```sql
SELECT source_id, parallax, parallax_error, pmra, pmdec
FROM gaiadr3.gaia_source WHERE source_id = 4472832130942575872
```

SIMBAD basic data by name (identifiers are normalised with double spaces, so join
through `ident`):

```sql
SELECT b.main_id, b.ra, b.dec, b.otype, b.plx_value
FROM basic AS b JOIN ident AS i ON i.oidref = b.oid
WHERE i.id = 'M 31'
```

VizieR tables must be quoted: `SELECT TOP 10 * FROM "J/ApJ/883/1/table1"`. Find the
table with a keyword search on `tap_schema.tables` (`description LIKE '%RR Lyrae%'`).

## Units and sanity checks

- Gaia `parallax` is in **mas**; distance in pc ≈ 1000 / parallax only when
  `parallax_over_error` > 10 (otherwise say so and avoid inverting).
- Gaia `pmra` already includes cos(dec); proper motions are mas/yr.
- SIMBAD `ra`/`dec` are degrees (ICRS, J2000 epoch).
- Always use `TOP`/`maxrec`; `maybe_truncated_by_maxrec: true` means you are missing
  rows.
- Spot-check one known object (e.g. Barnard's star, Gaia DR3 4472832130942575872,
  parallax ≈ 547 mas) when building a new query.
