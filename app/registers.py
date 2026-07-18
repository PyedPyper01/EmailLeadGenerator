#!/usr/bin/env python3
"""
TRADE-REGISTER sourcing for funeral directors — SAIF + NAFD, pulled WHOLESALE and
cached (monthly refresh), then served per postcode area from cache.

Sources (primary; Funeral Guide deliberately excluded — Dignity-owned, DB-right risk):
  SAIF  https://saif.org.uk/find-a-member/  (ASL store-locator: one call = every member,
        with address/postcode/phone/email/website). SAIF membership is INDEPENDENT BY
        CONSTITUTION — Dignity and Co-op cannot be members.
  NAFD  https://www.funeral-directory.co.uk/  (NAFD's own directory; sitemap lists every
        member firm page, slug ends in the postcode). NAFD is MIXED (independents + Dignity
        + Co-op) so NAFD-only firms go through evidence checks.

Pipeline: fetch+cache each register -> allocate by the firm's OWN postcode (area+district)
-> dedupe/merge into distinct company records -> classify by provenance -> enrich
(website/email/contact; Firecrawl if key) -> per-area report.

stdlib only. Reuses app/pipeline.py for crawling, discovery and the Companies House PSC data.
"""
import os, re, json, ssl, time, urllib.request, urllib.parse, glob
import pipeline as P   # crawl_email, discover_website, core_tokens, CH index/psc, consolidators

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "cache")
RAW = os.path.join(HERE, "data", "registers")
os.makedirs(CACHE, exist_ok=True); os.makedirs(RAW, exist_ok=True)

SAIF_FIND = "https://saif.org.uk/find-a-member/"
SAIF_AJAX = "https://saif.org.uk/wp-admin/admin-ajax.php"
NAFD_SITEMAP = "https://www.funeral-directory.co.uk/sitemap.xml"
NAFD_FIRM = "https://www.funeral-directory.co.uk/funeral-director/"

UA = P.UA; CTX = P.CTX

# consolidator brands (names) and their web domains — the ownership signals
CHAIN_NAME = re.compile(r"\b(co-?op(erative)?|funeralcare|dignity|funeral partners|memoria|"
                        r"westerleigh|central england co|east of england co|midcounties|"
                        r"southern co-?op|chelmsford star)\b", re.I)
CHAIN_DOMAIN = ("dignityfunerals", "coop.co.uk", "co-operative", "eastofengland.coop",
                "funeralpartners", "memoria", "westerleigh", "chelmsfordstar", "midcounties",
                "centralengland")
DISCLOSURE = re.compile(r"part of (the )?(dignity|funeral partners|co-?op|memoria|westerleigh)"
                        r"|owned by|a dignity|funeral partners (ltd|limited)|proud to be part of", re.I)

# ---------------- fetch helpers ----------------
def _get(url, timeout=40, headers=None):
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers: h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read().decode("utf-8", "replace")

def norm_pc(pc):
    s = re.sub(r"\s+", " ", (pc or "").strip().upper())
    m = re.match(r"^([A-Z]{1,2}\d[\dA-Z]?)\s*(\d[A-Z]{2})$", s.replace(" ", ""))
    return f"{m.group(1)} {m.group(2)}" if m else s
def pc_area(pc):
    m = re.match(r"^([A-Z]{1,2})\d", norm_pc(pc)); return m.group(1) if m else ""
def pc_district(pc):
    return norm_pc(pc).split(" ")[0]

# ---------------- SAIF ----------------
def fetch_saif(force=False, progress=lambda m: None):
    out = os.path.join(CACHE, "saif_members.json")
    if os.path.exists(out) and not force:
        return json.load(open(out))
    progress("SAIF: fetching complete national membership (one call)…")
    # get a fresh nonce from the find-a-member page
    nonce = None
    try:
        page = _get(SAIF_FIND, timeout=30)
        m = re.search(r'asl_load_stores[^}]*?nonce["\']?\s*[:=]\s*["\']([a-f0-9]+)', page) \
            or re.search(r'"nonce":"([a-f0-9]{8,})"', page) \
            or re.search(r'nonce=([a-f0-9]{8,})', page)
        if m: nonce = m.group(1)
    except Exception as e:
        progress(f"  (couldn't read nonce page: {e})")
    url = f"{SAIF_AJAX}?action=asl_load_stores&load_all=1&layout=1&category=2" + (f"&nonce={nonce}" if nonce else "")
    raw = _get(url, timeout=45, headers={"X-Requested-With": "XMLHttpRequest", "Referer": SAIF_FIND})
    data = json.loads(raw)
    json.dump(data, open(os.path.join(RAW, "saif_raw.json"), "w"))
    recs = []
    for s in data:
        if s.get("member_type") != "FD":
            continue
        pc = norm_pc(s.get("postal_code"))
        if not pc_area(pc):
            continue
        recs.append({
            "source": "SAIF", "name": (s.get("title") or "").strip(),
            "postcode": pc, "area": pc_area(pc), "district": pc_district(pc),
            "address": ", ".join(x for x in [s.get("street"), s.get("city"), s.get("state")] if x),
            "phone": (s.get("phone") or "").strip(),
            "email": (s.get("email") or "").strip().lower(),
            "website": (s.get("website") or "").strip(),
            "url": SAIF_FIND,
        })
    json.dump(recs, open(out, "w"), indent=1)
    progress(f"SAIF: {len(recs)} FD members cached.")
    return recs

