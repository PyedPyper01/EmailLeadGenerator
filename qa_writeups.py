#!/usr/bin/env python3
"""QA sampler: pull N random DRAFT profiles with their source site text, side by side,
so each generated claim can be checked against what the site actually says.
  python3 qa_writeups.py [N]     (default 50)  -> out/qa_sample.txt
Then read out/qa_sample.txt and count invented/embellished facts (claims in the profile
NOT supported by the source)."""
import os, sys, json, csv, random
HERE = os.path.dirname(os.path.abspath(__file__))
W = os.path.join(HERE, "out", "marketplace_writeups.csv")
C = os.path.join(HERE, "cache", "site_content.json")
OUT = os.path.join(HERE, "out", "qa_sample.txt")

n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
rows = [r for r in csv.DictReader(open(W, encoding="utf-8-sig")) if r["WRITEUP_STATUS"] == "DRAFT"]
content = json.load(open(C))
random.seed(1)   # reproducible sample
sample = random.sample(rows, min(n, len(rows)))

with open(OUT, "w", encoding="utf-8") as fo:
    for i, r in enumerate(sample, 1):
        src = content.get(r["COMPANY_ID"], {}).get("text", "")[:3000]
        fo.write(f"\n{'='*90}\n[{i}/{len(sample)}] {r['COMPANY']}   <{r['WEBSITE']}>\n{'='*90}\n")
        fo.write("PROFILE (to verify against source below):\n")
        for k in ["SUMMARY","SERVICES","FACILITIES","ACCREDITATIONS","AREAS_SERVED","HERITAGE","ESTABLISHED"]:
            if r[k]: fo.write(f"  {k}: {r[k]}\n")
        fo.write("\nSOURCE SITE TEXT:\n" + src + "\n")
print(f"Wrote {OUT}: {len(sample)} profile/source pairs for review.")
