#!/usr/bin/env python3
"""
Stage 1 — THE SPINE.
Stream the Companies House Free Company Data Product (bulk CSV inside a zip) and
filter to one category (SIC 96030 = funeral & related) in one area (CO postcode).

Free, official, no API key. Open Government Licence.
Source: https://download.companieshouse.gov.uk/en_output.html

Output: out/spine_co_funeral.csv  — one row per registered company that matches.
Prints honest counts (active vs other status, SIC noise).
"""
import zipfile, csv, io, re, sys, os, json

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP  = os.path.join(HERE, "data", "companies.zip")
OUT  = os.path.join(HERE, "out", "spine_co_funeral.csv")

# --- POC parameters -----------------------------------------------------------
SIC_PREFIX   = "96030"          # funeral & related activities (filter broadly on this one)
AREA_RE      = re.compile(r"^CO\d", re.I)   # CO1..CO16 = Colchester postcode area
# ------------------------------------------------------------------------------

# CSV column indexes (from the 55-col header)
C_NAME, C_NUM = 0, 1
C_AL1, C_AL2, C_TOWN, C_COUNTY, C_COUNTRY, C_PC = 4, 5, 6, 7, 8, 9
C_CAT, C_STATUS = 10, 11
C_INCORP = 14
C_SIC = [26, 27, 28, 29]

def norm_pc(pc):
    return re.sub(r"\s+", " ", (pc or "").strip().upper())

def sic_list(row):
    out = []
    for i in C_SIC:
        v = (row[i] or "").strip()
        if v:
            out.append(v)
    return out

def has_sic(row, prefix):
    for v in sic_list(row):
        code = v.split(" ")[0].strip()
        if code.startswith(prefix):
            return True
    return False

def main():
    z = zipfile.ZipFile(ZIP)
    name = z.namelist()[0]
    total = 0
    sic_hits = 0            # SIC 96030 anywhere in UK
    co_all = []            # SIC 96030 AND postcode CO*  (any status)
    with z.open(name) as f:
        txt = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
        r = csv.reader(txt)
        header = next(r)
        for row in r:
            if len(row) < 30:
                continue
            total += 1
            if not has_sic(row, SIC_PREFIX):
                continue
            sic_hits += 1
            pc = norm_pc(row[C_PC])
            if not AREA_RE.match(pc):
                continue
            co_all.append({
                "company_name": row[C_NAME].strip(),
                "company_number": row[C_NUM].strip(),
                "status": row[C_STATUS].strip(),
                "category": row[C_CAT].strip(),
                "incorp_date": row[C_INCORP].strip(),
                "reg_address": ", ".join(x.strip() for x in [row[C_AL1], row[C_AL2], row[C_TOWN], row[C_COUNTY]] if x.strip()),
                "post_town": row[C_TOWN].strip(),
                "county": row[C_COUNTY].strip(),
                "postcode": pc,
                "sic": " | ".join(sic_list(row)),
            })

    # split by status for honesty
    active = [c for c in co_all if c["status"].lower().startswith("active")]
    other  = [c for c in co_all if not c["status"].lower().startswith("active")]

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    cols = ["company_name","company_number","status","category","incorp_date",
            "reg_address","post_town","county","postcode","sic"]
    with open(OUT, "w", newline="") as fo:
        w = csv.DictWriter(fo, fieldnames=cols)
        w.writeheader()
        for c in sorted(co_all, key=lambda x: x["company_name"].lower()):
            w.writerow(c)

    print(f"Total company rows scanned .............. {total:,}")
    print(f"SIC {SIC_PREFIX} (funeral) anywhere in UK ... {sic_hits:,}")
    print(f"SIC {SIC_PREFIX} AND postcode CO* .......... {len(co_all)}")
    print(f"   - Active .............................. {len(active)}")
    print(f"   - Other status (dissolved/liq/etc.) ... {len(other)}")
    print(f"Wrote spine -> {OUT}")

    # dump a quick preview
    print("\nActive CO funeral-director companies (spine):")
    for c in sorted(active, key=lambda x: x["company_name"].lower()):
        print(f"  {c['company_number']}  {c['company_name'][:45]:45}  {c['postcode']:9}  {c['post_town']}")

if __name__ == "__main__":
    main()
