#!/usr/bin/env python3
import http.server, json, mimetypes, os, queue, socket, socketserver, threading
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "4310"))
CATALOG = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))

state = {
    "mode": "idle", "activeSlug": "01-reloops-overview", "paused": False,
    "captions": True, "muted": False, "volume": 1.0, "seekTo": None,
    "seq": 1, "currentTime": 0, "duration": 0, "updatedAt": 0,
}
sse_clients = set()
lock = threading.Lock()

def lan_ips():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return ips

def broadcast(ev_type, payload):
    msg = f"event: {ev_type}\ndata: {json.dumps(payload)}\n\n".encode("utf-8")
    with lock:
        for q in list(sse_clients):
            try: q.put_nowait(msg)
            except Exception: sse_clients.discard(q)

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass
    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/api/catalog":
            body = json.dumps({"catalog": CATALOG, "state": state, "lanIps": lan_ips(), "port": PORT}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if p == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            q = queue.Queue()
            with lock: sse_clients.add(q)
            try:
                self.wfile.write(f"event: state\ndata: {json.dumps(state)}\n\n".encode("utf-8"))
                self.wfile.flush()
                while True:
                    chunk = q.get()
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except Exception:
                with lock: sse_clients.discard(q)
            return
        if p == "/assets/logo-white.png": fp = ROOT / "assets/logo-white.png"
        elif p == "/assets/font.woff2": fp = ROOT / "assets/font.woff2"
        elif p == "/stand-b3-runtime.js": fp = ROOT / "stand-b3-runtime.js"
        elif p.startswith("/videos/"): fp = ROOT / "videos" / Path(p).name
        elif p in ("/ipad", "/ipad.html"): fp = ROOT / "ipad.html"
        elif p in ("/tv", "/tv.html"): fp = ROOT / "tv.html"
        else: fp = ROOT / "index.html"
        if not fp.exists() or not fp.is_file():
            self.send_error(404); return
        total = fp.stat().st_size
        ctype = {".mp4": "video/mp4", ".jpg": "image/jpeg", ".png": "image/png", ".vtt": "text/vtt; charset=utf-8", ".woff2": "font/woff2", ".js": "application/javascript; charset=utf-8"}.get(fp.suffix, "text/html; charset=utf-8")
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            parts = rng[6:].split("-")
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if len(parts) > 1 and parts[1] else total - 1
            end = min(end, total - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Content-Type", ctype)
            self.end_headers()
            with fp.open("rb") as f:
                f.seek(start)
                rem = end - start + 1
                while rem > 0:
                    buf = f.read(min(65536, rem))
                    if not buf: break
                    self.wfile.write(buf)
                    rem -= len(buf)
        else:
            self.send_response(200)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(total))
            self.send_header("Content-Type", ctype)
            self.end_headers()
            with fp.open("rb") as f:
                shutil.copyfileobj(f, self.wfile)
    def do_POST(self):
        if urlparse(self.path).path != "/api/command":
            self.send_error(404); return
        n = int(self.headers.get("Content-Length", "0"))
        cmd = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
        if cmd.get("action") == "progress":
            state["currentTime"] = float(cmd.get("currentTime") or 0)
            state["duration"] = float(cmd.get("duration") or state["duration"] or 0)
            broadcast("progress", {"currentTime": state["currentTime"], "duration": state["duration"], "activeSlug": state["activeSlug"], "mode": state["mode"], "paused": state["paused"]})
        else:
            state["seq"] += 1
            state["seekTo"] = None
            a = cmd.get("action")
            if a == "play":
                state["mode"] = "playing"; state["activeSlug"] = cmd.get("slug") or state["activeSlug"]; state["paused"] = False; state["currentTime"] = 0; state["seekTo"] = 0
            elif a == "togglePause": state["paused"] = not state["paused"]
            elif a == "pause": state["paused"] = True
            elif a == "resume": state["paused"] = False
            elif a == "seek": state["mode"] = "playing"; state["seekTo"] = float(cmd.get("time") or 0); state["currentTime"] = state["seekTo"]
            elif a == "replay": state["mode"] = "playing"; state["paused"] = False; state["seekTo"] = 0; state["currentTime"] = 0
            elif a == "toggleCaptions": state["captions"] = not state["captions"]
            elif a == "toggleMute": state["muted"] = not state["muted"]
            elif a == "idle": state["mode"] = "idle"; state["paused"] = False
            broadcast("state", state)
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

if __name__ == "__main__":
    import shutil
    ips = lan_ips()
    print(f"Stand B3 Demo running at http://localhost:{PORT}")
    if ips:
        print(f"Tristan iPad URL (same Wi-Fi/hotspot): http://{ips[0]}:{PORT}/ipad")
    ThreadedServer(("0.0.0.0", PORT), Handler).serve_forever()
