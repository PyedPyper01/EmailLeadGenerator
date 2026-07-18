#!/usr/bin/env python3
"""
Register-spine pipeline (library form) — reuses the exact POC logic.

One-time PREP (cached, slow):
  - build national SIC index from the Companies House bulk one-file  (~60s)
  - extract PSC records for every company in that index               (~90s, scans 2GB PSC bulk)
Per-AREA RUN (fast, reads cache):
  - filter national index by postcode area
  - OSM Overpass for trading branches + contacts
  - match OSM -> CH (strict: exact/outward postcode OR nationally-rare surname)
  - PSC independence verdict + PSC-owner -> OSM trading-name bridge
  - email gap-fill (Firecrawl if FIRECRAWL_API_KEY set, else free stdlib crawl)

No third-party packages. Python 3.9 stdlib only.
"""
import os, re, csv, json, io, zipfile, glob, ssl, time, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")
PSCDIR = os.path.join(DATA, "psc")
CACHE = os.path.join(HERE, "cache")
os.makedirs(CACHE, exist_ok=True)

CH_MONTH = "2026-07-01"
PSC_DATE = "2026-07-16"
CH_ONEFILE = f"https://download.companieshouse.gov.uk/BasicCompanyDataAsOneFile-{CH_MONTH}.zip"
PSC_URL = lambda i: f"https://download.companieshouse.gov.uk/psc-snapshot-{PSC_DATE}_{i}of32.zip"

# ---- categories (extendable). Filter broadly on the SIC prefix. --------------
CATEGORIES = {
    "96030": {"label": "Funeral directors (SIC 96030)", "sic_prefix": "96030",
              "osm": ['nwr["shop"="funeral_directors"]', 'nwr["office"="funeral_director"]',
                      'nwr["amenity"="funeral_hall"]']},
}

# ---- postcode-area bounding boxes (S,W,N,E) for the OSM query -----------------
BBOX = {
    "CO": (51.75, 0.50, 52.12, 1.35), "IP": (51.95, 0.70, 52.50, 1.50),
    "CM": (51.55, 0.20, 51.99, 0.98), "SS": (51.49, 0.55, 51.63, 1.00),
    "CB": (52.00, -0.15, 52.55, 0.60), "NR": (52.35, 0.70, 52.99, 1.85),
    "EN": (51.60, -0.22, 51.76, 0.06), "IG": (51.52, 0.01, 51.64, 0.27),
    "RM": (51.49, 0.10, 51.66, 0.42), "AL": (51.70, -0.45, 51.90, -0.10),
    "SG": (51.78, -0.35, 52.10, 0.20), "PE": (52.30, -0.55, 52.90, 0.35),
}

# ================= shared logic ported verbatim from the POC ===================
CHAINS = ["dignity","co-op","coop","co-operative","cooperative","funeral partners",
          "memoria","westerleigh","central england co","east of england co",
          "midcounties","southern co-op","chelmsford star","funeralcare","funeral partnership"]
CHAIN_SITE = ["dignityfunerals","coop.co.uk","co-operative","eastofengland.coop",
              "chelmsfordstar","funeralpartners","memoria","westerleigh"]
CONSOLIDATORS = ["dignity","funeral partners","co-op","co-operative","cooperative",
                 "memoria","westerleigh","central england co","east of england co",
                 "midcounties","southern co-op","funeral services partnership",
                 "funeral partnership","legacy funeral"]
STOP = re.compile(r"\b(ltd|limited|plc|llp|funeral|funerals|directors|director|services|"
                  r"service|the|and|of|family|group|co|home|homes|independent|"
                  r"bearers|bearing|memorial|cic)\b", re.I)
GENERIC = set("ltd limited plc funeral funerals directors director services service the "
              "and of family group co home homes independent bearers bearing memorial cic "
              "sons son mr mrs ms dr lord sir".split())

