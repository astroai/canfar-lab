---
name: observation-planning
description: Plan telescope observations with astroplan — target visibility, airmass, twilight, Moon separation, transit times and observability over a semester for CFHT, JCMT, Gemini and other sites. Use for proposals, observing runs, finding when a target is observable, or scheduling time-critical events (transits, occultations).
summary: "Visibility, airmass, twilight and Moon constraints with astroplan."
category: astronomy
metadata:
  maintainer: canfar-lab
---

# Observation planning

## Site and targets

```python
import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.time import Time
from astroplan import Observer, FixedTarget

obs = Observer.at_site("cfht", timezone="US/Hawaii")   # also "jcmt", "gemini_north",
                                                       # "gemini_south", "subaru", "keck", "dao"
target = FixedTarget(SkyCoord(56.75, 24.12, unit="deg"), name="Pleiades")
```

Resolve names with `astroai_resolve_target` and build the `SkyCoord` from its RA/Dec
(`FixedTarget.from_name` needs network access to Sesame, same source). Site list:
`EarthLocation.get_site_names()`.

## One night

```python
t = Time("2026-11-15 22:00")   # UTC = local noon at CFHT; the next dusk opens the night of Nov 15
dusk = obs.twilight_evening_astronomical(t, which="next")
dawn = obs.twilight_morning_astronomical(dusk, which="next")
transit = obs.target_meridian_transit_time(dusk, target, which="next")
altaz = obs.altaz(transit, target)             # altaz.alt, altaz.secz (airmass)
```

Times are UTC `Time` objects; convert for the user with `.to_datetime(obs.timezone)`.
Start from local noon so "the night of <date>" means that date's local evening, and
check `dusk < transit < dawn` — otherwise the target transits in daylight and its best
altitude that night is at dusk or dawn.

## Constraints and observability

```python
from astroplan import (AltitudeConstraint, AirmassConstraint, AtNightConstraint,
                       MoonSeparationConstraint, observability_table)

constraints = [AltitudeConstraint(30 * u.deg, 90 * u.deg), AirmassConstraint(2.0),
               AtNightConstraint.twilight_astronomical(),
               MoonSeparationConstraint(min=30 * u.deg)]
table = observability_table(constraints, obs, [target], time_range=[dusk, dawn])
```

For a semester, loop over nights (or use `months_observable`) and report the dates and
hours each target meets the constraints.

## Checks

- Maximum altitude at transit is `90° − |latitude − δ|` (CFHT/JCMT latitude ≈ +19.8°):
  a quick sanity check on any visibility result.
- Moon phase matters as much as separation for faint optical targets:
  `obs.moon_illumination(time)`.
- Precise predictions (exoplanet transits, occultations) need the ephemeris in BJD_TDB and
  its uncertainty propagated to the observing date; report the timing window.
- astroplan may download Earth-orientation (IERS) tables; if the network is blocked,
  set `from astropy.utils import iers; iers.conf.auto_download = False` and accept
  slightly reduced precision.
