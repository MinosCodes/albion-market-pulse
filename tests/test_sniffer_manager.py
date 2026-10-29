"""Tests for the background Albion Data Client manager (no real process or network)."""
from __future__ import annotations

import json
from urllib.request import Request, urlopen

import pytest

from albion_flips import sniffer
from albion_flips.config import AppConfig
from albion_flips.sniffer import SnifferManager
from albion_flips.web import FlipDataStore, start_web_server


def test_ingest_targets_feed_dashboard_and_optionally_aodp(tmp_path) -> None:
    shared = SnifferManager(port=9000, install_dir=tmp_path)
    assert shared.ingest_targets == "https+pow://albion-online-data.com,http://127.0.0.1:9000/api/ingest"
    private = SnifferManager(port=9000, install_dir=tmp_path, share_with_aodp=False)
    assert private.ingest_targets == "http://127.0.0.1:9000/api/ingest"


def test_sniffer_config_section() -> None:
    cfg = AppConfig.from_dict({"sniffer": {"enabled": False, "share_with_aodp": False}})
    assert cfg.sniffer_enabled is False
    assert cfg.sniffer_share_with_aodp is False
    assert cfg.sniffer_auto_download is True
    assert AppConfig.from_dict({}).sniffer_enabled is True


@pytest.mark.skipif(not sniffer.IS_WINDOWS, reason="automatic start is Windows-only")
def test_missing_npcap_is_reported_before_downloading(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sniffer, "npcap_state", lambda: {"installed": False, "admin_only": False, "winpcap_only": False})
    downloads = []
    manager = SnifferManager(port=9000, install_dir=tmp_path)
    monkeypatch.setattr(manager, "_download_client", lambda: downloads.append(1))
    manager.start()
    status = manager.status()
    assert status["state"] == "needs_npcap"
    assert status["help_url"] == sniffer.NPCAP_URL
    assert downloads == []


@pytest.mark.skipif(not sniffer.IS_WINDOWS, reason="automatic start is Windows-only")
def test_missing_client_without_auto_download(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sniffer, "npcap_state", lambda: {"installed": True, "admin_only": False, "winpcap_only": False})
    manager = SnifferManager(port=9000, install_dir=tmp_path, auto_download=False)
    manager.start()
    assert manager.status()["state"] == "missing"


def test_dashboard_reports_and_controls_sniffer() -> None:
    class FakeManager:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def status(self) -> dict:
            return {"state": "running", "message": "ok", "log_tail": []}

        def start(self) -> None:
            self.calls.append("start")

        def stop(self) -> None:
            self.calls.append("stop")

        def restart(self) -> None:
            self.calls.append("restart")

        def show_window(self) -> bool:
            self.calls.append("show")
            return True

    store = FlipDataStore()
    store.update(server="europe", last_refresh="now", opportunities=[], config=AppConfig())
    fake = FakeManager()
    store.sniffer_manager = fake
    server = start_web_server(store, port=18773)
    try:
        with urlopen("http://127.0.0.1:18773/api/flips", timeout=5) as resp:
            assert json.loads(resp.read())["sniffer_process"]["state"] == "running"
        for action in ("show", "stop"):
            req = Request(f"http://127.0.0.1:18773/api/sniffer/{action}", data=b"", method="POST")
            with urlopen(req, timeout=5) as resp:
                assert json.loads(resp.read())["status"] == "ok"
        assert "show" in fake.calls

        store.sniffer_manager = None
        req = Request("http://127.0.0.1:18773/api/sniffer/start", data=b"", method="POST")
        with urlopen(req, timeout=5) as resp:
            assert json.loads(resp.read())["status"] == "disabled"
    finally:
        server.shutdown()
        server.server_close()


def test_zone_change_warning_follows_client_log(tmp_path) -> None:
    manager = SnifferManager(port=9000, install_dir=tmp_path)
    manager._state = "running"
    manager._note_location("ERRO[2026-10-29T03:26:59+01:00] The players location has not yet been set. Please transition zones so the location can be identified.")
    assert manager.status()["needs_zone_change"] is True
    manager._note_location("INFO[2026-10-29T03:30:11+01:00] Updating player location to 3003.")
    status = manager.status()
    assert status["needs_zone_change"] is False
    assert status["location_id"] == "3003"