def core_tokens(name):
    s = re.sub(r"&"," and ", (name or "").lower())
    s = re.sub(r"[^a-z0-9 ]"," ", s)
    s = STOP.sub(" ", s)
    return {t for t in s.split() if len(t) >= 1 and not t.isdigit()}

def surnames(text):
    s = re.sub(r"[^a-z ]"," ", (text or "").lower())
    return {t for t in s.split() if len(t) > 2 and t not in GENERIC}

def is_chain(name, website="", brand=""):
    hay = f"{name} {brand}".lower()
    if any(c in hay for c in CHAINS): return True
    w = (website or "").lower()
    return any(c in w for c in CHAIN_SITE)

def is_consolidator(name):
    n = (name or "").lower()
    return any(c in n for c in CONSOLIDATORS)

def norm_pc(pc): return re.sub(r"\s+"," ",(pc or "").strip().upper())
def outward(pc): return norm_pc(pc).split(" ")[0]
def area_of(pc):
    m = re.match(r"^([A-Z]{1,2})", norm_pc(pc)); return m.group(1) if m else ""

# ---- email extraction / host-provider filter (ported) ------------------------
BAD_EMAIL = re.compile(r"@(?:example|sentry|wixpress|domain|email|yourcompany|company|test)\.|"
                       r"\.(?:png|jpg|jpeg|gif|svg|webp|css|js)$|^[a-f0-9]{16,}@|@2x", re.I)
HOST_PROVIDER = re.compile(r"^(?:34sp|wix|squarespace|godaddy|hostinger|123-?reg|fasthosts|ionos|"
                           r"1and1|names|tsohost|krystal|heartinternet|siteground|bluehost|weebly|"
                           r"wordpress|automattic|cloudflare|mailchimp|sentry|shopify|createandhost|"
                           r"itseeze|mediaworks|wixpress|sentry-next)\.", re.I)
CTX = ssl.create_default_context(); CTX.check_hostname=False; CTX.verify_mode=ssl.CERT_NONE
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"
FC_KEY = (os.environ.get("FIRECRAWL_API_KEY") or "").strip()

def edom(e): return (e.split("@")[1] or "").lower() if "@" in e else ""
def extract_emails(text):
    out=set()
    if not text: return out
    s=re.sub(r"\s*[\[(]\s*at\s*[\])]\s*/?\s*","@",str(text),flags=re.I)
    s=re.sub(r"\s*[\[(]\s*dot\s*[\])]\s*/?\s*",".",s,flags=re.I)
    for m in re.finditer(r"(?:mailto:)?([a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,})",s,re.I):
        e=m.group(1).lower().rstrip(".,;:)]")
        if not BAD_EMAIL.search(e) and len(e)<80: out.add(e)
    return out
def _fetch(url, timeout=12):
    try:
        req=urllib.request.Request(url, headers={"User-Agent":UA,"Accept":"text/html"})
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return r.read(1_500_000).decode("utf-8","replace")
    except Exception: return ""
def _firecrawl(url, key=None):
    body=json.dumps({"url":url,"formats":["markdown","links"],"onlyMainContent":False,
                     "timeout":20000,"waitFor":1200}).encode()
    req=urllib.request.Request("https://api.firecrawl.dev/v1/scrape", data=body,
        headers={"Content-Type":"application/json","Authorization":f"Bearer {(key or FC_KEY)}"})
    try:
        with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
            d=json.loads(r.read().decode("utf-8","replace"))
        data=d.get("data",{})
        return (data.get("markdown","")+" "+" ".join(str(l) for l in data.get("links",[])))
    except Exception: return ""
def _domain_of(w): return re.sub(r"^https?://","",(w or "").lower()).replace("www.","").split("/")[0].strip()
def _contact_links(html, origin, domain):
    links=[]
    for m in re.finditer(r'href=["\']([^"\']+)["\']', html, re.I):
        if re.search(r"contact|about|get-in-touch|find-us|enquir", m.group(1), re.I):
            try:
                u=urllib.parse.urljoin(origin, m.group(1))
                if domain in u: links.append(u)
            except Exception: pass
    return links[:2]
