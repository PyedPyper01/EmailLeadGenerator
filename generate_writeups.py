#!/usr/bin/env python3
"""
Marketplace supplier profiles for the independent funeral directors, generated from
each firm's OWN website content via the Claude Batch API.

  python3 app/writeup_content.py     # 1) crawl site content (no key) -> cache/site_content.json
  python3 generate_writeups.py       # 2) generate profiles (needs ANTHROPIC_API_KEY)

Batch API = 50% cost, async, no per-request timeouts (the Vet & Rank lesson).
Model is a config constant below — Haiku 4.5 is the right tier for 1,300 structured
extractions; change MODEL to "claude-opus-4-8" if you want maximum quality.

STRICT RULES enforced in the prompt: facts only, taken from the firm's own site; never
invent/embellish/infer; blank field if not stated; no unevidenced superlatives; consistent
structure; every profile WRITEUP_STATUS = DRAFT (supplier must approve before it goes live).
No website => blank write-up (those get written from a phone call instead).
"""
import os, sys, json, csv, time

MODEL = os.environ.get("WRITEUP_MODEL", "claude-haiku-4-5")   # or "claude-opus-4-8"
HERE = os.path.dirname(os.path.abspath(__file__))
FIRMS = os.path.join(HERE, "out", "marketplace_firms.csv")
CONTENT = os.path.join(HERE, "cache", "site_content.json")
OUT = os.path.join(HERE, "out", "marketplace_writeups.csv")
COLS = ["COMPANY_ID","COMPANY","WEBSITE","SUMMARY","SERVICES","FACILITIES","ACCREDITATIONS",
        "AREAS_SERVED","HERITAGE","ESTABLISHED","WRITEUP_STATUS"]

# Controlled vocabulary — SERVICES may ONLY be these tags (enforced by the schema enum),
# so 1,500 listings are filterable. Anything on the site that doesn't map is dropped.
SERVICE_TAGS = [
    "traditional funeral", "direct cremation", "woodland or natural burial", "burial",
    "cremation", "repatriation", "pre-paid funeral plans", "memorial masonry", "floristry",
    "horse-drawn or specialist transport", "own fleet", "chapel of rest",
    "24-hour availability", "home visits", "service in own chapel",
]

SYSTEM = (
    "You write short supplier profiles for a funeral-marketplace listing, read by bereaved "
    "families. You are given the raw text of a funeral director's OWN website. Extract ONLY "
    "facts stated on that site.\n"
    "ABSOLUTE RULES:\n"
    "- Facts only. Never invent, embellish, or infer. If something is not stated, leave the "
    "field empty ('' or []).\n"
    "- No superlatives or claims you cannot evidence ('best', 'award-winning', 'most trusted') "
    "unless the site states it and names the specific award.\n"
    "- CONFLICTS: if the site contradicts itself (e.g. 'fourth-generation' vs 'over 25 years'), "
    "prefer the more specific and more conservative statement. Never combine two contradictory "
    "claims into one.\n"
    "FIELDS:\n"
    "- summary: 40-60 words, plain and warm, no marketing hype. Cover who they are, how long "
    "established (only if stated), family-run or not (only if stated), and the area served. VARY "
    "THE OPENING across listings — sometimes lead with heritage, sometimes with the location "
    "served, sometimes with what makes them distinctive (own chapel, own fleet, green burial). "
    "Do NOT start every profile 'X is a family-run funeral director serving Y'. If the text "
    "doesn't support 40 words of fact, write a shorter factual summary rather than padding.\n"
    "- services: choose ONLY from the fixed tag list you are given. Map the site's wording to "
    "the closest tag and DROP anything that doesn't map to a tag. Do not invent tags. Consistency "
    "across listings matters more than matching the firm's exact wording.\n"
    "- facilities: physical things stated on the site that are NOT already a service tag — e.g. "
    "private car park, step-free / wheelchair access, on-site parking, reception/visiting room, "
    "refreshment facilities. Only where explicitly stated.\n"
    "- accreditations: only bodies the site names (SAIF, NAFD, BIE, etc.).\n"
    "- areas_served: place names the site says it serves.\n"
    "- heritage: the firm's longevity or generational claim, VERBATIM as stated (e.g. 'over 25 "
    "years', 'established for 175 years across five generations'). If longevity claims conflict, "
    "store only the more conservative one. Empty if none stated.\n"
    "- established: a specific founding YEAR only if stated (e.g. '1897'). Do NOT derive a year "
    "from 'X years'. Empty otherwise.\n"
    "Return only the structured object."
)

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "services": {"type": "array", "items": {"type": "string", "enum": SERVICE_TAGS}},
        "facilities": {"type": "array", "items": {"type": "string"}},
        "accreditations": {"type": "array", "items": {"type": "string"}},
        "areas_served": {"type": "array", "items": {"type": "string"}},
        "heritage": {"type": "string"},
        "established": {"type": "string"},
    },
    "required": ["summary","services","facilities","accreditations","areas_served","heritage","established"],
}

def known_accreditations(firm):
    s = firm.get("SOURCE","")
    out = []
    if "SAIF" in s: out.append("SAIF")
    if "NAFD" in s: out.append("NAFD")
    return out

