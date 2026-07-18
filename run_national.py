#!/usr/bin/env python3
"""National trade-register pass + consolidator verification.
Usage: python3 run_national.py    (needs prefetch caches; disclosure crawl cached after first run)"""
import sys, os, json, csv
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))
import registers as R

def prog(m): print("  …", m, flush=True)
res = R.run_national(progress=prog, verify=True)
nat, per_area, vr = res["national"], res["per_area"], res["verify"]

print("\n" + "="*74)
print("NATIONAL — UK independent funeral-director companies (SAIF + NAFD, verified)")
print("="*74)
print(f"  Source pull: SAIF {nat['saif_branches']} FD branches | NAFD {nat['nafd_firms']} firms | {nat['areas']} areas")
print(f"  Firecrawl: {'ON' if nat['firecrawl_on'] else 'OFF (free crawl)'}")
print()
print(f"  Independent counterparties BEFORE verification .. {nat['independents_before_verification']}")
print(f"    – confirmed consolidator-owned (removed) ...... {nat['verified_consolidator_owned']}")
print(f"    – possible, unconfirmed (kept, flagged) ....... {nat['possible_consolidator_unconfirmed']}")
print(f"  ★ TOTAL distinct INDEPENDENT COUNTERPARTIES ..... {nat['total_independent_counterparties']}")
print(f"      (distinct trading brands: {nat['total_independent_brands']})")
print(f"      • SAIF members / NAFD-only / both .......... {nat['saif_members']} / {nat['nafd_only']} / {nat['both']}")
print(f"  Chains/groups excluded ......................... {nat['chains_flagged']}")
print(f"  Independents with email / website .............. {nat['with_email']} / {nat['with_website']}")
print(f"  THIN-coverage areas ............................ {nat['thin_areas']}")

print(f"\n--- CONFIRMED consolidator-owned (were counted independent, now removed) [{len(vr['confirmed'])}] ---")
for c in sorted(vr["confirmed"], key=lambda x: x["company"].lower())[:60]:
    print(f"  ✗ {c['company'][:38]:38} {','.join(c['areas'])[:16]:16} {c.get('ownership_evidence','')[:70]}")
if len(vr["confirmed"]) > 60: print(f"  … +{len(vr['confirmed'])-60} more (see CSV)")

print(f"\n--- POSSIBLE consolidator-owned — fuzzy CH match only, KEPT as independent, flag for review [{len(vr['possible'])}] ---")
for c in sorted(vr["possible"], key=lambda x: x["company"].lower())[:30]:
    print(f"  ? {c['company'][:38]:38} {','.join(c['areas'])[:16]:16} {c.get('ownership_evidence','')[:60]}")
if len(vr["possible"]) > 30: print(f"  … +{len(vr['possible'])-30} more (see CSV)")

# ---- funnel-join per-area table (one row per postcode area) ----
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)
cols = ["postcode_area","independent_counterparties","independent_brands","saif_only","nafd_only",
        "both","saif_members","chains_excluded","with_email","with_website","coverage_gap","thin_coverage"]
with open(os.path.join(OUT, "funnel_supply_by_area.csv"), "w", newline="") as fo:
    w = csv.writer(fo); w.writerow(cols)
    for p in sorted(per_area, key=lambda x: x["area"]):
        w.writerow([p["area"], p["independent_counterparties"], p["independent_brands"], p["saif_only"],
                    p["nafd_only"], p["both"], p["saif_members"], p["chains_excluded"],
                    p["with_email"], p["with_website"], p["coverage_gap"], p["thin"]])

# companies + verification detail
with open(os.path.join(OUT, "registers_national_companies.csv"), "w", newline="") as fo:
    w = csv.writer(fo)
    w.writerow(["company","independence","verify","sources","trading_names","brand_count","areas",
                "districts","email","website","phone","ownership_evidence"])
    for c in sorted(res["counterparties"], key=lambda x:(x["independence"]!="INDEPENDENT", x["company"].lower())):
        w.writerow([c["company"], c["independence"], c.get("verify",""), "+".join(c["sources"]),
                    " | ".join(c["trading_names"]), c["brand_count"], ",".join(c["areas"]),
                    ",".join(c["districts"]), c["email"], c["website"], c["phone"], c.get("ownership_evidence","")])
with open(os.path.join(OUT, "registers_national_summary.json"), "w") as fo:
    json.dump(nat, fo, indent=1)
print(f"\nWrote out/funnel_supply_by_area.csv (join key: postcode_area), "
      f"registers_national_companies.csv, registers_national_summary.json")
