"""Logic of the Windows live client (standard library only, testable on any OS).

The client connects your PC to an Albion Market Pulse server:
it starts the official Albion Data Client (github.com/ao-data/albiondata-client) so that every
market window you open in Albion Online is uploaded to the community database (AODP) and to
``https://<server>/api/ingest/<ingest_key>``.

Security decisions
------------------
* The ingest key is NEVER built into the program. Anyone can download a release; a built-in key
  would let everybody push prices into your server. The server URL may be built in as a default
  (see ``client/windows/defaults.json``); the key is entered once or pasted as a pairing link.
* Only HTTPS is accepted (plain HTTP only for localhost testing), certificates are verified,
  URLs with user info, query or fragment are rejected, and the key format is checked.
* Settings live in the user's profile (%APPDATA%) and the key is never written to logs.
* The Data Client is downloaded only from the official GitHub release API and only started
  after its SHA-256 matches the digest GitHub publishes for that release asset.
* This program never touches the game process; the Data Client only reads network packets.
"""
from __future__ import annotations

import gzip
import hashlib
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlsplit

APP_NAME = "AlbionMarketPulse"
DEFAULT_AODP_UPLOAD = "https+pow://pow.europe.albion-online-data.com"
DATA_CLIENT_REPO = "ao-data/albiondata-client"
DATA_CLIENT_ASSET = "update-windows-amd64.exe.gz"  # the client binary itself, gzip-compressed
USER_AGENT = "albion-market-pulse-client/1.0"
KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
AODP_ALLOWED = re.compile(r"^https\+pow://pow\.(europe|west|east)\.albion-online-data\.com$")


class ClientError(Exception):
    """A problem the user can fix (shown in the window)."""


@dataclass
class ClientSettings:
    server_url: str = ""
    ingest_key: str = ""
    aodp_upload: str = DEFAULT_AODP_UPLOAD
    data_client_version: str = "latest"
    auto_start: bool = True   # connect and start immediately when the program opens
    overlay: dict = field(default_factory=dict)  # in-game overlay: enabled, x, y, alpha
    extra: dict = field(default_factory=dict)

    def complete(self) -> bool:
        try:
            validate_server_url(self.server_url)
            validate_ingest_key(self.ingest_key)
            return True
        except ClientError:
            return False


# ---------------------------------------------------------------- validation
def validate_server_url(url: str) -> str:
    """Returns the normalized base URL or raises ClientError."""
    url = (url or "").strip().rstrip("/")
    if not url:
        raise ClientError("Server address is missing.")
    parts = urlsplit(url)
    if parts.username or parts.password:
        raise ClientError("The server address must not contain a user name or password.")
    if parts.query or parts.fragment:
        raise ClientError("The server address must not contain '?' or '#'.")
    host = (parts.hostname or "").lower()
    if not host or " " in url:
        raise ClientError("The server address is not valid.")
    if parts.scheme == "https":
        return url
    if parts.scheme == "http" and _is_loopback(host):
        return url  # local testing only
    raise ClientError("The server address must start with https:// (the key must not travel unencrypted).")


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_ingest_key(key: str) -> str:
    key = (key or "").strip()
    if not KEY_RE.match(key):
        raise ClientError("The key is missing or invalid (16-128 letters, digits, '-' or '_').")
    return key


def validate_aodp_upload(target: str) -> str:
    target = (target or DEFAULT_AODP_UPLOAD).strip()
    if not AODP_ALLOWED.match(target):
        raise ClientError("The community upload target must be the official AODP address.")
    return target


def parse_pairing_link(text: str) -> tuple[str, str]:
    """'https://host/api/ingest/<key>[/marketorders.ingest]' -> (server_url, key)."""
    text = (text or "").strip()
    m = re.match(r"^(https?://[^/?#\s]+(?:/[^?#\s]*?)?)/api/ingest/([A-Za-z0-9_-]{16,128})(?:/[a-z]+\.ingest)?/?$", text)
    if not m:
        raise ClientError("This is not a pairing link. It looks like https://your-server/api/ingest/<key>.")
    return validate_server_url(m.group(1)), validate_ingest_key(m.group(2))


def ingest_url(s: ClientSettings) -> str:
    return f"{validate_server_url(s.server_url)}/api/ingest/{validate_ingest_key(s.ingest_key)}"


def data_client_args(s: ClientSettings) -> list[str]:
    """Command line for albiondata-client: upload to AODP and to our server."""
    return ["-i", f"{validate_aodp_upload(s.aodp_upload)},{ingest_url(s)}"]


def mask(key: str) -> str:
    return key[:4] + "..." + key[-2:] if len(key) > 8 else "..."


# ---------------------------------------------------------------- settings
def settings_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / APP_NAME / "client.json"


def install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def bundled_defaults_path() -> Path:
    # PyInstaller unpacks data files to sys._MEIPASS
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent / "client" / "windows"))
    return base / "defaults.json"


