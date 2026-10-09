from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlencode

import requests

from albion_flips.models import HistoryRecord, PriceRecord

logger = logging.getLogger(__name__)

USER_AGENT = "albion-market-analyzer/0.1"
DEFAULT_BATCH_SIZE = 100
MAX_URL_LENGTH = 4000


class AodpClient:
    SERVER_HOSTS: dict[str, str] = {
        "europe": "europe.albion-online-data.com",
        "americas": "west.albion-online-data.com",
        "asia": "east.albion-online-data.com",
    }

    def __init__(
        self,
        server: str = "europe",
        session: requests.Session | None = None,
        cache_dir: Path | str | None = ".cache",
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_url_length: int = MAX_URL_LENGTH,
        prices_cache_ttl_seconds: int = 60,
        history_cache_ttl_seconds: int = 21600,  # 6 hours
        max_retries: int = 3,
        backoff_factor: float = 1.0,
    ) -> None:
        self.server = server.lower()
        if self.server not in self.SERVER_HOSTS:
            raise ValueError(f"Unknown server '{server}'")
        self.host = self.SERVER_HOSTS[self.server]
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.batch_size = batch_size
        self.max_url_length = max_url_length
        self.prices_cache_ttl_seconds = prices_cache_ttl_seconds
        self.history_cache_ttl_seconds = history_cache_ttl_seconds
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor

    def _get_cache_path(self, key: str) -> Path | None:
        if not self.cache_dir:
            return None
        safe_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{safe_hash}.json"

    def _read_cache(self, key: str, ttl_seconds: int) -> Any | None:
        cache_file = self._get_cache_path(key)
        if not cache_file or not cache_file.is_file():
            return None
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                payload = json.load(f)
            cached_time = payload.get("timestamp", 0)
            if time.time() - cached_time < ttl_seconds:
                return payload.get("data")
        except Exception as e:
            logger.debug("Failed to read cache file %s: %s", cache_file, e)
        return None

    def _write_cache(self, key: str, data: Any) -> None:
        cache_file = self._get_cache_path(key)
        if not cache_file:
            return
        try:
            payload = {"timestamp": time.time(), "data": data}
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(payload, f)
        except Exception as e:
            logger.debug("Failed to write cache file %s: %s", cache_file, e)

    def _build_batches(
        self,
        item_ids: Sequence[str],
        base_url_prefix: str,
        base_url_suffix: str,
    ) -> list[list[str]]:
        """Splits item_ids into batches respecting both batch_size and max_url_length."""
        batches: list[list[str]] = []
        current_batch: list[str] = []

        for item_id in item_ids:
            if not current_batch:
                current_batch.append(item_id)
                continue

            # Check batch size limit
            if len(current_batch) >= self.batch_size:
                batches.append(current_batch)
                current_batch = [item_id]
                continue

            # Check URL length limit
            candidate_ids = ",".join(current_batch + [item_id])
            candidate_url = f"{base_url_prefix}{candidate_ids}{base_url_suffix}"
            if len(candidate_url) > self.max_url_length:
                batches.append(current_batch)
                current_batch = [item_id]
            else:
                current_batch.append(item_id)

        if current_batch:
            batches.append(current_batch)

        return batches

    def _fetch_with_retry(self, url: str) -> Any:
        retries = 0
        backoff = self.backoff_factor

        while True:
            try:
                response = self.session.get(url, timeout=15)
                if response.status_code == 200:
                    return response.json()

                if response.status_code in (429, 500, 502, 503, 504):
                    retries += 1
                    if retries > self.max_retries:
                        response.raise_for_status()

                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            sleep_time = float(retry_after)
                        except ValueError:
                            sleep_time = backoff
                    else:
                        sleep_time = backoff
                        backoff *= 2.0

                    logger.warning(
                        "Received HTTP %d for %s. Retrying in %.1fs (attempt %d/%d)",
                        response.status_code,
                        url,
                        sleep_time,
                        retries,
                        self.max_retries,
                    )
                    time.sleep(sleep_time)
                    continue

                response.raise_for_status()
            except requests.RequestException as e:
                retries += 1
                if retries > self.max_retries:
                    raise
                logger.warning("Request failed (%s). Retrying in %.1fs (attempt %d/%d)", e, backoff, retries, self.max_retries)
                time.sleep(backoff)
                backoff *= 2.0

    def get_prices(
        self,
        item_ids: Sequence[str],
        cities: Sequence[str],
        qualities: Sequence[int] = (1,),
        bypass_cache: bool = False,
    ) -> list[PriceRecord]:
        if not item_ids:
            return []

        base_url_prefix = f"https://{self.host}/api/v2/stats/prices/"
        params: dict[str, str] = {}
        if cities:
            params["locations"] = ",".join(cities)
        if qualities:
            params["qualities"] = ",".join(str(q) for q in qualities)

        query_str = f"?{urlencode(params)}" if params else ""
        base_url_suffix = f".json{query_str}"

        batches = self._build_batches(item_ids, base_url_prefix, base_url_suffix)
        results: list[PriceRecord] = []

        for batch in batches:
            url = f"{base_url_prefix}{','.join(batch)}{base_url_suffix}"
            cached_data = None if bypass_cache else self._read_cache(url, self.prices_cache_ttl_seconds)
            if cached_data is not None:
                data = cached_data
            else:
                data = self._fetch_with_retry(url)
                self._write_cache(url, data)

            if isinstance(data, list):
                for item in data:
                    results.append(PriceRecord.from_dict(item))

        return results

    def get_history(
        self,
        item_ids: Sequence[str],
        cities: Sequence[str],
        qualities: Sequence[int] = (1,),
        time_scale: int = 24,
    ) -> list[HistoryRecord]:
        if not item_ids:
            return []

        base_url_prefix = f"https://{self.host}/api/v2/stats/history/"
        params: dict[str, str] = {"time-scale": str(time_scale)}
        if cities:
            params["locations"] = ",".join(cities)
        if qualities:
            params["qualities"] = ",".join(str(q) for q in qualities)

        base_url_suffix = f".json?{urlencode(params)}"
        batches = self._build_batches(item_ids, base_url_prefix, base_url_suffix)
        results: list[HistoryRecord] = []

        for batch in batches:
            url = f"{base_url_prefix}{','.join(batch)}{base_url_suffix}"
            cached_data = self._read_cache(url, self.history_cache_ttl_seconds)
            if cached_data is not None:
                data = cached_data
            else:
                data = self._fetch_with_retry(url)
                self._write_cache(url, data)

            if isinstance(data, list):
                for item in data:
                    results.append(HistoryRecord.from_dict(item))

        return results


