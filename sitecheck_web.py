#!/usr/bin/env python3
"""
sitecheck_web - one-off test page: which sites accept downloads from THIS server?

Open the page, enter the password (set in the PASSWORD environment variable),
paste up to 10 public links, press Run. It tries them in batches of 3 with a rest between
batches (same slow pacing as the real bot, lowest-cost quality), shows PASS/FAIL per site with the short
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
MAX_LINKS = 10
BATCH_SIZE = 3
BATCH_REST = (150, 210)   # seconds of rest between batches
STATE = {"running": False, "lines": [], "done": True, "ip": "", "started": 0.0, "note": ""}
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
            STATE.update(running=True, done=False, lines=[], ip=server_ip(), started=time.time(), note="")
        for i, url in enumerate(urls):
            plat = lg.detect_platform(url)
            if plat == "other":
                line = (plat, url, "SKIPPED", "not a supported site")
            else:
                t0 = time.time()
                ok, plat, msg, limited = lg.download(url, "720", work, None)
                status = "PASS" if ok else "FAIL"
                line = (plat, url, status, f"{msg[:300]} ({time.time() - t0:.0f}s)")
            with LOCK:
                STATE["lines"].append(line)
            if i + 1 < len(urls):
                if (i + 1) % BATCH_SIZE == 0:
                    wait = random.uniform(*BATCH_REST)
                    with LOCK:
                        STATE["note"] = f"batch done, resting about {wait:.0f}s before the next 3"
                else:
                    wait = random.uniform(*lg.SHORT_PAUSE)   # same pacing as the real bot
                    with LOCK:
                        STATE["note"] = f"pausing about {wait:.0f}s"
                time.sleep(wait)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        with LOCK:
            STATE.update(running=False, done=True, note="")


PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Site check</title><style>body{{font-family:system-ui,sans-serif;max-width:720px;margin:24px auto;padding:0 16px}}
input[type=text],input[type=password]{{width:100%;padding:8px;margin:4px 0 12px;box-sizing:border-box}}
.PASS{{color:#0a7d2c;font-weight:700}}.FAIL{{color:#b3261e;font-weight:700}}.SKIPPED{{color:#8a6d00;font-weight:700}}
td,th{{padding:6px 8px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top;font-size:14px}}
code{{word-break:break-all}}</style>{body}"""


def form_page(msg=""):
    body = (f"<h2>Which sites accept this server?</h2><p>{html.escape(msg)}</p>"
            "<form method=post action=/run><label>Password<input type=password name=pw></label>"
            f"<label>Links, one per line (up to {MAX_LINKS})"
            "<textarea name=links rows=10 style='width:100%;padding:8px;margin:4px 0 12px;box-sizing:border-box' "
            "placeholder='paste public links here'></textarea></label>"
            "<button>Run check</button></form>"
            f"<p>Runs in batches of {BATCH_SIZE} with a rest of a few minutes between batches, so "
            "10 links take roughly 10 minutes. Keep this page open (it keeps the free server awake). "
            "Files are deleted afterwards; nothing goes to Telegram.</p>")
    return PAGE.format(body=body)


def result_page():
    with LOCK:
        lines, running, ip, note = list(STATE["lines"]), STATE["running"], STATE["ip"], STATE["note"]
    rows = "".join(
        f"<tr><td>{html.escape(l)}</td><td><code>{html.escape(u)}</code></td>"
        f"<td class={s}>{s}</td><td>{html.escape(m)}</td></tr>" for l, u, s, m in lines)
    refresh = "<meta http-equiv=refresh content=5>" if running else ""
    ff = "found" if shutil.which("ffmpeg") else "not found"
    body = (f"{refresh}<h2>Results</h2><p>This server's outbound IP: <b>{html.escape(ip or '...')}</b> "
            f"&middot; ffmpeg {ff} &middot; {('running... ' + html.escape(note)) if running else 'finished'}</p>"
            f"<p>{sum(1 for x in lines if x[2]=='PASS')} passed, {sum(1 for x in lines if x[2]=='FAIL')} failed, {len(lines)} done</p>"
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
        n = min(int(self.headers.get("Content-Length", 0)), 20000)
        form = parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
        pw = (form.get("pw") or [""])[0]
        if not PASSWORD or not hmac.compare_digest(pw.encode(), PASSWORD.encode()):
            time.sleep(1)   # slow down guessing
            return self._send(401, form_page("Wrong password (or none is set on the server)."))
        with LOCK:
            if STATE["running"]:
                return self._send(200, form_page("A check is already running; open /result."))
        urls = []
        for u in (form.get("links") or [""])[0].splitlines():
            u = u.strip()
            if u.startswith(("http://", "https://")) and u not in urls:
                urls.append(u)
        urls = urls[:MAX_LINKS]
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
