"""Astronomy data MCP tools against mocked HTTP services (no network)."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from canfar_workload import astro_data
from canfar_workload.mcp import handle_message

SESAME_M31 = """<?xml version="1.0" encoding="UTF-8"?>
<Sesame><Target option="~SNV"><name>M31</name>
<Resolver name="Sc=Simbad (CDS, via client/server)">
<otype>AGN</otype><jradeg>10.68470833</jradeg><jdedeg>41.26875000</jdedeg><oname>M  31</oname>
</Resolver></Target></Sesame>"""

SESAME_MISS = """<?xml version="1.0"?><Sesame><Target><name>nope</name>
<Resolver name="Sc=Simbad"><INFO>*** Nothing found ***</INFO></Resolver></Target></Sesame>"""

ARXIV = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<id>http://arxiv.org/abs/2401.00001v1</id><title>A  CANFAR
 paper</title><published>2024-01-01T00:00:00Z</published>
<summary>We use CANFAR.</summary><author><name>A. Author</name></author></entry></feed>"""


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Route astro_data HTTP through a handler; record requests."""
    seen: list[httpx.Request] = []
    routes: dict[str, httpx.Response] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        for prefix, response in routes.items():
            if str(request.url).startswith(prefix):
                return response
        return httpx.Response(404, text="no route")

    def client() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    monkeypatch.setattr(astro_data, "_client", client)
    monkeypatch.setenv("ASTROAI_DATA_DIR", str(tmp_path))
    return routes, seen


def _form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def test_resolve_target_parses_sesame(served) -> None:
    routes, seen = served
    routes[astro_data.SESAME_URL] = httpx.Response(200, text=SESAME_M31)
    out = astro_data.resolve_target("M31")
    assert out["ra_deg"] == pytest.approx(10.68470833)
    assert out["dec_deg"] == pytest.approx(41.26875)
    assert out["frame"] == "ICRS"
    assert seen[0].url.query == b"M31"


def test_resolve_target_encodes_spaces_and_reports_misses(served) -> None:
    routes, seen = served
    routes[astro_data.SESAME_URL] = httpx.Response(200, text=SESAME_MISS)
    with pytest.raises(RuntimeError, match="could not resolve"):
        astro_data.resolve_target("NGC 1275")
    assert seen[0].url.query == b"NGC%201275"


def test_tap_query_posts_sync_adql_and_previews(served) -> None:
    routes, seen = served
    url = astro_data.TAP_SERVICES["gaia"]["url"] + "/sync"
    routes[url] = httpx.Response(
        200,
        text="source_id,parallax\n4472832130942575872,546.97\n",
        headers={"content-type": "text/csv"},
    )
    out = astro_data.tap_query(
        "gaia", "SELECT TOP 1 source_id, parallax FROM gaiadr3.gaia_source", maxrec=5
    )
    form = _form(seen[0])
    assert form["LANG"] == "ADQL" and form["FORMAT"] == "csv" and form["MAXREC"] == "5"
    assert out["columns"] == ["source_id", "parallax"]
    assert out["rows"] == [["4472832130942575872", "546.97"]]
    assert out["row_count"] == 1
    assert "csv_path" not in out


def test_tap_query_saves_large_results(served, tmp_path: Path) -> None:
    routes, _ = served
    body = "n\n" + "".join(f"{i}\n" for i in range(120))
    routes[astro_data.TAP_SERVICES["vizier"]["url"]] = httpx.Response(
        200, text=body, headers={"content-type": "text/csv"}
    )
    out = astro_data.tap_query("vizier", 'SELECT n FROM "x"', preview=10)
    assert out["row_count"] == 120
    assert len(out["rows"]) == 10
    assert out["truncated_preview"] is True
    saved = Path(out["csv_path"])
    assert saved.parent == tmp_path
    assert saved.read_text() == body


def test_tap_query_surfaces_adql_errors(served) -> None:
    routes, _ = served
    routes[astro_data.TAP_SERVICES["cadc"]["url"]] = httpx.Response(
        400,
        text=(
            '<VOTABLE><RESOURCE><INFO name="QUERY_STATUS" value="ERROR">'
            "column not found: foo</INFO></RESOURCE></VOTABLE>"
        ),
    )
    with pytest.raises(RuntimeError, match="column not found: foo"):
        astro_data.tap_query("cadc", "SELECT foo FROM ivoa.ObsCore")


def test_tap_query_rejects_unknown_service() -> None:
    with pytest.raises(ValueError, match="unknown service"):
        astro_data.tap_query("nope", "SELECT 1")


def test_cadc_search_resolves_then_cone_searches(served) -> None:
    routes, seen = served
    routes[astro_data.SESAME_URL] = httpx.Response(200, text=SESAME_M31)
    routes[astro_data.TAP_SERVICES["cadc"]["url"]] = httpx.Response(
        200,
        text=(
            "obs_id,obs_collection,instrument_name\n"
            "1,CFHT,MegaPrime\n2,CFHT,MegaPrime\n3,JCMT,SCUBA-2\n"
        ),
        headers={"content-type": "text/csv"},
    )
    out = astro_data.cadc_search(target="M31", radius_deg=0.1, collection="CFHT")
    adql = _form(seen[1])["QUERY"]
    assert "CIRCLE('ICRS', 10.68470833, 41.26875, 0.1)" in adql
    assert "obs_collection='CFHT'" in adql
    assert out["by_instrument"] == {"CFHT/MegaPrime": 2, "JCMT/SCUBA-2": 1}
    assert out["resolved"]["ra_deg"] == pytest.approx(10.68470833)


def test_cadc_search_quotes_strings_and_checks_radius() -> None:
    with pytest.raises(ValueError, match="radius_deg"):
        astro_data.cadc_search(ra=1.0, dec=2.0, radius_deg=10)
    with pytest.raises(ValueError, match="target, or ra and dec"):
        astro_data.cadc_search()
    assert astro_data._adql_str("O'Brien") == "'O''Brien'"


def test_cadc_download_refuses_system_paths() -> None:
    with pytest.raises(ValueError, match="refusing to write"):
        astro_data.cadc_download("cadc:CFHT/x.fits", "/etc/astro")


def test_cadc_download_streams_urls(served, tmp_path: Path) -> None:
    routes, _ = served
    routes["https://example.org/data"] = httpx.Response(
        200,
        content=b"SIMPLE  =",
        headers={"content-disposition": 'attachment; filename="img.fits"'},
    )
    out = astro_data.cadc_download("https://example.org/data/x", str(tmp_path / "dl"))
    assert out["path"] == str(tmp_path / "dl" / "img.fits")
    assert out["bytes"] == 9


def test_ads_search_needs_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADS_API_TOKEN", raising=False)
    monkeypatch.delenv("ADS_DEV_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ADS_API_TOKEN"):
        astro_data.ads_search("bar")


def test_arxiv_search_parses_atom(served) -> None:
    routes, _ = served
    routes[astro_data.ARXIV_URL] = httpx.Response(200, text=ARXIV)
    out = astro_data.arxiv_search("all:CANFAR", 1)
    assert out["results"][0]["id"] == "2401.00001v1"
    assert out["results"][0]["title"] == "A CANFAR paper"
    assert out["results"][0]["first_author"] == "A. Author"


def test_arxiv_rate_limit_is_a_clean_error(served) -> None:
    routes, _ = served
    routes[astro_data.ARXIV_URL] = httpx.Response(429, text="Rate exceeded.")
    with pytest.raises(RuntimeError, match="rate limited"):
        astro_data.arxiv_search("all:x")


def test_mcp_call_returns_business_errors_as_results(served) -> None:
    routes, _ = served
    routes[astro_data.SESAME_URL] = httpx.Response(200, text=SESAME_MISS)
    resp = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "resolve_target", "arguments": {"name": "nope"}},
        }
    )
    assert resp["result"]["isError"] is True
    assert "could not resolve" in resp["result"]["content"][0]["text"]


def test_mcp_call_resolve_target(served) -> None:
    routes, _ = served
    routes[astro_data.SESAME_URL] = httpx.Response(200, text=SESAME_M31)
    resp = handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "resolve_target", "arguments": {"name": "M31"}},
        }
    )
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["dec_deg"] == pytest.approx(41.26875)
