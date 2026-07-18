#!/usr/bin/env python3
"""
Stage 2 — NATIONAL FUNERAL INDEX.
Dump every SIC 96030 (funeral) company in the UK, whatever its registered
postcode. Used to recover firms that TRADE in CO but are registered elsewhere
(e.g. Daniel Robinson & Sons, registered CM23) — the registered-vs-trading fix.
Output: out/ch_funeral_uk.csv
"""
import zipfile, csv, io, re, os
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP  = os.path.join(HERE, "data", "companies.zip")
OUT  = os.path.join(HERE, "out", "ch_funeral_uk.csv")
SIC_PREFIX = "96030"

def main():
    z = zipfile.ZipFile(ZIP); name = z.namelist()[0]
    rows = []
    with z.open(name) as f:
        txt = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
        r = csv.reader(txt); next(r)
        for row in r:
            if len(row) < 30: continue
            sics = [row[i] for i in (26,27,28,29) if row[i].strip()]
            if not any(s.strip().split(" ")[0].startswith(SIC_PREFIX) for s in sics):
                continue
            rows.append({
                "company_name": row[0].strip(),
                "company_number": row[1].strip(),
                "status": row[11].strip(),
                "postcode": re.sub(r"\s+"," ",row[9].strip().upper()),
                "post_town": row[6].strip(),
                "reg_address": ", ".join(x.strip() for x in [row[4],row[5],row[6],row[7]] if x.strip()),
                "sic": " | ".join(s.strip() for s in sics),
            })
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT,"w",newline="") as fo:
        w=csv.DictWriter(fo, fieldnames=list(rows[0].keys())); w.writeheader()
        for c in rows: w.writerow(c)
    print(f"National SIC {SIC_PREFIX} funeral companies: {len(rows)} -> {OUT}")

if __name__ == "__main__":
    main()
