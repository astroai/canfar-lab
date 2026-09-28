---
name: astro-literature
description: Search and cite the astronomy literature with NASA ADS and arXiv — field-specific query syntax (authors, bibstems, objects, citations, refereed), finding the paper behind a catalogue or method, and producing correct citations. Use for literature reviews, "what is known about X", finding reference values, or checking that a result is new.
summary: "ADS and arXiv query syntax, finding sources, and citing correctly."
category: astronomy
metadata:
  maintainer: canfar-lab
---

# Astronomy literature (ADS + arXiv)

## Tools

- `astroai_ads_search` (`query`, `rows` ≤ 50) — needs `ADS_API_TOKEN` (the user adds it in
  the AstroAI hub; token from https://ui.adsabs.harvard.edu/user/settings/token).
  Returns bibcode, title, first author, year, citation count.
- `astroai_arxiv_search` (`query`, `max_results`) — no key; preprints including very
  recent ones not yet in journals.

## ADS query syntax

| Want | Query |
| --- | --- |
| First author | `author:"^Lindegren, L"` |
| Any author | `author:"Bailer-Jones, C"` |
| Years | `year:2020-2025` |
| Words in abstract / title | `abs:"parallax zero point"`, `title:"Gaia"` |
| Journal | `bibstem:ApJ`, `bibstem:MNRAS`, `bibstem:A&A`, `bibstem:AJ` |
| Refereed only | `property:refereed` |
| About an object | `object:"M31"` (resolved through SIMBAD/NED) |
| Papers citing / cited by | `citations(bibcode:2021A&A...649A...4L)`, `references(bibcode:…)` |
| Reviews on a topic | `reviews(abs:"fast radio bursts")` |
| Related / trending | `similar(bibcode:…)`, `trending(abs:"…")` |

Combine with `AND`, `OR`, `NOT` and parentheses.

## arXiv query syntax

Field prefixes `ti:`, `au:`, `abs:`, `cat:`, `all:` with `AND`/`OR`/`ANDNOT`, e.g.
`cat:astro-ph.GA AND abs:"bar pattern speed"`. Astronomy categories: `astro-ph.CO`
(cosmology), `.EP` (planets), `.GA` (galaxies), `.HE` (high energy), `.IM`
(instrumentation/methods), `.SR` (stars).

## Practice

- Search before claiming novelty or quoting a value; cite the paper you took a number from
  (bibcode + first author + year), not a memory of it.
- For a catalogue or data set, cite the release paper named by the archive (VizieR
  `ReadMe`, CADC collection page, Gaia DR3 documentation) and follow the survey's
  acknowledgement text.
- Prefer refereed versions; say when a result is only on arXiv.
- Never invent bibcodes, DOIs or citation counts: if a search fails (no token, network),
  say so and give the query the user can run in the ADS web UI.
- Summaries: separate what papers show from what they suggest, and note disagreements
  between measurements with their uncertainties.
