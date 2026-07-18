#!/usr/bin/env bash
# Free-data supplier-sourcing spine — CO funeral directors POC.
# One command, no paid APIs. Optional: export FIRECRAWL_API_KEY for JS-rendered email gap-fill.
set -euo pipefail
cd "$(dirname "$0")"
DATA=data; OUT=out
mkdir -p "$DATA" "$DATA/psc" "$OUT"

CH_MONTH="2026-07-01"        # Companies House Free Company Data snapshot
PSC_DATE="2026-07-16"        # PSC bulk snapshot

echo "==> [1/6] Companies House bulk company data (496 MB, no key)"
[ -f "$DATA/companies.zip" ] || curl -# -o "$DATA/companies.zip" \
  "https://download.companieshouse.gov.uk/BasicCompanyDataAsOneFile-${CH_MONTH}.zip"

echo "==> [2/6] PSC bulk snapshot (32 files, ~2 GB, no key)"
for i in $(seq 1 32); do
  f="$DATA/psc/psc_${i}.zip"
  [ -f "$f" ] || curl -s -o "$f" "https://download.companieshouse.gov.uk/psc-snapshot-${PSC_DATE}_${i}of32.zip"
done

echo "==> [3/6] OSM Overpass — CO-area funeral directors (no key)"
if [ ! -f "$DATA/osm_co_raw.json" ]; then
  Q='[out:json][timeout:90];(nwr["shop"="funeral_directors"](51.7,0.45,52.15,1.35);nwr["office"="funeral_director"](51.7,0.45,52.15,1.35);nwr["amenity"="funeral_hall"](51.7,0.45,52.15,1.35););out center tags;'
  curl -s "https://overpass-api.de/api/interpreter" --data-urlencode "data=$Q" -o "$DATA/osm_co_raw.json"
fi

echo "==> [4/6] Build spine + national index + parse OSM"
python3 src/1_spine.py
python3 src/2_national_index.py
python3 src/3_osm.py

echo "==> [5/6] Match OSM→CH, scan PSC for independence"
python3 src/4_match.py
python3 src/5_psc_scan.py | tail -8

echo "==> [6/6] Assemble + email gap-fill  (Firecrawl if FIRECRAWL_API_KEY set, else free crawl)"
python3 src/6_assemble.py
python3 src/7_email.py

echo ""
echo "DONE. See out/leads.csv and REPORT.md"