def crawl_email(website, fc_key=None):
    """Return (emails_list, backend). Firecrawl if a key is available, else free stdlib."""
    key = (fc_key or FC_KEY or "").strip()
    domain=_domain_of(website)
    if not domain or "." not in domain: return [], "none"
    origin=f"https://{domain}"
    backend="firecrawl" if key else "free"
    grab=(lambda u:_firecrawl(u,key)) if key else _fetch
    html=grab(origin); emails=extract_emails(html); pages=1
    if not emails:
        targets=[f"{origin}/contact",f"{origin}/contact-us"] if key else (_contact_links(html,origin,domain) or [f"{origin}/contact"])
        for u in targets:
            emails|=extract_emails(grab(u)); pages+=1
            if emails or pages>=3: break
    allm=list(emails)
    same=[e for e in allm if edom(e)==domain or edom(e).endswith("."+domain) or domain.endswith("."+edom(e))]
    chosen=(same if same else [e for e in allm if not HOST_PROVIDER.match(edom(e))])
    return sorted(set(chosen))[:4], backend

# ---- web-search domain discovery (free, no key) ------------------------------
# Directory/aggregator/register hosts that are never the firm's OWN website.
AGGREGATORS = ("duckduckgo.","google.","bing.","192.com","yell.co","find-and-update",
    "company-information","find-open","facebook.","linkedin.","instagram.","twitter.","x.com",
    "funeralguide","dignityfunerals","coop.co.uk","co-operative","eastofengland.coop",
    "funeral-directory","nafd.org","saif.org","thebestof","cylex","tuugo","scoot.co","freeindex",
    "brownbook","opencorporates","endole","companieshouse","gov.uk","youtube.","tripadvisor",
    "yelp.","thomsonlocal","funeralzone","beyond.life","aboutmyarea","misterwhat","funeral-notices",
    "legacy.com","withgrace","localfuneral","yourfuneraldirectors","funeraldirectorsregister",
    "fenixfuneral","rosycompany","mimoji","funeralrelated","threebestrated","harveydarke",
    "wikipedia.","apple.com","bark.com","checkatrade","trustpilot","glassdoor","indeed.")
# Names that are NOT consumer funeral directors -> skip discovery (SIC-96030 noise).
NOISE_NAME = re.compile(r"firework|burial|cemeter|memorial|embalm|realisation|garden of rest|"
                        r"\bwood\b|masonry|monument|crematori", re.I)

_DDG_BLOCKED=[False]   # per-process cooldown once DuckDuckGo starts returning its anomaly page

def _ddg(query):
    if _DDG_BLOCKED[0]: return []
    try:
        data=urllib.parse.urlencode({"q":query,"kl":"uk-en"}).encode()
        req=urllib.request.Request("https://html.duckduckgo.com/html/", data=data,
                                   headers={"User-Agent":UA,"Accept":"text/html"})
        with urllib.request.urlopen(req, timeout=18, context=CTX) as r:
            html=r.read().decode("utf-8","replace")
    except Exception:
        return []
    if "result__a" not in html and ("anomaly" in html.lower() or "captcha" in html.lower()):
        _DDG_BLOCKED[0]=True; return []
    urls=[]
    for m in re.finditer(r'uddg=([^&"]+)', html):
        u=urllib.parse.unquote(m.group(1))
        if u.startswith("http"): urls.append(u)
    if not urls:
        urls=[m.group(1) for m in re.finditer(r'result__a"[^>]*href="(https?://[^"]+)"',html)]
    return urls

