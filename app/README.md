# Register Spine — local web app

A simple UI over the register-spine pipeline (the POC), so you can operate it like Postcode Prospector:
type a postcode area, hit **Run**, see a grid of firms with independence verdicts and emails, export CSV.

## Start it
Easiest: **double-click `start_app.command`** in the project root (opens the app in your browser).

Or from Terminal:
```bash
cd "Email Lead Generator"
python3 app/server.py                 # then open http://localhost:8770
# optional — enable the Firecrawl email pass for JS-rendered sites:
FIRECRAWL_API_KEY=fc-... python3 app/server.py
```
Python 3.9+ only — **no installs**. Stop it with Ctrl-C (or close the Terminal window).

## Use it
1. Type a **postcode area** (CO, IP, CM… — quick chips provided) and pick a **category** (funeral directors / SIC 96030).
2. *(Optional)* paste a **Firecrawl key** to enable JS-rendered email scraping for that run. Blank = free crawl
   + web-search discovery (no key needed). The app shows a banner telling you which mode ran.
3. **Run**. The **first ever run** does a one-time "preparing register data" step (~2–3 min) that builds and
   caches the national index + PSC map. Every run after that is fast (OSM + web-search + crawl, ~15–25s).
3. Read the grid:
   - **Green INDEPENDENT** = family/individual PSC owner (shown). **Red GROUP/CHAIN** = corporate PSC owner
     (name shown). **UNKNOWN** = no PSC filed.
   - Columns: firm, independence + owner, registered address, trading name(s), company number, website, email, phone, sources, flags.
   - **Flags** call out registered-vs-trading mismatches (e.g. "registered in RM, trades in CO") and reg-only firms with no OSM trading presence.
4. **Found but NOT in the register** — a separate table of sole traders / partnerships the register can't see
   (plus chain branches), so you don't lose that coverage.
5. **Export CSV** on either table.

## How it maps to the pipeline (`app/pipeline.py`)
Reuses the POC logic verbatim: strict OSM→CH matching, the **PSC-owner→OSM trading-name bridge**
(links "A Simple Goodbye Ltd" ↔ Roy Gwinnell ↔ gwinnell.co.uk), the tightened consolidator/chain list,
and the host-provider email filter (drops 34SP/Wix/etc.).

Pipeline per run: national SIC index (cached) → filter by area → OSM Overpass (trading branches + contacts,
with mirror fallback) → PSC verdict from cache → assemble + bridge → **website discovery** for firms with no
OSM site (guess likely domains from the name and probe them, verified against the firm's town/postcode; a
DuckDuckGo fallback runs until it rate-limits) → email crawl (Firecrawl if key, else free).

### Website/email discovery (fills the "artificially thin" gap)
OSM rarely has a firm's website, so most emails come from discovery + crawl. For each registered firm with no
site, the app **guesses likely domains** from the name (`lk` → `lkfuneraldirectors.co.uk`, `poulton` →
`poultonfunerals.com`) and probes them in parallel — no search-engine rate limits. A candidate is only kept if
the page reads as a funeral site **and** matches the firm's town/postcode or a distinctive (≥6-char) name token,
so common-surname collisions (e.g. a different "Smith") are rejected. Initialism firms (L&K, PGO) are matched
leniently because their domains are specific. Discovered sites are tagged `web-search` in the Sources column;
treat them as **candidates to verify**, not gospel. Result for CO: email coverage ~3/20 → **9/20** (≈9 of the
~10 genuine funeral directors) with no Firecrawl key.

## Caches (built once, reused)
- `data/companies.zip` (496 MB) and `data/psc/*.zip` (~2 GB) — the raw Companies House bulk downloads.
- `cache/index_96030.csv` and `cache/psc_96030.json` — the prepared national index + PSC map.

Delete `cache/` to force a rebuild (e.g. after a new monthly Companies House snapshot). Update the snapshot
dates in `app/pipeline.py` (`CH_MONTH`, `PSC_DATE`) to pull a fresh month.

## Notes / limits (same honest caveats as the POC — see ../REPORT.md)
- Only registered **limited companies** appear in the top grid; sole traders are in the second table.
- SIC 96030 is self-reported → expect some noise (fireworks, burial grounds, masons) in the shortlist.
- OSM coverage is uneven; areas need a bounding box in `BBOX` (CO/IP/CM/SS/CB/NR/EN/IG/RM/AL/SG/PE included).
- Firecrawl is off unless `FIRECRAWL_API_KEY` is set — the app flags this in a banner.
- Adding a category = add one entry to `CATEGORIES` in `app/pipeline.py` (SIC prefix + OSM tags), then re-run
  (first run for that SIC rebuilds its index + PSC map once).