# ---------------- NAFD ----------------
_PC_SLUG = re.compile(r"([a-z]{1,2}\d[\da-z]?\d[a-z]{2})$")
def fetch_nafd_index(force=False, progress=lambda m: None):
    out = os.path.join(CACHE, "nafd_members.json")
    if os.path.exists(out) and not force:
        return json.load(open(out))
    progress("NAFD: fetching directory sitemap (every member firm)…")
    sm = _get(NAFD_SITEMAP, timeout=45)
    open(os.path.join(RAW, "nafd_sitemap.xml"), "w").write(sm)
    urls = [u for u in re.findall(r"<loc>([^<]+)</loc>", sm) if "/funeral-director/" in u]
    recs = []; nopc = 0
    for u in urls:
        slug = u.rstrip("/").split("/")[-1]
        m = _PC_SLUG.search(slug)
        if not m:
            nopc += 1; continue
        raw = m.group(1).upper()
        pc = norm_pc(raw)
        name = slug[:m.start()].rstrip("-").replace("-", " ").title()
        recs.append({"source": "NAFD", "name": name, "postcode": pc,
                     "area": pc_area(pc), "district": pc_district(pc),
                     "url": u, "address": "", "phone": "", "email": "", "website": ""})
    json.dump(recs, open(out, "w"), indent=1)
    progress(f"NAFD: {len(recs)} member firms cached ({nopc} slugs had no postcode).")
    return recs

def _clean_site(u):
    d = re.sub(r"^https?://", "", (u or "").lower()).replace("www.", "").split("/")[0]
    bad = ("funeral-directory", "nafd.org", "facebook", "google", "twitter", "instagram",
           "linkedin", "schema.org", "gstatic", "gtag", "youtube", "tiktok", "whatsapp",
           "apple.com", "rsms.me", "knowall.net", "reactivegraphics", "cloudflare", "jsdelivr",
           "fontawesome", "w3.org")
    if not d or "." not in d or any(b in d for b in bad):
        return ""
    if P.HOST_PROVIDER.match(d):
        return ""
    return d

def fetch_nafd_firm(url, cache_map, progress=lambda m: None):
    """Enrich one NAFD firm from its directory page: phone, address, website (which for
    chain branches is itself the ownership signal, e.g. dignityfunerals.co.uk)."""
    slug = url.rstrip("/").split("/")[-1]
    if slug in cache_map:
        return cache_map[slug]
    try:
        h = _get(url, timeout=20)
    except Exception:
        cache_map[slug] = {}; return {}
    tel = re.findall(r'href="tel:([+0-9 ]+)"', h)
    phone = re.sub(r"\s+", " ", tel[0]).strip() if tel else ""
    # firm website = first external link that isn't directory/social/CDN
    site = ""
    for u in re.findall(r'href="(https?://[^"]+)"', h):
        d = _clean_site(u)
        if d:
            site = d; break
    # address from JSON-LD-ish fields
    addr = {}
    for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode"):
        m = re.search(rf'"{k}"\s*:\s*"([^"]+)"', h)
        if m: addr[k] = m.group(1)
    address = ", ".join(addr.get(k, "") for k in ("streetAddress", "addressLocality", "addressRegion") if addr.get(k))
    disclosure = bool(DISCLOSURE.search(re.sub(r"<[^>]+>", " ", h)))
    rec = {"phone": phone, "website": ("https://" + site + "/") if site else "",
           "address": address, "site_domain": site, "disclosure": disclosure}
    cache_map[slug] = rec
    return rec

# ---------------- normalization / dedupe ----------------
LOC_WORDS = set("of the and at near branch office".split())
def name_root(name):
    """Distinctive company key for de-duplication. Uses the PUBLIC/trading name: if a name
    carries 'trading as X' / 't/a X', X is what NAFD and humans use, so key on that. Drops
    '- Branchtown' suffixes, legal + generic words, and locations."""
    s0 = name or ""
    m = re.search(r"\b(?:t/?a|trading as)\b\s*(.+)$", s0, flags=re.I)
    s = m.group(1) if m else s0
    s = re.split(r"\s[-–]\s", s)[0]                              # drop '- Branchtown'
    s = re.split(r"\s+of\s+", s, maxsplit=1, flags=re.I)[0]      # drop 'Of Town' branch locator
    s = re.sub(r"&", " and ", s).lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\b(ltd|limited|plc|llp|funeral|funerals|directors|director|services|service|"
               r"group|home|homes|independent|family|sons|son|brothers|bros|brother|co)\b", " ", s)
    toks = [t for t in s.split() if t and t not in LOC_WORDS]
    return " ".join(toks[:3]) or s0.lower().strip()

