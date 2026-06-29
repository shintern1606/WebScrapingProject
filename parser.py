"""
BeautifulSoup and JSON parsing helpers for the JLG site.

Phase 1 reads the Equipment mega-menu hierarchy from __NEXT_DATA__ after
Selenium opens the menu. Phase 2 parses category pages and extracts models
after Selenium clicks each series in the left navigation.
"""

from __future__ import annotations

import json
import re
from typing import Any

from bs4 import BeautifulSoup

from config import EQUIPMENT_TYPES, SKIP_MENU_CATEGORIES, SKIP_URL_FRAGMENTS
from utils import logger, normalise_url, path_depth, slug_to_title


def _should_skip_menu_category(name: str) -> bool:
    """Return True for mega-menu groups that are not product listings."""
    lower = name.lower().strip()
    if lower in SKIP_MENU_CATEGORIES:
        return True
    return "equipment selector" in lower


def _normalise_equipment_name(name: str) -> str:
    """Map menu labels to consistent Equipment column values."""
    if "used" in name.lower():
        return "JLG Used Equipment"
    if "dumper" in name.lower():
        return "Dumpers and Forklifts"
    if name.lower() == "low level access":
        return "Low Level Access"
    return name.strip()


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _is_equipment_listing_url(url: str) -> bool:
    """Return True for equipment listing URLs we should crawl."""
    if "/en/equipment/" not in url:
        return False
    if any(fragment in url for fragment in SKIP_URL_FRAGMENTS):
        return False
    segments = [segment for segment in url.split("/") if segment]
    if "jlg.com" in url:
        segments = [
            segment
            for segment in url.replace("https://www.jlg.com", "").split("/")
            if segment
        ]
    if len(segments) < 3:
        return False
    return segments[2] in EQUIPMENT_TYPES


def _extract_link(node: dict[str, Any]) -> tuple[str, str]:
    """Return (label, href) from a Sitecore navigation node."""
    label = (node.get("displayName") or node.get("name") or "").strip()
    href = ""
    fields = node.get("fields", {})

    for key in ("clLinkURL", "niLinkURL"):
        link_field = fields.get(key, {}).get("value", {})
        if isinstance(link_field, dict):
            href = link_field.get("href") or link_field.get("url") or href

    return label, normalise_url(href) if href else ""