class OfflineClient:
    """Mock client that serves data from local fixture JSON files."""

    def __init__(self, fixtures_dir: Path | str = "tests/fixtures") -> None:
        self.fixtures_dir = Path(fixtures_dir)
        self.prices_file = self.fixtures_dir / "prices_sample.json"
        self.history_file = self.fixtures_dir / "history_sample.json"

    def get_prices(
        self,
        item_ids: Sequence[str] | None = None,
        cities: Sequence[str] | None = None,
        qualities: Sequence[int] = (1,),
        bypass_cache: bool = False,
    ) -> list[PriceRecord]:
        if not self.prices_file.is_file():
            raise FileNotFoundError(f"Fixture not found: {self.prices_file}")
        with open(self.prices_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        records = [PriceRecord.from_dict(item) for item in data]
        if item_ids:
            records = [r for r in records if r.item_id in item_ids]
        if cities:
            records = [r for r in records if r.city in cities]
        if qualities:
            records = [r for r in records if r.quality in qualities]
        return records

    def get_history(
        self,
        item_ids: Sequence[str] | None = None,
        cities: Sequence[str] | None = None,
        qualities: Sequence[int] = (1,),
        time_scale: int = 24,
    ) -> list[HistoryRecord]:
        if not self.history_file.is_file():
            raise FileNotFoundError(f"Fixture not found: {self.history_file}")
        with open(self.history_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        records = [HistoryRecord.from_dict(item) for item in data]
        if item_ids:
            records = [r for r in records if r.item_id in item_ids]
        if cities:
            records = [r for r in records if r.location in cities]
        if qualities:
            records = [r for r in records if r.quality in qualities]
        return records
