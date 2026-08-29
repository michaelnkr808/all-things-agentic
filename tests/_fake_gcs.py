"""A minimal stand-in for the two GCS JSON API calls our adapter makes:
list objects under a prefix, and download one object. Enough to prove the
adapter speaks the real wire protocol without a Google Cloud account."""
import json, re, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs, unquote

OBJECTS = {
    "finance/q3-actuals.csv": "team,line_item,budgeted_usd,actual_usd\nengineering,infra,50000,42000\n",
    "finance/cloud-renewal.md": "# Cloud Renewal\n\nRenews Oct 1 at +7%.\n",
    "finance/subdir/forecast.md": "# FY26 Forecast\n\nAssumes 9% opex growth.\n",
    "other-dept/secret.md": "MUST NOT APPEAR: outside the department prefix.\n",
}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="application/json"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        # the real client downloads media from /download/storage/v1/...
        m = re.match(r"^(?:/download)?/storage/v1/b/([^/]+)/o/(.+)$", u.path)
        if m and q.get("alt") == ["media"]:
            key = unquote(m.group(2))
            if key not in OBJECTS: return self._send(404, '{"error":"no such object"}')
            return self._send(200, OBJECTS[key], "text/plain")
        m = re.match(r"^/storage/v1/b/([^/]+)/o$", u.path)
        if m:
            prefix = (q.get("prefix") or [""])[0]
            items = [{"name": k, "size": str(len(v)), "bucket": m.group(1),
                      "id": k, "generation": "1", "contentType": "text/plain"}
                     for k, v in OBJECTS.items() if k.startswith(prefix)]
            return self._send(200, json.dumps({"kind": "storage#objects", "items": items}))
        m = re.match(r"^/storage/v1/b/([^/]+)$", u.path)
        if m: return self._send(200, json.dumps({"name": m.group(1)}))
        self._send(404, '{"error":"unhandled"}')

def serve():
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
