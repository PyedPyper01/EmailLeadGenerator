#!/usr/bin/env bash
# Rebuild ALL caches from scratch, so a fresh clone is one command from producing
# out/register_supply.csv. No API keys required for this script (write-ups are a
# separate, key-gated step — see SETUP.md). Everything is idempotent: steps that
# find an existing cache skip themselves, so re-running only fills what's missing.
#
#   ./rebuild_caches.sh
#
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p data data/psc data/registers cache out

echo "==> [1/5] Companies House bulk company data + PSC snapshot (~2.5 GB, one time)"
python3 -c "import sys; sys.path.insert(0,'app'); import pipeline; pipeline.download_bulk(lambda m: print('   ', m))"

echo "==> [2/5] National SIC-96030 index + PSC ownership map (~2.5 min)"
python3 -c "import sys; sys.path.insert(0,'app'); import pipeline; pipeline.ensure_prep('96030', lambda m: print('   ', m))"

echo "==> [3/5] NAFD firm pages (chain detection) + CH account categories (~10 min)"
python3 app/prefetch.py all

echo "==> [4/5] Trade registers (SAIF + NAFD), national verification, and supply files"
#   export_brevo.py runs the full pull: fetch SAIF/NAFD -> merge/verify -> enrich
#   (website/email; disclosure crawl) -> writes out/register_supply.csv + by-area + possible.
python3 export_brevo.py

echo "==> [5/5] Marketplace profile source content (for write-ups; no key needed to crawl)"
python3 app/writeup_content.py || echo "   (content crawl skipped/failed — non-fatal)"

echo ""
echo "DONE. Prospector hand-off files are in out/:"
echo "   register_supply.csv, register_supply_by_area.csv, register_supply_possible.csv"
echo ""
echo "Optional (needs ANTHROPIC_API_KEY): generate marketplace profiles ->"
echo "   export ANTHROPIC_API_KEY=…  &&  python3 generate_writeups.py"
