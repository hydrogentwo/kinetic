"""The local HTTP brain the phone overlay talks to.

Every route is on loopback by default. With ``--lan`` it also listens on the
network so the phone can reach the laptop, and then a pairing token is required
on every route except ``/health`` and the demo page; the token is printed as a
QR at startup and pasted once into the app.

Routes (all JSON):

``GET  /health``                    what model, which backend, is it alive
``GET  /profile``                   which apps are watchable, and the rules for each
``GET  /intent`` ``PUT /intent``    read and change the standing intentions
``POST /read``                      a screen read -> reading + options
``POST /guard``                     a draft -> verdict
``POST /feedback``                  which option was taken
``GET  /history`` ``GET  /stats``   local history and patterns
``GET  /``                          the demo page (works with no model at all)
"""

from __future__ import annotations

import json
import re
import secrets
import socket
import threading
import time
import urllib.parse
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import apps as apps_mod
from .config import Config
from .guard import review
from .model import Client, ModelError
from .reading import read_conversation
from .scenarios import all_scenarios
from .store import NullStore, Store
from .transcript import parse

WEB = Path(__file__).parent / "web"


class Brain:
    """Everything the routes need, in one object that is easy to fake in tests."""

    def __init__(self, cfg: Config, client: Client, store: Store | None = None, fixture: Path | None = None):
        self.cfg = cfg
        self.client = client
        self.store = store if store is not None else NullStore()
        self.fixture = fixture
        self.started = time.time()
        # thread -> (when, option texts). "Already offered" has to mean
        # recently: a thread reopened tomorrow should get its best answer back.
        self._recent: dict[str, tuple[float, list[str]]] = {}
        self.recent_ttl_s = 300.0
        self._lock = threading.Lock()

    # -- model ------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        ok, why = self.client.health()
        return {
            "ok": ok,
            "backend": self.client.name,
            "detail": why,
            "base_url": getattr(getattr(self.client, "openai", None), "cfg", self.cfg.model).base_url
            if hasattr(getattr(self.client, "openai", None), "cfg") else self.cfg.model.base_url,
            "uptime_s": int(time.time() - self.started),
            "app": "second-thought",
            "version": "0.1.0",
        }

    def read(self, body: dict[str, Any]) -> dict[str, Any]:
        cfg = self.cfg
        read = parse(
            body,
            app=body.get("app") or body.get("package"),
            self_names=body.get("self_names") or [],
            draft=body.get("draft"),
            max_turns=cfg.watch.max_turns,
            max_chars=cfg.watch.max_chars,
            keep_senders=cfg.watch.keep_senders,
        )
        if not cfg.watch.enabled:
            return {"ok": False, "error": "watching is off", "transcript": read.as_dict()}
        if read.app not in cfg.watch.apps and read.app != "generic":
            return {"ok": False, "error": f"{read.app} is not in the watch list",
                    "transcript": read.as_dict()}

        steer = str(body.get("steer") or body.get("want_to_say") or "")
        draft = read.draft if cfg.watch.include_own_draft else ""
        key = f"{read.app}:{read.thread}:{','.join(read.others())}"
        with self._lock:
            when, texts = self._recent.get(key, (0.0, []))
            exclude = list(texts) if (time.time() - when) < self.recent_ttl_s else []
        reading = read_conversation(self.client, read, cfg, steer=steer, draft=draft, exclude=exclude)
        with self._lock:
            if reading.options:
                self._recent[key] = (time.time(), ([o.text for o in reading.options] + exclude)[:12])
        rid = self.store.record_reading(reading, read)
        out = {
            "ok": not reading.error,
            "reading_id": rid,
            "transcript": read.as_dict(),
            **reading.as_dict(),
        }
        if reading.error:
            out["error"] = reading.error
        return out

    def guard(self, body: dict[str, Any]) -> dict[str, Any]:
        read = parse(
            body,
            app=body.get("app") or body.get("package"),
            self_names=body.get("self_names") or [],
            max_turns=self.cfg.watch.max_turns,
            max_chars=self.cfg.watch.max_chars,
        )
        draft = str(body.get("draft") or read.draft or "")
        if not draft.strip():
            return {"ok": True, "risk": 0, "reasons": [], "worth_showing": False, "backend": "none"}
        verdict = review(self.client, read, draft, self.cfg)
        show = verdict.risk >= self.cfg.guard.min_risk and self.cfg.guard.strength != "off"
        if show:
            self.store.record_guard(app=read.app, people=read.others(), verdict=verdict)
        return {
            "ok": not verdict.error,
            "worth_showing": show,
            "strength": self.cfg.guard.strength,
            **verdict.as_dict(),
        }

    def feedback(self, body: dict[str, Any]) -> dict[str, Any]:
        action = str(body.get("action") or "copy")
        if action == "used_fix":
            followed = self.store.record_followed(str(body.get("app") or ""))
            return {"ok": True, "followed": followed}
        text = str(body.get("text") or "")
        self.store.record_choice(
            reading_id=int(body.get("reading_id") or 0),
            app=str(body.get("app") or ""),
            option=_Opt(text, str(body.get("move") or ""), int(body.get("risk") or 0)),
            action=action,
        )
        return {"ok": True}

    def intent(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        if body:
            if "goals" in body:
                self.cfg.intent.goals = [str(g) for g in body["goals"] if str(g).strip()]
            if "add_goal" in body and str(body["add_goal"]).strip():
                self.cfg.intent.goals.append(str(body["add_goal"]).strip()[:300])
            if "remove_goal" in body:
                target = str(body["remove_goal"]).strip().lower()
                self.cfg.intent.goals = [g for g in self.cfg.intent.goals if g.strip().lower() != target]
            if "voice" in body:
                self.cfg.intent.voice = str(body["voice"])[:1000]
            if "avoid" in body:
                self.cfg.intent.avoid = [str(a) for a in body["avoid"] if str(a).strip()]
            if "add_avoid" in body and str(body["add_avoid"]).strip():
                self.cfg.intent.avoid.append(str(body["add_avoid"]).strip()[:200])
            if "people" in body and isinstance(body["people"], dict):
                self.cfg.intent.people.update({str(k): str(v) for k, v in body["people"].items()})
            if body.get("save", True):
                self.cfg.save()
        return {"ok": True, "intent": asdict(self.cfg.intent)}


class _Opt:
    """Shim so the store can take a posted option without the full dataclass."""

    def __init__(self, text: str, move: str, risk: int):
        self.text, self.move, self.risk, self.adjusted = text, move, risk, risk


# ---------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = "second-thought/0.1"
    protocol_version = "HTTP/1.1"
    brain: Brain  # set by serve()
    token: str = ""
    quiet: bool = True

    # -- helpers ----------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        if not self.quiet:
            print(f"{self.address_string()} {fmt % args}")

    def _send_json(self, obj: Any, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _send_file(self, path: Path, ctype: str) -> None:
        if not path.exists():
            self._send_json({"ok": False, "error": "not found"}, 404)
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _cors(self) -> None:
        # The overlay is a WebView on the phone hitting this over the LAN; the
        # demo page is served from here. Keeping CORS open on loopback is fine
        # because the token is what gates it when it is not loopback.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")

    def _body(self) -> dict[str, Any]:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        if n > 4 * 1024 * 1024:
            raise ValueError("body too large")
        raw = self.rfile.read(n)
        try:
            val = json.loads(raw.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as e:
            raise ValueError(f"body is not JSON: {e}") from e
        if not isinstance(val, dict):
            raise ValueError("body must be a JSON object")
        return val

    def _authorised(self, path: str) -> bool:
        if not self.token:
            return True
        if path in ("/health", "/", "/app.js", "/style.css", "/favicon.ico", "/scenarios",
                    "/overlay.html"):
            return True
            return True
        given = self.headers.get("Authorization") or ""
        given = given.replace("Bearer ", "").strip()
        if not given:
            q = urllib.parse.urlparse(self.path).query
            given = (urllib.parse.parse_qs(q).get("token") or [""])[0]
        return secrets.compare_digest(given, self.token)

    # -- routes -----------------------------------------------------------

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if not self._authorised(path):
            self._send_json({"ok": False, "error": "bad or missing token"}, 401)
            return
        b = self.brain
        if path == "/health":
            self._send_json(b.status())
        elif path == "/profile":
            self._send_json({
                "ok": True,
                "watch": {"enabled": b.cfg.watch.enabled, "apps": b.cfg.watch.apps},
                "watchable": [
                    {"id": p.id, "label": p.label, "packages": list(p.android_packages),
                     "register": p.register, "followup_weight": p.followup_weight}
                    for p in apps_mod.APPS.values() if p.id != "generic"
                ],
                "allowlist_packages": apps_mod.allowlist(b.cfg.watch.apps),
                "guard": asdict(b.cfg.guard),
            })
        elif path == "/intent":
            self._send_json({"ok": True, "intent": asdict(b.cfg.intent)})
        elif path == "/scenarios":
            try:
                self._send_json({"ok": True, "scenarios": all_scenarios()})
            except Exception as e:  # noqa: BLE001
                self._send_json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
        elif path == "/history":
            self._send_json({"ok": True, "readings": b.store.recent(30)})
        elif path == "/stats":
            self._send_json({"ok": True, "stats": b.store.stats().as_dict(),
                             "patterns": b.store.patterns()})
        elif path in ("/", "/index.html"):
            q = urllib.parse.urlparse(self.path).query
            overlay = (urllib.parse.parse_qs(q).get("overlay") or [""])[0] in ("1", "true")
            # The same origin serves the demo page and the floating panel the
            # phone loads, so both reach /read and /guard without CORS games.
            self._send_file(WEB / ("overlay.html" if overlay else "index.html"),
                            "text/html; charset=utf-8")
        elif path == "/overlay.html":
            # Same origin as the brain so the panel's fetch() needs no CORS and
            # no token in a URL: it authorises with the header like everything else.
            self._send_file(WEB / "overlay.html", "text/html; charset=utf-8")
        elif path == "/app.js":
            self._send_file(WEB / "app.js", "application/javascript; charset=utf-8")
        elif path == "/style.css":
            self._send_file(WEB / "style.css", "text/css; charset=utf-8")
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self._send_json({"ok": False, "error": f"no route {path}"}, 404)

    def do_PUT(self) -> None:  # noqa: N802
        self.do_POST()

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if not self._authorised(path):
            self._send_json({"ok": False, "error": "bad or missing token"}, 401)
            return
        try:
            body = self._body()
        except ValueError as e:
            self._send_json({"ok": False, "error": str(e)}, 400)
            return
        b = self.brain
        try:
            if path == "/read":
                self._send_json(b.read(body))
            elif path == "/guard":
                self._send_json(b.guard(body))
            elif path == "/feedback":
                self._send_json(b.feedback(body))
            elif path == "/intent":
                self._send_json(b.intent(body))
            elif path == "/health":
                self._send_json(b.status())
            else:
                self._send_json({"ok": False, "error": f"no route {path}"}, 404)
        except ModelError as e:
            self._send_json({"ok": False, "error": str(e), "backend": "error"}, 502)
        except Exception as e:  # noqa: BLE001 - a broken read must not kill the server
            self._send_json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)


def local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def ascii_qr(text: str) -> str:
    """A terminal pairing QR. No dependency, and the token is what it encodes."""
    try:
        import qrcode  # type: ignore
    except Exception:
        return ""
    qr = qrcode.QRCode(border=1)
    qr.add_data(text)
    qr.make()
    out = []
    for row in qr.get_matrix():
        out.append("".join("██" if cell else "  " for cell in row))
    return "\n".join(out)


def serve(cfg: Config, client: Client, *, host: str | None = None, port: int | None = None,
          lan: bool = False, token: str | None = None, quiet: bool = True,
          fixture: Path | None = None) -> ThreadingHTTPServer:
    store = Store(cfg.db_path, enabled=cfg.server.store_history)
    brain = Brain(cfg, client, store, fixture=fixture)
    if lan:
        cfg.server.host = "0.0.0.0"
        cfg.server.token = token or cfg.server.token or secrets.token_urlsafe(12)
    bind = host or cfg.server.host
    p = port or cfg.server.port
    Handler.brain = brain
    Handler.token = cfg.server.token
    Handler.quiet = quiet
    httpd = ThreadingHTTPServer((bind, p), Handler)
    httpd.daemon_threads = True
    return httpd


def run(cfg: Config, client: Client, *, lan: bool = False, port: int | None = None,
        quiet: bool = True, fixture: Path | None = None) -> None:
    httpd = serve(cfg, client, lan=lan, port=port, quiet=quiet, fixture=fixture)
    host, actual = httpd.server_address[:2]
    base = f"http://127.0.0.1:{actual}"
    print(f"second-thought listening on {base}")
    print(f"  demo:   {base}/")
    print(f"  health: {base}/health")
    if host == "0.0.0.0":
        ip = local_ip()
        url = f"http://{ip}:{actual}"
        print(f"  phone:  {url}   token: {httpd.RequestHandlerClass.token}")
        qr = ascii_qr(url + "/?token=" + httpd.RequestHandlerClass.token)
        if qr:
            print(qr)
        else:
            print("  (install qrcode for a terminal QR; the URL and token above are enough)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
