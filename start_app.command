#!/bin/bash
# Double-click this file to start the Register Spine app, then open the browser.
cd "$(dirname "$0")"
echo "Starting Register Spine on http://localhost:8770 …"
echo "(Leave this window open while you use the app. Close it to stop.)"
echo ""
# open the browser after a moment, then run the server in the foreground
( sleep 1.5; open "http://localhost:8770" ) &
exec python3 app/server.py