def _name_head(name):
    """The leading proper-name words of a company name, before the generic words.
    '&' is dropped (L&K -> l k -> 'lk'); connective words are skipped, not breaks."""
    s=re.sub(r"&","",(name or "").lower())
    s=re.sub(r"[^a-z0-9 ]"," ",s)
    stops={"funeral","funerals","directors","director","services","service","ltd",
           "limited","plc","llp","group","company"}
    out=[]
    for w in s.split():
        if w in stops: break
        if w in ("and","the","of","co"): continue
        out.append(w)
    return out

def _slug(words): return re.sub(r"[^a-z0-9]","","".join(words))

GENERIC_STEM = {"family","funeral","funerals","services","service","directors","director",
                "memorial","group","home","homes","and","the","sons","son","independent","co"}

def _guess_domains(name):
    """Likely own-domain candidates from the firm name — no search engine, no rate limit.
    Ordered by suffix first so surname+funerals (e.g. poultonfunerals) isn't crowded out.
    Generic words (family, services…) are never used as a stem on their own."""
    head=_name_head(name)
    if not head: return []
    raw=[_slug(head), _slug([w for w in head if w not in GENERIC_STEM][-1:] or head[-1:]),
         _slug(head[:1])]
    stems=[b for b in dict.fromkeys(raw) if len(b)>=2 and b not in GENERIC_STEM]
    cands=[]
    for suf in ["funerals","funeraldirectors","","funeralservices","fd"]:
        for st in stems:
            for tld in (".co.uk",".com"):
                cands.append(st+suf+tld)
    seen=set(); out=[]
    for c in cands:
        if c in seen: continue
        seen.add(c); out.append(c)
    return out[:16]

def _probe(domain, name_tokens, loc_terms, lenient=False):
    """GET the homepage (short timeout); keep it only if the page reads as a funeral site AND
    matches this firm's location (town/postcode) OR a distinctive (len>=6) name token. For an
    initialism firm (L&K, PGO) the guessed domain is itself specific, so a funeral page suffices."""
    html=_fetch(f"https://{domain}", timeout=6)
    if not html: return False
    low=html.lower()
    if not any(w in low for w in ("funeral","cremation","bereave")): return False
    if any(t in low for t in loc_terms): return True
    if any(t in low for t in name_tokens if len(t)>=6): return True
    return lenient and "funeral" in domain

def discover_website(name, town="", core=None, postcode=""):
    """Find the firm's OWN website. Primary: guess likely domains and probe them (parallel,
    no rate limit). Secondary: DuckDuckGo (until it blocks). A hit must be a funeral site that
    matches this firm's location (town/postcode) or a distinctive name token."""
    if NOISE_NAME.search(name or ""): return ""
    core = core if core is not None else core_tokens(name)
    name_tokens=[t for t in core if len(t)>=3] or [_slug(_name_head(name))]
    # location terms: town words (len>=4), postcode outward, postcode without space
    loc={w.lower() for w in re.split(r"[ ,.]", town or "") if len(w)>=4}
    pc=norm_pc(postcode)
    if pc:
        loc.add(outward(pc).lower()); loc.add(pc.replace(" ","").lower())
    # 1) guess + probe candidates concurrently, first valid wins
    head=_name_head(name)
    lenient=len(_slug(head))<=3   # initialism (L&K, PGO) — domain is specific, low collision risk
    cands=_guess_domains(name)
    if cands:
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            futs={ex.submit(_probe, d, name_tokens, loc, lenient): d for d in cands}
            hits=[futs[f] for f in cf.as_completed(futs) if f.result()]
        if hits:
            return "https://"+sorted(hits, key=len)[0]+"/"
    # 2) fall back to a search query (skips itself once DDG is blocked)
    strong=[t for t in core if len(t)>=3]; concat2=re.sub(r"[^a-z0-9]","",(name or "").lower())
    q=f"{re.sub(r'( limited| ltd)$','',(name or ''),flags=re.I)} {town} funeral directors".strip()
    for u in _ddg(q)[:10]:
        dom=_domain_of(u)
        if not dom or any(a in dom for a in AGGREGATORS): continue
        if any(t in dom for t in strong) or (len(concat2)>=4 and concat2[:6] in dom):
            return f"https://{dom}/"
    return ""

