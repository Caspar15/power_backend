from __future__ import annotations

import re
from typing import Iterable, List, Optional

FEEDER_PATTERN = re.compile(r"^[A-Z]+[0-9]{3,}")
SEPARATOR_PATTERN = re.compile(r"[、，,；;]+")
FULLWIDTH_TRANSLATION = str.maketrans(
    {
        ord("０"): "0",
        ord("１"): "1",
        ord("２"): "2",
        ord("３"): "3",
        ord("４"): "4",
        ord("５"): "5",
        ord("６"): "6",
        ord("７"): "7",
        ord("８"): "8",
        ord("９"): "9",
        ord("（"): "(",
        ord("）"): ")",
        ord("－"): "-",
        ord("—"): "-",
        ord("～"): "-",
        ord("〜"): "-",
        ord("至"): "-",
        ord("、"): ",",
        ord("，"): ",",
        ord("；"): ",",
        ord("　"): " ",
        ord("‧"): ",",
        ord("．"): ",",
        ord("・"): ",",
        ord("﹒"): ",",
        ord(""): "-",
    }
)
ADMIN_KEYWORDS = ("縣", "市", "區", "鄉", "鎮", "村", "里")
ADDRESS_EXCLUDE = ("路", "街", "巷", "弄", "段", "道", "號")
DETAIL_MARKERS = ("號", "巷", "弄", "段", "樓")
REGION_CHARS = ("縣", "市", "區")
STREET_TOKENS = ("大道", "路", "街")
STREET_SUFFIX_CHARS = set("一二三四五六七八九十零〇東西南北上下中甲乙丙丁段")
TOKEN_PATTERNS = {
    token: re.compile(r"([\u4e00-\u9fffA-Za-z0-9\-]{1,8}%s)" % re.escape(token))
    for token in STREET_TOKENS
}
STREET_DROP_PREFIXES = ("車行地下道", "地下道", "出口", "入口", "天橋", "隧道")
CITY_AREA_PATTERN = re.compile(r"(?P<city>[\u4e00-\u9fff]{2,3}市)(?P<area>[\u4e00-\u9fff]{1,3}區)")
INTERSECTION_CONNECTORS = ("與", "及")
STOPWORDS = {
    "無",
    "無停電",
    "無停電案件",
    "無停電用戶",
    "無停電用戶資料",
    "無停電案件資料",
    "取消停電",
    "取消工作停電",
}
TRAILING_CHARS = " ，,、。"
TRAILING_SUFFIXES = ("等", "含公設", "含公共設施", "口", "交叉口")
TRAILING_REGEXES = [
    r"(?:右|左)?\d+\s*公尺[^，,。]*$",
    r"(?:右|左|前|後|旁|對面|附近|上方|下方|邊|側)[^，,。]*$",
    r"(?:臨時(?:用電)?|抽水站|加壓站|電房|照明|號誌燈|監視器)[^，,。]*$",
    r"[（(][^）)]*[）)]$",
]
RANGE_PATTERN = re.compile(
    r"^(?P<prefix>.*?)(?P<start>\d+)(?:-|~|～|至)(?P<end>\d+)(?P<suffix>.*)$"
)
CANCEL_KEYWORDS = ("取消停電", "取消工作停電", "取消供電", "取消作業")


def normalize_label(value: Optional[str]) -> str:
    return value.strip() if value else ""


def normalize_text(value: str) -> str:
    normalized = value.translate(FULLWIDTH_TRANSLATION)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def looks_like_area(text: str) -> bool:
    if any(ch.isdigit() for ch in text):
        return False
    if any(ex in text for ex in ADDRESS_EXCLUDE):
        return False
    return any(token in text for token in ADMIN_KEYWORDS)


def has_digits(text: str) -> bool:
    return any(ch.isdigit() for ch in text)


def extract_address_candidates(description: str) -> List[str]:
    lines = [normalize_text(line) for line in description.splitlines()]
    candidates: List[str] = []
    seen = set()
    region_prefix: Optional[str] = None
    for line in lines:
        if not line:
            continue
        if line in STOPWORDS:
            continue
        if FEEDER_PATTERN.match(line):
            continue
        if looks_like_area(line):
            region_prefix = line
            if line not in seen:
                candidates.append(line)
                seen.add(line)
            continue
        parts = [part.strip() for part in SEPARATOR_PATTERN.split(line) if part.strip()]
        if not parts:
            parts = [line]
        local_prefix = derive_local_prefix(parts[0])
        for part in parts:
            composed = part
            if (
                local_prefix
                and not composed.startswith(local_prefix)
                and not contains_region_reference(composed)
            ):
                composed = f"{local_prefix}{composed}"
            if region_prefix and not composed.startswith(region_prefix):
                composed = f"{region_prefix}{composed}"
            segments = split_redundant_region_segments(composed, region_prefix)
            for segment in segments:
                segment = collapse_duplicate_region(segment, region_prefix)
                for variant in split_range_variants(segment):
                    for expanded in expand_intersection_segments(variant):
                        cleaned = clean_address_text(expanded)
                        if not cleaned or cleaned in STOPWORDS:
                            continue
                        if cleaned not in seen:
                            candidates.append(cleaned)
                            seen.add(cleaned)
    return candidates


def derive_local_prefix(text: str) -> str:
    for index, char in enumerate(text):
        if char.isdigit():
            return text[:index]
    return ""


def contains_region_reference(text: str) -> bool:
    return any(char in text for char in REGION_CHARS)


def collapse_duplicate_region(text: str, region_prefix: Optional[str]) -> str:
    if not region_prefix:
        return text
    first = text.find(region_prefix)
    if first == -1:
        return text
    second = text.find(region_prefix, first + len(region_prefix))
    if second == -1:
        return text
    return text[:second] + text[second + len(region_prefix) :]


