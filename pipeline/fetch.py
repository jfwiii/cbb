"""HTTP GET with per-host throttling, retries, and an optional on-disk cache."""
from __future__ import annotations

import collections
import gzip
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import config


class Fetcher:
    def __init__(self, rate: float = config.RATE_PER_HOST, cache_dir: Path | None = None):
        self.min_interval = 1.0 / rate
        self.cache_dir = cache_dir
        self.last_hit: dict[str, float] = {}
        self.stats = collections.Counter()

    def _cache_path(self, url: str) -> Path:
        h = hashlib.sha1(url.encode()).hexdigest()
        return self.cache_dir / h[:2] / f"{h}.json.gz"

    def get_json(self, url: str, use_cache: bool = True):
        """Return (status, parsed JSON or None, raw bytes or None).

        status: HTTP code, -1 for a non-JSON body, -2 for a network failure.
        """
        if self.cache_dir and use_cache:
            p = self._cache_path(url)
            if p.exists():
                self.stats["cache_hit"] += 1
                with gzip.open(p, "rb") as f:
                    rec = json.loads(f.read())
                raw = rec["raw"].encode() if rec["raw"] is not None else None
                return rec["status"], (json.loads(raw) if raw else None), raw

        host = urllib.parse.urlsplit(url).netloc
        status, body, raw = None, None, None
        for attempt in range(5):
            wait = self.last_hit.get(host, 0) + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last_hit[host] = time.monotonic()
            req = urllib.request.Request(
                url, headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=45) as r:
                    status, raw = r.status, r.read()
                self.stats[f"http_{status}"] += 1
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    status, body = -1, None
                break
            except urllib.error.HTTPError as e:
                status = e.code
                self.stats[f"http_{status}"] += 1
                if status in (429, 500, 502, 503, 504):
                    time.sleep(2 ** attempt * 2)
                    continue
                break
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
                self.stats["net_error"] += 1
                status = -2
                time.sleep(2 ** attempt * 2)

        if self.cache_dir and use_cache and status in (200, 404):
            p = self._cache_path(url)
            p.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(p, "wb") as f:
                f.write(json.dumps({"url": url, "status": status,
                                    "raw": raw.decode() if raw else None}).encode())
        return status, body, raw
