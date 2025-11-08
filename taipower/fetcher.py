from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import requests
import urllib3
from bs4 import BeautifulSoup
from requests import Response

from .processing import extract_address_candidates, is_cancelled_notice

BASE_PAGE = "https://www.taipower.com.tw/2289/2406/2420/2421/11934/"
USER_AGENT = "TaipowerCrawler/1.0 (+github.com/codex-taipower-crawler)"
DATE_PATTERN = re.compile(
    r"(?P<year>\d+)\s*年\s*(?P<month>\d+)\s*月\s*(?P<day>\d+)\s*日"
)
ROC_YEAR_OFFSET = 1911
DEFAULT_MAPBOX_ENDPOINT = "mapbox.places"
MAPBOX_ENDPOINTS = {"mapbox.places", "mapbox.places-permanent"}


@dataclass
class BranchLink:
    region: str
    name: str
    url: str

    @property
    def code(self) -> Optional[str]:
        parsed = urlparse(self.url)
        for part in parsed.path.split("/"):
            if part.startswith("d") and part[1:].isdigit():
                return part
        return None


class TaipowerCrawler:
    def __init__(self, *, timeout: int, allow_insecure_fallback: bool) -> None:
        self.timeout = timeout
        self.allow_insecure_fallback = allow_insecure_fallback
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": USER_AGENT, "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8"}
        )
        self._force_insecure = False
        self._insecure_warned = False

    def fetch(self, url: str) -> str:
        verify = not self._force_insecure
        try:
            response = self._request(url, verify=verify)
        except requests.exceptions.SSLError:
            if not self.allow_insecure_fallback or not verify:
                raise
            logging.warning(
                "SSL verification failed for %s; retrying without certificate validation.",
                url,
            )
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            self._force_insecure = True
            response = self._request(url, verify=False)
            if not self._insecure_warned:
                logging.warning(
                    "Running in insecure mode. TLS certificates will not be verified."
                )
                self._insecure_warned = True
        return response.text

    def _request(self, url: str, *, verify: bool) -> Response:
        response = self.session.get(url, timeout=self.timeout, verify=verify)
        response.raise_for_status()
        return response


def extract_branches(html: str) -> List[BranchLink]:
    soup = BeautifulSoup(html, "html.parser")
    branches: List[BranchLink] = []
    seen = set()
    for section in soup.select("section.seeAlso.drawer"):
        heading = section.find(class_="heading")
        region = heading.get_text(strip=True) if heading else "未分類"
        for anchor in section.select("a[href]"):
            href = anchor["href"]
            absolute_url = urljoin(BASE_PAGE, href)
            if "/branch/" not in absolute_url:
                continue
            key = (region, anchor.get_text(strip=True), absolute_url)
            if key in seen:
                continue
            seen.add(key)
            branches.append(
                BranchLink(
                    region=region,
                    name=anchor.get_text(strip=True),
                    url=absolute_url,
                )
            )
    return branches


def parse_branch_tables(html: str) -> List[dict]:
    soup = BeautifulSoup(html, "html.parser")
    notices: List[dict] = []
    for table in soup.select("table.rwdTable.bulletin"):
        caption_text = table.caption.get_text(" ", strip=True) if table.caption else ""
        iso_date, roc_date = parse_caption_date(caption_text)
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) != 2:
                continue
            time_text = cells[0].get_text(" ", strip=True)
            note_text = cells[1].get_text("\n", strip=True)
            if not (time_text or note_text):
                continue
            addresses = extract_address_candidates(note_text)
            cancelled = is_cancelled_notice(caption_text, note_text, time_text)
            notices.append(
                {
                    "caption": caption_text,
                    "date": iso_date,
                    "roc_date": roc_date,
                    "time_window": time_text,
                    "description": note_text,
                    "addresses": addresses,
                    "cancelled": cancelled,
                }
            )
    return notices


def parse_caption_date(text: str) -> tuple[Optional[str], Optional[str]]:
    match = DATE_PATTERN.search(text)
    if not match:
        return None, None
    try:
        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
    except ValueError:
        return None, None
    roc_year = year if year < 1900 else year - ROC_YEAR_OFFSET
    iso_year = year if year >= 1900 else year + ROC_YEAR_OFFSET
    roc_date = f"{roc_year:03d}-{month:02d}-{day:02d}"
    iso_date = f"{iso_year:04d}-{month:02d}-{day:02d}"
    return iso_date, roc_date


__all__ = [
    "BASE_PAGE",
    "BranchLink",
    "MAPBOX_ENDPOINTS",
    "TaipowerCrawler",
    "extract_branches",
    "parse_branch_tables",
    "DEFAULT_MAPBOX_ENDPOINT",
]