# ============================ PREP (one-time) =================================
def _index_path(sic): return os.path.join(CACHE, f"index_{sic}.csv")
def _psc_path(sic):   return os.path.join(CACHE, f"psc_{sic}.json")

def cache_status(sic="96030"):
    return {
        "companies_zip": os.path.exists(os.path.join(DATA,"companies.zip")),
        "psc_files": len(glob.glob(os.path.join(PSCDIR,"*.zip"))),
        "index": os.path.exists(_index_path(sic)),
        "psc_index": os.path.exists(_psc_path(sic)),
    }

def download_bulk(progress=lambda m: None):
    zp=os.path.join(DATA,"companies.zip")
    os.makedirs(DATA, exist_ok=True); os.makedirs(PSCDIR, exist_ok=True)
    if not os.path.exists(zp):
        progress("Downloading Companies House bulk company data (~496 MB, one time)…")
        urllib.request.urlretrieve(CH_ONEFILE, zp)
    have=len(glob.glob(os.path.join(PSCDIR,"*.zip")))
    if have < 32:
        progress(f"Downloading PSC bulk snapshot (32 files, ~2 GB, one time)… have {have}/32")
        for i in range(1,33):
            fp=os.path.join(PSCDIR,f"psc_{i}.zip")
            if not os.path.exists(fp): urllib.request.urlretrieve(PSC_URL(i), fp)
        progress("PSC download complete.")

def build_index(sic="96030", progress=lambda m: None):
    """National SIC index from the CH bulk one-file. Cached."""
    outp=_index_path(sic)
    if os.path.exists(outp): return outp
    progress(f"Building national SIC {sic} index from the register (~60s, one time)…")
    zp=os.path.join(DATA,"companies.zip")
    z=zipfile.ZipFile(zp); name=z.namelist()[0]; rows=[]
    with z.open(name) as f:
        txt=io.TextIOWrapper(f, encoding="utf-8", errors="replace"); r=csv.reader(txt); next(r)
        for row in r:
            if len(row) < 30: continue
            sics=[row[i] for i in (26,27,28,29) if row[i].strip()]
            if not any(s.strip().split(" ")[0].startswith(sic) for s in sics): continue
            rows.append({
                "company_name":row[0].strip(),"company_number":row[1].strip(),"status":row[11].strip(),
                "postcode":norm_pc(row[9]),"post_town":row[6].strip(),
                "reg_address":", ".join(x.strip() for x in [row[4],row[5],row[6],row[7]] if x.strip()),
                "sic":" | ".join(s.strip() for s in sics),
            })
    with open(outp,"w",newline="") as fo:
        w=csv.DictWriter(fo, fieldnames=list(rows[0].keys())); w.writeheader()
        for c in rows: w.writerow(c)
    progress(f"Index built: {len(rows)} SIC {sic} companies nationwide.")
    return outp

def build_psc_index(sic="96030", progress=lambda m: None):
    """Extract PSC records for every company in the SIC index. One 2GB scan. Cached."""
    outp=_psc_path(sic)
    if os.path.exists(outp): return outp
    idx=list(csv.DictReader(open(build_index(sic, progress))))
    nums=[c["company_number"] for c in idx]
    prefixes=tuple(('{"company_number":"%s"' % n).encode() for n in nums)
    numset=set(nums); found={n:[] for n in nums}
    files=sorted(glob.glob(os.path.join(PSCDIR,"*.zip")))
    progress(f"Scanning {len(files)} PSC files once for {len(nums)} companies (~90s, one time)…")
    for i,fp in enumerate(files,1):
        z=zipfile.ZipFile(fp)
        with z.open(z.namelist()[0]) as f:
            for line in f:
                if not line.startswith(prefixes): continue
                rec=json.loads(line); cn=rec.get("company_number")
                if cn not in numset: continue
                d=rec.get("data",{})
                found[cn].append({"kind":d.get("kind",""),"name":d.get("name",""),
                                  "ceased":bool(d.get("ceased_on"))})
        if i%8==0 or i==len(files): progress(f"  PSC scan {i}/{len(files)}")
    json.dump(found, open(outp,"w"))
    progress("PSC index built.")
    return outp

