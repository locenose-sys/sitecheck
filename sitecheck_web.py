#!/usr/bin/env python3
"""
sitecheck_web - one-off test page: which sites accept downloads from THIS server?

Open the page, enter the password (set in the PASSWORD environment variable),
paste up to 4 public links, press Run. It tries each link with linkgrab (same slow
pacing as the real bot, lowest-cost quality), shows PASS/FAIL per site with the short
error, then deletes the files. Nothing is sent to Telegram. Only supported sites are
accepted, so it cannot be used as an open proxy.
"""
import hmac
import html
import os
import random
import secrets
import shutil
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import linkgrab as lg

PASSWORD = os.environ.get("PASSWORD", "")
PORT = int(os.environ.get("PORT", "10000"))
MAX_LINKS = 4
DEFAULTS = {
    "YouTube": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    "Instagram reel": "https://www.instagram.com/reel/DfGxfjBOD46/",
    "Twitter/X": "",
    "Pinterest": "",
}
STATE = {"running": False, "lines": [], "done": True, "ip": "", "started": 0.0}
SESSIONS = set()
LOCK = threading.Lock()


def server_ip():
    try:
        return urllib.request.urlopen("https://api.ipify.org", timeout=8).read().decode().strip()
    except Exception as e:
        return f"unknown ({type(e).__name__})"


def run_checks(urls):
    work = Path(tempfile.mkdtemp(prefix="sitecheck_"))
    try:
        with LOCK:
            STATE.update(running=True, done=False, lines=[], ip=server_ip(), started=time.time())
        for i, (label, url) in enumerate(urls):
            plat = lg.detect_platform(url)
            if plat == "other":
                line = (label, url, "SKIPPED", "not a supported site")
            else:
                t0 = time.time()
                ok, plat, msg, limited = lg.download(url, "720", work, None)
                status = "PASS" if ok else "FAIL"
                line = (label, url, status, f"{msg[:300]} ({time.time() - t0:.0f}s)")
            with LOCK:
                STATE["lines"].append(line)
            if i + 1 < len(urls):
                time.sleep(random.uniform(*lg.SHORT_PAUSE))   # same pacing as the real bot
    finally:
        shutil.rmtree(work, ignore_errors=True)
        with LOCK:
            STATE.update(running=False, done=True)


PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Site check</title><style>body{{font-family:system-ui,sans-serif;max-width:720px;margin:24px auto;padding:0 16px}}
input[type=text],input[type=password]{{width:100%;padding:8px;margin:4px 0 12px;box-sizing:border-box}}
.PASS{{color:#0a7d2c;font-weight:700}}.FAIL{{color:#b3261e;font-weight:700}}.SKIPPED{{color:#8a6d00;font-weight:700}}
td,th{{padding:6px 8px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top;font-size:14px}}
code{{word-break:break-all}}</style>{body}"""


def form_page(msg=""):
    fields = "".join(
        f"<label>{html.escape(k)}<input type=text name='u{i}' value='{html.escape(v)}' placeholder='paste a public link'></label>"
        for i, (k, v) in enumerate(DEFAULTS.items()))
    body = (f"<h2>Which sites accept this server?</h2><p>{html.escape(msg)}</p>"
            "<form method=post action=/run><label>Password<input type=password name=pw></label>"
            f"{fields}<button>Run check</button></form>"
            "<p>Takes about a minute. Files are deleted afterwards; nothing goes to Telegram.</p>")
    return PAGE.format(body=body)


def result_page():
    with LOCK:
        lines, running, ip = list(STATE["lines"]), STATE["running"], STATE["ip"]
    rows = "".join(
        f"<tr><td>{html.escape(l)}</td><td><code>{html.escape(u)}</code></td>"
        f"<td class={s}>{s}</td><td>{html.escape(m)}</td></tr>" for l, u, s, m in lines)
    refresh = "<meta http-equiv=refresh content=5>" if running else ""
    ff = "found" if shutil.which("ffmpeg") else "not found"
    body = (f"{refresh}<h2>Results</h2><p>This server's outbound IP: <b>{html.escape(ip or '...')}</b> "
            f"&middot; ffmpeg {ff} &middot; {'running...' if running else 'finished'}</p>"
            f"<table><tr><th>Site</th><th>Link</th><th>Result</th><th>Detail</th></tr>{rows}</table>"
            "<p><a href=/>Run again</a></p>")
    return PAGE.format(body=body)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, headers=None):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "sc" and v in SESSIONS:
                return True
        return False

    def do_GET(self):
        if self.path.startswith("/result"):
            if not self._authed():
                return self._send(303, "", {"Location": "/"})
            return self._send(200, result_page())
        if self.path == "/health":
            return self._send(200, "ok")
        self._send(200, form_page())

    def do_POST(self):
        n = min(int(self.headers.get("Content-Length", 0)), 10000)
        form = parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
        pw = (form.get("pw") or [""])[0]
        if not PASSWORD or not hmac.compare_digest(pw.encode(), PASSWORD.encode()):
            time.sleep(1)   # slow down guessing
            return self._send(401, form_page("Wrong password (or none is set on the server)."))
        with LOCK:
            if STATE["running"]:
                return self._send(200, form_page("A check is already running; open /result."))
        labels = list(DEFAULTS)
        urls = []
        for i, label in enumerate(labels[:MAX_LINKS]):
            u = (form.get(f"u{i}") or [""])[0].strip()
            if u.startswith(("http://", "https://")):
                urls.append((label, u))
        if not urls:
            return self._send(200, form_page("Paste at least one link."))
        token = secrets.token_urlsafe(24)
        SESSIONS.add(token)
        threading.Thread(target=run_checks, args=(urls,), daemon=True).start()
        self._send(303, "", {"Location": "/result", "Set-Cookie": f"sc={token}; HttpOnly; Path=/; SameSite=Strict"})


if __name__ == "__main__":
    if not PASSWORD:
        print("WARNING: PASSWORD is not set; the page will refuse every run.")
    print(f"Site check listening on port {PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