# generic/common tokens that must NOT drive a Companies-House ownership match on their own
_GENERIC_MATCH = {"and", "the", "inc", "incorporating", "st", "of"}
def _strong_name_match(root, croot):
    """True only on a confident firm-name match: exact roots, or containment / >=2 shared tokens
    WITH a distinctive (>=4-char, non-generic) shared surname. Bare initials or a lone common
    first name (Peter/Thomas) never match — that wrongly deletes real independents."""
    shared = root & croot
    if not shared: return False
    distinctive = {t for t in shared if len(t) >= 4 and t not in _GENERIC_MATCH}
    if root == croot: return True
    if (root <= croot or croot <= root) and distinctive: return True
    return len(shared) >= 2 and bool(distinctive)

# ---------------- classification ----------------
def _ch_corporate_owner(name, postcode):
    """Match a register firm to a Companies House SIC-96030 company (name+postcode) and
    return a consolidator corporate-PSC name if present (hard ownership evidence)."""
    idx, psc = _load_ch()
    root = set(name_root(name).split())
    if not root: return None
    dist = pc_district(postcode)
    for c in idx:
        if pc_district(c["postcode"]) != dist:
            continue
        croot = set(name_root(c["company_name"]).split())
        if root & croot and (root <= croot or croot <= root or len(root & croot) >= 2):
            for pr in psc.get(c["company_number"], []):
                if not pr.get("ceased") and ("corporate" in pr["kind"] or "legal-person" in pr["kind"]):
                    if P.is_consolidator(pr["name"]):
                        return pr["name"]
    return None

_CH_INDEX = None; _CH_PSC = None; _CH_ACCT = None
def _load_ch():
    global _CH_INDEX, _CH_PSC, _CH_ACCT
    if _CH_INDEX is None:
        import csv
        ip = P._index_path("96030"); pp = P._psc_path("96030")
        _CH_INDEX = list(csv.DictReader(open(ip))) if os.path.exists(ip) else []
        _CH_PSC = json.load(open(pp)) if os.path.exists(pp) else {}
        ap = os.path.join(CACHE, "ch_account_category.json")
        _CH_ACCT = json.load(open(ap)) if os.path.exists(ap) else {}
    return _CH_INDEX, _CH_PSC

# account categories that mark a non-trading shell (dormant / holding / not yet filed)
DEAD_ACCT = {"DORMANT", "NO ACCOUNTS FILED", "AUDIT EXEMPTION SUBSIDIARY", "MICRO ENTITY"}
def _is_nontrading(ch):
    """Filter obvious dormant/holding/not-yet-trading shells from the coverage-gap list."""
    acct = (_CH_ACCT or {}).get(ch.get("company_number", ""), {})
    cat = (acct.get("account_category") or "").upper()
    if cat in ("DORMANT", "NO ACCOUNTS FILED"):
        return True
    return False

def coverage_gaps(area, reg_roots):
    """Companies House SIC-96030 ACTIVE firms in `area` that are in NEITHER register and look
    like genuine trading firms (drop dormant/holding shells + SIC-96030 noise)."""
    def matches(nm):
        r = set(name_root(nm).split())
        if not r: return False
        for rr in reg_roots:
            rs = set(rr.split())
            if rs and (r <= rs or rs <= r): return True
        return False
    NOISE = re.compile(r"firework|burial|cemeter|memorial|masonr|monument|embalm|crematori|"
                       r"realisation|garden of rest|\bwood\b|\bcic\b", re.I)
    gaps = []
    for ch in _CH_INDEX:
        if pc_area(ch["postcode"]) != area: continue
        if not ch["status"].lower().startswith("active"): continue
        if matches(ch["company_name"]): continue
        if NOISE.search(ch["company_name"]): continue
        if _is_nontrading(ch): continue
        gaps.append({"name": ch["company_name"], "postcode": ch["postcode"]})
    return gaps

# ---------------- merge into distinct companies (union-find) ----------------
def _auth_domain(r):
    """Authoritative domain for a branch: its own website, else its email domain.
    (Used for merging; only trust SAIF data + NAFD firm-page data, NOT guessed sites.)"""
    w = _clean_site(r.get("website") or "")
    if w: return w
    e = r.get("email", "")
    return e.split("@")[-1].strip().lower() if "@" in e else ""

