#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

from collections import OrderedDict

from taipower.fetcher import (
    BASE_PAGE,
    DEFAULT_MAPBOX_ENDPOINT,
    MAPBOX_ENDPOINTS,
    BranchLink,
    TaipowerCrawler,
    extract_branches,
    parse_branch_tables,
)
from taipower.geocode import MapboxGeocoder
from taipower.processing import (
    derive_street_labels,
    extract_city_area,
    normalize_for_lookup,
)


def apply_filters(
    branches: Iterable[BranchLink],
    *,
    allowed_regions: Optional[Iterable[str]],
    allowed_branches: Optional[Iterable[str]],
) -> List[BranchLink]:
    filtered: List[BranchLink] = []
    region_filter = (
        {normalize_for_lookup(r) for r in allowed_regions} if allowed_regions else None
    )
    branch_filter = (
        {normalize_for_lookup(b) for b in allowed_branches}
        if allowed_branches
        else None
    )
    for branch in branches:
        region_key = normalize_for_lookup(branch.region)
        branch_key = normalize_for_lookup(branch.name)
        if region_filter and region_key not in region_filter:
            continue
        if branch_filter and branch_key not in branch_filter:
            continue
        filtered.append(branch)
    return filtered


def collect_city_area(
    addresses: List[str],
) -> tuple[Optional[str], Optional[str], List[str], List[str]]:
    primary_city: Optional[str] = None
    primary_area: Optional[str] = None
    cities: List[str] = []
    areas: List[str] = []
    seen_cities = set()
    seen_areas = set()
    for addr in addresses:
        city, area = extract_city_area(addr)
        if city and city not in seen_cities:
            seen_cities.add(city)
            cities.append(city)
            if primary_city is None:
                primary_city = city
        if area and area not in seen_areas:
            seen_areas.add(area)
            areas.append(area)
            if primary_area is None:
                primary_area = area
    return primary_city, primary_area, cities, areas


def build_result(branches_data: List[dict]) -> dict:
    return {
        "source": BASE_PAGE,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "branch_count": len(branches_data),
        "branches": branches_data,
    }


