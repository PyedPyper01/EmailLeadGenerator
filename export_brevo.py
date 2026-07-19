#!/usr/bin/env python3
"""Package the verified independent funeral directors into ready-to-use files:
  1) out/brevo_funeral_directors.csv   — Brevo import (independents WITH email)
  2) out/phone_only_funeral_directors.csv — verified independents with phone but NO email
  3) out/possible_consolidator_review.csv — the 28 'possible' (kept OUT of outreach) + CH links
  4) out/contactable_by_area.csv        — counts per postcode area (email / phone-only / total)
No new analysis — reads the cached national pass."""
import sys, os, re, csv, html
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "app"))
import registers as R

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
CH_URL = "https://find-and-update.company-information.service.gov.uk/company/"

def clean(s):
    s = html.unescape((s or "").strip()).replace("\\'", "'").replace('\\"', '"')
    return re.sub(r"\s{2,}", " ", s)

_OK_TLD = {"uk","com","net","org","info","biz","io","london","scot","wales","cymru",
           "life","care","email","co","eu","me","org.uk","co.uk"}
def _valid_email(e):
    if not re.match(r"^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z.]{2,}$", e): return False
    tld = e.rsplit(".", 1)[-1]
    return tld in _OK_TLD  # reject crawl artifacts like '...co.ukl'

def best_email(email_str):
    parts = [e.strip().lower().rstrip(".") for e in re.split(r"[;,]", email_str or "") if "@" in e]
    parts = [e for e in parts if _valid_email(e)]
    if not parts: return ""
    pref = ("info@", "enquiries@", "enquiry@", "contact@", "office@", "admin@", "hello@", "mail@")
    for p in pref:
        for e in parts:
            if e.startswith(p): return e
    return parts[0]

def to_e164(phone):
    if not phone: return ""
    p = re.split(r"[;,/]", phone)[0]
    d = re.sub(r"[^\d+]", "", p)
    if d.startswith("+"):
        d2 = re.sub(r"\D", "", d)
        return "+" + d2 if len(d2) >= 11 else ""
    d = re.sub(r"\D", "", d)
    if d.startswith("44") and len(d) >= 11: return "+" + d
    if d.startswith("0") and len(d) >= 10:  return "+44" + d[1:]
    return ""

def rep_branch(c):
    """A representative branch for the firm's address/postcode: prefer a SAIF branch (full
    address) that matches the firm email, else any SAIF branch, else any branch with a postcode."""
    branches = [b for br in c.get("_brands", []) for b in br.get("branches", [])]
    email = best_email(c.get("email", ""))
    def score(b):
        s = 0
        if b.get("source") == "SAIF": s += 4
        if b.get("address"): s += 2
        if b.get("postcode"): s += 1
        if email and (b.get("email") or "").lower().find(email) >= 0: s += 3
        return s
    branches = [b for b in branches if b.get("postcode")]
    return max(branches, key=score) if branches else {}

def trading_brands(c):
    others = [clean(t) for t in c.get("trading_names", []) if clean(t) != clean(c["company"])]
    seen, out = set(), []
    for t in others:
        k = t.lower()
        if k not in seen: seen.add(k); out.append(t)
    return " | ".join(out)

