---
name: vo-cutouts-datalink
description: Get only the pixels you need from archive images and cubes — IVOA DataLink and SODA server-side cutouts at CADC (CFHT, JCMT, VLASS, …) and other VO archives with pyvo, instead of downloading full mosaics. Use for postage stamps of many targets, small regions of large survey images, or spectral sub-ranges of cubes.
summary: "Server-side SODA cutouts via DataLink at CADC and other VO archives."
category: astronomy
metadata:
  maintainer: canfar-lab
---

# Server-side cutouts (DataLink + SODA)

A full CFHT MegaCam exposure is hundreds of MB (40 CCDs); a 1′ cutout is ~1 MB. Cut on
the server.

## From ObsCore to a cutout

```python
import os
import astropy.units as u
import pyvo

tap = pyvo.dal.TAPService("https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/argus")
res = tap.run_sync("""
  SELECT TOP 10 obs_publisher_did, obs_id, instrument_name, em_min, em_max,
         t_min, access_url, access_format
  FROM ivoa.ObsCore
  WHERE obs_collection = 'CFHT' AND instrument_name = 'MegaPrime' AND calib_level = 2
    AND dataproduct_type = 'image'
    AND 1 = INTERSECTS(s_region, CIRCLE('ICRS', 10.6847, 41.2688, 0.05))""")

row = res[0]
dl = row.getdatalink()                    # semantics: #this, #cutout, #preview, #thumbnail
cut = dl.get_first_proc().processed(circle=(10.6847 * u.deg, 41.2688 * u.deg, 1 * u.arcmin))
with open(f"{os.environ['SCRATCH']}/{row['obs_id']}_cut.fits", "wb") as f:
    f.write(cut.read())
```

- `access_format` `…content=datalink` means `access_url` is a DataLink document, not the
  file itself.
- `processed()` also takes `band=` (two wavelength or frequency Quantities, e.g. a
  spectral sub-range of a cube), `range=` (an RA/Dec box: lon_min, lon_max, lat_min,
  lat_max) and `polygon=`; pass Quantities.
- A record without a SODA service descriptor makes `processed()` fall back silently to
  the full file: check the size before looping over many rows.
- Multi-extension images return one HDU per detector the region touches (e.g. `ccd24`,
  `ccd25`): loop over image HDUs, each with its own WCS.
- CADC ObsCore has no `energy_bandpassname`; select filters by `em_min`/`em_max`
  (metres) or join `caom2.Plane` for `energy_bandpassName`.
- Proprietary data needs a certificate (`canfar login` in the terminal writes
  `~/.ssl/cadcproxy.pem`); give pyvo an authenticated session:

  ```python
  import os
  from pyvo.auth import AuthSession
  s = AuthSession()
  s.credentials.set_client_certificate(os.path.expanduser("~/.ssl/cadcproxy.pem"))
  tap = pyvo.dal.TAPService("https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/argus", session=s)
  ```

## Many targets

Loop over targets and rows, skip rows whose footprint only grazes the target, write one
file per target/observation, and record the publisher DID in the FITS header or a
manifest. For hundreds of targets, run the loop as independent tasks (canfar-ray-scaling)
— each cutout is an independent HTTP request.

## Other archives

- `pyvo.dal.SIA2Service` / `SSAService` for image and spectral access where a service
  offers them; `pyvo.registry.search(servicetype="sia2", keywords=[...])` to find one.
- MAST (HST/JWST/TESS): `astroquery.mast` (`Observations`, `Tesscut` for TESS FFI cutouts).

## Check

Open each cutout, confirm the target is inside (`WCS.world_to_pixel`), check the pixel
scale and units (`BUNIT`), and note the observation date for variable sources.