def write_output(data: dict, output: str, indent: int) -> None:
    if output == "-":
        json.dump(data, os.sys.stdout, ensure_ascii=False, indent=indent)
        os.sys.stdout.write("\n")
        return
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=indent)


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crawl Taipower planned outage announcements."
    )
    parser.add_argument(
        "-o",
        "--output",
        default="taipower_outages.json",
        help="Path to output JSON file (use '-' for stdout).",
    )
    parser.add_argument(
        "--regions",
        nargs="+",
        help="Only crawl regions whose headings match the provided names.",
    )
    parser.add_argument(
        "--branches",
        nargs="+",
        help="Only crawl the specified branch names.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of branches to crawl (after filters).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="HTTP timeout per request in seconds (default: 20).",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="Indentation level for JSON output (default: 2).",
    )
    parser.add_argument(
        "--raw-output",
        help="Optional path to store the unprocessed crawl result (no cleaned addresses/geocode).",
    )
    parser.add_argument(
        "--mapbox-token",
        help="Mapbox access token for geocoding (fallback to MAPBOX_ACCESS_TOKEN env).",
    )
    parser.add_argument(
        "--mapbox-endpoint",
        default=DEFAULT_MAPBOX_ENDPOINT,
        choices=sorted(MAPBOX_ENDPOINTS),
        help="Mapbox geocoding endpoint to use.",
    )
    parser.add_argument(
        "--mapbox-country",
        default="tw",
        help="Comma-separated ISO-2 country codes to limit Mapbox results (default: tw).",
    )
    parser.add_argument(
        "--mapbox-language",
        default="zh-Hant",
        help="IETF language tag for Mapbox results (default: zh-Hant).",
    )
    parser.add_argument(
        "--mapbox-types",
        default="address,place",
        help="Comma-separated Mapbox feature types (default: address,place).",
    )
    parser.add_argument(
        "--mapbox-result-limit",
        type=int,
        default=1,
        help="Maximum number of Mapbox candidates to request per address (default: 1).",
    )
    parser.add_argument(
        "--mapbox-proximity",
        help="Bias Mapbox results toward this lon,lat coordinate.",
    )
    parser.add_argument(
        "--mapbox-bbox",
        help="Restrict Mapbox results to this bounding box (minLon,minLat,maxLon,maxLat).",
    )
    parser.add_argument(
        "--mapbox-autocomplete",
        action="store_true",
        help="Enable Mapbox autocomplete mode (default disabled).",
    )
    parser.add_argument(
        "--mapbox-delay",
        type=float,
        default=0.0,
        help="Sleep seconds between Mapbox requests (default: 0).",
    )
    parser.add_argument(
        "--geocode-cache",
        help="Path to a JSON cache file for Mapbox geocoding responses.",
    )
    parser.add_argument(
        "--disable-geocode",
        action="store_true",
        help="Skip geocoding even if a Mapbox token is configured.",
    )
    parser.add_argument(
        "--no-insecure-fallback",
        action="store_true",
        help="Fail instead of retrying when TLS certificate verification fails.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_arguments()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    crawler = TaipowerCrawler(
        timeout=args.timeout, allow_insecure_fallback=not args.no_insecure_fallback
    )
    try:
        main_html = crawler.fetch(BASE_PAGE)
    except Exception as exc:  # noqa: BLE001
        logging.error("Failed to fetch main page: %s", exc)
        return 1

    branches = extract_branches(main_html)
    if not branches:
        logging.error("Could not find any branch links on the main page.")
        return 1

    branches = apply_filters(
        branches, allowed_regions=args.regions, allowed_branches=args.branches
    )
    if args.limit is not None:
        branches = branches[: args.limit]

    if not branches:
        logging.warning("No branches left after applying filters.")

    branches_payload: List[dict] = []
    raw_branches_payload: List[dict] = []
    mapbox_token = args.mapbox_token or os.getenv("MAPBOX_ACCESS_TOKEN")
    geocoder: Optional[MapboxGeocoder] = None
    if mapbox_token and not args.disable_geocode:
        logging.info("Mapbox geocoding is enabled.")
        geocoder = MapboxGeocoder(
            token=mapbox_token,
            endpoint=args.mapbox_endpoint,
            country=args.mapbox_country,
            language=args.mapbox_language,
            result_limit=args.mapbox_result_limit,
            types=args.mapbox_types,
            bbox=args.mapbox_bbox,
            proximity=args.mapbox_proximity,
            autocomplete=args.mapbox_autocomplete,
            timeout=args.timeout,
            delay=args.mapbox_delay,
            cache_path=args.geocode_cache,
        )

    for index, branch in enumerate(branches, start=1):
        logging.info("(%d/%d) Fetching %s", index, len(branches), branch.name)
        try:
            branch_html = crawler.fetch(branch.url)
        except Exception as exc:  # noqa: BLE001
            logging.error("Failed to fetch %s: %s", branch.url, exc)
            branches_payload.append(
                {
                    "region": branch.region,
                    "name": branch.name,
                    "url": branch.url,
                    "branch_code": branch.code,
                    "notices": [],
                    "error": str(exc),
                }
            )
            continue

        parsed_notices: List[dict] = []
        for notice in parse_branch_tables(branch_html):
            addresses = notice.get("addresses") or []
            city, area, cities, areas = collect_city_area(addresses)
            notice["cities"] = cities
            notice["areas"] = areas
            parsed_notices.append(notice)

        notices = []
        for notice in parsed_notices:
            addresses = notice.get("addresses") or []
            cities = notice.get("cities") or []
            if cities:
                if "台北市" not in cities:
                    continue
            else:
                if not any("台北市" in addr for addr in addresses):
                    continue
            notices.append(notice)
        for notice in notices:
            addresses = notice.get("addresses") or []
            notice["address_entry_count"] = len(addresses)
            notice["type"] = "停電"
            street_map: OrderedDict[str, List[str]] = OrderedDict()
            for addr in addresses:
                labels = derive_street_labels(addr)
                for street in labels:
                    if street not in street_map:
                        street_map[street] = []
                    street_map[street].append(addr)
            street_labels = list(street_map.keys())
            notice["address_streets"] = street_labels
            notice["address_groups"] = street_map
            notice["address_group_counts"] = {
                street: len(entries) for street, entries in street_map.items()
            }
        clean_notices: List[dict] = []
        raw_notices: List[dict] = []
        for notice in notices:
            cleaned = {
                "caption": notice.get("caption"),
                "date": notice.get("date"),
                "roc_date": notice.get("roc_date"),
                "description": notice.get("description"),
                "type": notice.get("type"),
                "addresses": notice.get("addresses"),
                "address_entry_count": notice.get("address_entry_count"),
                "address_streets": notice.get("address_streets"),
                "address_group_counts": notice.get("address_group_counts"),
                "cities": notice.get("cities"),
                "areas": notice.get("areas"),
            }
            if notice.get("address_groups"):
                cleaned["address_groups"] = notice.get("address_groups")
            clean_notices.append(cleaned)
            raw_notices.append(dict(cleaned))
        if geocoder:
            for notice in notices:
                if notice.get("cancelled"):
                    notice["geocode"] = None
                    continue
                addresses = notice.get("addresses") or []
                if not addresses:
                    continue
                geocode_data = geocoder.geocode_addresses(addresses)
                notice["geocode"] = geocode_data

        branches_payload.append(
            {
                "region": branch.region,
                "name": branch.name,
                "url": branch.url,
                "branch_code": branch.code,
                "notice_count": len(clean_notices),
                "notices": clean_notices,
            }
        )
        raw_branches_payload.append(
            {
                "region": branch.region,
                "name": branch.name,
                "url": branch.url,
                "branch_code": branch.code,
                "notice_count": len(raw_notices),
                "notices": raw_notices,
            }
        )

    data = build_result(branches_payload)
    raw_data = build_result(raw_branches_payload) if args.raw_output else None
    try:
        write_output(data, args.output, args.indent)
        if raw_data and args.raw_output:
            write_output(raw_data, args.raw_output, args.indent)
    except OSError as exc:
        logging.error("Failed to write output: %s", exc)
        if geocoder:
            geocoder.close()
        return 1

    logging.info("Wrote %s branches to %s", len(branches_payload), args.output)
    if geocoder:
        geocoder.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