def split_redundant_region_segments(
    text: str, region_prefix: Optional[str]
) -> List[str]:
    if not region_prefix:
        return [text]
    matches = [m.start() for m in re.finditer(re.escape(region_prefix), text)]
    if len(matches) <= 1 or matches[0] != 0:
        return [text]
    segments = []
    for idx, start in enumerate(matches):
        end = matches[idx + 1] if idx + 1 < len(matches) else len(text)
        segments.append(text[start:end].strip())
    return segments


def split_range_variants(text: str) -> List[str]:
    match = RANGE_PATTERN.match(text)
    if not match:
        return [text]
    try:
        start = int(match.group("start"))
        end = int(match.group("end"))
    except ValueError:
        return [text]
    if end < start:
        start, end = end, start
    if end - start > 200:
        return [text]
    prefix = match.group("prefix")
    suffix = match.group("suffix")
    first = f"{prefix}{start}{suffix}"
    last = f"{prefix}{end}{suffix}"
    return [first.strip(), last.strip()]


def clean_address_text(text: str) -> str:
    cleaned = text.strip(TRAILING_CHARS)
    for pattern in TRAILING_REGEXES:
        cleaned = re.sub(pattern, "", cleaned).strip(TRAILING_CHARS)
    for suffix in TRAILING_SUFFIXES:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip(TRAILING_CHARS)
    return cleaned


def is_cancelled_notice(*texts: str) -> bool:
    for text in texts:
        if not text:
            continue
        for keyword in CANCEL_KEYWORDS:
            if keyword in text:
                return True
    return False


def normalize_for_lookup(value: Optional[str]) -> str:
    return normalize_label(value).casefold()


def address_priority(value: str) -> tuple[int, int]:
    has_number = has_digits(value)
    has_detail = any(marker in value for marker in DETAIL_MARKERS)
    has_street = "路" in value or "街" in value or "巷" in value
    if has_number or has_detail:
        priority = 0
    elif has_street:
        priority = 1
    else:
        priority = 2
    return (priority, len(value))


def derive_street_labels(text: str) -> List[str]:
    labels: List[str] = []
    for segment in expand_intersection_segments(text):
        label = _derive_single_street(segment)
        if label:
            labels.append(label)
    return labels


def _derive_single_street(text: str) -> Optional[str]:
    if not text:
        return None
    area_prefix = extract_area_prefix(text)
    for token in STREET_TOKENS:
        idx = text.find(token)
        if idx == -1:
            continue
        prefix_segment = text[:idx]
        working_segment = (
            prefix_segment[len(area_prefix) :] if area_prefix and prefix_segment.startswith(area_prefix) else prefix_segment
        )
        core_match = re.search(r"[\u4e00-\u9fffA-Za-z0-9\-]{1,10}$", working_segment)
        if not core_match:
            core = ""
        else:
            core = core_match.group()
        for drop in STREET_DROP_PREFIXES:
            if core.startswith(drop):
                core = core[len(drop) :]
                break
        end = idx + len(token)
        suffix = ""
        while end < len(text) and text[end] in STREET_SUFFIX_CHARS:
            suffix += text[end]
            end += 1
        road_core = f"{core}{token}{suffix}"
        for drop in STREET_DROP_PREFIXES:
            pos = road_core.rfind(drop)
            if pos != -1 and pos + len(drop) < len(road_core):
                road_core = road_core[pos + len(drop) :]
                break
        if area_prefix and road_core.startswith(area_prefix):
            road_core = road_core[len(area_prefix) :]
        if not road_core.strip():
            continue
        if area_prefix:
            return f"{area_prefix}{road_core}"
        return road_core
    return None


def expand_intersection_segments(text: str) -> List[str]:
    segment = text
    for connector in INTERSECTION_CONNECTORS:
        if connector in segment:
            parts = [
                part.strip(TRAILING_CHARS)
                for part in segment.split(connector)
                if part.strip(TRAILING_CHARS)
            ]
            if len(parts) >= 2:
                area_prefix = extract_area_prefix(parts[0])
                expanded: List[str] = []
                for part in parts:
                    if area_prefix and not contains_region_reference(part):
                        expanded.append(f"{area_prefix}{part}")
                    else:
                        expanded.append(part)
                return expanded
    return [segment]


def extract_area_prefix(text: str) -> str:
    last_index = -1
    for char in REGION_CHARS:
        idx = text.rfind(char)
        if idx > last_index:
            last_index = idx
    if last_index == -1:
        return ""
    return text[: last_index + 1]


def extract_city_area(text: str) -> tuple[Optional[str], Optional[str]]:
    match = CITY_AREA_PATTERN.search(text)
    if not match:
        return None, None
    return match.group("city"), match.group("area")


def extract_notice_reason(description: str) -> Optional[str]:
    if not description:
        return None
    for line in description.splitlines():
        candidate = line.strip()
        if candidate:
            return candidate
    return None


def normalize_time_window(text: str) -> Optional[str]:
    if not text:
        return None
    normalized = normalize_text(text)
    normalized = normalized.replace("點", "時").replace("点", "時")
    normalized = normalized.replace("﹕", ":").replace("：", ":")
    normalized = re.sub(r"\s*-\s*", " - ", normalized)
    normalized = re.sub(r"\s*至\s*", " - ", normalized)
    normalized = normalized.strip()
    return normalized or None


__all__ = [
    "FULLWIDTH_TRANSLATION",
    "STOPWORDS",
    "extract_address_candidates",
    "is_cancelled_notice",
    "normalize_for_lookup",
    "normalize_label",
    "address_priority",
    "derive_street_labels",
    "extract_city_area",
    "extract_notice_reason",
    "normalize_time_window",
]
