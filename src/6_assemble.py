#!/usr/bin/env python3
"""
Stage 6 — ASSEMBLE.
Join: registered target companies + PSC independence + OSM trading contacts.
Key trick: bridge OSM trading firms to the registered entity by matching a PSC
OWNER surname to the OSM trading name (official-data join, not a guess).
e.g. "A Simple Goodbye Ltd" (PSC: Roy Gwinnell) <- OSM "R Gwinnell & Sons" -> gwinnell.co.uk

Output: out/leads_pre_email.csv   (websites attached; emails filled in stage 7)
        out/osm_unregistered.csv   (CO trading firms with NO CH match — sole traders)
"""
import csv, json, os, re
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTD = os.path.join(HERE, "out")

GENERIC = set("ltd limited plc funeral funerals directors director services service the "
              "and of family group co home homes independent bearers bearing memorial cic "
              "sons son family's mr mrs ms mr. dr lord sir".split())

def surnames(text):
    s = re.sub(r"[^a-z ]"," ", (text or "").lower())
    return {t for t in s.split() if len(t)>2 and t not in GENERIC}

def load(fn):
    with open(os.path.join(OUTD, fn)) as f:
        return list(csv.DictReader(f))

def main():
    targets = load("target_companies.csv")
    psc = json.load(open(os.path.join(OUTD,"psc_results.json")))
    mr = load("osm_match_report.csv")
    osm = load("osm_co.csv")
    # optional hand-verified web-search discoveries (company_number -> website/phone/email/note)
    disc = {}
    dpath = os.path.join(OUTD,"websites_discovered.csv")
    if os.path.exists(dpath):
        for r in csv.DictReader(open(dpath)):
            disc[r["company_number"].strip()] = r

    # direct OSM contact by matched ch_number
    osm_by_num = {}
    for r in mr:
        # skip chain-flagged OSM firms: a Co-op/Dignity branch that name-collides with an
        # independent (e.g. "Clacton Co-operative..." vs "Clacton Funeral Service Ltd") must
        # not be attached to that independent as a trading name.
        if r["matched"]=="Y" and r["ch_number"] and not r["chain_flag"]:
            osm_by_num.setdefault(r["ch_number"], []).append(r)

    # unmatched OSM firms (candidate bridges / sole-trader appendix)
    unmatched = [r for r in mr if r["matched"]=="N"]

    leads = []
    used_bridge_names = set()
    for t in targets:
        num = t["company_number"]
        p = psc.get(num, {})
        verdict = p.get("verdict","")
        owners = p.get("owners", [])
        owner_surn = set()
        for o in owners: owner_surn |= surnames(o)

        website=phone=email=""; trade_names=[]; src=set([t.get("source","CH")])
        # 1) direct OSM matches
        for r in osm_by_num.get(num, []):
            website = website or r["website"]; phone = phone or r["phone"]; email = email or r["email"]
            if r["osm_name"]: trade_names.append(r["osm_name"])
            src.add("OSM")
        # 2) PSC-owner -> OSM trading-name bridge
        for r in unmatched:
            if r["chain_flag"]: continue
            osm_surn = surnames(r["osm_name"])
            if owner_surn & osm_surn:
                website = website or r["website"]; phone = phone or r["phone"]; email = email or r["email"]
                trade_names.append(r["osm_name"]); used_bridge_names.add(r["osm_name"])
                src.add("PSC-owner->OSM bridge")

        # 3) hand-verified web-search discovery (fills gaps only)
        note=""
        if num in disc:
            d=disc[num]
            if d.get("website") and not website: website=d["website"]
            if d.get("phone") and not phone: phone=d["phone"]
            if d.get("email") and not email: email=d["email"]
            note=d.get("note","")
            src.add("web-search")

        leads.append({
            "company_name": t["company_name"], "company_number": num, "status": t["status"],
            "independence": verdict,
            "psc_owners": "; ".join(owners),
            "trading_names": " | ".join(sorted(set(trade_names))),
            "reg_postcode": t["postcode"], "reg_address": t.get("reg_address",""),
            "website": website, "phone": phone, "email": email,
            "sic": t.get("sic",""),
            "sources": ", ".join(sorted(src)),
            "note": note,
        })

    cols=["company_name","company_number","status","independence","psc_owners","trading_names",
          "reg_postcode","reg_address","website","phone","email","sic","sources","note"]
    with open(os.path.join(OUTD,"leads_pre_email.csv"),"w",newline="") as fo:
        w=csv.DictWriter(fo,fieldnames=cols); w.writeheader()
        for l in sorted(leads,key=lambda x:x["company_name"].lower()): w.writerow(l)

    # appendix: CO trading firms with no CH match and not used as a bridge (true sole traders/unregistered)
    appx=[]
    seen=set()
    for r in unmatched:
        if r["osm_name"] in used_bridge_names: continue
        key=re.sub(r"[^a-z0-9]","",r["osm_name"].lower())
        if key in seen: continue
        seen.add(key)
        appx.append({"osm_name":r["osm_name"],"osm_postcode":r["osm_postcode"],
                     "chain_flag":r["chain_flag"],"website":r["website"],"phone":r["phone"],"email":r["email"]})
    with open(os.path.join(OUTD,"osm_unregistered.csv"),"w",newline="") as fo:
        w=csv.DictWriter(fo,fieldnames=list(appx[0].keys())); w.writeheader()
        for a in appx: w.writerow(a)

    withweb=sum(1 for l in leads if l["website"])
    bridged=sum(1 for l in leads if "bridge" in l["sources"])
    print(f"Registered leads: {len(leads)} | with website (OSM+bridge): {withweb} | via PSC-owner bridge: {bridged}")
    print(f"OSM-only unregistered/sole-trader appendix: {len(appx)} firms")
    print("\nLeads with a website found:")
    for l in sorted(leads,key=lambda x:x["company_name"].lower()):
        if l["website"]:
            print(f"  {l['company_name'][:36]:36} {l['website'][:44]:44} [{l['sources']}]")

if __name__ == "__main__":
    main()
