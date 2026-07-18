#!/usr/bin/env python3
"""
Local web app for the register-spine pipeline.  Python stdlib only — no installs.

  python3 app/server.py            # then open http://localhost:8770
  FIRECRAWL_API_KEY=fc-... python3 app/server.py   # enable Firecrawl email pass

Endpoints:
  GET  /                 -> UI
  GET  /api/status       -> cache readiness + categories/areas
  POST /api/run          -> {area, sic}  starts a background job, returns {job_id}
  GET  /api/job?id=...   -> {state, log[], result?}
"""
import os, sys, json, threading, uuid, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline as P

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("PORT", "8770"))
JOBS = {}       # id -> {state, log, result, error}
JOBS_LOCK = threading.Lock()

def _run_job(job_id, area, sic, fc_key):
    job = JOBS[job_id]
    def prog(m):
        with JOBS_LOCK:
            job["log"].append(m)
    try:
        job["state"] = "running"
        job["result"] = P.run_area(area, sic, prog, firecrawl_key=fc_key)
        job["state"] = "done"
    except Exception as e:
        job["error"] = f"{e}\n{traceback.format_exc()}"
        job["state"] = "error"
        prog(f"ERROR: {e}")

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)): body = json.dumps(body).encode()
        elif isinstance(body, str): body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path == "/index.html":
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path == "/api/status":
            return self._send(200, {
                "cache": P.cache_status(),
                "categories": [{"sic": k, "label": v["label"]} for k, v in P.CATEGORIES.items()],
                "areas": sorted(P.BBOX.keys()),
                "firecrawl": bool(P.FC_KEY),
            })
        if path == "/api/job":
            from urllib.parse import parse_qs, urlparse
            qs = parse_qs(urlparse(self.path).query)
            jid = (qs.get("id") or [""])[0]
            with JOBS_LOCK:
                job = JOBS.get(jid)
                if not job: return self._send(404, {"error": "no such job"})
                out = {"state": job["state"], "log": job["log"][-40:]}
                if job["state"] == "done": out["result"] = job["result"]
                if job["state"] == "error": out["error"] = job["error"]
            return self._send(200, out)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if path == "/api/run":
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
            area = (body.get("area") or "").strip().upper()
            sic = (body.get("sic") or "96030").strip()
            fc_key = (body.get("firecrawl_key") or "").strip()
            if not area:
                return self._send(400, {"error": "area required"})
            job_id = uuid.uuid4().hex[:12]
            JOBS[job_id] = {"state": "queued", "log": [], "result": None, "error": None}
            threading.Thread(target=_run_job, args=(job_id, area, sic, fc_key), daemon=True).start()
            return self._send(200, {"job_id": job_id})
        return self._send(404, {"error": "not found"})

if __name__ == "__main__":
    print(f"Register-spine app on http://localhost:{PORT}  (Firecrawl: {'ON' if P.FC_KEY else 'off — free crawl'})")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