def main():
    def prog(m): print("  …", m, flush=True)
    res = R.run_national(progress=prog, verify=True)
    cps = res["counterparties"]

    # Fill EMAIL for independents that lack one, using the SAME crawl the per-area run uses
    # (NAFD pages give phone+website but not email). Cached, so re-runs are instant.
    import json, concurrent.futures as cf
    fc = (os.environ.get("FIRECRAWL_API_KEY") or "").strip()
    ecache_path = os.path.join(R.CACHE, "email_enrich_cache.json")
    ecache = json.load(open(ecache_path)) if os.path.exists(ecache_path) else {}
    # enrich any independent lacking a WEBSITE (needed for a marketplace listing) or an EMAIL
    need = [c for c in cps if c["independence"] == "INDEPENDENT" and c.get("verify") != "POSSIBLE"
            and (not best_email(c.get("email", "")) or not c.get("website"))]
    prog(f"Enriching {len(need)} independents (website/email) ({'Firecrawl' if fc else 'free crawl + domain-guess'})…")
    def enrich(c):
        key = clean(c["company"]).lower() + "|" + ",".join(c["districts"])
        cached = ecache.get(key, {})
        c["website"] = c.get("website") or cached.get("website", "")
        c["email"] = c.get("email") or cached.get("email", "")
        if c.get("website") and c.get("email"):
            return  # nothing more to find
        b = rep_branch(c)
        if not c.get("website"):
            site = R.P.discover_website(c["company"], (b.get("address", "").split(",")[-1] if b else ""),
                                        None, b.get("postcode", "") if b else "")
            if site: c["website"] = site
        if c.get("website") and not best_email(c.get("email", "")):
            try:
                emails, _ = R.P.crawl_email(c["website"], fc)
                if emails: c["email"] = "; ".join(emails)
            except Exception: pass
        ecache[key] = {"email": c.get("email", ""), "website": c.get("website", "")}
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(enrich, need))
    json.dump(ecache, open(ecache_path, "w"))

    brevo, phone_only, possible, firms = [], [], [], []
    id_seen = {}
    def company_id(name, district):
        base = re.sub(r"[^a-z0-9]+", "-", clean(name).lower()).strip("-")[:48] + "-" + (district or "xx").lower()
        n = id_seen.get(base, 0); id_seen[base] = n + 1
        return base if n == 0 else f"{base}-{n+1}"
    for c in cps:
        if c.get("verify") == "POSSIBLE":
            pb = rep_branch(c)
            possible.append({
                "COMPANY_ID": company_id(c["company"], pb.get("district", "")),
                "COMPANY": clean(c["company"]), "TRADING_BRANDS": trading_brands(c),
                "ADDRESS": clean(pb.get("address", "")), "POSTCODE": pb.get("postcode", ""),
                "DISTRICT": pb.get("district", ""), "POSTCODE_AREA": pb.get("area", ""),
                "PHONE": to_e164(c.get("phone", "")), "EMAIL": best_email(c.get("email", "")),
                "WEBSITE": c.get("website", ""),
                "AREAS": ",".join(c["areas"]), "DISTRICTS": ",".join(c["districts"]),
                "SUSPECTED_PARENT": c.get("ownership_evidence", "").split(":")[-1].strip()[:60],
                "EVIDENCE": c.get("ownership_evidence", ""),
                "CH_MATCH_NAME": c.get("ch_name", ""),
                "COMPANIES_HOUSE_URL": (CH_URL + c["ch_number"]) if c.get("ch_number") else "",
            })
            continue
        if c["independence"] != "INDEPENDENT":
            continue
        email = best_email(c.get("email", ""))
        # ONE ROW PER AREA (not one per firm nationally). A multi-branch firm — e.g. F.A. Albin trading in
        # SE and DA, or P L Mulligan in SE and DA — must appear once in EACH area it operates in, with that
        # area's branch address + phone, sharing the same COMPANY_ID root. Branches WITHIN an area still
        # collapse to one row. (One slot per area per firm — what the slot model assumes.)
        #
        # AREAS come from the TRADING branches (SAIF/NAFD addresses) ONLY — never Companies House. CH is for
        # verification, not location, so a firm filed under its registered-office area (Albin: E13) still
        # lists under where it actually trades (SE16 → SE, plus DA), not the registered office.
        tbranches = [b for br in c.get("_brands", []) for b in br.get("branches", [])
                     if b.get("source") in ("SAIF", "NAFD") and b.get("postcode") and b.get("area")]
        by_area = {}
        for b in tbranches:
            by_area.setdefault(b["area"], []).append(b)
        if not by_area:                       # rare: no trading branch with a postcode — keep old single-row behaviour
            rb = rep_branch(c)
            if rb.get("area"):
                by_area[rb["area"]] = [rb]
        def _bscore(b):                        # best branch WITHIN an area for the address/phone shown
            s = 0
            if b.get("source") == "SAIF": s += 4
            if b.get("address"): s += 2
            if email and (b.get("email") or "").lower().find(email) >= 0: s += 3
            return s
        for a, bs in sorted(by_area.items()):
            b = max(bs, key=_bscore)
            cid = company_id(c["company"], b.get("district", "") or a)
            row = {
                "EMAIL": email, "FIRSTNAME": "", "LASTNAME": "",
                "COMPANY": clean(c["company"]),
                "SMS": to_e164(b.get("phone") or c.get("phone", "")),   # THIS area's branch phone
                "WEBSITE": c.get("website", ""),
                "ADDRESS": clean(b.get("address", "")),
                "POSTCODE": b.get("postcode", ""),
                "DISTRICT": b.get("district", ""),
                "POSTCODE_AREA": a,
                "CATEGORY": "Funeral directors",
                "SOURCE": "+".join(c["sources"]),
                "SAIF_MEMBER": "Y" if "SAIF" in c["sources"] else "N",
                "TRADING_BRANDS": trading_brands(c),
            }
            # master marketplace record (all independents, incl. web-only, keyed on COMPANY_ID)
            firms.append({"COMPANY_ID": cid, **row, "AREAS": ",".join(c["areas"]),
                          "DISTRICTS": ",".join(c["districts"]), "BRAND_COUNT": c.get("brand_count", 1),
                          "PHONE_RAW": b.get("phone") or c.get("phone", "")})
            if email:
                brevo.append(row)
            elif row["SMS"]:
                phone_only.append(row)

    # dedupe brevo by EMAIL
    seen, dedup = set(), []
    for r in brevo:
        if r["EMAIL"] in seen: continue
        seen.add(r["EMAIL"]); dedup.append(r)
    brevo = dedup

    cols = ["EMAIL","FIRSTNAME","LASTNAME","COMPANY","SMS","WEBSITE","ADDRESS","POSTCODE",
            "DISTRICT","POSTCODE_AREA","CATEGORY","SOURCE","SAIF_MEMBER","TRADING_BRANDS"]
    def write(path, rows, fieldnames, bom=False):
        enc = "utf-8-sig" if bom else "utf-8"
        with open(os.path.join(OUT, path), "w", newline="", encoding=enc) as fo:
            w = csv.DictWriter(fo, fieldnames=fieldnames); w.writeheader()
            for r in rows: w.writerow({k: r.get(k, "") for k in fieldnames})

    write("brevo_funeral_directors.csv", brevo, cols, bom=True)
    write("phone_only_funeral_directors.csv", phone_only, cols, bom=True)
    write("possible_consolidator_review.csv", possible,
          ["COMPANY","AREAS","DISTRICTS","EMAIL","WEBSITE","CH_MATCH_NAME","COMPANIES_HOUSE_URL","EVIDENCE"])
    # master marketplace record (join base for the write-ups), keyed on COMPANY_ID
    write("marketplace_firms.csv", firms,
          ["COMPANY_ID","COMPANY","WEBSITE","EMAIL","SMS","ADDRESS","POSTCODE","DISTRICT",
           "POSTCODE_AREA","DISTRICTS","AREAS","SOURCE","SAIF_MEMBER","TRADING_BRANDS"], bom=True)

    # per-area counts + website coverage
    from collections import defaultdict
    area = defaultdict(lambda: {"email": 0, "phone_only": 0, "web": 0, "total": 0})
    for f in firms:
        a = f["POSTCODE_AREA"] or "?"
        area[a]["total"] += 1
        if f["EMAIL"]: area[a]["email"] += 1
        elif f["SMS"]: area[a]["phone_only"] += 1
        if f["WEBSITE"]: area[a]["web"] += 1
    rows = [{"POSTCODE_AREA": a, "with_email": v["email"], "phone_only": v["phone_only"],
             "total_contactable": v["email"] + v["phone_only"], "with_website": v["web"],
             "firms": v["total"],
             "website_rate": f"{round(100*v['web']/v['total'])}%" if v["total"] else "0%"}
            for a, v in area.items()]
    rows.sort(key=lambda x: -x["total_contactable"])
    write("contactable_by_area.csv", rows,
          ["POSTCODE_AREA","firms","with_email","phone_only","total_contactable","with_website","website_rate"])

    # ============ STANDARD SUPPLY FILES FOR POSTCODE PROSPECTOR ============
    # register_supply.csv is the canonical hand-off: one row per verified independent
    # counterparty, keyed on COMPANY_ID (joins to marketplace_writeups.csv). Confirmed
    # consolidators excluded; POSSIBLE consolidator-owned firms kept OUT (separate file).
    import datetime
    vdate = datetime.date.today().isoformat()
    def src_tag(s):
        p = s.split("+"); saif = "SAIF" in p; nafd = "NAFD" in p
        return "both" if (saif and nafd) else ("saif" if saif else "nafd")

    supply = [{
        "COMPANY_ID": f["COMPANY_ID"], "COMPANY": f["COMPANY"], "TRADING_BRANDS": f["TRADING_BRANDS"],
        "ADDRESS": f["ADDRESS"], "POSTCODE": f["POSTCODE"], "DISTRICT": f["DISTRICT"],
        "POSTCODE_AREA": f["POSTCODE_AREA"], "PHONE": f["SMS"], "EMAIL": f["EMAIL"],
        "CONTACT_NAME": "",  # registers are firm-level; no contact person
        "WEBSITE": f["WEBSITE"], "CATEGORY": f["CATEGORY"], "SOURCE": src_tag(f["SOURCE"]),
        "SAIF_MEMBER": f["SAIF_MEMBER"], "INDEPENDENT_OR_CHAIN": "independent",
        "PARENT_GROUP": "", "VERIFIED_DATE": vdate,
    } for f in firms]
    SUPPLY_COLS = ["COMPANY_ID","COMPANY","TRADING_BRANDS","ADDRESS","POSTCODE","DISTRICT",
        "POSTCODE_AREA","PHONE","EMAIL","CONTACT_NAME","WEBSITE","CATEGORY","SOURCE",
        "SAIF_MEMBER","INDEPENDENT_OR_CHAIN","PARENT_GROUP","VERIFIED_DATE"]
    write("register_supply.csv", supply, SUPPLY_COLS, bom=True)

    # flagged POSSIBLE consolidator-owned — kept OUT of the main supply, for adjudication
    poss_supply = [{
        "COMPANY_ID": p["COMPANY_ID"], "COMPANY": p["COMPANY"], "TRADING_BRANDS": p.get("TRADING_BRANDS",""),
        "ADDRESS": p.get("ADDRESS",""), "POSTCODE": p.get("POSTCODE",""), "DISTRICT": p.get("DISTRICT",""),
        "POSTCODE_AREA": p.get("POSTCODE_AREA",""), "PHONE": p.get("PHONE",""), "EMAIL": p.get("EMAIL",""),
        "CONTACT_NAME": "", "WEBSITE": p.get("WEBSITE",""), "CATEGORY": "Funeral directors",
        "SOURCE": "", "SAIF_MEMBER": "", "INDEPENDENT_OR_CHAIN": "possible-chain",
        "PARENT_GROUP": p.get("SUSPECTED_PARENT",""), "VERIFIED_DATE": vdate,
        "COMPANIES_HOUSE_URL": p.get("COMPANIES_HOUSE_URL",""), "EVIDENCE": p.get("EVIDENCE",""),
    } for p in possible]
    write("register_supply_possible.csv", poss_supply, SUPPLY_COLS + ["COMPANIES_HOUSE_URL","EVIDENCE"])

    # by-area supply counts (primary area = the firm's registered-office area)
    from collections import defaultdict as _dd
    S = _dd(lambda: dict(counterparties=0, brands=0, saif=0, nafd_only=0, both=0,
                         chains_excluded=0, with_email=0, with_phone=0, with_website=0))
    for f in firms:
        a = f["POSTCODE_AREA"] or "?"; d = S[a]; st = src_tag(f["SOURCE"])
        d["counterparties"] += 1; d["brands"] += int(f.get("BRAND_COUNT", 1))
        d["saif" if st == "saif" else ("nafd_only" if st == "nafd" else "both")] += 1
        if f["EMAIL"]: d["with_email"] += 1
        if f["SMS"]: d["with_phone"] += 1
        if f["WEBSITE"]: d["with_website"] += 1
    for c in cps:
        if c["independence"] == "CHAIN":
            S[rep_branch(c).get("area") or "?"]["chains_excluded"] += 1
    supply_area = [{"POSTCODE_AREA": a, **v} for a, v in S.items()]
    supply_area.sort(key=lambda x: -x["counterparties"])
    write("register_supply_by_area.csv", supply_area,
          ["POSTCODE_AREA","counterparties","brands","saif","nafd_only","both",
           "chains_excluded","with_email","with_phone","with_website"])

    wsite = sum(1 for f in firms if f["WEBSITE"])
    print("\n" + "="*64)
    print(f"  ★ PROSPECTOR SUPPLY ....... {len(supply)} independent counterparties -> out/register_supply.csv")
    print(f"    by-area counts .......... out/register_supply_by_area.csv")
    print(f"    possible (flagged, OUT) . {len(possible)} -> out/register_supply_possible.csv")
    print(f"  With WEBSITE .............. {wsite} ({round(100*wsite/len(firms))}%)")
    print(f"  (legacy) Brevo email/phone  {len(brevo)} / {len(phone_only)}")
    print("="*64)
    print("\nMost contactable areas (email / phone / total / website-rate):")
    print(f"  {'area':6}{'email':>6}{'phone':>6}{'total':>6}{'web%':>7}")
    for r in rows[:20]:
        print(f"  {r['POSTCODE_AREA']:6}{r['with_email']:>6}{r['phone_only']:>6}{r['total_contactable']:>6}{r['website_rate']:>7}")

if __name__ == "__main__":
    main()
