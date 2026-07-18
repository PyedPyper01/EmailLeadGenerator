#!/usr/bin/env python3
"""
Stage 5 — PSC INDEPENDENCE (official ownership signal).
Stream the Companies House PSC bulk snapshot (32 zips, line-delimited JSON, no
API key) and pull every PSC record for our target company numbers. Then decide
independence from OFFICIAL ownership, not guessed from emails:
  - a corporate/legal-entity PSC whose name matches a known consolidator -> CHAIN
  - a corporate PSC (non-consolidator)                                    -> GROUP/holding (review)
  - only individual PSCs                                                  -> INDEPENDENT
  - no PSC at all                                                         -> UNKNOWN (small/exempt)
Output: out/psc_results.json  { company_number: {pscs:[...], verdict, owners} }
"""
import zipfile, json, os, glob, re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PSC_DIR = os.path.join(HERE, "data", "psc")
TARGETS = os.path.join(HERE, "out", "target_companies.csv")
OUT = os.path.join(HERE, "out", "psc_results.json")

CONSOLIDATORS = ["dignity","funeral partners","co-op","co-operative","cooperative",
                 "memoria","westerleigh","central england co","east of england co",
                 "midcounties","southern co-op","funeral services partnership",
                 "funeral partnership","legacy funeral"]

def is_consolidator(name):
    n=(name or "").lower()
    return any(c in n for c in CONSOLIDATORS)

def load_targets():
    import csv
    with open(TARGETS) as f:
        return {r["company_number"].strip(): r for r in csv.DictReader(f)}

def main():
    targets = load_targets()
    nums = list(targets.keys())
    # fast byte-prefix filter: PSC lines start {"company_number":"XXXXXXXX",...
    prefixes = tuple(('{"company_number":"%s"' % n).encode() for n in nums)
    found = {n: [] for n in nums}

    files = sorted(glob.glob(os.path.join(PSC_DIR, "*.zip")))
    print(f"Scanning {len(files)} PSC files for {len(nums)} target companies...")
    for i, fp in enumerate(files, 1):
        z = zipfile.ZipFile(fp)
        with z.open(z.namelist()[0]) as f:
            for line in f:
                if not line.startswith(prefixes):
                    continue
                rec = json.loads(line)
                cn = rec.get("company_number")
                if cn not in found:
                    continue
                d = rec.get("data", {})
                found[cn].append({
                    "kind": d.get("kind",""),
                    "name": d.get("name",""),
                    "nature": d.get("natures_of_control", []),
                    "ceased": bool(d.get("ceased_on")),
                })
        print(f"  [{i}/{len(files)}] {os.path.basename(fp)}")

    results = {}
    for n in nums:
        pscs = [p for p in found[n] if not p["ceased"]]
        corp = [p for p in pscs if "corporate" in p["kind"] or "legal-person" in p["kind"]]
        indiv = [p for p in pscs if "individual" in p["kind"]]
        cons = [p for p in corp if is_consolidator(p["name"])]
        if cons:
            verdict = "CHAIN"
        elif corp:
            verdict = "GROUP/holding (review)"
        elif indiv:
            verdict = "INDEPENDENT"
        else:
            verdict = "UNKNOWN (no active PSC)"
        results[n] = {
            "company_name": targets[n]["company_name"],
            "verdict": verdict,
            "owners": [p["name"] for p in pscs],
            "corporate_owners": [p["name"] for p in corp],
            "pscs": pscs,
        }

    with open(OUT, "w") as fo:
        json.dump(results, fo, indent=1)

    from collections import Counter
    c = Counter(r["verdict"] for r in results.values())
    print("\nPSC independence verdicts:")
    for k,v in c.most_common():
        print(f"  {k:28} {v}")
    print(f"\nWrote -> {OUT}")
    print("\nPer-company:")
    for n,r in sorted(results.items(), key=lambda x:x[1]["company_name"].lower()):
        owners = "; ".join(r["owners"][:3]) or "(none)"
        print(f"  {n}  {r['company_name'][:40]:40} {r['verdict'][:22]:22} owners: {owners[:50]}")

if __name__ == "__main__":
    main()
