"""Tests for the Windows live client logic (albion_flips/liveclient.py). Runs on any OS."""
from __future__ import annotations

import gzip
import hashlib
import json
import socket
from pathlib import Path

import pytest

from albion_flips import liveclient as lc
from albion_flips.config import AppConfig
from albion_flips.server import Auth, make_handler
from albion_flips.web import FlipDataStore, start_web_server

KEY = "k" * 32


# ---------------------------------------------------------------- validation
@pytest.mark.parametrize("url", ["https://pulse.example.com", "https://pulse.example.com/", "http://127.0.0.1:8765", "http://localhost:8765"])
def test_valid_server_urls(url: str) -> None:
    assert lc.validate_server_url(url) == url.rstrip("/")


@pytest.mark.parametrize("url", ["", "http://pulse.example.com", "ftp://x.com", "https://user:pw@x.com", "https://x.com/?a=1", "https://x.com/#f", "pulse.example.com", "http://192.168.1.5:8765"])
def test_rejected_server_urls(url: str) -> None:
    with pytest.raises(lc.ClientError):
        lc.validate_server_url(url)


def test_key_and_aodp_validation() -> None:
    assert lc.validate_ingest_key(" " + KEY + " ") == KEY
    for bad in ("", "short", "a" * 15, "has space" + "x" * 10, "x" * 129, "ä" * 20):
        with pytest.raises(lc.ClientError):
            lc.validate_ingest_key(bad)
    assert lc.validate_aodp_upload("https+pow://pow.west.albion-online-data.com")
    with pytest.raises(lc.ClientError):
        lc.validate_aodp_upload("https://evil.example.com")


def test_pairing_link() -> None:
    assert lc.parse_pairing_link(f"https://pulse.example.com/api/ingest/{KEY}") == ("https://pulse.example.com", KEY)
    assert lc.parse_pairing_link(f"https://pulse.example.com/api/ingest/{KEY}/marketorders.ingest") == ("https://pulse.example.com", KEY)
    for bad in ("https://pulse.example.com", f"http://pulse.example.com/api/ingest/{KEY}", "random text"):
        with pytest.raises(lc.ClientError):
            lc.parse_pairing_link(bad)


def test_data_client_arguments() -> None:
    s = lc.ClientSettings(server_url="https://pulse.example.com/", ingest_key=KEY)
    assert lc.data_client_args(s) == ["-i", f"https+pow://pow.europe.albion-online-data.com,https://pulse.example.com/api/ingest/{KEY}"]
    assert KEY not in lc.mask(KEY)


# ---------------------------------------------------------------- settings
def test_settings_roundtrip_and_defaults(tmp_path: Path) -> None:
    d = tmp_path / "defaults.json"
    d.write_text(json.dumps({"server_url": "https://my.server"}), encoding="utf-8")
    defaults = lc.load_defaults(d)
    assert defaults.server_url == "https://my.server" and not defaults.complete()  # no key built in
    p = tmp_path / "client.json"
    defaults.ingest_key = KEY
    lc.save_settings(defaults, p)
    s = lc.load_settings(p, lc.load_defaults(d))
    assert s.complete() and s.ingest_key == KEY and s.server_url == "https://my.server"
    # a user can switch to another server; the saved choice wins over the built-in default
    s.server_url = "https://other.server"
    lc.save_settings(s, p)
    assert lc.load_settings(p, lc.load_defaults(d)).server_url == "https://other.server"
    with pytest.raises(lc.ClientError):
        lc.save_settings(lc.ClientSettings(server_url="http://insecure.example", ingest_key=KEY), p)


def test_repository_defaults_contain_no_key() -> None:
    data = json.loads(Path("client/windows/defaults.json").read_text(encoding="utf-8"))
    assert "ingest_key" not in data and "access_key" not in data


# ---------------------------------------------------------------- connection check against a real server
def test_check_connection_against_running_server() -> None:
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    store = FlipDataStore()
    store.last_config = AppConfig()
    httpd = start_web_server(store, port=port, host="127.0.0.1",
                             handler_cls=make_handler(Auth("A" * 32, KEY, allow_local_ingest=False), dict))
    try:
        ok = lc.ClientSettings(server_url=f"http://127.0.0.1:{port}", ingest_key=KEY)
        assert "Connected" in lc.check_connection(ok)
        bad = lc.ClientSettings(server_url=f"http://127.0.0.1:{port}", ingest_key="z" * 32)
        with pytest.raises(lc.ClientError, match="rejected the key"):
            lc.check_connection(bad)
    finally:
        httpd.shutdown()


def test_check_connection_detects_wrong_server() -> None:
    fake = lambda m, u, b, h: (200, b"<html>some other site</html>")
    with pytest.raises(lc.ClientError, match="does not look like"):
        lc.check_connection(lc.ClientSettings(server_url="https://x.example", ingest_key=KEY), fake)


# ---------------------------------------------------------------- data client download
def _release(digest: str | None, url: str | None = None) -> bytes:
    asset = {"name": lc.DATA_CLIENT_ASSET, "size": 10,
             "browser_download_url": url or f"https://github.com/{lc.DATA_CLIENT_REPO}/releases/download/0.1.61/{lc.DATA_CLIENT_ASSET}"}
    if digest is not None:
        asset["digest"] = digest
    return json.dumps({"tag_name": "0.1.61", "assets": [asset]}).encode()


def test_release_requires_checksum_and_official_url() -> None:
    good = "sha256:" + "a" * 64
    assert lc.fetch_release(http_fn=lambda *a: (200, _release(good))).sha256 == "a" * 64
    with pytest.raises(lc.ClientError, match="checksum"):
        lc.fetch_release(http_fn=lambda *a: (200, _release(None)))
    with pytest.raises(lc.ClientError, match="Unexpected download"):
        lc.fetch_release(http_fn=lambda *a: (200, _release(good, "https://evil.example.com/x.gz")))


def test_install_only_when_checksum_matches(tmp_path: Path) -> None:
    payload = b"MZ fake windows binary"
    gz = gzip.compress(payload)
    digest = hashlib.sha256(gz).hexdigest()
    write = lambda url, target: Path(target).write_bytes(gz)

    exe = lc.install_data_client(tmp_path / "ok", lc.ReleaseAsset("0.1.61", "u", digest, len(gz)), write)
    assert exe.read_bytes() == payload and lc.installed_version(tmp_path / "ok") == "0.1.61"

    tampered = lambda url, target: Path(target).write_bytes(gzip.compress(b"MZ evil"))
    with pytest.raises(lc.ClientError, match="NOT installed"):
        lc.install_data_client(tmp_path / "bad", lc.ReleaseAsset("0.1.61", "u", digest, len(gz)), tampered)
    assert not (tmp_path / "bad" / "albiondata-client.exe").exists()
