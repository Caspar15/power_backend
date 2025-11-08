from __future__ import annotations

import json
import time
import logging
from pathlib import Path
from typing import Iterable, List, Optional
from urllib.parse import quote

import requests

from .processing import STOPWORDS, address_priority, normalize_label


class MapboxGeocoder:
    BASE_URL = "https://api.mapbox.com/geocoding/v5/"

    def __init__(
        self,
        token: str,
        *,
        endpoint: str = "mapbox.places",
        country: Optional[str] = "tw",
        language: str = "zh-Hant",
        result_limit: int = 1,
        types: Optional[str] = "address,place",
        bbox: Optional[str] = None,
        proximity: Optional[str] = None,
        autocomplete: bool = False,
        timeout: int = 20,
        delay: float = 0.0,
        cache_path: Optional[str] = None,
    ) -> None:
        from taipower.fetcher import MAPBOX_ENDPOINTS  # local import to avoid cycle

        if endpoint not in MAPBOX_ENDPOINTS:
            raise ValueError(f"Unsupported Mapbox endpoint: {endpoint}")
        if not token:
            raise ValueError("Mapbox access token is required.")
        self.token = token
        self.endpoint = endpoint
        self.country = country
        self.language = language
        self.result_limit = max(1, min(result_limit, 10))
        self.types = types
        self.bbox = bbox
        self.proximity = proximity
        self.autocomplete = autocomplete
        self.timeout = timeout
        self.delay = max(0.0, delay)
        self.session = requests.Session()
        self.cache_path = Path(cache_path) if cache_path else None
        self._cache: dict[str, Optional[dict]] = {}
        if self.cache_path:
            self._load_cache()

    def _load_cache(self) -> None:
        if not self.cache_path or not self.cache_path.exists():
            return
        try:
            with self.cache_path.open(encoding="utf-8") as handle:
                data = json.load(handle)
                if isinstance(data, dict):
                    self._cache = data
        except json.JSONDecodeError:
            pass

    def save_cache(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cache_path.open("w", encoding="utf-8") as handle:
            json.dump(self._cache, handle, ensure_ascii=False, indent=2)

    def close(self) -> None:
        self.session.close()
        self.save_cache()

    def geocode_addresses(self, addresses: Iterable[str]) -> Optional[dict]:
        best_result: Optional[dict] = None
        for query in self._prioritize_addresses(addresses):
            result = self.geocode(query)
            if not result:
                continue
            if self._is_precise(result):
                return result
            if best_result is None:
                best_result = result
        return best_result

    def _prioritize_addresses(self, addresses: Iterable[str]) -> List[str]:
        normalized: List[str] = []
        seen = set()
        for raw in addresses:
            query = normalize_label(raw)
            if not query or query in STOPWORDS or query in seen:
                continue
            seen.add(query)
            normalized.append(query)
        normalized.sort(key=address_priority)
        return normalized

    @staticmethod
    def _is_precise(result: dict) -> bool:
        types = result.get("types") or []
        return any(t in ("address", "poi") for t in types)

    def geocode(self, query: str) -> Optional[dict]:
        if query in self._cache:
            return self._cache[query]
        encoded = quote(query, safe="")
        url = f"{self.BASE_URL}{self.endpoint}/{encoded}.json"
        params = {
            "access_token": self.token,
            "limit": self.result_limit,
            "language": self.language,
            "autocomplete": str(self.autocomplete).lower(),
        }
        if self.country:
            params["country"] = self.country
        if self.types:
            params["types"] = self.types
        if self.bbox:
            params["bbox"] = self.bbox
        if self.proximity:
            params["proximity"] = self.proximity
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            logging.error("Mapbox request failed for '%s': %s", query, exc)
            self._cache[query] = None
            return None
        if self.delay:
            time.sleep(self.delay)
        payload = response.json()
        features = payload.get("features") or []
        for feature in features:
            formatted = self._format_feature(query, feature)
            if formatted:
                self._cache[query] = formatted
                return formatted
        self._cache[query] = None
        return None

    def _format_feature(self, query: str, feature: dict) -> Optional[dict]:
        center = feature.get("center")
        if not center or len(center) < 2:
            return None
        properties = feature.get("properties") or {}
        return {
            "query": query,
            "matched_name": feature.get("place_name"),
            "lat": center[1],
            "lng": center[0],
            "accuracy": properties.get("accuracy"),
            "relevance": feature.get("relevance"),
            "types": feature.get("place_type"),
            "source": self.endpoint,
            "bbox": feature.get("bbox"),
            "context": feature.get("context"),
        }


__all__ = ["MapboxGeocoder"]
