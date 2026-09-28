---
name: fits-wcs-cutouts
description: Open FITS images, inspect headers and WCS, convert between pixel and sky coordinates, and make cutouts that keep a valid WCS. Use for any FITS image task (CFHT MegaCam, HST, JWST, VLASS, …), postage stamps, or overlaying positions on images.
category: astronomy
metadata:
  maintainer: canfar-lab
---

# FITS, WCS and cutouts

## Open and inspect

```python
from astropy.io import fits
from astropy.wcs import WCS

with fits.open(path) as hdul:
    hdul.info()                      # which HDU holds the image?
    hdu = next(h for h in hdul if h.data is not None and h.data.ndim >= 2)
    data, header = hdu.data, hdu.header
wcs = WCS(header)
print(wcs)                            # CTYPE should be RA---TAN/DEC--TAN (or similar)
```

- Compressed `.fits.fz` files put the image in HDU 1 (`CompImageHDU`).
- MegaCam `p` files are multi-extension (one HDU per CCD): pick the CCD containing the
  target with `wcs.footprint_contains(coord)`.
- Check `BUNIT`, `EXPTIME`, `PHOTZP`/`MAGZP` before doing photometry.

## Pixel ↔ sky

```python
from astropy.coordinates import SkyCoord
import astropy.units as u

c = SkyCoord(10.6847 * u.deg, 41.2688 * u.deg, frame="icrs")
x, y = wcs.world_to_pixel(c)          # 0-based pixel coordinates
back = wcs.pixel_to_world(x, y)
assert c.separation(back) < 1 * u.mas # round-trip sanity check
```

FITS headers are 1-based (`CRPIX`); astropy's pixel API is 0-based.

## Cutouts that keep the WCS

```python
from astropy.nddata import Cutout2D

cut = Cutout2D(data, position=c, size=(2 * u.arcmin, 2 * u.arcmin), wcs=wcs, mode="partial")
out = fits.PrimaryHDU(cut.data, header=cut.wcs.to_header())
out.header["BUNIT"] = header.get("BUNIT", "")
out.writeto(f"{scratch}/cutout.fits", overwrite=True)
```

Verify: the target's pixel position in the cutout from `cut.wcs.world_to_pixel(c)` should
match `cut.to_cutout_position(wcs.world_to_pixel(c))` to < 0.1 pixel.

For large archive images, prefer server-side cutouts (CADC SODA `cutout=` parameter on the
data URL) over downloading the full file.

## Plotting

```python
import matplotlib.pyplot as plt
from astropy.visualization import ImageNormalize, ZScaleInterval

fig = plt.figure(figsize=(6, 6))
ax = fig.add_subplot(projection=cut.wcs)
ax.imshow(cut.data, origin="lower", cmap="gray",
          norm=ImageNormalize(cut.data, interval=ZScaleInterval()))
ax.set_xlabel("RA"); ax.set_ylabel("Dec")
fig.savefig("cutout.png", dpi=150, bbox_inches="tight")
```
