#!/usr/bin/env python3
"""One-time national prefetch for the register national pass:
 1) every NAFD firm's directory page (phone/website/ownership) -> cache/nafd_firm_details.json
 2) Companies House account category (dormant flag) for SIC-96030 -> cache/ch_account_category.json
Both are cached and skipped if already complete. Safe to re-run (resumes)."""
import os, sys, json, re, zipfile, io, csv, time, concurrent.futures as cf
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import registers as R

def prefetch_nafd():
    nafd = R.fetch_nafd_index(progress=lambda m: print("  …", m, flush=True))
    path = os.path.join(R.CACHE, "nafd_firm_details.json")
    cache = json.load(open(path)) if os.path.exists(path) else {}
    todo = [r for r in nafd if r["url"].rstrip("/").split("/")[-1] not in cache]
    print(f"NAFD firm pages: {len(cache)} cached, {len(todo)} to fetch", flush=True)
    done = 0
    def _f(r):
        R.fetch_nafd_firm(r["url"], cache)
    for i in range(0, len(todo), 200):
        batch = todo[i:i+200]
        with cf.ThreadPoolExecutor(max_workers=10) as ex:
            list(ex.map(_f, batch))
        done += len(batch)
        json.dump(cache, open(path, "w"))
        print(f"  NAFD firms fetched {done}/{len(todo)} (total cached {len(cache)})", flush=True)
    print("NAFD firm prefetch complete.", flush=True)

def scan_ch_accounts():
    """AccountCategory (col 19) + IncorporationDate (col 14) for SIC-96030 companies."""
    path = os.path.join(R.CACHE, "ch_account_category.json")
    if os.path.exists(path):
        print("CH account-category cache already present.", flush=True); return
    zp = os.path.join(R.HERE, "data", "companies.zip")
    z = zipfile.ZipFile(zp); name = z.namelist()[0]
    out = {}
    with z.open(name) as f:
        txt = io.TextIOWrapper(f, encoding="utf-8", errors="replace"); rdr = csv.reader(txt); next(rdr)
        for row in rdr:
            if len(row) < 30: continue
            sics = [row[i] for i in (26,27,28,29) if row[i].strip()]
            if not any(s.strip().split(" ")[0].startswith("96030") for s in sics): continue
            out[row[1].strip()] = {"account_category": row[19].strip(), "incorp": row[14].strip()}
    json.dump(out, open(path, "w"))
    print(f"CH account-category cache: {len(out)} companies.", flush=True)

if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("all", "nafd"): prefetch_nafd()
    if what in ("all", "ch"): scan_ch_accounts()
