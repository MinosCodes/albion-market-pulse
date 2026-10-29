"""Headless server mode for Linux: ``python -m albion_flips.server --config /etc/albion-pulse/config.json``

What it adds compared to ``cli --watch --web`` on a desktop
-----------------------------------------------------------
* Runs without a terminal UI or desktop window, logs to stdout (journald), stops cleanly on SIGTERM.
* Polls AODP around the clock, so prices keep flowing even when nobody has the dashboard open.
  AODP is fed by every player running the Albion Data Client, so this works without your PC.
* Stores prices and daily history in SQLite (storage.py): 7/30-day reference prices for the
  bait-price check (robust.py), history beyond AODP's window, and a warm start after restarts.
* Protects the dashboard: a login link (``/login?k=<access_key>``) sets a cookie for 400 days.
  ``public_read`` lets friends look without being able to change anything.
* Accepts live prices from your Windows PC: the Albion Data Client uploads to
  ``https://<server>/api/ingest/<ingest_key>`` in addition to AODP (see client/windows/).
  Without the key nobody can feed fake prices into your dashboard.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import secrets
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence
from urllib.parse import parse_qs, urlsplit

from albion_flips.analyzer import analyze_flips, parse_aodp_datetime
from albion_flips.api import AodpClient
from albion_flips.config import AppConfig, ConfigError, load_config, load_items
from albion_flips.models import PriceRecord
from albion_flips.ratelimit import RateLimiter
from albion_flips.storage import MarketStore
from albion_flips.web import FlipDataStore, FlipRequestHandler, start_web_server

logger = logging.getLogger("albion_flips.server")

COOKIE_NAME = "amp_auth"
COOKIE_MAX_AGE = 400 * 86400
MAX_INGEST_BYTES = 5_000_000
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


# ---------------------------------------------------------------- authentication
class Auth:
    """Login cookie for the dashboard and a separate key for live price uploads."""

    def __init__(self, access_key: str = "", ingest_key: str = "", public_read: bool = False, allow_local_ingest: bool = True) -> None:
        self.access_key = access_key
        self.ingest_key = ingest_key
        self.public_read = public_read
        self.allow_local_ingest = allow_local_ingest
        # The cookie holds an HMAC of the access key, never the key itself.
        self.cookie_value = hmac.new(access_key.encode(), b"albion-market-pulse-v1", hashlib.sha256).hexdigest() if access_key else ""
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def same(a: str, b: str) -> bool:
        return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())

    def session_ok(self, cookie_header: str | None) -> bool:
        if not self.access_key:
            return True  # no access key configured = no login (local use)
        for part in (cookie_header or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE_NAME and self.same(value, self.cookie_value):
                return True
        return False

    def can_read(self, cookie_header: str | None) -> bool:
        return self.public_read or self.session_ok(cookie_header)

    # brute-force protection: 10 failed attempts per IP lock the login for 15 minutes
    def locked(self, ip: str) -> bool:
        with self._lock:
            f = self._fails.get(ip)
            if not f:
                return False
            if time.time() - f[1] > 900:
                del self._fails[ip]
                return False
            return f[0] >= 10

    def failed(self, ip: str) -> None:
        with self._lock:
            f = self._fails.setdefault(ip, [0, time.time()])
            f[0] += 1


def split_ingest_path(path: str) -> tuple[str | None, str]:
    """'/api/ingest/<key>/marketorders.ingest' -> ('<key>', '/api/ingest/marketorders.ingest').

    The Albion Data Client appends '/marketorders.ingest' (or '/markethistories.ingest')
    to every upload URL, so the key has to live in the path, not in a query string.
    """
    if not path.startswith("/api/ingest/"):
        return None, path
    rest = path[len("/api/ingest/"):]
    first, _, tail = rest.partition("/")
    if not first or first.endswith(".ingest"):
        return None, path
    return first, "/api/ingest" + ("/" + tail if tail else "")


LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Albion Market Pulse - Login</title><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0f172a;color:#e2e8f0;font:15px/1.5 system-ui,sans-serif}
form{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:28px;width:min(340px,90vw)}h1{font-size:20px;margin:0 0 4px}p{color:#94a3b8;margin:0 0 18px;font-size:13px}
input{width:100%;box-sizing:border-box;padding:10px;border-radius:8px;border:1px solid #475569;background:#0f172a;color:inherit;font:inherit}
button{margin-top:12px;width:100%;padding:10px;border:0;border-radius:8px;background:#38bdf8;color:#0f172a;font-weight:600;font:inherit;cursor:pointer}.e{color:#f87171;margin-top:10px;font-size:13px}</style></head>
<body><form method="post" action="/login"><h1>Albion Market Pulse</h1><p>Enter the access key once. This browser stays signed in for a year.</p>
<input type="password" name="k" placeholder="Access key" autofocus autocomplete="current-password"><button>Sign in</button>__MSG__</form></body></html>"""