def load_defaults(path: Path | None = None) -> ClientSettings:
    p = path or bundled_defaults_path()
    s = ClientSettings()
    if p.is_file():
        data = json.loads(p.read_text(encoding="utf-8"))
        s.server_url = str(data.get("server_url", "") or "")
        s.aodp_upload = str(data.get("aodp_upload", DEFAULT_AODP_UPLOAD) or DEFAULT_AODP_UPLOAD)
        s.data_client_version = str(data.get("data_client_version", "latest") or "latest")
    return s


def load_settings(path: Path | None = None, defaults: ClientSettings | None = None) -> ClientSettings:
    s = defaults or load_defaults()
    p = path or settings_path()
    if p.is_file():
        data = json.loads(p.read_text(encoding="utf-8"))
        for k in ("server_url", "ingest_key", "aodp_upload", "data_client_version"):
            if data.get(k):
                setattr(s, k, str(data[k]))
        if "auto_start" in data:
            s.auto_start = bool(data["auto_start"])
        if isinstance(data.get("overlay"), dict):
            s.overlay = clean_overlay_prefs(data["overlay"])
    return s


def save_settings(s: ClientSettings, path: Path | None = None) -> Path:
    s.server_url = validate_server_url(s.server_url)
    s.ingest_key = validate_ingest_key(s.ingest_key)
    s.aodp_upload = validate_aodp_upload(s.aodp_upload)
    p = path or settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    s.overlay = clean_overlay_prefs(s.overlay)
    data = asdict(s)
    data.pop("extra", None)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)  # no effect on Windows, where %APPDATA% is per-user anyway
    except OSError:
        pass
    return p


# ---------------------------------------------------------------- network
HttpFn = Callable[[str, str, bytes | None, dict], tuple[int, bytes]]


