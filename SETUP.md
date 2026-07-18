# Email Lead Generator — Register Sourcing Engine

ELG is the **register sourcing engine**. It pulls national UK trade-register data
(SAIF + NAFD, verified against Companies House PSC), merges it into distinct
independent companies, and writes standard **supply files** that **Postcode
Prospector** consumes. ELG has no UI and does no downstream analysis — it produces
data files.

Current category: **funeral directors**. The architecture generalises: each new
category (masons, celebrants, will writers…) is a different `CATEGORY` value plus a
different set of source adapters, writing the **same** `register_supply.csv` shape.

---

## 1. Clone & install

```bash
git clone https://github.com/PyedPyper01/EmailLeadGenerator.git
cd EmailLeadGenerator
```

- **Python 3.9+**, standard library only — no install needed to build the supply files.
- The **write-ups** step (optional, marketplace profiles) needs the Anthropic SDK:
  ```bash
  python3 -m pip install --user anthropic
  ```

## 2. Keys — which go where

| Key | Needed for | How to set | Notes |
|-----|-----------|-----------|-------|
| *(none)* | Companies House bulk + PSC, SAIF, NAFD, OSM/Overpass | — | All free, no key. **No `CH_API_KEY` is used** — ELG uses the free bulk downloads, not the CH API. |
| `FIRECRAWL_API_KEY` | *Optional.* Better email/ownership extraction from JS-rendered sites | `export FIRECRAWL_API_KEY=fc-…` | Falls back to a free stdlib crawler if unset. |
| `ANTHROPIC_API_KEY` | *Optional.* Marketplace profile write-ups only | `export ANTHROPIC_API_KEY=sk-ant-…` in `~/.zshenv` | Never commit it. `.zshenv`/`.env` are git-ignored. |

Keys are read from the environment (`os.environ`); none are stored in the repo.

## 3. Rebuild all caches from scratch (one command)

```bash
./rebuild_caches.sh
```

Downloads the Companies House bulk company file (~496 MB) and PSC snapshot (~2 GB),
builds the national SIC index + PSC ownership map, prefetches all NAFD firm pages and
CH account categories, fetches SAIF + NAFD, verifies consolidator ownership, enriches
websites/emails, and writes the supply files. **~2.5 GB download, ~20–30 min the first
time.** Idempotent — re-running only fills missing caches. Nothing here is committed;
`data/`, `cache/` and `out/` are all git-ignored.

## 4. Run a pull (produce the supply files)

After caches exist, a full pull is:

```bash
python3 export_brevo.py
```

This runs the national register pull + verification + enrichment and (re)writes
`out/register_supply.csv`, `out/register_supply_by_area.csv`,
`out/register_supply_possible.csv` (plus legacy Brevo lists).

## 5. Marketplace profiles (optional, key-gated)

```bash
python3 app/writeup_content.py                 # crawl firms' own sites (no key)
export ANTHROPIC_API_KEY=sk-ant-…              # then generate via the Batch API (Haiku)
python3 generate_writeups.py                   # -> out/marketplace_writeups.csv
```
The completed batch id is cached (`cache/writeup_batch_ids.json`) so re-runs resume
rather than re-paying. Delete that file to regenerate against fresh content.

---

## Data hand-off spec — what Prospector consumes

All hand-off files are UTF-8 **with BOM**, in `out/`. **Join key across all files is
`COMPANY_ID`** (a stable slug: `sluggified-company-name-<district>`).

### `out/register_supply.csv` — the primary file (one row per verified independent counterparty)

| Column | Meaning |
|--------|---------|
| `COMPANY_ID` | Stable join key (also keys `marketplace_writeups.csv`). |
| `COMPANY` | The commercial counterparty / parent trading name. |
| `TRADING_BRANDS` | Other brands rolled into this counterparty (pipe `\|`-separated). Empty if single-brand. Approach the owner once, not once per brand. |
| `ADDRESS` | Representative registered/branch address. |
| `POSTCODE` / `DISTRICT` / `POSTCODE_AREA` | Representative postcode, its outward district (e.g. `CO9`), and area (e.g. `CO`). |
| `PHONE` | E.164 (`+44…`), or empty. |
| `EMAIL` | Best single contact email, or empty. |
| `CONTACT_NAME` | Empty — registers are firm-level (no named contact). Reserved for future sources. |
| `WEBSITE` | Firm's own website, or empty. |
| `CATEGORY` | e.g. `Funeral directors`. Changes per category; row shape stays identical. |
| `SOURCE` | `saif` \| `nafd` \| `both`. |
| `SAIF_MEMBER` | `Y` / `N`. (SAIF membership = independent by constitution.) |
| `INDEPENDENT_OR_CHAIN` | Always `independent` in this file (chains excluded). |
| `PARENT_GROUP` | Empty for independents. Populated only where a consolidator owns the firm (not in this file). |
| `VERIFIED_DATE` | ISO date of the pull that produced the row. |

**Exclusions:** confirmed consolidator-owned firms (Dignity, Co-op, Funeral Partners,
`.coop` societies, etc.) are removed. The unadjudicated **"possible" consolidator-owned**
firms are **not** in this file — see below.

### `out/register_supply_possible.csv` — flagged, held OUT of supply

Same columns as `register_supply.csv` plus `COMPANIES_HOUSE_URL` and `EVIDENCE`;
`INDEPENDENT_OR_CHAIN = possible-chain`, `PARENT_GROUP` = the suspected consolidator.
These are fuzzy Companies-House-PSC matches awaiting a human decision — review, then
either fold into supply or exclude.

### `out/register_supply_by_area.csv` — per-postcode-area counts

`POSTCODE_AREA, counterparties, brands, saif, nafd_only, both, chains_excluded,
with_email, with_phone, with_website`. Counts are by the firm's **registered-office
area** (one row per counterparty). `saif + nafd_only + both = counterparties`
(so `saif` here is SAIF-exclusive; total SAIF members = `saif + both`).

### `out/marketplace_writeups.csv` — supplier profiles (keyed on `COMPANY_ID`)

`COMPANY_ID, COMPANY, WEBSITE, SUMMARY, SERVICES, FACILITIES, ACCREDITATIONS,
AREAS_SERVED, HERITAGE, ESTABLISHED, WRITEUP_STATUS`. `SERVICES` is a controlled
vocabulary (fixed tag list, pipe-separated). Every row is `WRITEUP_STATUS = DRAFT` —
suppliers approve their own profile before it goes live. Joins 1:1 to
`register_supply.csv` on `COMPANY_ID`.

---

## Repository layout

```
app/pipeline.py        CH bulk spine, PSC scan, OSM, crawling, domain-guess, Firecrawl
app/registers.py       SAIF + NAFD adapters, merge/rollup, consolidator verification, run_national()
app/prefetch.py        one-time caches: all NAFD firm pages + CH account categories
app/writeup_content.py crawl firms' own sites for profile source text
export_brevo.py        the pull: run_national + enrich -> writes register_supply*.csv (+ legacy Brevo)
generate_writeups.py   Batch-API marketplace profiles (Haiku, structured output)
run_national.py        national pull report (per-area + national totals)
run_registers.py       single-area register report (debug)
rebuild_caches.sh      one-command full rebuild on a fresh machine
data/  cache/  out/    downloads / caches / outputs — all git-ignored
```

## Adding a category later

Add a source adapter (like `fetch_saif` / `fetch_nafd_index` in `app/registers.py`)
for the category's trade body, set its `CATEGORY` string, and the same merge →
verify → `register_supply.csv` path applies. Prospector reads the identical file
shape; only `CATEGORY` differs.
