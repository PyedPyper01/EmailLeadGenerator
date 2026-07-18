#!/usr/bin/env python3
"""
Stage 7 — EMAIL GAP-FILL.
For firms with a website but no email, crawl the site's own pages for the
embedded contact address (homepage + a contact/about page). Ports the proven
Postcode Prospector extraction: de-obfuscate at/dot, filter junk + web-host
addresses (34SP/Wix/etc.), prefer the firm's own-domain address.

Two crawl backends:
  - FREE (default): stdlib urllib fetch of raw HTML (no key). Misses JS-rendered
    emails but works for most funeral-director sites (footer mailto/text).
  - FIRECRAWL: if FIRECRAWL_API_KEY is set, uses Firecrawl /scrape (renders JS)
    for firms the free pass can't crack.

Input:  out/leads_pre_email.csv
Output: out/leads.csv   (final)
"""
import csv, os, re, json, urllib.request, urllib.error, ssl
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTD = os.path.join(HERE, "out")
FC_KEY = (os.environ.get("FIRECRAWL_API_KEY") or "").strip()

BAD_EMAIL = re.compile(r"@(?:example|sentry|wixpress|domain|email|yourcompany|company|test)\.|"
                       r"\.(?:png|jpg|jpeg|gif|svg|webp|css|js)$|^[a-f0-9]{16,}@|@2x", re.I)
HOST_PROVIDER = re.compile(r"^(?:34sp|wix|squarespace|godaddy|hostinger|123-?reg|fasthosts|ionos|"
                           r"1and1|names|tsohost|krystal|heartinternet|siteground|bluehost|weebly|"
                           r"wordpress|automattic|cloudflare|mailchimp|sentry|shopify|createandhost|"
                           r"itseeze|mediaworks|wixpress|sentry-next)\.", re.I)
CTX = ssl.create_default_context(); CTX.check_hostname=False; CTX.verify_mode=ssl.CERT_NONE
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"

def edom(e): return (e.split("@")[1] or "").lower() if "@" in e else ""

def extract_emails(text):
    out=set()
    if not text: return out
    s=str(text)
    s=re.sub(r"\s*[\[(]\s*at\s*[\])]\s*/?\s*","@",s,flags=re.I)
    s=re.sub(r"\s*[\[(]\s*dot\s*[\])]\s*/?\s*",".",s,flags=re.I)
    for m in re.finditer(r"(?:mailto:)?([a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,})",s,re.I):
        e=m.group(1).lower().rstrip(".,;:)]")
        if not BAD_EMAIL.search(e) and len(e)<80: out.add(e)
    return out

def fetch(url):
    try:
        req=urllib.request.Request(url, headers={"User-Agent":UA,"Accept":"text/html"})
        with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
            return r.read(1_500_000).decode("utf-8","replace")
    except Exception as e:
        return ""

def firecrawl(url):
    body=json.dumps({"url":url,"formats":["markdown","links"],"onlyMainContent":False,"timeout":20000,"waitFor":1200}).encode()
    req=urllib.request.Request("https://api.firecrawl.dev/v1/scrape", data=body,
        headers={"Content-Type":"application/json","Authorization":f"Bearer {FC_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
            d=json.loads(r.read().decode("utf-8","replace"))
        data=d.get("data",{})
        txt=data.get("markdown","")+" "+ " ".join(str(l) for l in data.get("links",[]))
        return txt
    except Exception as e:
        return ""

def domain_of(website):
    return re.sub(r"^https?://","",website.lower()).replace("www.","").split("/")[0].strip()

def find_contact_links(html, origin, domain):
    links=set()
    for m in re.finditer(r'href=["\']([^"\']+)["\']', html, re.I):
        h=m.group(1)
        if re.search(r"contact|about|get-in-touch|find-us|enquir",h,re.I):
            try:
                u=urllib.parse.urljoin(origin,h)
                if domain in u: links.add(u)
            except Exception: pass
    return list(links)[:2]

import urllib.parse
def crawl(website):
    domain=domain_of(website)
    if not domain or "." not in domain: return [], "no-domain", 0
    origin=f"https://{domain}"
    pages=0; emails=set()
    backend="firecrawl" if FC_KEY else "free"
    def grab(u):
        return firecrawl(u) if FC_KEY else fetch(u)
    html=grab(origin); pages+=1
    emails|=extract_emails(html)
    if not emails:
        for u in ([f"{origin}/contact", f"{origin}/contact-us"] if FC_KEY else find_contact_links(html,origin,domain) or [f"{origin}/contact"]):
            emails|=extract_emails(grab(u)); pages+=1
            if emails or pages>=3: break
    # prefer own-domain address; else drop known host providers
    allm=list(emails)
    same=[e for e in allm if edom(e)==domain or edom(e).endswith("."+domain) or domain.endswith("."+edom(e))]
    chosen=(same if same else [e for e in allm if not HOST_PROVIDER.match(edom(e))])
    return sorted(set(chosen))[:4], backend, pages

def main():
    leads=list(csv.DictReader(open(os.path.join(OUTD,"leads_pre_email.csv"))))
    print(f"Email backend: {'FIRECRAWL (JS-rendered)' if FC_KEY else 'FREE stdlib crawl (no key; set FIRECRAWL_API_KEY for JS sites)'}")
    got_osm=0; got_crawl=0
    for l in leads:
        if l["email"]:
            got_osm+=1; l["email_source"]="OSM"; continue
        if l["website"]:
            emails,backend,pages=crawl(l["website"])
            if emails:
                l["email"]="; ".join(emails); l["email_source"]=f"crawl({backend})"; got_crawl+=1
                print(f"  + {l['company_name'][:34]:34} {l['email']}  ({pages} pages, {backend})")
            else:
                l["email_source"]=f"crawl-miss({backend})"
                print(f"  - {l['company_name'][:34]:34} no email  ({l['website']})")
        else:
            l["email_source"]="no-website"

    cols=list(leads[0].keys())
    if "email_source" not in cols: cols.append("email_source")
    with open(os.path.join(OUTD,"leads.csv"),"w",newline="") as fo:
        w=csv.DictWriter(fo,fieldnames=cols); w.writeheader()
        for l in leads: w.writerow(l)
    withemail=sum(1 for l in leads if l["email"])
    print(f"\nEmails: from OSM {got_osm} | from crawl {got_crawl} | total {withemail}/{len(leads)} leads")
    print(f"Wrote -> {OUTD}/leads.csv")

if __name__ == "__main__":
    main()
