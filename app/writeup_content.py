#!/usr/bin/env python3
"""Crawl each independent firm's OWN website for the text a marketplace profile is
written from (homepage + about/services pages). Cached by COMPANY_ID. No API key needed.
Output: cache/site_content.json  { company_id: {text, pages[], website} }"""
import os, sys, re, csv, json, ssl, urllib.request, urllib.parse, concurrent.futures as cf
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "cache")
FIRMS = os.path.join(HERE, "out", "marketplace_firms.csv")
OUT = os.path.join(CACHE, "site_content.json")
CTX = ssl.create_default_context(); CTX.check_hostname=False; CTX.verify_mode=ssl.CERT_NONE
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"
FC_KEY = (os.environ.get("FIRECRAWL_API_KEY") or "").strip()

def firecrawl(url):
    """JS-rendered fallback: many funeral sites are SPA/JS and return no visible HTML text to the
    plain crawl. Firecrawl renders the page and returns clean markdown. Only used when the free crawl
    got nothing AND a key is set (metered), so we never spend a credit on a site the crawler could read."""
    if not FC_KEY:
        return ""
    try:
        body = json.dumps({"url": url, "formats": ["markdown"], "onlyMainContent": False}).encode()
        req = urllib.request.Request("https://api.firecrawl.dev/v1/scrape", data=body,
              headers={"Authorization": "Bearer " + FC_KEY, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=45, context=CTX) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        md = ((d.get("data") or {}).get("markdown")) or ""
        return re.sub(r"\s+", " ", md).strip()[:9000]
    except Exception:
        return ""

def fetch(url, timeout=12):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            return r.read(2_000_000).decode("utf-8", "replace")
    except Exception:
        return ""

def visible_text(html):
    html = re.sub(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?is)<!--.*?-->", " ", html)
    txt = re.sub(r"(?s)<[^>]+>", " ", html)
    import html as _h
    txt = _h.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()

def domain_of(w):
    return re.sub(r"^https?://", "", (w or "").lower()).replace("www.", "").split("/")[0].strip()

CONTACT_RE = re.compile(r"about|our-?(story|history|team|services|funerals)|services|who-?we-?are|"
                        r"what-?we-?(do|offer)|prices?|pre-?paid|plans", re.I)

def crawl(website):
    dom = domain_of(website)
    if not dom or "." not in dom: return {"text": "", "pages": []}
    origin = "https://" + dom
    home = fetch(origin)
    pages = [origin]; parts = []
    if home: parts.append(visible_text(home))
    # find up to 3 relevant same-domain links
    links = []
    for m in re.finditer(r'href=["\']([^"\']+)["\']', home, re.I):
        h = m.group(1)
        if CONTACT_RE.search(h):
            try:
                u = urllib.parse.urljoin(origin, h)
                if dom in u and u not in links and u != origin:
                    links.append(u)
            except Exception: pass
    for u in links[:3]:
        t = visible_text(fetch(u))
        if t: parts.append(t); pages.append(u)
    text = " \n ".join(parts)[:9000]
    # JS-rendered fallback: the plain crawl read nothing → let Firecrawl render it (metered, only when needed)
    if len(text) < 200:
        fc = firecrawl(origin)
        if len(fc) > len(text):
            text = fc
    return {"text": text, "pages": pages, "website": website}

def main():
    firms = list(csv.DictReader(open(FIRMS, encoding="utf-8-sig")))
    cache = json.load(open(OUT)) if os.path.exists(OUT) else {}
    # crawl anything uncrawled; plus, when a Firecrawl key is set, RE-crawl cached-but-empty sites
    # (the JS-rendered ones the plain crawl couldn't read) so they get a Firecrawl pass this run.
    def needs(f):
        if not f["WEBSITE"]: return False
        c = cache.get(f["COMPANY_ID"])
        if c is None: return True
        return bool(FC_KEY) and len((c or {}).get("text", "")) < 200
    todo = [f for f in firms if needs(f)]
    print(f"Firms: {len(firms)} | with website: {sum(1 for f in firms if f['WEBSITE'])} | "
          f"to crawl: {len(todo)}{' (incl. empty re-crawl via Firecrawl)' if FC_KEY else ''}", flush=True)
    def one(f):
        cache[f["COMPANY_ID"]] = crawl(f["WEBSITE"])
    for i in range(0, len(todo), 100):
        batch = todo[i:i+100]
        with cf.ThreadPoolExecutor(max_workers=12) as ex:
            list(ex.map(one, batch))
        json.dump(cache, open(OUT, "w"))
        print(f"  crawled {min(i+100,len(todo))}/{len(todo)}", flush=True)
    got = sum(1 for f in firms if cache.get(f["COMPANY_ID"], {}).get("text"))
    print(f"Content captured for {got} firms -> {OUT}", flush=True)

if __name__ == "__main__":
    main()
