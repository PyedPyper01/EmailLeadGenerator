#!/usr/bin/env python3
"""Per-area trade-register runner. Usage: python3 run_registers.py [AREA]"""
import sys, os, json, csv
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))
import registers as R

area = (sys.argv[1] if len(sys.argv) > 1 else "CO").upper()
def prog(m): print("  …", m, flush=True)

res = R.run_area(area, enrich=True, progress=prog)
s = res["stats"]
print("\n" + "="*72)
print(f"TRADE-REGISTER RESULT — {area}")
print("="*72)
print(f"  Distinct trading BRANDS ............... {s['distinct_brands']}")
print(f"  Distinct COMMERCIAL COUNTERPARTIES ... {s['distinct_counterparties']}")
print(f"    INDEPENDENT counterparties (funnel #) {s['independent_counterparties']}   "
      f"[brands: {s['independent_brands']}]")
print(f"      • SAIF-only / NAFD-only / both ... {s['saif']} / {s['nafd_only']} / {s['both']}")
print(f"    chain/group flagged ................. {s['chain']}")
print(f"  Independents with email / website .... {s['with_email']} / {s['with_website']}")
print(f"  Firecrawl: {'ON' if s['firecrawl_on'] else 'OFF (free crawl + domain-guess)'}")
print(f"  [coverage] CH SIC-96030 in {area}: {s['ch_96030_registered_in_area']} | "
      f"genuine trading firms in neither register: {s['coverage_gap_in_ch_not_register']}"
      + ("  ⚠THIN" if s['thin_coverage'] else ""))

print("\n--- INDEPENDENT commercial counterparties ---")
for c in res["counterparties"]:
    if c["independence"] != "INDEPENDENT": continue
    tn = "  (trading as: " + ", ".join(c["trading_names"]) + ")" if c["brand_count"] > 1 else ""
    print(f"  • {c['company'][:40]:40} [{'+'.join(c['sources']):9}] {','.join(c['districts'])}{tn}")
    print(f"      {c['email'] or '(no email)':38} {c['website'] or ''}")

print("\n--- CHAIN / GROUP flagged ---")
for c in res["counterparties"]:
    if c["independence"] != "CHAIN": continue
    tn = " [" + ", ".join(c["trading_names"]) + "]" if c["brand_count"] > 1 else ""
    print(f"  ✗ {c['company'][:40]:40} {c['basis']}{tn}")

print(f"\n--- COVERAGE GAP: genuine trading firms in Companies House (SIC 96030, active, "
      f"non-dormant) but NOT in SAIF/NAFD [{len(res['coverage_gaps'])}] ---")
for g in res["coverage_gaps"]:
    print(f"    ? {g['name'][:44]:44} {g['postcode']}")

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", f"registers_{area}.csv")
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out, "w", newline="") as fo:
    w = csv.writer(fo)
    w.writerow(["company","independence","sources","trading_names","brand_count","districts","email","website","phone","basis"])
    for c in res["counterparties"]:
        w.writerow([c["company"], c["independence"], "+".join(c["sources"]), " | ".join(c["trading_names"]),
                    c["brand_count"], ",".join(c["districts"]), c["email"], c["website"], c["phone"], c["basis"]])
print(f"\nWrote {out}")