def merge_companies(saif, nafd):
    """Union-find branches into distinct companies. Union when: same name-root, OR shared
    authoritative domain (folds an independent multi-brand group like Hunnaball, AND folds
    a chain's branches together), OR one root is a token-subset of another in the same district."""
    recs = list(saif) + list(nafd)
    n = len(recs)
    parent = list(range(n))
    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    def union(i, j): parent[find(i)] = find(j)

    # BRAND level: merge by (name-root + postcode area). Folds same-name branches + SAIF/NAFD
    # duplicates of the SAME trading firm, but keeps an operator's separately-branded firms
    # (Geo Paskell vs Hunnaball) as distinct BRANDS. Area-scoping prevents unrelated same-name
    # firms in different regions merging in the national pass. (Counterparty rollup happens
    # later, by shared own-domain = shared ownership.)
    roots = [name_root(r["name"]) for r in recs]
    by_root = {}
    for i in range(n):
        by_root.setdefault((roots[i], recs[i]["area"]), []).append(i)
    for grp in by_root.values():
        for j in grp[1:]: union(grp[0], j)

    groups = {}
    for i in range(n): groups.setdefault(find(i), []).append(recs[i])
    comps = []
    for g in groups.values():
        saif_recs = [r for r in g if r["source"] == "SAIF"]
        disp = max((r["name"] for r in saif_recs), key=len, default="") or max((r["name"] for r in g), key=len)
        def pick(field):  # prefer authoritative SAIF value, then any
            for r in saif_recs + g:
                if r.get(field): return r[field]
            return ""
        comps.append({
            "company": disp, "root": name_root(disp),
            "sources": set(r["source"] for r in g), "branches": g,
            "areas": set(r["area"] for r in g), "districts": set(r["district"] for r in g),
            "domains": set(d for d in (_auth_domain(r) for r in g) if d),
            "website": pick("website"), "email": pick("email"), "phone": pick("phone"), "contact": "",
        })
    return comps

# ---------------- classify provenance ----------------
def classify(c, progress=lambda m: None):
    """Provenance-based independence. Chain = HARD ownership evidence only:
    consolidator website domain, Companies House corporate PSC, or on-site disclosure.
    A domain shared across branches is a MERGE signal, not chain evidence (an independent
    group like Hunnaball shares its own domain too)."""
    saif = "SAIF" in c["sources"]; nafd = "NAFD" in c["sources"]
    # any *.coop domain = a co-operative society (Scotmid, Lincolnshire, Central England, …)
    chain_dom = next((d for d in c["domains"]
                      if d.endswith(".coop") or any(cd in d for cd in CHAIN_DOMAIN)), "")
    ch_chain = _ch_corporate_owner(c["company"], c["branches"][0]["postcode"])
    hard_chain = None
    if chain_dom:
        hard_chain = f"website domain ({chain_dom})"
    elif ch_chain:
        hard_chain = f"Companies House corporate PSC: {ch_chain}"
    elif c.get("_disclosure"):
        hard_chain = "on-site ownership disclosure"

    if saif:
        # SAIF = independent BY CONSTITUTION; immune to name-pattern exclusion.
        if hard_chain:
            # only HARD ownership evidence may override — log loudly
            c["independence"] = "CHAIN"
            c["basis"] = f"SAIF member BUT overridden by hard evidence: {hard_chain}"
            c["flags"] = ["⚠ SAIF-CONSTITUTION OVERRIDE FIRED"]
            progress(f"‼ SAIF OVERRIDE: {c['company']} flagged CHAIN by {hard_chain} — verify!")
        else:
            c["independence"] = "INDEPENDENT"
            c["basis"] = "SAIF membership (independent by constitution)" + (" + NAFD" if nafd else "")
            c["flags"] = []
        return
    # NAFD-only -> evidence checks
    if hard_chain:
        c["independence"] = "CHAIN"; c["basis"] = f"NAFD-only; chain evidence: {hard_chain}"; c["flags"] = []
    elif CHAIN_NAME.search(c["company"]):
        c["independence"] = "CHAIN"; c["basis"] = f"NAFD-only; consolidator brand name"; c["flags"] = []
    else:
        c["independence"] = "INDEPENDENT"
        c["basis"] = "NAFD-only; no chain evidence (Companies House PSC / domain / disclosure)"
        c["flags"] = ["unverified — neither SAIF nor chain evidence"]

# ---------------- counterparty rollup ----------------
FREE_MAIL = {"gmail.com","googlemail.com","yahoo.co.uk","yahoo.com","hotmail.com","hotmail.co.uk",
             "outlook.com","live.com","live.co.uk","icloud.com","me.com","aol.com","btinternet.com",
             "btconnect.com","tiscali.co.uk","sky.com","virginmedia.com","talktalk.net","msn.com",
             "mail.com","gmx.com","protonmail.com"}