def make_handler(auth: Auth, status_fn: Callable[[], dict[str, Any]]) -> type[FlipRequestHandler]:
    class GuardedHandler(FlipRequestHandler):
        # ---- helpers
        def _ip(self) -> str:
            fwd = self.headers.get("X-Forwarded-For")
            return fwd.split(",")[0].strip() if fwd else self.client_address[0]

        def _direct_local(self) -> bool:
            # Behind a reverse proxy every request comes from 127.0.0.1, so a request that carries
            # X-Forwarded-For is never treated as local.
            return not self.headers.get("X-Forwarded-For") and self.client_address[0] in LOOPBACK

        def _https(self) -> bool:
            return self.headers.get("X-Forwarded-Proto", "").lower() == "https"

        def _send(self, code: int, body: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

        def _login_page(self, code: int = 200, msg: str = "") -> None:
            html = LOGIN_PAGE.replace("__MSG__", f'<div class="e">{msg}</div>' if msg else "")
            self._send(code, html.encode(), "text/html; charset=utf-8")

        def _grant(self) -> None:
            flags = "; Secure" if self._https() else ""
            self._send(302, b"", "text/plain", {
                "Location": "/",
                "Set-Cookie": f"{COOKIE_NAME}={auth.cookie_value}; Max-Age={COOKIE_MAX_AGE}; Path=/; HttpOnly; SameSite=Lax{flags}",
            })

        def _need_read(self, path: str) -> bool:
            """True if the request was answered with a denial."""
            if auth.can_read(self.headers.get("Cookie")):
                return False
            if path.startswith("/api/"):
                self._json(401, {"error": "login required"})
            else:
                self._send(302, b"", "text/plain", {"Location": "/login"})
            return True

        def _need_session(self) -> bool:
            if auth.session_ok(self.headers.get("Cookie")):
                return False
            self._json(401, {"error": "changes require login (open your login link)"})
            return True

        # ---- routes
        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/healthz":
                return self._json(200, {"ok": True})
            if path == "/login":
                key = parse_qs(urlsplit(self.path).query).get("k", [""])[0]
                if not key:
                    return self._login_page()
                ip = self._ip()
                if auth.locked(ip):
                    return self._login_page(429, "Too many attempts, wait 15 minutes.")
                if auth.same(key, auth.access_key):
                    return self._grant()
                auth.failed(ip)
                return self._login_page(401, "This link is not valid.")
            if path == "/logout":
                return self._send(302, b"", "text/plain", {"Location": "/login", "Set-Cookie": f"{COOKIE_NAME}=; Max-Age=0; Path=/"})
            if path == "/api/refresh":
                # triggers a full AODP sweep: never for anonymous visitors
                if self._need_session():
                    return
                return super().do_GET()
            if self._need_read(path):
                return
            if path == "/api/server_status":
                return self._json(200, status_fn())
            return super().do_GET()

        def do_HEAD(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/healthz":
                return self._json(200, {"ok": True})
            if self._need_read(path):
                return
            return super().do_HEAD()

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            length = int(self.headers.get("Content-Length", 0) or 0)
            if path == "/login":
                ip = self._ip()
                if auth.locked(ip):
                    return self._login_page(429, "Too many attempts, wait 15 minutes.")
                raw = self.rfile.read(min(length, 4096)).decode("utf-8", "replace")
                key = parse_qs(raw).get("k", [""])[0]
                if auth.same(key, auth.access_key):
                    return self._grant()
                auth.failed(ip)
                return self._login_page(401, "Wrong access key.")

            if path.startswith("/api/ingest") or path.endswith(".ingest"):
                if length > MAX_INGEST_BYTES:
                    return self._json(413, {"error": "payload too large"})
                if auth.locked(self._ip()):
                    return self._json(429, {"error": "too many invalid keys, wait 15 minutes"})
                key, clean = split_ingest_path(path)
                key = key or self.headers.get("X-Ingest-Key", "")
                ok = (
                    (auth.ingest_key and auth.same(key, auth.ingest_key))
                    or (auth.allow_local_ingest and self._direct_local())
                    or (key == "" and auth.access_key and auth.session_ok(self.headers.get("Cookie")))  # dashboard "test" button
                    or (not auth.ingest_key and not auth.access_key)  # nothing configured = local desktop-like use
                )
                if not ok:
                    if key:
                        auth.failed(self._ip())
                    return self._json(401, {"error": "invalid ingest key"})
                self.path = clean
                return super().do_POST()

            # everything else changes state (fulfill, override, clear_overrides, refresh, refresh_item)
            if self._need_session():
                return
            return super().do_POST()

    return GuardedHandler


# ---------------------------------------------------------------- polling
def merge_prices(api_prices: Sequence[PriceRecord], current: Sequence[PriceRecord]) -> list[PriceRecord]:
    """Keeps the newer sell and buy side per item/city/quality.

    Live uploads from the Windows client can be newer than what AODP returns a few seconds
    later; without merging, every sweep would overwrite fresh live prices with older ones.
    """
    def newer(a: str, b: str) -> bool:
        da, db = parse_aodp_datetime(a), parse_aodp_datetime(b)
        return da is not None and (db is None or da > db)

    out: dict[tuple[str, str, int], PriceRecord] = {}
    for p in api_prices:
        out[(p.item_id, p.city, p.quality)] = PriceRecord(**{f: getattr(p, f) for f in PriceRecord.__slots__})
    for c in current:
        key = (c.item_id, c.city, c.quality)
        p = out.get(key)
        if p is None:
            out[key] = c
            continue
        if c.sell_price_min > 0 and newer(c.sell_price_min_date, p.sell_price_min_date):
            p.sell_price_min, p.sell_price_min_date = c.sell_price_min, c.sell_price_min_date
        if c.buy_price_max > 0 and newer(c.buy_price_max_date, p.buy_price_max_date):
            p.buy_price_max, p.buy_price_max_date = c.buy_price_max, c.buy_price_max_date
    return list(out.values())


class Poller:
    def __init__(
        self,
        config: AppConfig,
        item_ids: list[str],
        client: Any,
        market: MarketStore,
        store: FlipDataStore,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.config, self.item_ids, self.client, self.market, self.store, self.now_fn = config, item_ids, client, market, store, now_fn
        self.cycles = 0
        self.errors = 0
        self.last_error = ""
        self.last_cycle = ""
        self.last_duration = 0.0
        self.last_changed = 0
        self._last_prune = 0.0
        self._wake = threading.Event()

    def request_sweep(self) -> None:
        """Dashboard 'refresh' button: start the next sweep now instead of running it in the web thread."""
        self._wake.set()

    def cycle(self) -> int:
        """One full sweep. Returns the number of flips found."""
        cfg, now, t0 = self.config, self.now_fn(), time.monotonic()
        prices = self.client.get_prices(item_ids=self.item_ids, cities=cfg.cities, qualities=cfg.qualities, bypass_cache=True)
        self.last_changed = self.market.record_prices(prices, "api")
        active = sorted({p.item_id for p in prices if p.sell_price_min > 0 or p.buy_price_max > 0})
        # AodpClient caches history for 6 hours, so this is only expensive on the first sweep.
        history = self.client.get_history(item_ids=active or self.item_ids[:100], cities=cfg.cities, qualities=cfg.qualities)
        self.market.record_history(history)
        refs = self.market.references(now)
        merged = merge_prices(prices, self.store.last_prices)
        self.store.reference = refs
        flips = analyze_flips(
            prices=merged, history=history, config=cfg, now=now,
            fulfilled_orders=self.store.fulfilled_orders, price_overrides=self.store.price_overrides, reference=refs,
        )
        self.store.update(server=cfg.server, last_refresh=now.strftime("%Y-%m-%d %H:%M:%S UTC"), opportunities=flips,
                          prices=merged, history=history, now=now, config=cfg)
        if time.monotonic() - self._last_prune > 3600:
            self.market.prune(now, cfg.service.raw_keep_days, cfg.service.daily_keep_days)
            self._last_prune = time.monotonic()
        self.cycles += 1
        self.last_cycle = now.strftime("%Y-%m-%d %H:%M:%S UTC")
        self.last_duration = time.monotonic() - t0
        logger.info("sweep %d: %d prices (%d changed), %d history records, %d flips in %.1fs",
                    self.cycles, len(prices), self.last_changed, len(history), len(flips), self.last_duration)
        return len(flips)

    def run(self, stop: threading.Event) -> None:
        while not stop.is_set():
            started = time.monotonic()
            try:
                self.cycle()
            except Exception as e:  # keep running: AODP outages must not kill the service
                self.errors += 1
                self.last_error = f"{type(e).__name__}: {e}"[:300]
                logger.exception("sweep failed")
            wait = max(5.0, self.config.refresh_seconds - (time.monotonic() - started))
            self._wake.wait(wait)
            self._wake.clear()

    def status(self) -> dict[str, Any]:
        return {
            "cycles": self.cycles, "errors": self.errors, "last_error": self.last_error,
            "last_cycle": self.last_cycle, "last_duration_s": round(self.last_duration, 1),
            "last_changed_prices": self.last_changed, "items": len(self.item_ids),
            "aodp_requests": getattr(self.client, "requests_sent", None),
            "refresh_seconds": self.config.refresh_seconds, "db": self.market.stats(),
            "live_client_seconds_ago": round(time.time() - self.store.sniffer_last_timestamp) if self.store.sniffer_last_timestamp else None,
        }


# ---------------------------------------------------------------- entry point
def init_keys(config_path: str | None, write: bool) -> int:
    access, ingest = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    if write and config_path:
        p = Path(config_path)
        data = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        svc = data.setdefault("service", {})
        svc["access_key"], svc["ingest_key"] = access, ingest
        p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        try:
            p.chmod(0o600)
        except OSError:
            pass
        print(f"Keys written to {p}")
    print(f"access_key: {access}\ningest_key: {ingest}")
    print("Login link:      https://<your-host>/login?k=" + access)
    print("Live client URL: https://<your-host>/api/ingest/" + ingest)
    return 0


def show_links(config: AppConfig) -> int:
    """Prints the owner's login link and the pairing link for the Windows client."""
    svc = config.service
    base = (svc.public_url or "https://<your-host>").rstrip("/")
    if not svc.access_key or not svc.ingest_key:
        print("No keys configured yet. Run: --init-keys --write")
        return 1
    print("Login link (keep it private, opens the dashboard for a year):")
    print(f"  {base}/login?k={svc.access_key}")
    print("Pairing link for the Windows client (paste it into AlbionMarketPulseClient.exe):")
    print(f"  {base}/api/ingest/{svc.ingest_key}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Albion Market Pulse - headless Linux server")
    ap.add_argument("--config", default=None, help="config.json (default: ./config.json)")
    ap.add_argument("--items", default="data/items.json")
    ap.add_argument("--once", action="store_true", help="run one sweep, print a summary and exit")
    ap.add_argument("--init-keys", action="store_true", help="generate access and ingest keys")
    ap.add_argument("--write", action="store_true", help="with --init-keys: store the keys in --config")
    ap.add_argument("--show-links", action="store_true", help="print the login link and the pairing link for the Windows client")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    if args.init_keys:
        return init_keys(args.config, args.write)
    try:
        config = load_config(args.config)
        if args.show_links:
            return show_links(config)
        item_ids = load_items(args.items)
    except (FileNotFoundError, ConfigError) as e:
        logger.error("configuration error: %s", e)
        return 1

    data_dir = Path(config.service.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    limiter = RateLimiter(windows=((config.aodp_rate_per_minute, 60.0), (config.aodp_rate_per_5min, 300.0)))
    client = AodpClient(server=config.server, cache_dir=data_dir / "cache",
                        prices_cache_ttl_seconds=min(60, config.refresh_seconds), rate_limiter=limiter)
    market = MarketStore(data_dir / "market.db")
    store = FlipDataStore(overrides_path=data_dir / "overrides.json")
    store.client, store.last_config, store.server = client, config, config.server
    store.ingest_listener = lambda recs: market.record_prices(recs, "live")
    poller = Poller(config, item_ids, client, market, store)

    if args.once:
        n = poller.cycle()
        top = store.get_data()["flips"][:5]
        print(json.dumps({"flips": n, "top": [{k: f[k] for k in ("item_id", "buy_city", "sell_city", "profit_per_item", "est_daily_profit", "price_note")} for f in top], "status": poller.status()}, indent=2))
        market.close()
        return 0

    # warm start: last known prices so the dashboard is not empty while the first sweep runs
    latest = market.load_latest()
    if latest:
        store.last_prices = latest
        store.reference = market.references(datetime.now(timezone.utc))
        logger.info("warm start with %d stored prices", len(latest))

    svc = config.service
    if not svc.access_key:
        logger.warning("no service.access_key set: the dashboard has NO login. Run --init-keys --write.")
    auth = Auth(svc.access_key, svc.ingest_key, svc.public_read, svc.allow_local_ingest)
    store.set_refresh_callback(poller.request_sweep)
    httpd = start_web_server(store, port=config.web_port, host=config.web_host, handler_cls=make_handler(auth, poller.status))

    stop = threading.Event()
    def _stop(signum: int, _frame: Any) -> None:
        logger.info("signal %d received, shutting down", signum)
        stop.set()
        poller.request_sweep()  # wake the poller so it sees the stop flag
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    worker = threading.Thread(target=poller.run, args=(stop,), name="poller", daemon=True)
    worker.start()
    stop.wait()
    httpd.shutdown()
    worker.join(timeout=30)
    market.close()
    logger.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
