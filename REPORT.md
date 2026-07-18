# Free-Data Supplier Sourcing — Proof of Concept
## CO funeral directors, end to end (Companies House → PSC → OSM → crawl)

**Date:** 16 Jul 2026 · **Category:** funeral directors (SIC 96030) · **Area:** CO postcode (Colchester)
**Goal:** prove a *free, official* supplier spine can replace paid Google Places — before generalising to 45 categories / all-UK.

**Bottom line:** the free spine works and is *cleaner and more trustworthy* than scraping for the firms that are **registered limited companies** — Companies House gives you an official name, number, address, status and, via PSC, a **provable ownership-based independence check**. But it has a hard **coverage floor**: a large share of genuine independent funeral directors are **sole traders / partnerships that never appear in the company register**, and **OSM branch coverage is patchy**. On the task's own litmus test — **Daniel Robinson (CO9) — the free approach misses it entirely.** The honest verdict: use the free register as the *spine and independence oracle*, but you still need a directory/Places-style layer (or Firecrawl domain discovery) to reach full trading coverage.

---

## The four free sources (no paid APIs, no keys except optional Firecrawl)

| # | Source | What it gave us | Cost |
|---|--------|-----------------|------|
| 1 | **Companies House Free Company Data** (bulk CSV, Open Gov Licence) | Official spine: name, number, reg address, SIC, status | Free, no key. 496 MB one-file download, 5.73 M companies |
| 2 | **Companies House PSC bulk** (32 × 72 MB JSON) | Ownership → independence (individual/family vs corporate consolidator) | Free, no key. 2.0 GB |
| 3 | **OpenStreetMap Overpass** | Trading branches + phone/website (the Google-Places replacement) | Free, no key |
| 4 | **Website crawl** (free stdlib; Firecrawl optional) | Email gap-fill from the firm's own site | Free (Firecrawl paid, optional) |

Pipeline: **CH bulk → filter SIC 96030 + postcode CO → PSC join (drop chains) → OSM contacts → crawl emails → CSV**.

---

## Acceptance-test answers

### 1. How many registered funeral-director companies in CO, from Companies House alone?
- Scanned **5,734,779** live UK companies in the bulk file.
- **3,606** carry SIC **96030** (funeral & related) nationally.
- **18** of those are registered at a **CO** postcode → the spine. **17 Active**, 1 non-active.
- Honest note on SIC noise: of the 18, only ~**10–12 are genuine consumer funeral directors**. The rest are SIC-96030 self-reporting noise: a fireworks company (Heavens Above Fireworks — ashes-in-fireworks), two **natural-burial grounds** (Greenwoodland, Oakfield Wood), a **cemetery** (Lexden Garden of Rest), a **memorial mason** (Barnard), a **B2B embalming/bearing supplier** (Lee Houghton), a **CIC** (Libitina), plus one firm **in liquidation** (PGO Realisations = the old P.G. Oxley) and one **being struck off** (Steele Rose). *SIC is self-reported and imperfect — filter broadly, then expect to hand-classify.*

### 2. Independence split, from PSC?
Joined all 20 target companies (18 CO-registered + 2 recovered by trading match) to the PSC bulk:
- **INDEPENDENT (individual/family PSC): 18**
- **GROUP/holding (corporate PSC, review): 1** (Heavens Above Fireworks → owned by Dynamic Fireworks Ltd)
- **UNKNOWN (no PSC filed): 1** (Libitina CIC)
- **CHAIN: 0.** This is itself a finding: **Co-op / Dignity branches do not register as separate CO companies** — they operate under the parent society's registration — so a SIC+postcode filter on the register **naturally excludes the chains**. The chains only appear in OSM (by brand), where we flag and drop them. PSC confirms every registered CO funeral company is family/individually owned.

**PSC also bridged trading names → registered entities** (official-data join, not a guess):
- "A Simple Goodbye Ltd" → PSC owner **Roy Gwinnell** → this *is* **R Gwinnell & Sons** (gwinnell.co.uk)
- "Clacton Funeral Service Ltd" → PSC owner **Lesley Barlow** → trades as **Lesley Barlow & Family**
- "PGO Realisations Ltd" → PSC owners **Philip & Bruce Oxley** → the **P.G. Oxley** business (now in liquidation)

