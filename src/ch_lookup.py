#!/usr/bin/env python3
"""Ad-hoc: search the full CH bulk file for funeral (SIC 96030) companies whose
name contains a query string — used to find firms that TRADE in CO but are
registered under a different postcode (the registered-vs-trading caveat)."""
import zipfile, csv, io, re, sys, os
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP  = os.path.join(HERE, "data", "companies.zip")
q = " ".join(sys.argv[1:]).upper() or "ROBINSON"
sic_prefix = "96030"
z = zipfile.ZipFile(ZIP); name = z.namelist()[0]
with z.open(name) as f:
    txt = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
    r = csv.reader(txt); next(r)
    for row in r:
        if len(row) < 30: continue
        nm = row[0].upper()
        if q not in nm: continue
        sics = [row[i] for i in (26,27,28,29) if row[i].strip()]
        if not any(s.strip().split(" ")[0].startswith(sic_prefix) for s in sics):
            continue
        print(f"{row[1]}  {row[0][:42]:42}  {row[11]:20}  PC={row[9]:9}  {row[6]}")