def primary_domain(c):
    """The company's OWN registrable domain — the shared-ownership key. Website domain first,
    else the most common own-domain email (free webmail like gmail is NOT an ownership key)."""
    w = _clean_site(c.get("website") or "")
    if w:
        return w
    from collections import Counter
    doms = Counter()
    for b in c["branches"]:
        e = (b.get("email") or "").split("@")[-1].strip().lower()
        if e and e not in FREE_MAIL and "." in e and not P.HOST_PROVIDER.match(e):
            doms[e] += 1
    return doms.most_common(1)[0][0] if doms else ""

def roll_up_counterparties(brands):
    """Roll BRAND records up into COMMERCIAL COUNTERPARTIES: brands that share an own-domain
    (= shared ownership) become ONE company, one slot, brands retained as trading names.
    This is honest slot-counting, NOT a chain flag — CHAIN stays reserved for consolidators."""
    n = len(brands); parent = list(range(n))
    def find(i):
        while parent[i] != i: parent[i] = parent[parent[i]]; i = parent[i]
        return i
    doms = [primary_domain(b) for b in brands]
    by_dom = {}
    for i in range(n):
        if doms[i]: by_dom.setdefault(doms[i], []).append(i)
    for grp in by_dom.values():
        for j in grp[1:]: parent[find(j)] = find(grp[0])
    groups = {}
    for i in range(n): groups.setdefault(find(i), []).append(brands[i])
    out = []
    for g in groups.values():
        chain = any(b["independence"] == "CHAIN" for b in g)
        dom = primary_domain(g[0]) or ""
        domstem = dom.split(".")[0] if dom else ""
        # display name: prefer the brand whose name matches the shared domain (the group's
        # real trading identity, e.g. hunnaball.co.uk -> "Hunnaball…"), then SAIF, then longest
        dom_named = [b["company"] for b in g if domstem and domstem[:6] in re.sub(r"[^a-z]", "", b["company"].lower())]
        disp = (max(dom_named, key=len) if dom_named
                else max((b["company"] for b in g if "SAIF" in b["sources"]), key=len, default="")
                or max((b["company"] for b in g), key=len))
        trading = sorted({b["company"] for b in g})
        srcs = set().union(*[b["sources"] for b in g])
        districts = sorted(set().union(*[set(b["districts"]) for b in g]))
        areas = sorted(set().union(*[set(b["areas"]) for b in g]))
        email = next((b["email"] for b in g if b.get("email")), "")
        website = next((b["website"] for b in g if b.get("website")), "")
        phone = next((b["phone"] for b in g if b.get("phone")), "")
        out.append({
            "company": disp, "independence": ("CHAIN" if chain else "INDEPENDENT"),
            "sources": sorted(srcs), "trading_names": trading, "brand_count": len(g),
            "districts": districts, "areas": areas, "domain": primary_domain(g[0]) or "",
            "email": email, "website": website, "phone": phone,
            "basis": g[0].get("basis",""), "_brands": g,
        })
    return out