### 3. Website/email — OSM vs crawl?
- **OSM** returned **30** CO funeral POIs. Of the firms that mapped to a registered independent, OSM directly supplied **3 websites** (Hunnaball, H&AW Palmer, + Gwinnell via the PSC bridge) and **0 emails** for registered independents.
- **Website crawl (free, no Firecrawl key needed)** turned those + web-search-discovered domains into **5 real emails**: contact@gwinnell.co.uk, admin@hunnaball.co.uk, palmersofmelford@tiscali.co.uk, lkfuneralservices@yahoo.co.uk, stuart@poultonfunerals.com. Two more emails came from directory listings (West & Coe, Smith Family).
- **Final: 7 / 20 leads have an email, 6 have a website, 4 have a phone.** Among the ~10–12 *genuine* consumer FDs the email hit-rate is materially higher (~6–7 of them).
- **Firecrawl was not run** — `FIRECRAWL_API_KEY` is only set in the Postcode Prospector Netlify env, not locally. The stage is built (`src/7_email.py` auto-uses Firecrawl when the key is present) and would add JS-rendered sites the free pass misses. **To run it, export the key and re-run stage 7.**

### 4. Compare against Postcode Prospector for CO — especially Daniel Robinson (CO9)
- **Daniel Robinson & Sons (CO9): the free approach MISSES it — twice over.**
  1. It exists in Companies House (`00455065`) but is **registered at CM23 3AL (Bishop's Stortford)**, not a CO postcode → excluded by the postcode filter.
  2. Its Halstead **CO9 branch is not tagged in OpenStreetMap** (a targeted Overpass query around Halstead returned **zero** funeral POIs) → the trading layer can't recover it either.
  So a firm PP finds via Google Places is invisible to *register + OSM*. This is the single clearest argument that the free spine alone is **not** a full Places replacement.
- **What the free approach found that is real and clean:** Hunnaball, L&K, Clacton Funeral Service (Lesley Barlow), Smith Family, Steele Rose (the *other* CO9 Halstead firm), Gwinnell, H&AW Palmer, Eustace King & Birkin, West & Coe — all with **official ownership-verified independence**, which PP's email-guessing cannot match for trustworthiness.
- **What it found extra / that scraping would miss:** the ownership bridges (Gwinnell ↔ A Simple Goodbye, Barlow ↔ Clacton Funeral Service) and hard signals like **"in liquidation"** / **"proposed strike-off"** that only the register carries.

### 5. Honest coverage, gaps, registered-vs-trading
- **Registered-address ≠ trading-address is a real problem, in both directions:**
  - *Trades in CO, registered elsewhere* → **missed** by the postcode filter: Daniel Robinson (CM23).
  - *Registered in CO, trades elsewhere* → **false positive** for "CO funeral director": **Stuart Poulton Ltd** is registered CO15 but actually operates in **Epping/North Weald (CM16)**; **West & Coe** is a Dagenham (RM10) group with a Frinton branch.
- **Sole-trader gap (the big one):** 6 genuine CO independents visible in OSM have **no Companies House match at all** — they are sole traders / partnerships: **R Gwinnell & Sons*, Titford Funeral Service, Kemble / Kemble Funeral Home, P.G. Oxley*, J. Edwards (Sudbury), Martin & Sons (Clare), Harwich & Dovercourt Independent.** (*Gwinnell and Oxley were recovered *only* because a linked Ltd shares a PSC owner — most sole traders have no such bridge.) A register-first spine structurally cannot see these.
- **OSM completeness is uneven:** good in Colchester/Clacton, but missed Halstead entirely; contact tags are sparse (only 8/30 CO POIs had a website, 0 had email for independents).
- **Chain detection is solid:** brand/website tags (Co-op, dignityfunerals.co.uk) + the PSC corporate-owner check agree; e.g. OSM shows **Titford is now Dignity-owned** and **Brown Fenn & Parker → dignityfunerals.co.uk**.

---

## Verdict — does the free spine beat scraping?
**For independence and data quality: yes.** Companies House + PSC is official, auditable, free, and gives an ownership-based independent/chain verdict that email-guessing cannot. It also adds status signals (liquidation, strike-off) and trading-name bridges.

**For raw coverage of small firms: not on its own.** Sole traders and unincorporated family firms — a big slice of independent funeral directors — never appear in the company register, and OSM doesn't reliably fill the gap. Daniel Robinson (CO9) is the proof.

**Recommended production shape:** free CH+PSC as the **spine + independence oracle**; OSM for trading branches/contacts; then a **directory/Places-style or Firecrawl-domain-discovery layer** to catch the sole traders and reg-vs-trading mismatches. Keep the "no CH match" appendix as first-class output (those *are* leads, just unincorporated).

---

## Files
- `out/leads.csv` — final: one row per registered CO funeral company (independence, owners, trading names, website/phone/email, sources, caveats).
- `out/osm_unregistered.csv` — CO trading firms with **no** CH match (sole traders + chains), so they aren't lost.
- `out/spine_co_funeral.csv` — the raw CH spine (18). `out/ch_funeral_uk.csv` — national 96030 index (3,606).
- `out/psc_results.json` — PSC records + verdict per company. `out/osm_match_report.csv` — every OSM→CH match decision.
