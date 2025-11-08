from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from flask import Flask, jsonify, request

DATA_PATH = Path(os.getenv("TAIPOWER_DATA_PATH", "processed.json"))


def load_data() -> Dict[str, Any]:
    if not DATA_PATH.exists():
        raise FileNotFoundError(f"Data file not found: {DATA_PATH}")
    with DATA_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


class DataStore:
    def __init__(self) -> None:
        self._cache: Dict[str, Any] | None = None
        self._mtime: float | None = None

    def get(self) -> Dict[str, Any]:
        current_mtime = DATA_PATH.stat().st_mtime if DATA_PATH.exists() else None
        if self._cache is None or self._mtime != current_mtime:
            self._cache = load_data()
            self._mtime = current_mtime
        return self._cache


def create_app() -> Flask:
    app = Flask(__name__)
    store = DataStore()

    @app.get("/health")
    def health() -> Any:
        return {"status": "ok", "data_file": str(DATA_PATH)}

    def _filter_outages(
        *,
        date_filter: Optional[str],
        street_filter: Optional[str],
        area_filter: Optional[str],
        include_groups: bool,
    ) -> list[dict[str, Any]]:
        data = store.get()
        outages: list[dict[str, Any]] = []
        for branch in data.get("branches", []):
            for notice in branch.get("notices", []):
                if date_filter and notice.get("date") != date_filter:
                    continue
                if street_filter and street_filter not in (notice.get("address_streets") or []):
                    continue
                if area_filter and area_filter not in (notice.get("areas") or []):
                    continue
                payload = {
                    "date": notice.get("date"),
                    "time_window": notice.get("time_window"),
                    "reason": notice.get("reason"),
                    "type": notice.get("type"),
                    "areas": notice.get("areas"),
                    "addresses": notice.get("addresses"),
                    "address_entry_count": notice.get("address_entry_count"),
                    "address_streets": notice.get("address_streets"),
                    "address_group_counts": notice.get("address_group_counts"),
                }
                if include_groups:
                    payload["address_groups"] = notice.get("address_groups")
                outages.append(payload)
        return outages

    @app.get("/outages")
    def list_outages_get() -> Any:
        date_filter = request.args.get("date")
        street_filter = request.args.get("street")
        area_filter = request.args.get("area")
        include_groups = request.args.get("include_groups", "false").lower() in (
            "1",
            "true",
            "yes",
        )
        outages = _filter_outages(
            date_filter=date_filter,
            street_filter=street_filter,
            area_filter=area_filter,
            include_groups=include_groups,
        )
        return jsonify({"count": len(outages), "outages": outages})

    @app.post("/outages/query")
    def list_outages_post() -> Any:
        payload = request.get_json(force=True) or {}
        date_filter = payload.get("date")
        street_filter = payload.get("street")
        area_filter = payload.get("area")
        include_groups = bool(payload.get("include_groups"))
        outages = _filter_outages(
            date_filter=date_filter,
            street_filter=street_filter,
            area_filter=area_filter,
            include_groups=include_groups,
        )
        return jsonify({"count": len(outages), "outages": outages})

    return app


app = create_app()


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0") in {"1", "true", "yes"}
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=debug)
