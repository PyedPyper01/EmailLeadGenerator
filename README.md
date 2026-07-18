# Email Lead Generator — free-data supplier-sourcing spine (POC)

Replaces paid Google Places with **free, official** sources for sourcing independent suppliers.
Proof of concept: **funeral directors (SIC 96030) in the CO postcode area**. See **[REPORT.md](REPORT.md)** for the findings.

## ▶ Operate it (web app)
Double-click **`start_app.command`** (or `python3 app/server.py`) → open `http://localhost:8770` →
type an area (CO, IP, CM…), hit **Run**, see the grid, export CSV. Full guide: **[app/README.md](app/README.md)**.
The command-line version below (`run_all.sh` + `src/`) is the original POC that the app wraps.

## What it does
Four free sources, no paid APIs (Firecrawl optional):

1. **Companies House Free Company Data** (bulk CSV) → official spine, filtered by SIC 96030 + postcode CO.
2. **Companies House PSC bulk** (JSON) → ownership → independence (family/individual vs corporate consolidator = chain).
3. **OpenStreetMap Overpass** → trading branches + phone/website (the Places replacement).
4. **Website crawl** (free stdlib; Firecrawl when `FIRECRAWL_API_KEY` is set) → email gap-fill.

Output: `out/leads.csv` — one row per registered independent company with contacts, ownership, and honest caveats;
plus `out/osm_unregistered.csv` for CO trading firms with no company-register match (sole traders).

## Run
```bash
./run_all.sh                       # downloads data (~2.5 GB first run), builds everything
# optional, for JS-rendered email discovery:
export FIRECRAWL_API_KEY=fc-...    # only needed for stage 7's Firecrawl backend
```
No API keys are required for sources 1–3. Python 3.9+ standard library only (no pip installs).
First run downloads the Companies House one-file company snapshot (~496 MB) and the 32-part PSC
snapshot (~2 GB); both are cached under `data/` and skipped on re-runs.

## Pipeline (src/)
| Stage | Script | Output |
|-------|--------|--------|
| 1 | `1_spine.py` | `out/spine_co_funeral.csv` — CH companies, SIC 96030 + postcode CO |
| 2 | `2_national_index.py` | `out/ch_funeral_uk.csv` — all UK 96030 (for reg-vs-trading recovery) |
| 3 | `3_osm.py` | `out/osm_co.csv` — CO funeral POIs + contacts |
| 4 | `4_match.py` | `out/target_companies.csv`, `out/osm_match_report.csv` |
| 5 | `5_psc_scan.py` | `out/psc_results.json` — PSC records + independence verdict |
| 6 | `6_assemble.py` | `out/leads_pre_email.csv`, `out/osm_unregistered.csv` |
| 7 | `7_email.py` | `out/leads.csv` — final |

`out/websites_discovered.csv` holds hand-verified web-search domains/emails (with per-row caveats) merged in stage 6.
`src/ch_lookup.py` is an ad-hoc national name search over the CH bulk file.

## Key caveats (see REPORT.md for detail)
- **Registered address ≠ trading address** — misses firms trading in CO but registered elsewhere
  (Daniel Robinson, CM23) and false-positives firms registered in CO but trading elsewhere (Stuart Poulton → Epping).
- **Sole traders are invisible** to the company register — a large share of independents. Kept in `osm_unregistered.csv`.
- **SIC 96030 is self-reported** — expect noise (fireworks, burial grounds, masons); hand-classify the shortlist.
- **OSM coverage is uneven** — good in Colchester/Clacton, missed Halstead.

## Do NOT generalise yet
This is a single-category / single-area proof. The full 45-category, all-UK build is out of scope until this is judged.
