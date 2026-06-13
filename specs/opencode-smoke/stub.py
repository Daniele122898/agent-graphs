"""Tiny stand-in for our backend's delegation endpoint. Logs every call (so we
can prove the custom tool fired with session+agent identity) and returns a
canned teammate answer. Run on :8799."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        print("ASK_AGENT_CALL " + json.dumps(body), flush=True)
        answer = (
            f"[reviewer reply to '{body.get('agent')}' in session "
            f"{body.get('sessionID')}]: The code looks correct. Ship it."
        )
        payload = answer.encode()
        self.send_response(200)
        self.send_header("content-type", "text/plain")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", 8799), H).serve_forever()