# ---------------- per-area run ----------------
def run_area(area, enrich=True, firecrawl_key=None, progress=lambda m: None):
    area = area.strip().upper()
    fc = (firecrawl_key or P.FC_KEY or "").strip()
    saif = fetch_saif(progress=progress)
    nafd = fetch_nafd_index(progress=progress)
    _load_ch()

    saif_a = [r for r in saif if r["area"] == area]
    nafd_a = [r for r in nafd if r["area"] == area]
    progress(f"{area}: SAIF branches {len(saif_a)} | NAFD firms {len(nafd_a)}")

    # enrich NAFD firms in-area from their directory page (phone/website/disclosure)
    firm_cache_path = os.path.join(CACHE, "nafd_firm_details.json")
    firm_cache = json.load(open(firm_cache_path)) if os.path.exists(firm_cache_path) else {}
    if enrich and nafd_a:
        progress(f"NAFD: reading {len(nafd_a)} firm pages for phone/website/ownership…")
        import concurrent.futures as cf
        def _f(r):
            d = fetch_nafd_firm(r["url"], firm_cache)
            r["phone"] = r["phone"] or d.get("phone", "")
            r["website"] = r["website"] or d.get("website", "")
            r["address"] = r["address"] or d.get("address", "")
            r["_disclosure"] = d.get("disclosure", False)
            return r
        with cf.ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(_f, nafd_a))
        json.dump(firm_cache, open(firm_cache_path, "w"))

    comps = merge_companies(saif_a, nafd_a)
    # propagate NAFD disclosure onto merged company
    for c in comps:
        if any(b.get("_disclosure") for b in c["branches"]): c["_disclosure"] = True

    for c in comps:
        classify(c, progress)

    # enrich contacts: fill website/email for INDEPENDENT companies that still lack them
    if enrich:
        need = [c for c in comps if c["independence"] == "INDEPENDENT" and (not c["email"] or not c["website"])]
        if need: progress(f"Enriching {len(need)} independents for website/email "
                          f"({'Firecrawl' if fc else 'free crawl + domain-guess'})…")
        for c in need:
            b0 = c["branches"][0]
            if not c["website"]:
                site = P.discover_website(c["company"], b0.get("address","").split(",")[-1], None, b0["postcode"])
                if site: c["website"] = site
            if c["website"] and not c["email"]:
                emails, backend = P.crawl_email(c["website"], fc)
                if emails: c["email"] = "; ".join(emails)
                # ownership disclosure on the firm's OWN site
                try:
                    html = (P._firecrawl(c["website"], fc) if fc else P._fetch(c["website"], 8))
                    if DISCLOSURE.search(html or ""):
                        c["_disclosure"] = True
                        if c["independence"] == "INDEPENDENT" and "SAIF" not in c["sources"]:
                            c["independence"] = "CHAIN"; c["basis"] = "NAFD-only; on-site ownership disclosure"
                except Exception: pass

    # ----- report -----
    def bucket(c):
        s = c["sources"]
        if c["independence"] == "CHAIN": return "chain"
        if s == {"SAIF"}: return "saif_only"
        if s == {"NAFD"}: return "nafd_only"
        return "both"
    for c in comps: c["bucket"] = bucket(c)
    from collections import Counter as C2
    bk = C2(c["bucket"] for c in comps)
    indep = [c for c in comps if c["independence"] == "INDEPENDENT"]

    # roll BRANDS up into COMMERCIAL COUNTERPARTIES (shared own-domain = one slot)
    counterparties = roll_up_counterparties(comps)
    cp_indep = [c for c in counterparties if c["independence"] == "INDEPENDENT"]
    cp_bk = C2(("both" if len(c["sources"]) == 2 else ("saif_only" if c["sources"] == ["SAIF"] else "nafd_only"))
               for c in cp_indep)

    # ---- coverage honesty (dormant/holding shells filtered out) ----
    reg_roots = set()
    for c in comps:
        reg_roots.add(c["root"])
        for b in c["branches"]: reg_roots.add(name_root(b["name"]))
    real_gaps = coverage_gaps(area, reg_roots)
    ch_in_area = sum(1 for c in _CH_INDEX if pc_area(c["postcode"]) == area)
    thin = len(cp_indep) < max(3, 0.5 * (len(cp_indep) + len(real_gaps)))

    stats = {
        "area": area,
        "distinct_brands": len(comps),
        "distinct_counterparties": len(counterparties),
        "independent_brands": len(indep),
        "independent_counterparties": len(cp_indep),
        "saif": cp_bk.get("saif_only", 0), "nafd_only": cp_bk.get("nafd_only", 0),
        "both": cp_bk.get("both", 0),
        "chain": sum(1 for c in counterparties if c["independence"] == "CHAIN"),
        "saif_branches": len(saif_a), "nafd_branches": len(nafd_a),
        "with_email": sum(1 for c in cp_indep if c["email"]),
        "with_website": sum(1 for c in cp_indep if c["website"]),
        "ch_96030_registered_in_area": ch_in_area,
        "coverage_gap_in_ch_not_register": len(real_gaps),
        "thin_coverage": thin,
        "firecrawl_on": bool(fc),
    }
    # serialize sets
    for c in comps:
        c["sources"] = sorted(c["sources"]); c["areas"] = sorted(c["areas"])
        c["districts"] = sorted(c["districts"]); c["domains"] = sorted(c["domains"])
    comps.sort(key=lambda x: (x["independence"] != "INDEPENDENT", x["company"].lower()))
    counterparties.sort(key=lambda x: (x["independence"] != "INDEPENDENT", x["company"].lower()))
    return {"stats": stats, "companies": comps, "counterparties": counterparties,
            "coverage_gaps": real_gaps}

# ---------------- attach cached NAFD firm details (national, no fetching) ----------------
def attach_nafd_cached(nafd):
    path = os.path.join(CACHE, "nafd_firm_details.json")
    cache = json.load(open(path)) if os.path.exists(path) else {}
    hit = 0
    for r in nafd:
        slug = r["url"].rstrip("/").split("/")[-1]
        d = cache.get(slug)
        if d:
            hit += 1
            r["website"] = r["website"] or d.get("website", "")
            r["phone"] = r["phone"] or d.get("phone", "")
            r["address"] = r["address"] or d.get("address", "")
            r["_disclosure"] = d.get("disclosure", False)
    return hit, len(cache)

