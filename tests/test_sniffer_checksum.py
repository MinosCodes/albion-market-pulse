"""The managed Data Client is only installed when its SHA-256 matches GitHub's digest."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from albion_flips import liveclient as lc
from albion_flips.sniffer import SnifferManager

PAYLOAD = b"MZ fake windows binary"
GZ = gzip.compress(PAYLOAD)
URL = f"https://github.com/{lc.DATA_CLIENT_REPO}/releases/download/0.1.61/{lc.DATA_CLIENT_ASSET}"


def _api(digest: str | None):
    asset = {"name": lc.DATA_CLIENT_ASSET, "size": len(GZ), "browser_download_url": URL}
    if digest is not None:
        asset["digest"] = digest
    body = json.dumps({"tag_name": "0.1.61", "assets": [asset]}).encode()
    return lambda *a: (200, body)


def _manager(tmp_path: Path, digest: str | None, data: bytes) -> SnifferManager:
    return SnifferManager(install_dir=tmp_path, http_fn=_api(digest),
                          download_fn=lambda url, target: Path(target).write_bytes(data))


def test_installs_when_checksum_matches(tmp_path: Path) -> None:
    m = _manager(tmp_path, "sha256:" + hashlib.sha256(GZ).hexdigest(), GZ)
    m._download_client()
    assert m.exe_path.read_bytes() == PAYLOAD and m._version == "0.1.61"


def test_tampered_download_is_not_installed(tmp_path: Path) -> None:
    m = _manager(tmp_path, "sha256:" + hashlib.sha256(GZ).hexdigest(), gzip.compress(b"MZ evil"))
    with pytest.raises(lc.ClientError, match="NOT installed"):
        m._download_client()
    assert not m.exe_path.exists()


def test_release_without_checksum_is_refused(tmp_path: Path) -> None:
    m = _manager(tmp_path, None, GZ)
    with pytest.raises(lc.ClientError, match="checksum"):
        m._download_client()
    assert not m.exe_path.exists()