def _child_lists(node: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """Return every child-list field present on a navigation node."""
    fields = node.get("fields", {})
    lists: list[list[dict[str, Any]]] = []

    for key in ("clChildLinkList", "niColumn1List", "niColumn2List"):
        value = fields.get(key)
        if isinstance(value, list) and value:
            lists.append(value)

    return lists


def _find_equipment_nav_root(data: dict[str, Any]) -> dict[str, Any] | None:
    """Locate the Equipment navigation node inside __NEXT_DATA__."""
    header_items = (
        data.get("props", {})
        .get("pageProps", {})
        .get("layoutData", {})
        .get("sitecore", {})
        .get("route", {})
        .get("placeholders", {})
        .get("headless-header", [])
    )

    def matches_equipment(node: dict[str, Any]) -> bool:
        label = (node.get("displayName") or node.get("name") or "").strip().lower()
        return label == "equipment"

    for item in header_items:
        fields = item.get("fields", {})

        for nav_link in fields.get("phLinks", []):
            if matches_equipment(nav_link):
                return nav_link

        for child in fields.get("clChildLinkList", []):
            if matches_equipment(child):
                return child

            for nested in child.get("fields", {}).get("clChildLinkList", []):
                if matches_equipment(nested):
                    return nested

    return None


def parse_equipment_menu(html: str) -> list[dict[str, str]]:
    """
    Parse the Equipment mega-menu hierarchy from page HTML.

    Returns rows shaped like:
        {
            "equipment": "Boom Lifts",
            "header_1": "Telescopic",
            "header_2": "",
            "url": "https://www.jlg.com/en/equipment/boom-lifts/telescopic",
        }
    """
    soup = _soup(html)
    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if not script or not script.string:
        logger.warning("No __NEXT_DATA__ found while parsing equipment menu")
        return _parse_equipment_menu_from_links(soup)

    try:
        data = json.loads(script.string)
    except json.JSONDecodeError as exc:
        logger.warning("Could not decode __NEXT_DATA__: %s", exc)
        return _parse_equipment_menu_from_links(soup)

    equipment_root = _find_equipment_nav_root(data)
    if not equipment_root:
        logger.warning("Equipment navigation node not found in __NEXT_DATA__")
        return _parse_equipment_menu_from_links(soup)

    rows: list[dict[str, str]] = []
    categories: list[dict[str, Any]] = []
    for child_list in _child_lists(equipment_root):
        categories.extend(child_list)

    for category in categories:
        equipment_name = (category.get("displayName") or category.get("name") or "").strip()
        if not equipment_name:
            continue

        if _should_skip_menu_category(equipment_name):
            continue

        equipment_name = _normalise_equipment_name(equipment_name)
        sub_links = category.get("fields", {}).get("clChildLinkList", [])
        if not sub_links:
            _, category_url = _extract_link(category)
            if category_url and _is_equipment_listing_url(category_url):
                rows.append(
                    {
                        "equipment": equipment_name,
                        "header_1": "",
                        "header_2": "",
                        "url": category_url,
                    }
                )
            continue

        for sub in sub_links:
            header_1, sub_url = _extract_link(sub)
            if not header_1:
                header_1 = (sub.get("displayName") or sub.get("name") or "").strip()

            grandchildren = sub.get("fields", {}).get("clChildLinkList", [])
            if grandchildren:
                for child in grandchildren:
                    header_2, child_url = _extract_link(child)
                    if not header_2:
                        header_2 = (child.get("displayName") or child.get("name") or "").strip()
                    if child_url and _is_equipment_listing_url(child_url):
                        rows.append(
                            {
                                "equipment": equipment_name,
                                "header_1": header_1,
                                "header_2": header_2,
                                "url": child_url,
                            }
                        )
            elif sub_url and _is_equipment_listing_url(sub_url):
                rows.append(
                    {
                        "equipment": equipment_name,
                        "header_1": header_1,
                        "header_2": "",
                        "url": sub_url,
                    }
                )

    if rows:
        logger.info("Parsed %d menu entries from __NEXT_DATA__", len(rows))
        return _dedupe_menu_rows(rows)

    return _parse_equipment_menu_from_links(soup)


def _parse_equipment_menu_from_links(soup: BeautifulSoup) -> list[dict[str, str]]:
    """Fallback parser that scans visible menu links in the DOM."""
    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        url = normalise_url(anchor["href"])
        if not _is_equipment_listing_url(url):
            continue
        depth = path_depth(url)
        if depth not in (3, 4, 5):
            continue
        if url in seen:
            continue
        seen.add(url)

        text = anchor.get_text(" ", strip=True)
        if not text or len(text) > 80:
            slug = url.rstrip("/").split("/")[-1]
            text = slug_to_title(slug)

        if depth == 3:
            rows.append({"equipment": text, "header_1": "", "header_2": "", "url": url})
        elif depth == 4:
            equipment_slug = url.split("/")[5]
            rows.append(
                {
                    "equipment": slug_to_title(equipment_slug),
                    "header_1": text,
                    "header_2": "",
                    "url": url,
                }
            )
        else:
            parts = [segment for segment in url.replace("https://www.jlg.com", "").split("/") if segment]
            rows.append(
                {
                    "equipment": slug_to_title(parts[2]),
                    "header_1": slug_to_title(parts[3]),
                    "header_2": slug_to_title(parts[4]) if len(parts) > 4 else "",
                    "url": url,
                }
            )

    return _dedupe_menu_rows(rows)


def discover_child_listing_urls(
    html: str,
    parent_url: str,
    equipment_name: str,
) -> list[dict[str, str]]:
    """
    Discover immediate child listing URLs beneath a shallow category page.

    Used for Vertical Lifts, Stock Pickers, and similar pages whose mega-menu
    entry points directly to a depth-3 landing page with nested listings.
    """
    parent_path = parent_url.replace("https://www.jlg.com", "").rstrip("/")
    parent_depth = path_depth(parent_url)
    parent_slug = parent_path.rstrip("/").split("/")[-1]
    target_depth = parent_depth + 1

    candidates: set[str] = set()
    soup = _soup(html)

    for anchor in soup.find_all("a", href=True):
        candidates.add(normalise_url(anchor["href"]))

    script = soup.find("script", {"id": "__NEXT_DATA__"})
    if script and script.string:
        pattern = rf"/en/equipment/{re.escape(parent_slug)}/[a-z0-9\-/]+"
        for match in re.findall(pattern, script.string, re.I):
            if "/content/" not in match:
                candidates.add(normalise_url(match))

    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    for candidate in sorted(candidates):
        if not _is_equipment_listing_url(candidate):
            continue
        relative = candidate.replace("https://www.jlg.com", "")
        if not relative.startswith(parent_path + "/"):
            continue

        depth = path_depth(candidate)
        if depth < target_depth:
            continue

        if depth > target_depth:
            segments = [segment for segment in relative.strip("/").split("/") if segment]
            listing_segments = segments[:target_depth]
            candidate = "https://www.jlg.com/" + "/".join(listing_segments)

        if candidate in seen:
            continue
        seen.add(candidate)

        header_1 = slug_to_title(candidate.rstrip("/").split("/")[-1])
        rows.append(
            {
                "equipment": equipment_name,
                "header_1": header_1,
                "header_2": "",
                "url": candidate,
            }
        )

    return rows


def _dedupe_menu_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Remove duplicate menu rows while preserving order."""
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for row in rows:
        url = row["url"]
        if url in seen:
            continue
        seen.add(url)
        unique.append(row)
    return unique


def parse_section_series_map(html: str) -> list[tuple[str, str]]:
    """
    Build a list of (section, series) pairs from the left navigation headings.

    Only series-like headings are returned (those containing 'Series' or ending
    with a known series suffix).
    """
    soup = _soup(html)
    pairs: list[tuple[str, str]] = []
    current_section = ""

    for element in soup.find_all(["h5", "h6"]):
        text = element.get_text(strip=True)
        if not text:
            continue
        if element.name == "h5":
            current_section = text
            continue
        if element.name == "h6" and current_section and _looks_like_series(text):
            pairs.append((current_section, text))

    # Preserve order while removing duplicates.
    seen: set[tuple[str, str]] = set()
    unique: list[tuple[str, str]] = []
    for pair in pairs:
        if pair in seen:
            continue
        seen.add(pair)
        unique.append(pair)
    return unique


def _looks_like_series(label: str) -> bool:
    """Heuristic to distinguish series headings from model headings."""
    if "series" in label.lower():
        return True
    if re.search(r"\b(?:and|&)\b", label, re.I):
        return True
    return False


def parse_model_links(html: str, parent_url: str) -> list[dict[str, str]]:
    """
    Extract individual model links from rendered category/series HTML.

    Returns rows with `model_name` and `model_url`.
    """
    soup = _soup(html)
    parent_path = parent_url.replace("https://www.jlg.com", "").rstrip("/")
    min_depth = path_depth(parent_url) + 1
    seen: set[str] = set()
    models: list[dict[str, str]] = []

    for anchor in soup.find_all("a", href=True):
        url = normalise_url(anchor["href"])
        if not url.startswith("https://www.jlg.com/en/equipment/"):
            continue
        if not url.replace("https://www.jlg.com", "").startswith(parent_path + "/"):
            continue
        if path_depth(url) < min_depth:
            continue
        if url in seen:
            continue
        seen.add(url)

        name = anchor.get_text(" ", strip=True)
        if name.lower().startswith("view the "):
            name = name[9:].strip()
        if not name or name.lower() == "skip to content":
            name = url.rstrip("/").split("/")[-1].upper()

        models.append({"model_name": name, "model_url": url})

    return models


def _looks_like_model(label: str) -> bool:
    """Heuristic for model headings that appear directly in the left nav."""
    if "series" in label.lower():
        return False
    if label.lower() in {"articulating", "telescopic", "electric & hybrid", "engine powered"}:
        return False
    return bool(re.match(r"^[A-Z0-9][A-Z0-9\\s\\-]{1,}$", label, re.I))