# ---------------- NATIONAL pass ----------------
def run_national(progress=lambda m: None, verify=True, firecrawl_key=None):
    fc = (firecrawl_key or P.FC_KEY or "").strip()
    saif = fetch_saif(progress=progress)
    nafd = fetch_nafd_index(progress=progress)
    _load_ch()
    hit, ncache = attach_nafd_cached(nafd)
    progress(f"NAFD firm details attached from cache: {hit}/{len(nafd)} (cache has {ncache}).")

    progress("Merging brands nationally (root+area)…")
    comps = merge_companies(saif, nafd)
    for c in comps:
        if any(b.get("_disclosure") for b in c["branches"]): c["_disclosure"] = True
    progress(f"Classifying {len(comps)} brand records…")
    for c in comps: classify(c)

    progress("Rolling up commercial counterparties (shared own-domain)…")
    counterparties = roll_up_counterparties(comps)
    before = sum(1 for c in counterparties if c["independence"] == "INDEPENDENT")

    # consolidator-ownership verification (Funeral Partners etc. behind local branding)
    verify_res = {"confirmed": [], "possible": []}
    if verify:
        verify_res = verify_consolidator(counterparties, fc_key=fc, progress=progress)
    # propagate final independence down to member brands (for brand-level per-area counts)
    for c in counterparties:
        for b in c.get("_brands", []):
            b["_final_independence"] = c["independence"]

    cp_indep = [c for c in counterparties if c["independence"] == "INDEPENDENT"]

    # per-area register roots (for gap matching)
    from collections import defaultdict, Counter
    area_roots = defaultdict(set)
    for c in comps:
        for b in c["branches"]:
            area_roots[b["area"]].add(name_root(b["name"]))
        for a in c["areas"]:
            area_roots[a].add(c["root"])

    # per-area tallies (a counterparty counts in each area it operates in)
    areas = sorted(set(a for c in counterparties for a in c["areas"]))
    per_area = []
    progress(f"Aggregating {len(areas)} postcode areas…")
    for a in areas:
        cps = [c for c in counterparties if a in c["areas"]]
        ind = [c for c in cps if c["independence"] == "INDEPENDENT"]
        # distinct trading BRANDS in area = independent brand records with a branch here
        brands_here = sum(1 for c in comps
                          if c.get("_final_independence", c["independence"]) == "INDEPENDENT"
                          and a in c["areas"])
        split = Counter(("both" if len(c["sources"]) == 2 else ("saif_only" if c["sources"] == ["SAIF"] else "nafd_only")) for c in ind)
        gaps = coverage_gaps(a, area_roots[a])
        thin = len(ind) < max(3, 0.5 * (len(ind) + len(gaps)))
        per_area.append({
            "area": a,
            "independent_counterparties": len(ind),
            "independent_brands": brands_here,
            "saif_only": split.get("saif_only", 0),
            "nafd_only": split.get("nafd_only", 0),
            "both": split.get("both", 0),
            "saif_members": sum(1 for c in ind if "SAIF" in c["sources"]),
            "chains_excluded": sum(1 for c in cps if c["independence"] == "CHAIN"),
            "with_email": sum(1 for c in ind if c["email"]),
            "with_website": sum(1 for c in ind if c["website"]),
            "coverage_gap": len(gaps),
            "thin": thin,
        })

    national = {
        "total_independent_counterparties": len(cp_indep),
        "total_independent_brands": sum(1 for c in comps if c.get("_final_independence", c["independence"]) == "INDEPENDENT"),
        "saif_members": sum(1 for c in cp_indep if "SAIF" in c["sources"]),
        "nafd_only": sum(1 for c in cp_indep if c["sources"] == ["NAFD"]),
        "both": sum(1 for c in cp_indep if len(c["sources"]) == 2),
        "chains_flagged": sum(1 for c in counterparties if c["independence"] == "CHAIN"),
        "independents_before_verification": before,
        "verified_consolidator_owned": len(verify_res["confirmed"]),
        "possible_consolidator_unconfirmed": len(verify_res["possible"]),
        "with_email": sum(1 for c in cp_indep if c["email"]),
        "with_website": sum(1 for c in cp_indep if c["website"]),
        "saif_branches": len(saif), "nafd_firms": len(nafd),
        "areas": len(areas),
        "thin_areas": sum(1 for p in per_area if p["thin"]),
        "firecrawl_on": bool(fc),
    }
    return {"national": national, "per_area": per_area, "counterparties": counterparties,
            "verify": verify_res}

# ================= consolidator-ownership verification (Funeral Partners etc.) =================
# Broad consolidator PSC-name matcher (Funeral Partners hides behind regional holdcos).
CONSOL_PSC = re.compile(r"funeral partners|dignity|co-?operative|\bco-?op\b|memoria|westerleigh|"
                        r"midcounties|central england co|east of england co|scotmid|scottish midland|"
                        r"lincolnshire co|southern co|funeral services partnership|kingdom funeral",
                        re.I)
# On-site ownership disclosure (footer 'part of Funeral Partners', copyright, parent links)
DISCLOSE2 = re.compile(r"part of (the )?(funeral partners|dignity|co-?op|memoria|westerleigh)"
                       r"|proud to be part of|(©|copyright)[^<]{0,30}(funeral partners|dignity)"
                       r"|funeral partners (ltd|limited)|a dignity (funerals|company)"
                       r"|member of the dignity|owned (and operated )?by (funeral partners|dignity)", re.I)