def http(method: str, url: str, body: bytes | None = None, headers: dict | None = None, timeout: float = 15) -> tuple[int, bytes]:
    """Small HTTPS helper with certificate verification (urllib default)."""
    h = {"User-Agent": USER_AGENT, **(headers or {})}
    req = urllib.request.Request(url, data=body, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        raise ClientError(f"Cannot reach the server: {e.reason}") from None


def check_connection(s: ClientSettings, http_fn: HttpFn | None = None) -> str:
    """Verifies server and key. Returns a short message or raises ClientError."""
    call = http_fn or (lambda m, u, b, h: http(m, u, b, h))
    base = validate_server_url(s.server_url)
    code, body = call("GET", base + "/healthz", None, {})
    if code != 200 or b'"ok"' not in body:
        raise ClientError(f"{base} does not look like an Albion Market Pulse server (HTTP {code}).")
    # an empty order list is accepted by the server and changes nothing
    code, _ = call("POST", ingest_url(s) + "/marketorders.ingest", b'{"Orders":[]}', {"Content-Type": "application/json"})
    if code == 401:
        raise ClientError("The server rejected the key. Ask the server owner for a new pairing link.")
    if code == 429:
        raise ClientError("Too many wrong keys from this connection. Wait 15 minutes.")
    if code != 200:
        raise ClientError(f"The server answered HTTP {code} to the key check.")
    return f"Connected to {base} (key {mask(s.ingest_key)})."


# ---------------------------------------------------------------- data client
@dataclass
class ReleaseAsset:
    version: str
    url: str
    sha256: str
    size: int


def fetch_release(version: str = "latest", http_fn: HttpFn | None = None) -> ReleaseAsset:
    call = http_fn or (lambda m, u, b, h: http(m, u, b, h))
    api = f"https://api.github.com/repos/{DATA_CLIENT_REPO}/releases/" + ("latest" if version == "latest" else f"tags/{version}")
    code, body = call("GET", api, None, {"Accept": "application/vnd.github+json"})
    if code != 200:
        raise ClientError(f"Could not read the Albion Data Client release from GitHub (HTTP {code}).")
    rel = json.loads(body)
    for a in rel.get("assets", []):
        if a.get("name") == DATA_CLIENT_ASSET:
            digest = str(a.get("digest") or "")
            if not digest.startswith("sha256:") or len(digest) != 71:
                raise ClientError("GitHub did not publish a SHA-256 checksum for this release; refusing to install it.")
            url = str(a.get("browser_download_url", ""))
            if not url.startswith(f"https://github.com/{DATA_CLIENT_REPO}/releases/download/"):
                raise ClientError("Unexpected download address for the Albion Data Client.")
            return ReleaseAsset(str(rel.get("tag_name", "")), url, digest[7:].lower(), int(a.get("size", 0)))
    raise ClientError(f"The release has no file named {DATA_CLIENT_ASSET}.")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def install_data_client(dest: Path, asset: ReleaseAsset, download: Callable[[str, Path], None] | None = None) -> Path:
    """Downloads, verifies and unpacks the Data Client. Nothing is executed before the checksum matches."""
    dest.mkdir(parents=True, exist_ok=True)
    exe = dest / "albiondata-client.exe"
    with tempfile.TemporaryDirectory() as td:
        gz = Path(td) / DATA_CLIENT_ASSET
        (download or _download)(asset.url, gz)
        got = sha256_file(gz)
        if got != asset.sha256:
            raise ClientError("Checksum mismatch: the downloaded Albion Data Client was NOT installed.")
        tmp_exe = Path(td) / "albiondata-client.exe"
        with gzip.open(gz, "rb") as src, open(tmp_exe, "wb") as out:
            shutil.copyfileobj(src, out)
        if exe.exists():
            exe.unlink()  # fails cleanly if the old client is still running
        shutil.move(str(tmp_exe), str(exe))
    (dest / "version.txt").write_text(f"{asset.version}\n{asset.sha256}\n", encoding="utf-8")
    return exe


def _download(url: str, target: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as r, open(target, "wb") as f:
        shutil.copyfileobj(r, f)


def installed_version(dest: Path) -> str:
    v = dest / "version.txt"
    return v.read_text(encoding="utf-8").splitlines()[0].strip() if v.is_file() else ""


def npcap_installed() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Npcap").Close()
        return True
    except OSError:
        return Path(os.environ.get("WINDIR", r"C:\Windows"), "System32", "Npcap").is_dir()


def data_client_running() -> bool:
    if sys.platform != "win32":
        return False
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq albiondata-client.exe", "/NH"],
                         capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    return "albiondata-client.exe" in out.lower()


def start_data_client(exe: Path, args: list[str]) -> None:
    """Starts the Data Client with administrator rights (needed for packet capture via Npcap)."""
    if sys.platform != "win32":
        raise ClientError("The live client only runs on Windows.")
    import ctypes
    params = subprocess.list2cmdline(args)
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", str(exe), params, str(exe.parent), 1)
    if rc <= 32:
        raise ClientError("The Albion Data Client was not started (administrator rights declined?).")


# ---------------------------------------------------------------- overlay
OVERLAY_DEFAULTS = {"enabled": True, "x": 40, "y": 140, "alpha": 0.88}
# Process names of the game client (Steam and standalone launcher).
GAME_PROCESSES = ("albion-online.exe", "albiononline.exe")


def clean_overlay_prefs(raw: dict | None) -> dict:
    """Only known keys with sane values end up in client.json."""
    out = dict(OVERLAY_DEFAULTS)
    raw = raw or {}
    if "enabled" in raw:
        out["enabled"] = bool(raw["enabled"])
    for k in ("x", "y"):
        try:
            out[k] = max(-4000, min(10000, int(raw.get(k, out[k]))))
        except (TypeError, ValueError):
            pass
    try:
        out["alpha"] = max(0.3, min(1.0, float(raw.get("alpha", out["alpha"]))))
    except (TypeError, ValueError):
        pass
    return out


def overlay_url(s: ClientSettings, what: str) -> str:
    """Overlay endpoints use the same key as the upload, so no second login is needed."""
    if what not in ("summary", "events", "item"):
        raise ValueError(what)
    return f"{validate_server_url(s.server_url)}/api/overlay/{validate_ingest_key(s.ingest_key)}/{what}"


def fetch_overlay_summary(s: ClientSettings, http_fn: HttpFn | None = None) -> dict:
    call = http_fn or (lambda m, u, b, h: http(m, u, b, h))
    code, body = call("GET", overlay_url(s, "summary"), None, {"Accept": "application/json"})
    if code == 401:
        raise ClientError("The server rejected the key. Ask the server owner for a new pairing link.")
    if code == 404:
        raise ClientError("This server has no overlay feed yet (update the server).")
    if code != 200:
        raise ClientError(f"The server answered HTTP {code}.")
    return json.loads(body)


def iter_sse(lines) -> "Iterator[dict]":
    """Parses a text/event-stream (an iterable of bytes or str lines) into JSON events.

    Comment lines (': ping') and unknown fields are ignored; events with broken JSON are skipped.
    """
    data: list[str] = []
    for raw in lines:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        line = line.rstrip("\r\n")
        if line == "":
            if data:
                try:
                    obj = json.loads("\n".join(data))
                    if isinstance(obj, dict):
                        yield obj
                except ValueError:
                    pass
                data = []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        if name == "data":
            data.append(value[1:] if value.startswith(" ") else value)


def open_event_stream(s: ClientSettings, timeout: float = 40):
    """Opens the live event stream. The server sends a keep-alive every 15 s, so a read timeout
    of 40 s means the connection is dead and the caller should reconnect."""
    req = urllib.request.Request(overlay_url(s, "events"), headers={"User-Agent": USER_AGENT, "Accept": "text/event-stream"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ClientError("The server rejected the key. Ask the server owner for a new pairing link.") from None
        if e.code == 404:
            raise ClientError("This server has no overlay feed yet (update the server).") from None
        raise ClientError(f"The server answered HTTP {e.code}.") from None
    except urllib.error.URLError as e:
        raise ClientError(f"Cannot reach the server: {e.reason}") from None


def silver(n: float | int | None) -> str:
    """1234 -> '1,234', 123456 -> '123k', 1234567 -> '1.23m'."""
    if n is None:
        return "–"
    n = float(n)
    sign = "-" if n < 0 else ""
    a = abs(n)
    if a >= 1_000_000:
        return f"{sign}{a / 1_000_000:.2f}m"
    if a >= 100_000:
        return f"{sign}{a / 1000:.0f}k"
    return f"{sign}{a:,.0f}"
