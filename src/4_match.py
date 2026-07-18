#!/usr/bin/env python3
"""
Stage 4 — MATCH OSM trading firms to the Companies House register.
Join by name (+ East-of-England postcode bias) so we recover the registered
entity for firms that trade in CO but may be registered elsewhere.
Produces the full target company-number set = spine (CO-registered) UNION
OSM-matched national entities. Also flags OSM firms with NO CH match
(sole traders / unregistered) and obvious chains.

Output:
  out/target_companies.csv  (company_number set to run PSC against)
  out/osm_match_report.csv  (per OSM firm: matched? which company?)
"""
import csv, re, os
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTD = os.path.join(HERE, "out")

# Known consolidators / chains — a name hit here => CHAIN (also used later vs PSC).
# Only real consolidator brands; NOT generic words like "funeral services".
CHAINS = ["dignity","co-op","coop","co-operative","cooperative","funeral partners",
          "memoria","westerleigh","central england co","east of england co",
          "midcounties","southern co-op","chelmsford star","funeralcare","funeral partnership"]
# brand/website tokens that mark a chain outright
CHAIN_SITE = ["dignityfunerals","coop.co.uk","co-operative","eastofengland.coop",
              "chelmsfordstar","funeralpartners","memoria","westerleigh"]

STOP = re.compile(r"\b(ltd|limited|plc|llp|funeral|funerals|directors|director|services|"
                  r"service|the|and|of|family|group|co|home|homes|independent|"
                  r"bearers|bearing|memorial|cic)\b", re.I)

def core_tokens(name):
    s = name.lower()
    s = re.sub(r"&", " and ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = STOP.sub(" ", s)
    toks = [t for t in s.split() if len(t) >= 1 and not t.isdigit()]
    return set(toks)

def is_chain(name, website="", brand=""):
    hay = f"{name} {brand}".lower()
    if any(c in hay for c in CHAINS): return True
    w = (website or "").lower()
    if any(c in w for c in CHAIN_SITE): return True
    return False

def outward(pc):  # "CO16 8PA" -> "CO16"
    return (pc or "").split(" ")[0].upper()

def load(fn):
    with open(os.path.join(OUTD, fn)) as f:
        return list(csv.DictReader(f))

def main():
    spine = load("spine_co_funeral.csv")
    nat   = load("ch_funeral_uk.csv")
    osm   = load("osm_co.csv")

    # index national by core tokens + national token frequency (how "rare"/distinctive a surname is)
    nat_idx = []
    tok_freq = {}
    for c in nat:
        ct = core_tokens(c["company_name"])
        nat_idx.append((ct, c))
        for t in ct:
            tok_freq[t] = tok_freq.get(t, 0) + 1

    match_rows = []
    matched_numbers = {}   # company_number -> record (from spine or national)

    # spine companies are matched by definition
    for c in spine:
        matched_numbers[c["company_number"]] = {**c, "source":"CH-CO-registered"}

    for o in osm:
        nm = o["osm_name"]
        chain = is_chain(nm, o.get("website",""), o.get("brand",""))
        toks = core_tokens(nm)
        opc_out = outward(o["postcode"])

        # candidates: any national funeral row sharing >=1 distinctive token
        cands = []
        for ntoks, c in nat_idx:
            shared = toks & ntoks
            if not shared: continue
            cands.append((shared, c))

        hit = None; reason = ""
        for shared, c in cands:
            same_pc  = o["postcode"] and o["postcode"] == c["postcode"]
            same_out = opc_out and opc_out == outward(c["postcode"])
            # rarest shared surname token: <=2 national funeral firms carry it => distinctive
            rare = min((tok_freq.get(t, 99) for t in shared), default=99) <= 2
            if same_pc:
                hit, reason = c, "exact-postcode"; break
            if same_out and rare:
                hit, reason = c, "outward+rare-name"; break
            if rare and not hit:
                hit, reason = c, "rare-name-national"  # keep looking for a postcode-stronger one
        # if only a non-rare, non-postcode candidate exists -> reject (namesake risk)

        match_rows.append({
            "osm_name": nm, "osm_postcode": o["postcode"], "chain_flag":"CHAIN" if chain else "",
            "matched":"Y" if hit else "N",
            "ch_name": hit["company_name"] if hit else "",
            "ch_number": hit["company_number"] if hit else "",
            "ch_postcode": hit["postcode"] if hit else "",
            "match_reason": reason,
            "website": o.get("website",""), "phone": o.get("phone",""), "email": o.get("email",""),
        })
        if hit and not chain:
            rec = matched_numbers.get(hit["company_number"], {})
            matched_numbers[hit["company_number"]] = {
                "company_name": hit["company_name"], "company_number": hit["company_number"],
                "status": hit["status"], "postcode": hit["postcode"],
                "post_town": hit.get("post_town",""), "reg_address": hit.get("reg_address",""),
                "sic": hit.get("sic",""),
                "source": (rec.get("source","") + "+OSM-national").lstrip("+") if rec else "OSM-national",
            }

    # write target set
    cols=["company_name","company_number","status","postcode","post_town","reg_address","sic","source"]
    with open(os.path.join(OUTD,"target_companies.csv"),"w",newline="") as fo:
        w=csv.DictWriter(fo,fieldnames=cols); w.writeheader()
        for n,rec in sorted(matched_numbers.items()):
            w.writerow({k:rec.get(k,"") for k in cols})
    with open(os.path.join(OUTD,"osm_match_report.csv"),"w",newline="") as fo:
        w=csv.DictWriter(fo,fieldnames=list(match_rows[0].keys())); w.writeheader()
        for r in match_rows: w.writerow(r)

    n_match=sum(1 for r in match_rows if r["matched"]=="Y")
    n_chain=sum(1 for r in match_rows if r["chain_flag"])
    n_nomatch=sum(1 for r in match_rows if r["matched"]=="N")
    print(f"OSM CO firms: {len(osm)}  | matched to CH: {n_match} | no CH match: {n_nomatch} | chains(by name/site): {n_chain}")
    print(f"Target company-number set (spine ∪ OSM-national, chains excluded): {len(matched_numbers)}")
    print("\nOSM firms with NO Companies House match (possible sole traders / unregistered):")
    for r in match_rows:
        if r["matched"]=="N":
            print(f"  - {r['osm_name']}  ({r['osm_postcode']})  {'[chain]' if r['chain_flag'] else ''}")

if __name__ == "__main__":
    main()