def merge_accr(known, found):
    seen = set(); out = []
    for a in list(known) + list(found):
        k = a.strip().lower()
        if k and k not in seen: seen.add(k); out.append(a.strip())
    return out

def main():
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    firms = list(csv.DictReader(open(FIRMS, encoding="utf-8-sig")))
    content = json.load(open(CONTENT)) if os.path.exists(CONTENT) else {}
    have_text = {cid for cid, d in content.items() if d.get("text")}
    to_gen = [f for f in firms if f["COMPANY_ID"] in have_text]
    print(f"Firms: {len(firms)} | with site content: {len(to_gen)} | no website (blank draft): {len(firms)-len(to_gen)}")
    print(f"Model: {MODEL} | Batch API (50% cost, async)")

    if not key:
        print("\nANTHROPIC_API_KEY not set — cannot run generation. Set it and re-run:")
        print("  export ANTHROPIC_API_KEY=sk-ant-…  &&  python3 generate_writeups.py")
        print("(Site content is already crawled and cached; generation is the only step that needs the key.)")
        return

    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request
    client = anthropic.Anthropic(api_key=key)

    def build_req(f):
        text = content[f["COMPANY_ID"]]["text"]
        user = (f"Funeral director: {f['COMPANY']}\nWebsite: {f['WEBSITE']}\n"
                f"Postcode districts we have on file: {f.get('DISTRICTS','')}\n\n"
                f"SERVICES tag list (use ONLY these, drop anything that doesn't map):\n"
                + ", ".join(SERVICE_TAGS) + "\n\n"
                f"--- WEBSITE TEXT ---\n{text}")
        return Request(custom_id=f["COMPANY_ID"], params=MessageCreateParamsNonStreaming(
            model=MODEL, max_tokens=1024, system=SYSTEM,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        ))

    # Submit in Batch API chunks — OR resume from already-submitted batch ids, so a re-run
    # never re-pays for a batch that already ran. Delete cache/writeup_batch_ids.json to resubmit.
    bidfile = os.path.join(HERE, "cache", "writeup_batch_ids.json")
    results = {}
    if os.path.exists(bidfile):
        batch_ids = json.load(open(bidfile))
        print(f"  resuming from {len(batch_ids)} existing batch(es): {batch_ids}")
    else:
        batch_ids = []
        for i in range(0, len(to_gen), 2000):
            chunk = to_gen[i:i+2000]
            batch = client.messages.batches.create(requests=[build_req(f) for f in chunk])
            batch_ids.append(batch.id)
            print(f"  batch {batch.id} submitted ({len(chunk)} firms)", flush=True)
        json.dump(batch_ids, open(bidfile, "w"))
    for bid in batch_ids:
        while True:
            b = client.messages.batches.retrieve(bid)
            if b.processing_status == "ended": break
            time.sleep(30)
        ok = 0
        for r in client.messages.batches.results(bid):
            if r.result.type == "succeeded":
                txt = next((bl.text for bl in r.result.message.content if bl.type == "text"), "")
                try: results[r.custom_id] = json.loads(txt); ok += 1
                except Exception: results[r.custom_id] = None
            else:
                results[r.custom_id] = None
        print(f"  batch {bid} done ({ok} ok)", flush=True)

    rows = []
    for f in firms:
        cid = f["COMPANY_ID"]; d = results.get(cid)
        if d:
            accr = merge_accr(known_accreditations(f), d.get("accreditations", []))
            areas = d.get("areas_served") or [x for x in f.get("DISTRICTS","").split(",") if x]
            rows.append({
                "COMPANY_ID": cid, "COMPANY": f["COMPANY"], "WEBSITE": f["WEBSITE"],
                "SUMMARY": d.get("summary",""),
                "SERVICES": " | ".join(d.get("services",[])),
                "FACILITIES": " | ".join(d.get("facilities",[])),
                "ACCREDITATIONS": " | ".join(accr),
                "AREAS_SERVED": " | ".join(areas),
                "HERITAGE": d.get("heritage",""),
                "ESTABLISHED": d.get("established",""),
                "WRITEUP_STATUS": "DRAFT",
            })
        else:
            rows.append({"COMPANY_ID": cid, "COMPANY": f["COMPANY"], "WEBSITE": f["WEBSITE"],
                         "SUMMARY":"","SERVICES":"","FACILITIES":"","ACCREDITATIONS":"",
                         "AREAS_SERVED": "","HERITAGE":"","ESTABLISHED":"",
                         "WRITEUP_STATUS": "" if not f["WEBSITE"] else "NO_CONTENT"})

    with open(OUT, "w", newline="", encoding="utf-8-sig") as fo:
        w = csv.DictWriter(fo, fieldnames=COLS); w.writeheader()
        for r in rows: w.writerow(r)
    drafts = sum(1 for r in rows if r["WRITEUP_STATUS"] == "DRAFT")
    print(f"\nWrote {OUT}: {drafts} DRAFT profiles, {len(rows)-drafts} blank (no website/content).")

if __name__ == "__main__":
    main()
