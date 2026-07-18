#!/usr/bin/env python3
"""
Stage 3 — OSM CONTACTS (free Google-Places replacement).
Parse the Overpass result, keep CO-postcode funeral POIs (the target trading
area), dedupe firms across branches, and pull phone/website/email/hours.
Output: out/osm_co.csv  (one row per distinct firm+postcode POI)
"""
import json, csv, re, os
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW  = os.path.join(HERE, "data", "osm_co_raw.json")
OUT  = os.path.join(HERE, "out", "osm_co.csv")

def pick(t,*ks):
    for k in ks:
        if t.get(k): return t[k].strip()
    return ""

def clean_site(u):
    u=(u or "").strip()
    if not u: return ""
    return u

def main():
    d=json.load(open(RAW)); els=d.get("elements",[])
    rows=[]
    for e in els:
        t=e.get("tags",{})
        nm=(t.get("name") or "").strip()
        if not nm: continue
        pc=re.sub(r"\s+"," ",(t.get("addr:postcode") or "").strip().upper())
        rows.append({
            "osm_name": nm,
            "postcode": pc,
            "in_co": "Y" if pc.startswith("CO") else "",
            "city": pick(t,"addr:city","addr:town"),
            "street": " ".join(x for x in [t.get("addr:housenumber",""),t.get("addr:street","")] if x).strip(),
            "phone": pick(t,"phone","contact:phone"),
            "website": clean_site(pick(t,"website","contact:website")),
            "email": pick(t,"email","contact:email"),
            "brand": pick(t,"brand"),
            "hours": pick(t,"opening_hours"),
        })
    # keep CO-postcode POIs as the trading set (bbox also caught CM/IP spillover)
    co = [r for r in rows if r["in_co"]=="Y"]
    # dedupe by (normalized name, postcode)
    seen=set(); dedup=[]
    for r in sorted(co, key=lambda x:x["postcode"]):
        key=(re.sub(r"[^a-z0-9]","",r["osm_name"].lower()), r["postcode"])
        if key in seen: continue
        seen.add(key); dedup.append(r)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT,"w",newline="") as fo:
        w=csv.DictWriter(fo, fieldnames=list(dedup[0].keys())); w.writeheader()
        for r in dedup: w.writerow(r)
    spill=[r for r in rows if r["postcode"] and not r["in_co"]]
    print(f"OSM POIs total: {len(rows)} | CO-postcode: {len(co)} | deduped CO firms: {len(dedup)} | bbox spillover (CM/IP, excluded): {len(spill)}")
    print(f"  with website: {sum(1 for r in dedup if r['website'])} | phone: {sum(1 for r in dedup if r['phone'])} | email: {sum(1 for r in dedup if r['email'])}")
    print(f"Wrote -> {OUT}")

if __name__ == "__main__":
    main()