def ensure_prep(sic="96030", progress=lambda m: None):
    download_bulk(progress)
    build_index(sic, progress)
    build_psc_index(sic, progress)

# ============================ per-AREA RUN ====================================
def _overpass(area, sic, progress=lambda m: None):
    bbox=BBOX.get(area)
    q_parts=CATEGORIES[sic]["osm"]
    if bbox:
        s,w,n,e=bbox
        body="".join(f'{p}({s},{w},{n},{e});' for p in q_parts)
    else:
        # no bbox known: fall back to a name/addr filter across a wide EE box, then postcode-filter
        body="".join(f'{p}(51.3,-0.6,53.0,1.9);' for p in q_parts)
        progress(f"(no bounding box for area {area}; using wide fallback + postcode filter)")
    Q=f"[out:json][timeout:60];({body});out center tags;"
    # Multiple Overpass mirrors: the main endpoint is intermittently WAF-blocked (HTTP 406)
    # and some mirrors are slow/empty — try each and take the first that returns POIs.
    endpoints=["https://overpass-api.de/api/interpreter",
               "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
               "https://overpass.kumi.systems/api/interpreter",
               "https://overpass.osm.ch/api/interpreter"]
    data=urllib.parse.urlencode({"data":Q}).encode()
    last=""; best=[]
    for ep in endpoints:
        try:
            req=urllib.request.Request(ep, data=data, headers={
                "User-Agent":"register-spine/1.0 (supplier sourcing POC)",
                "Accept":"application/json",
                "Content-Type":"application/x-www-form-urlencoded"})
            with urllib.request.urlopen(req, timeout=70, context=CTX) as r:
                els=json.loads(r.read().decode("utf-8","replace")).get("elements",[])
            if els:
                return els
            best=best or els
        except Exception as ex:
            last=f"{ep.split('//')[1].split('/')[0]}: {ex}"; continue
    if not best: progress(f"OSM query failed on all mirrors ({last})")
    return best

def _osm_firms(area, sic, progress):
    els=_overpass(area, sic, progress)
    rows=[]
    for el in els:
        t=el.get("tags",{}); nm=(t.get("name") or "").strip()
        if not nm: continue
        pc=norm_pc(t.get("addr:postcode",""))
        if area_of(pc)!=area:  # keep only POIs whose postcode is in the selected area
            continue
        def pick(*ks):
            for k in ks:
                if t.get(k): return t[k].strip()
            return ""
        rows.append({"osm_name":nm,"postcode":pc,"city":pick("addr:city","addr:town"),
                     "phone":pick("phone","contact:phone"),
                     "website":pick("website","contact:website"),
                     "email":pick("email","contact:email"),"brand":pick("brand")})
    # dedupe by (norm name, postcode)
    seen=set(); out=[]
    for r in sorted(rows,key=lambda x:x["postcode"]):
        k=(re.sub(r"[^a-z0-9]","",r["osm_name"].lower()),r["postcode"])
        if k in seen: continue
        seen.add(k); out.append(r)
    return out

def _verdict(pscs):
    live=[p for p in pscs if not p["ceased"]]
    corp=[p for p in live if "corporate" in p["kind"] or "legal-person" in p["kind"]]
    indiv=[p for p in live if "individual" in p["kind"]]
    cons=[p for p in corp if is_consolidator(p["name"])]
    if cons: v="CHAIN"
    elif corp: v="GROUP"
    elif indiv: v="INDEPENDENT"
    else: v="UNKNOWN"
    return v, [p["name"] for p in live], [p["name"] for p in corp]