CONSOL_LINK = re.compile(r"funeralpartners\.co\.uk|dignityfunerals\.co\.uk|[a-z0-9.-]+\.coop\b", re.I)

def _consol_ch_index():
    """The (few) CH SIC-96030 companies whose DIRECT corporate PSC is a known consolidator."""
    idx, psc = _load_ch()
    out = []
    for c in idx:
        for pr in psc.get(c["company_number"], []):
            if pr.get("ceased"): continue
            if ("corporate" in pr["kind"] or "legal-person" in pr["kind"]) and CONSOL_PSC.search(pr["name"]):
                out.append((set(name_root(c["company_name"]).split()), pc_district(c["postcode"]),
                            c["company_name"], pr["name"], c["company_number"]))
                break
    return out

def _disclosure(website, fc_key, cache):
    """Crawl a firm site once for consolidator disclosure / parent-link. Cached by domain."""
    dom = _clean_site(website)
    if not dom: return {}
    if dom in cache: return cache[dom]
    html = ""
    try:
        html = (P._firecrawl("https://" + dom, fc_key) if fc_key else P._fetch("https://" + dom, 8)) or ""
    except Exception:
        html = ""
    text = re.sub(r"<[^>]+>", " ", html)
    m = DISCLOSE2.search(text)
    link = CONSOL_LINK.search(html)
    which = ""
    if m: which = m.group(0)[:60]
    elif link: which = link.group(0)
    res = {"disclosed": bool(m), "parent_link": bool(link), "which": which}
    cache[dom] = res
    return res

def verify_consolidator(counterparties, fc_key="", progress=lambda m: None):
    """Catch hidden consolidator ownership behind local branding. CONFIDENCE RULE: a fuzzy CH
    name match may ENRICH but never deletes on its own — flipping INDEPENDENT->CHAIN requires
    registered-office (district) corroboration OR a second independent evidence type."""
    consol_ch = _consol_ch_index()
    progress(f"CH companies with a consolidator corporate PSC: {len(consol_ch)}")
    dpath = os.path.join(CACHE, "disclosure_cache.json")
    dcache = json.load(open(dpath)) if os.path.exists(dpath) else {}
    indeps = [c for c in counterparties if c["independence"] == "INDEPENDENT"]
    to_crawl = [c for c in indeps if c.get("website")]
    progress(f"Crawling {len(to_crawl)} independent sites for ownership disclosure "
             f"({'Firecrawl' if fc_key else 'free crawl'})…")
    import concurrent.futures as cf
    def _one(c):
        # (A) CH corporate consolidator PSC — national name match
        root = set(name_root(c["company"]).split())
        ch_hit = None; corroborated = False
        if root:
            for croot, cdist, cname, psc_name, cnum in consol_ch:
                if croot and _strong_name_match(root, croot):
                    ch_hit = psc_name; c["ch_number"] = cnum; c["ch_name"] = cname
                    if cdist in c["districts"]:
                        corroborated = True
                    break
        # (B) on-site disclosure
        disc = _disclosure(c.get("website", ""), fc_key, dcache) if c.get("website") else {}
        disclosed = disc.get("disclosed") or disc.get("parent_link")
        # apply confidence rule
        evidence = []
        if ch_hit: evidence.append(f"CH corporate PSC: {ch_hit}" + (" (office corroborated)" if corroborated else " (uncorroborated)"))
        if disc.get("disclosed"): evidence.append(f"on-site disclosure: {disc.get('which')}")
        elif disc.get("parent_link"): evidence.append(f"parent/consolidator link: {disc.get('which')}")
        confirmed = (ch_hit and corroborated) or disc.get("disclosed") or \
                    (ch_hit and disclosed) or (ch_hit and disc.get("parent_link"))
        c["ownership_evidence"] = "; ".join(evidence)
        if confirmed:
            c["independence"] = "CHAIN"
            c["basis"] = "consolidator-owned (verification): " + "; ".join(evidence)
            c["verify"] = "CONFIRMED_CHAIN"
        elif ch_hit:
            c["verify"] = "POSSIBLE"   # fuzzy match only — flagged, NOT removed (confidence rule)
        else:
            c["verify"] = "INDEPENDENT"
        return c
    with cf.ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(_one, indeps))
    json.dump(dcache, open(dpath, "w"))
    confirmed = [c for c in indeps if c["verify"] == "CONFIRMED_CHAIN"]
    possible = [c for c in indeps if c["verify"] == "POSSIBLE"]
    progress(f"Verification: {len(confirmed)} CONFIRMED consolidator-owned, {len(possible)} POSSIBLE (kept, flagged).")
    return {"confirmed": confirmed, "possible": possible}
