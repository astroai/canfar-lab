"""Astronomy data tools for the MCP server: name resolution, VO TAP queries,
CADC archive search and download, VOSpace, and literature search.

Plain HTTP (httpx) against IVOA/CDS/ADS/arXiv endpoints, so the tools work in
lean images without astropy/astroquery. Every tool returns a small table or a
file path: large results are written to disk and only a preview is inlined.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from canfar_workload import __version__

USER_AGENT = f"canfar-lab/{__version__} (+https://github.com/astroai/canfar-lab)"
TIMEOUT = httpx.Timeout(120.0, connect=15.0)

#: TAP services reachable anonymously. Keys are what agents pass as ``service``.
TAP_SERVICES: dict[str, dict[str, str]] = {
    "cadc": {
        "url": "https://ws.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/argus",
        "about": "CADC archive (ivoa.ObsCore, caom2.*): CFHT, JCMT, Gemini, HST, JWST, VLASS…",
    },
    "gaia": {
        "url": "https://gea.esac.esa.int/tap-server/tap",
        "about": "ESA Gaia archive (gaiadr3.gaia_source, …)",
    },
    "simbad": {
        "url": "https://simbad.cds.unistra.fr/simbad/sim-tap",
        "about": "SIMBAD objects (basic, ident, flux, …)",
    },
    "vizier": {
        "url": "https://tapvizier.cds.unistra.fr/TAPVizieR/tap",
        "about": 'VizieR catalogues; quote table names, e.g. "I/355/gaiadr3"',
    },
    "ned": {
        "url": "https://ned.ipac.caltech.edu/tap",
        "about": "NASA/IPAC Extragalactic Database (NEDTAP.objdir, …)",
    },
    "mast": {
        "url": "https://mast.stsci.edu/vo-tap/api/v0.1/caom",
        "about": "MAST CAOM observations (HST, JWST, TESS, …) via dbo.* tables",
    },
}

MAX_ROWS = 100_000
DEFAULT_PREVIEW = 50
SESAME_URL = "https://cds.unistra.fr/cgi-bin/nph-sesame/-ox/~SNV"
ADS_URL = "https://api.adsabs.harvard.edu/v1/search/query"
ARXIV_URL = "https://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"


def _client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def _results_dir() -> Path:
    """Where large tables land: session scratch when present, else the work dir."""
    for key in ("ASTROAI_DATA_DIR", "SCRATCH"):
        value = os.environ.get(key, "").strip()
        if value and Path(value).is_dir():
            return Path(value) / "astro-data" if key == "SCRATCH" else Path(value)
    return Path.cwd() / "astro-data"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")[:60] or "query"


def _table(text: str, *, preview: int, save_as: str | None, label: str) -> dict[str, Any]:
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return {"columns": [], "row_count": 0, "rows": []}
    columns, data = rows[0], rows[1:]
    out: dict[str, Any] = {
        "columns": columns,
        "row_count": len(data),
        "rows": data[:preview],
    }
    if len(data) > preview or save_as:
        path = Path(save_as) if save_as else _results_dir() / f"{_slug(label)}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        out["csv_path"] = str(path)
        out["truncated_preview"] = len(data) > preview
    return out


def _http_error(exc: httpx.HTTPStatusError) -> RuntimeError:
    status = exc.response.status_code
    body = exc.response.text.strip()
    # TAP services put the ADQL error inside a VOTable INFO element.
    info = re.search(r"<INFO[^>]*>(.*?)</INFO>", body, re.S)
    detail = (info.group(1) if info else body)[:600]
    if status == 429:
        return RuntimeError(f"rate limited by {exc.request.url.host}; wait a minute and retry")
    return RuntimeError(f"HTTP {status} from {exc.request.url.host}: {detail}")


def tap_query(
    service: str,
    adql: str,
    *,
    maxrec: int = 1000,
    preview: int = DEFAULT_PREVIEW,
    save_as: str | None = None,
) -> dict[str, Any]:
    """Synchronous ADQL query against a known TAP service (or a TAP base URL)."""
    if not adql.strip():
        raise ValueError("adql is required")
    base = TAP_SERVICES.get(service, {}).get("url") or service
    if not base.startswith(("http://", "https://")):
        raise ValueError(f"unknown service {service!r}; one of {', '.join(TAP_SERVICES)} or a URL")
    maxrec = max(1, min(int(maxrec), MAX_ROWS))
    data = {
        "REQUEST": "doQuery",
        "LANG": "ADQL",
        "FORMAT": "csv",
        "MAXREC": str(maxrec),
        "QUERY": adql,
    }
    try:
        with _client() as client:
            resp = client.post(base.rstrip("/") + "/sync", data=data)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise _http_error(exc) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"{service}: {exc}") from exc
    if "xml" in resp.headers.get("content-type", ""):
        raise _http_error(httpx.HTTPStatusError("TAP error", request=resp.request, response=resp))
    digest = hashlib.sha1(adql.encode()).hexdigest()[:10]
    out = _table(resp.text, preview=preview, save_as=save_as, label=f"{_slug(service)}-{digest}")
    out["service"] = service
    out["maxrec"] = maxrec
    out["maybe_truncated_by_maxrec"] = out["row_count"] >= maxrec
    return out


def resolve_target(name: str) -> dict[str, Any]:
    """Resolve an object name to ICRS degrees via CDS Sesame (SIMBAD, NED, VizieR)."""
    if not name.strip():
        raise ValueError("name is required")
    try:
        with _client() as client:
            resp = client.get(f"{SESAME_URL}?{quote(name.strip())}")
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise _http_error(exc) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(f"sesame: {exc}") from exc
    root = ET.fromstring(resp.text)
    for resolver in root.iter("Resolver"):
        ra, dec = resolver.findtext("jradeg"), resolver.findtext("jdedeg")
        if ra and dec:
            return {
                "name": name,
                "ra_deg": float(ra),
                "dec_deg": float(dec),
                "frame": "ICRS",
                "resolver": resolver.get("name", ""),
                "object_type": resolver.findtext("otype"),
                "canonical_name": resolver.findtext("oname"),
            }
    raise RuntimeError(f"Sesame could not resolve {name!r}")


def _adql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def cadc_search(
    *,
    target: str | None = None,
    ra: float | None = None,
    dec: float | None = None,
    radius_deg: float = 0.05,
    collection: str | None = None,
    instrument: str | None = None,
    calib_level: int | None = None,
    maxrec: int = 200,
    preview: int = DEFAULT_PREVIEW,
) -> dict[str, Any]:
    """Cone search of CADC ObsCore around a target name or position."""
    resolved = None
    if target and (ra is None or dec is None):
        resolved = resolve_target(target)
        ra, dec = resolved["ra_deg"], resolved["dec_deg"]
    if ra is None or dec is None:
        raise ValueError("give target, or ra and dec in degrees")
    if not 0 < radius_deg <= 5:
        raise ValueError("radius_deg must be in (0, 5]")
    where = [
        f"1=INTERSECTS(s_region, CIRCLE('ICRS', {float(ra)}, {float(dec)}, {float(radius_deg)}))"
    ]
    if collection:
        where.append(f"obs_collection={_adql_str(collection)}")
    if instrument:
        where.append(f"instrument_name={_adql_str(instrument)}")
    if calib_level is not None:
        where.append(f"calib_level={int(calib_level)}")
    adql = (
        "SELECT obs_id, obs_collection, instrument_name, target_name, em_min, em_max, "
        "dataproduct_type, calib_level, t_min, t_exptime, s_ra, s_dec, obs_publisher_did "
        "FROM ivoa.ObsCore WHERE " + " AND ".join(where) + " ORDER BY t_min DESC"
    )
    out = tap_query("cadc", adql, maxrec=maxrec, preview=preview)
    cols = out.get("columns", [])
    counts: dict[str, int] = {}
    rows = out.get("rows", [])
    if "csv_path" in out:
        with open(out["csv_path"], encoding="utf-8") as fh:
            rows = list(csv.reader(fh))[1:]
    if {"obs_collection", "instrument_name"} <= set(cols):
        ci, ii = cols.index("obs_collection"), cols.index("instrument_name")
        for row in rows:
            key = f"{row[ci]}/{row[ii]}"
            counts[key] = counts.get(key, 0) + 1
    out.update(
        {
            "ra_deg": ra,
            "dec_deg": dec,
            "radius_deg": radius_deg,
            "adql": adql,
            "by_instrument": counts,
        }
    )
    if resolved:
        out["resolved"] = resolved
    return out


def _allowed_dest(dest: Path) -> Path:
    dest = dest.expanduser().resolve()
    roots = [Path("/arc"), Path("/scratch"), Path.home(), Path.cwd(), Path("/tmp")]
    for key in ("SCRATCH", "SRCDIR", "WORK"):
        if os.environ.get(key):
            roots.append(Path(os.environ[key]))
    if not any(dest == r.resolve() or r.resolve() in dest.parents for r in roots):
        raise ValueError(f"refusing to write outside /arc, scratch, home or the work dir: {dest}")
    return dest


def cadc_download(uri: str, dest_dir: str | None = None) -> dict[str, Any]:
    """Download one CADC file (``cadc:COLLECTION/file``, ``ivo://`` DID via cadcget) or URL."""
    if not uri.strip():
        raise ValueError("uri is required")
    dest = _allowed_dest(Path(dest_dir) if dest_dir else _results_dir() / "downloads")
    dest.mkdir(parents=True, exist_ok=True)
    if uri.startswith(("http://", "https://")):
        name = _slug(Path(httpx.URL(uri).path).name or "download")
        target = dest / name
        try:
            with _client() as client, client.stream("GET", uri) as resp:
                resp.raise_for_status()
                cd = resp.headers.get("content-disposition", "")
                match = re.search(r'filename="?([^";]+)', cd)
                if match:
                    target = dest / _slug(match.group(1))
                with target.open("wb") as fh:
                    for chunk in resp.iter_bytes(1 << 20):
                        fh.write(chunk)
        except httpx.HTTPStatusError as exc:
            raise _http_error(exc) from exc
        return {"path": str(target), "bytes": target.stat().st_size}
    cadcget = shutil.which("cadcget")
    if not cadcget:
        raise RuntimeError("cadcget not found (pip install cadcdata)")
    before = {p.name for p in dest.iterdir()}
    proc = subprocess.run(
        [cadcget, uri], cwd=dest, capture_output=True, text=True, timeout=3600, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"cadcget failed: {(proc.stderr or proc.stdout).strip()[-600:]}")
    new = sorted(p for p in dest.iterdir() if p.name not in before)
    return {
        "paths": [str(p) for p in new],
        "bytes": sum(p.stat().st_size for p in new),
        "dest_dir": str(dest),
    }


def _vos(cmd: list[str], timeout: int) -> str:
    exe = shutil.which(cmd[0])
    if not exe:
        raise RuntimeError(f"{cmd[0]} not found (pip install vos)")
    proc = subprocess.run(
        [exe, *cmd[1:]], capture_output=True, text=True, timeout=timeout, check=False
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip()[-600:]
        hint = (
            " — run `canfar login` for a CADC certificate"
            if "cert" in err.lower() or "401" in err
            else ""
        )
        raise RuntimeError(f"{cmd[0]} failed: {err}{hint}")
    return proc.stdout


def vospace_list(uri: str) -> dict[str, Any]:
    """List a VOSpace node (``vos:``, ``arc:`` or ``vault:`` URI)."""
    if not uri.strip():
        raise ValueError("uri is required")
    lines = [line for line in _vos(["vls", "-l", uri], 120).splitlines() if line.strip()]
    return {"uri": uri, "entries": lines[:500], "count": len(lines)}


def vospace_copy(source: str, destination: str) -> dict[str, Any]:
    """Copy between VOSpace and local storage (either side may be a VOSpace URI)."""
    if not source.strip() or not destination.strip():
        raise ValueError("source and destination are required")
    if ":" not in destination.split("/", 1)[0]:
        _allowed_dest(Path(destination).parent)
    _vos(["vcp", source, destination], 7200)
    return {"source": source, "destination": destination, "ok": True}


def ads_search(query: str, rows: int = 10) -> dict[str, Any]:
    """NASA ADS literature search. Needs ADS_API_TOKEN (Studio hub → keys)."""
    token = os.environ.get("ADS_API_TOKEN") or os.environ.get("ADS_DEV_KEY")
    if not token:
        raise RuntimeError(
            "ADS_API_TOKEN is not set: create one at "
            "https://ui.adsabs.harvard.edu/user/settings/token "
            "and add it in the Studio hub (Agents & keys)"
        )
    params = {
        "q": query,
        "rows": max(1, min(int(rows), 50)),
        "fl": "bibcode,title,author,year,citation_count,doi",
    }
    try:
        with _client() as client:
            resp = client.get(ADS_URL, params=params, headers={"Authorization": f"Bearer {token}"})
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise _http_error(exc) from exc
    docs = resp.json().get("response", {}).get("docs", [])
    return {
        "query": query,
        "results": [
            {
                "bibcode": d.get("bibcode"),
                "title": (d.get("title") or [""])[0],
                "first_author": (d.get("author") or [""])[0],
                "year": d.get("year"),
                "citations": d.get("citation_count"),
                "url": f"https://ui.adsabs.harvard.edu/abs/{d.get('bibcode')}",
            }
            for d in docs
        ],
    }


def arxiv_search(query: str, max_results: int = 10) -> dict[str, Any]:
    """arXiv search (Atom API); ``query`` uses arXiv syntax (``cat:astro-ph.GA AND abs:bar``)."""
    if not query.strip():
        raise ValueError("query is required")
    params = {
        "search_query": query,
        "max_results": max(1, min(int(max_results), 50)),
        "sortBy": "relevance",
    }
    try:
        with _client() as client:
            resp = client.get(ARXIV_URL, params=params)
            resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise _http_error(exc) from exc
    root = ET.fromstring(resp.text)
    results = []
    for entry in root.iter(f"{_ATOM}entry"):
        results.append(
            {
                "id": (entry.findtext(f"{_ATOM}id") or "").rsplit("/abs/", 1)[-1],
                "title": " ".join((entry.findtext(f"{_ATOM}title") or "").split()),
                "first_author": entry.findtext(f"{_ATOM}author/{_ATOM}name"),
                "published": (entry.findtext(f"{_ATOM}published") or "")[:10],
                "summary": " ".join((entry.findtext(f"{_ATOM}summary") or "").split())[:400],
            }
        )
    return {"query": query, "results": results}


def _opt_float(args: dict[str, Any], key: str) -> float | None:
    return None if args.get(key) is None else float(args[key])


def _tool_tap_services(args: dict[str, Any]) -> dict[str, Any]:
    del args
    return {"services": {k: v["about"] for k, v in TAP_SERVICES.items()}}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "resolve_target",
        "description": (
            "Resolve an astronomical object name (M31, NGC 1275, Barnard's star) to ICRS "
            "RA/Dec in degrees via CDS Sesame."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        "handler": lambda a: resolve_target(str(a.get("name", ""))),
    },
    {
        "name": "tap_services",
        "description": (
            "List the TAP services tap_query knows by name (CADC, Gaia, SIMBAD, VizieR, NED, MAST)."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "handler": _tool_tap_services,
    },
    {
        "name": "tap_query",
        "description": (
            "Run an ADQL query on a TAP service (cadc, gaia, simbad, vizier, ned, mast, or a "
            "TAP base URL). Returns columns, a row preview and, for larger results, a CSV path. "
            "Use TOP/MAXREC to stay small."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "service": {"type": "string", "description": "Service name or TAP base URL."},
                "adql": {"type": "string"},
                "maxrec": {"type": "integer", "default": 1000},
                "preview": {"type": "integer", "default": DEFAULT_PREVIEW},
                "save_as": {
                    "type": "string",
                    "description": "Optional CSV path for the full result.",
                },
            },
            "required": ["service", "adql"],
        },
        "handler": lambda a: tap_query(
            str(a.get("service", "")),
            str(a.get("adql", "")),
            maxrec=int(a.get("maxrec", 1000)),
            preview=int(a.get("preview", DEFAULT_PREVIEW)),
            save_as=a.get("save_as"),
        ),
    },
    {
        "name": "cadc_search",
        "description": (
            "Find CADC archive observations (ObsCore) around a target name or RA/Dec: "
            "collection, instrument, wavelength range, date, exposure and the publisher DID "
            "to pass to cadc_download."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "ra": {"type": "number", "description": "ICRS degrees"},
                "dec": {"type": "number", "description": "ICRS degrees"},
                "radius_deg": {"type": "number", "default": 0.05},
                "collection": {
                    "type": "string",
                    "description": "e.g. CFHT, JCMT, GEMINI, HST, JWST, VLASS",
                },
                "instrument": {"type": "string", "description": "e.g. MegaPrime, WIRCam, SCUBA-2"},
                "calib_level": {"type": "integer"},
                "maxrec": {"type": "integer", "default": 200},
            },
        },
        "handler": lambda a: cadc_search(
            target=a.get("target"),
            ra=_opt_float(a, "ra"),
            dec=_opt_float(a, "dec"),
            radius_deg=float(a.get("radius_deg", 0.05)),
            collection=a.get("collection"),
            instrument=a.get("instrument"),
            calib_level=None if a.get("calib_level") is None else int(a["calib_level"]),
            maxrec=int(a.get("maxrec", 200)),
        ),
    },
    {
        "name": "cadc_download",
        "description": (
            "Download a CADC file (cadc:COLLECTION/file.fits or an ivo:// publisher DID, via "
            "cadcget) or an http(s) URL into dest_dir (default: session scratch). Returns local "
            "paths."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"uri": {"type": "string"}, "dest_dir": {"type": "string"}},
            "required": ["uri"],
        },
        "handler": lambda a: cadc_download(str(a.get("uri", "")), a.get("dest_dir")),
    },
    {
        "name": "vospace_list",
        "description": "List a VOSpace directory (vos:, arc: or vault: URI). Needs `canfar login`.",
        "inputSchema": {
            "type": "object",
            "properties": {"uri": {"type": "string"}},
            "required": ["uri"],
        },
        "handler": lambda a: vospace_list(str(a.get("uri", ""))),
    },
    {
        "name": "vospace_copy",
        "description": (
            "Copy a file between VOSpace and local storage with vcp. Needs `canfar login`."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"source": {"type": "string"}, "destination": {"type": "string"}},
            "required": ["source", "destination"],
        },
        "handler": lambda a: vospace_copy(str(a.get("source", "")), str(a.get("destination", ""))),
    },
    {
        "name": "ads_search",
        "description": "Search NASA ADS (papers, citations). Needs ADS_API_TOKEN.",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "rows": {"type": "integer", "default": 10}},
            "required": ["query"],
        },
        "handler": lambda a: ads_search(str(a.get("query", "")), int(a.get("rows", 10))),
    },
    {
        "name": "arxiv_search",
        "description": "Search arXiv preprints, e.g. 'cat:astro-ph.CO AND abs:\"weak lensing\"'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 10},
            },
            "required": ["query"],
        },
        "handler": lambda a: arxiv_search(str(a.get("query", "")), int(a.get("max_results", 10))),
    },
]