def run_area(area, sic="96030", progress=lambda m: None, firecrawl_key=None, discover=True):
    area=area.strip().upper()
    fc_key=(firecrawl_key or FC_KEY or "").strip()
    ensure_prep(sic, progress)
    idx=list(csv.DictReader(open(_index_path(sic))))
    psc=json.load(open(_psc_path(sic)))

    # 1) spine: national index filtered to the area
    area_re=re.compile(rf"^{re.escape(area)}\d")
    spine=[c for c in idx if area_re.match(c["postcode"])]
    progress(f"Register spine: {len(spine)} SIC {sic} companies registered in {area}.")

    # token frequency for rare-name gating
    nat_tokens=[]; tok_freq={}
    for c in idx:
        ct=core_tokens(c["company_name"]); nat_tokens.append((ct,c))
        for t in ct: tok_freq[t]=tok_freq.get(t,0)+1

    # 2) OSM trading firms in area
    progress(f"Querying OpenStreetMap for trading branches in {area}…")
    osm=_osm_firms(area, sic, progress)
    progress(f"OSM: {len(osm)} funeral POIs with a {area} postcode.")

    # 3) match OSM -> CH (strict) ; collect target set = spine ∪ confident national matches
    targets={c["company_number"]:{**c,"source":{"CH-"+area+"-registered"}} for c in spine}
    match_rows=[]
    for o in osm:
        nm=o["osm_name"]; chain=is_chain(nm,o.get("website",""),o.get("brand",""))
        toks=core_tokens(nm); opc_out=outward(o["postcode"]); hit=None; reason=""
        for ntoks,c in nat_tokens:
            shared=toks&ntoks
            if not shared: continue
            same_pc=o["postcode"] and o["postcode"]==c["postcode"]
            same_out=opc_out and opc_out==outward(c["postcode"])
            rare=min((tok_freq.get(t,99) for t in shared),default=99)<=2
            if same_pc: hit,reason=c,"exact-postcode"; break
            if same_out and rare: hit,reason=c,"outward+rare-name"; break
            if rare and not hit: hit,reason=c,"rare-name-national"
        match_rows.append({**o,"chain_flag":chain,"matched":bool(hit),
                           "ch_number":hit["company_number"] if hit else "",
                           "ch_name":hit["company_name"] if hit else "","reason":reason})
        if hit and not chain:
            rec=targets.get(hit["company_number"])
            src=(rec["source"] if rec else set()); src.add("OSM-national")
            targets[hit["company_number"]]={**hit,"source":src}

    # direct OSM contact by matched number (skip chains)
    osm_by_num={}
    for r in match_rows:
        if r["matched"] and r["ch_number"] and not r["chain_flag"]:
            osm_by_num.setdefault(r["ch_number"],[]).append(r)
    unmatched=[r for r in match_rows if not r["matched"]]

    # 4) assemble leads
    leads=[]; used_bridge=set()
    for num,t in targets.items():
        v,owners,corp=_verdict(psc.get(num,[]))
        owner_surn=set()
        for o in owners: owner_surn|=surnames(o)
        website=phone=email=""; trade=[]; src=set(t.get("source",set()))
        for r in osm_by_num.get(num,[]):
            website=website or r["website"]; phone=phone or r["phone"]; email=email or r["email"]
            trade.append(r["osm_name"]); src.add("OSM")
        for r in unmatched:
            if r["chain_flag"]: continue
            if owner_surn & surnames(r["osm_name"]):
                website=website or r["website"]; phone=phone or r["phone"]; email=email or r["email"]
                trade.append(r["osm_name"]); used_bridge.add(r["osm_name"]); src.add("PSC-owner→OSM bridge")
        reg_area=area_of(t["postcode"])
        flags=[]
        if reg_area and reg_area!=area:
            flags.append(f"registered in {reg_area}, trades in {area}")
        if not trade:
            flags.append(f"no OSM trading presence found in {area} (reg-only / may trade elsewhere)")
        leads.append({
            "company_name":t["company_name"],"company_number":num,"status":t["status"],
            "independence":v,"psc_owners":"; ".join(owners),"corporate_owners":"; ".join(corp),
            "trading_names":" | ".join(sorted(set(trade))),
            "reg_postcode":t["postcode"],"reg_address":t.get("reg_address",""),"post_town":t.get("post_town",""),
            "website":website,"phone":phone,"email":email,"email_source":"OSM" if email else "",
            "sources":", ".join(sorted(src)),"flags":"; ".join(flags),"sic":t.get("sic",""),
        })

    # 5) email gap-fill: (a) web-search a website for firms that have none, (b) crawl for the email
    firecrawl_used=bool(fc_key)
    discovered=0
    if discover:
        need=[l for l in leads if not l["website"] and not NOISE_NAME.search(l["company_name"])]
        if need: progress(f"Web-searching {len(need)} firms for a website (domain-guess + DuckDuckGo)…")
        import concurrent.futures as cf
        def _find(l):
            return l, discover_website(l["company_name"], l.get("post_town",""),
                                       core_tokens(l["company_name"]), l.get("reg_postcode",""))
        with cf.ThreadPoolExecutor(max_workers=5) as ex:
            for l,site in ex.map(_find, need):
                if site:
                    l["website"]=site; discovered+=1
                    l["sources"]=(l["sources"]+", web-search").strip(", ")
        if need: progress(f"  discovered {discovered} websites via web search.")
    to_crawl=[l for l in leads if l["website"] and not l["email"]]
    if to_crawl: progress(f"Crawling {len(to_crawl)} websites for emails ({'Firecrawl' if fc_key else 'free crawl'})…")
    for l in to_crawl:
        emails,backend=crawl_email(l["website"], fc_key)
        if emails:
            l["email"]="; ".join(emails); l["email_source"]=f"crawl:{backend}"

    # 6) unregistered appendix (OSM firms with no CH match, not used as bridge)
    unreg=[]; seen=set()
    for r in unmatched:
        if r["osm_name"] in used_bridge: continue
        k=re.sub(r"[^a-z0-9]","",r["osm_name"].lower())
        if k in seen: continue
        seen.add(k)
        unreg.append({"osm_name":r["osm_name"],"postcode":r["postcode"],"city":r.get("city",""),
                      "kind":"chain" if r["chain_flag"] else "independent (unregistered)",
                      "website":r["website"],"phone":r["phone"],"email":r["email"]})

    leads.sort(key=lambda x:x["company_name"].lower())
    unreg.sort(key=lambda x:(x["kind"],x["osm_name"].lower()))
    from collections import Counter
    stats={
        "area":area,"category":CATEGORIES[sic]["label"],
        "registered_total":len(leads),
        "independence":dict(Counter(l["independence"] for l in leads)),
        "with_email":sum(1 for l in leads if l["email"]),
        "with_website":sum(1 for l in leads if l["website"]),
        "with_phone":sum(1 for l in leads if l["phone"]),
        "unregistered_total":len(unreg),
        "unregistered_independent":sum(1 for u in unreg if u["kind"].startswith("independent")),
        "unregistered_chain":sum(1 for u in unreg if u["kind"]=="chain"),
        "mismatch_flags":sum(1 for l in leads if l["flags"]),
        "websites_discovered":discovered,
        "firecrawl_used":firecrawl_used,
        "firecrawl_note":"" if firecrawl_used else "Firecrawl off (no key) — used free crawl + web-search discovery; a few JS-rendered sites may still be missed. Paste a key above to enable it.",
    }
    progress(f"Done: {len(leads)} registered firms, {stats['with_email']} with email, {len(unreg)} unregistered.")
    return {"leads":leads,"unregistered":unreg,"stats":stats}
