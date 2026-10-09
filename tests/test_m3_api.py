from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest
import requests

from albion_flips.api import AodpClient, OfflineClient


def test_case_14_batching_250_items() -> None:
    """Case 14: AodpClient batching 250 IDs produces 3 requests, none over the URL length budget."""
    mock_session = MagicMock(spec=requests.Session())
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = []
    mock_session.get.return_value = mock_response

    client = AodpClient(
        server="europe",
        session=mock_session,
        cache_dir=None,  # disable caching for batch test
        batch_size=100,
        max_url_length=4000,
    )

    item_ids = [f"ITEM_{i:04d}" for i in range(250)]
    cities = ["Bridgewatch", "Martlock"]

    results = client.get_prices(item_ids=item_ids, cities=cities, qualities=[1])

    # Should have made exactly 3 calls (100, 100, 50)
    assert mock_session.get.call_count == 3

    for call_args in mock_session.get.call_args_list:
        url = call_args[0][0]
        assert len(url) <= 4000
        assert url.startswith("https://europe.albion-online-data.com/api/v2/stats/prices/")
        assert ".json?locations=Bridgewatch%2CMartlock&qualities=1" in url or "locations=Bridgewatch" in url


def test_aodp_caching(tmp_path: Path) -> None:
    """Verify that requests are cached to disk within TTL."""
    mock_session = MagicMock(spec=requests.Session())
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = [{"item_id": "T4_PLANKS", "city": "Martlock"}]
    mock_session.get.return_value = mock_response

    client = AodpClient(
        server="europe",
        session=mock_session,
        cache_dir=tmp_path / "cache",
        prices_cache_ttl_seconds=60,
    )

    res1 = client.get_prices(["T4_PLANKS"], ["Martlock"])
    assert len(res1) == 1
    assert mock_session.get.call_count == 1

    # Second call should hit disk cache
    res2 = client.get_prices(["T4_PLANKS"], ["Martlock"])
    assert len(res2) == 1
    assert mock_session.get.call_count == 1


def test_offline_client() -> None:
    """Verify OfflineClient reads fixtures correctly."""
    offline = OfflineClient("tests/fixtures")
    prices = offline.get_prices()
    assert len(prices) > 0
    history = offline.get_history()
    assert len(history) > 0
