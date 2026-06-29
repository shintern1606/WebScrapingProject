"""
Core scraping engine implementing the two-phase JLG equipment workflow.

Phase 1 — Equipment mega-menu traversal
    Hover the Equipment menu and map Header 1 / Header 2 / Header 3 links.

Phase 2 — Category endpoint scraping
    Visit each listing URL, walk the left navigation (sections → series),
    and extract every equipment model with its direct product URL.
"""

from __future__ import annotations

import time
from typing import Any

from selenium import webdriver

import browser
import parser
from config import BASE_URL, CHECKPOINT_EVERY, EQUIPMENT_URL, MAX_RETRIES
from utils import (
    append_to_checkpoint,
    is_valid_model_name,
    is_valid_model_url,
    load_checkpoint,
    log_hierarchy,
    logger,
    path_depth,
    random_delay,
)


def _build_row(
    menu_entry: dict[str, str],
    section: str,
    series: str,
    model_name: str,
    model_url: str,
) -> dict[str, str]:
    """Merge Phase 1 and Phase 2 hierarchy levels into a flat row."""
    headers: list[str] = [
        menu_entry.get("header_1", "").strip(),
        menu_entry.get("header_2", "").strip(),
        section.strip(),
        series.strip(),
    ]
    headers = [value for value in headers if value]

    row: dict[str, str] = {
        "equipment": menu_entry.get("equipment", "").strip(),
        "equipment_model": model_name.strip(),
        "product_url": model_url.strip(),
    }
    for index, value in enumerate(headers, start=1):
        row[f"header_{index}"] = value
    return row


def _header_fieldnames(rows: list[dict[str, str]]) -> list[str]:
    """Return sorted dynamic header column names present in *rows*."""
    names: set[str] = set()
    for row in rows:
        for key in row:
            if key.startswith("header_"):
                names.add(key)

    def sort_key(name: str) -> int:
        suffix = name.split("_", 1)[1]
        return int(suffix) if suffix.isdigit() else 999

    return sorted(names, key=sort_key)


def _checkpoint_fieldnames(rows: list[dict[str, str]]) -> list[str]:
    return ["equipment", *_header_fieldnames(rows), "equipment_model", "product_url"]


def phase1_map_equipment_menu(driver: webdriver.Chrome) -> list[dict[str, str]]:
    """
    Phase 1: open the homepage, hover Equipment, and map the mega-menu.

    The menu hierarchy is read from the rendered page (including __NEXT_DATA__)
    after each top-level category is hovered to satisfy dynamic menu traversal.
    """
    logger.info("Phase 1: mapping Equipment mega-menu from %s", BASE_URL)
    html = browser.goto_page(driver, BASE_URL)
    if not html:
        logger.error("Could not load homepage — aborting Phase 1")
        return []

    browser.open_equipment_menu(driver)
    menu_rows = parser.parse_equipment_menu(driver.page_source)

    if menu_rows:
        equipment_names = sorted({row["equipment"] for row in menu_rows if row["equipment"]})
        for equipment_name in equipment_names:
            try:
                browser.hover_menu_category(driver, equipment_name)
                hovered_rows = parser.parse_equipment_menu(driver.page_source)
                known_urls = {row["url"] for row in menu_rows}
                for row in hovered_rows:
                    if row["url"] not in known_urls:
                        menu_rows.append(row)
                        known_urls.add(row["url"])
            except Exception as exc:  # noqa: BLE001
                logger.debug("Hover failed for %s: %s", equipment_name, exc)

    menu_rows = _dedupe_menu_rows(menu_rows)

    if not menu_rows:
        logger.info("Menu empty after hover — falling back to %s", EQUIPMENT_URL)
        html = browser.goto_page(driver, EQUIPMENT_URL)
        if html:
            menu_rows = parser.parse_equipment_menu(html)

    for row in menu_rows:
        logger.info(
            "  Menu: %s → %s → %s (%s)",
            row.get("equipment") or "-",
            row.get("header_1") or "-",
            row.get("header_2") or "-",
            row["url"],
        )

    logger.info("Phase 1 complete: discovered %d listing URLs", len(menu_rows))
    return menu_rows


def expand_shallow_menu_entries(
    driver: webdriver.Chrome,
    menu_rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    """
    Replace shallow depth-3 landing pages with their discovered sub-listings.

    Vertical Lifts, Stock Pickers, and similar categories often expose models
    only under nested listing URLs rather than on the top-level landing page.
    """
    expanded: list[dict[str, str]] = []
    seen_urls: set[str] = set()

    for entry in menu_rows:
        url = entry["url"]
        needs_expansion = path_depth(url) <= 4 and not entry.get("header_1")

        if needs_expansion:
            html = browser.load_category_page(driver, url)
            if html:
                children = parser.discover_child_listing_urls(
                    html, url, entry["equipment"]
                )
                if children:
                    logger.info(
                        "Expanded %s into %d sub-listings",
                        entry["equipment"],
                        len(children),
                    )
                    for child in children:
                        if child["url"] in seen_urls:
                            continue
                        seen_urls.add(child["url"])
                        expanded.append(child)
                    continue

        if url in seen_urls:
            continue
        seen_urls.add(url)
        expanded.append(entry)

    return expanded


def phase2_scrape_category(
    driver: webdriver.Chrome,
    menu_entry: dict[str, str],
) -> list[dict[str, str]]:
    """
    Phase 2: scrape all models beneath a single category endpoint.

    The left navigation is traversed by clicking each series heading and
    reading fully rendered model cards from the live DOM.
    """
    url = menu_entry["url"]
    html = browser.load_category_page(driver, url)
    if not html:
        logger.warning("Could not load category page: %s", url)
        return []

    section_series = parser.parse_section_series_map(html)
    rows: list[dict[str, str]] = []
    seen_models: set[str] = set()

    def collect_models(section: str, series: str) -> None:
        models = browser.extract_models_via_js(driver, url)
        if not models:
            models = parser.parse_model_links(driver.page_source, url)

        logger.info("Found %d models...", len(models))

        for model in models:
            model_url = model["model_url"]
            model_name = model["model_name"]
            if not is_valid_model_name(model_name):
                continue
            if not is_valid_model_url(model_url, parent_url=url):
                continue
            if model_url in seen_models:
                continue
            seen_models.add(model_url)

            logger.info("Currently scraping model: %s", model_name)
            rows.append(
                _build_row(menu_entry, section, series, model_name, model_url)
            )

    if section_series:
        for section, series in section_series:
            hierarchy = [
                menu_entry.get("equipment", ""),
                menu_entry.get("header_1", ""),
                menu_entry.get("header_2", ""),
                section,
                series,
            ]
            hierarchy = [level for level in hierarchy if level]
            log_hierarchy("Currently scraping:", hierarchy)

            if series:
                clicked = browser.click_series_heading(driver, series)
                if clicked:
                    browser.scroll_page(driver, pause=0.2)
                    browser.click_load_more_buttons(driver)
                    time.sleep(1.5)
                else:
                    logger.debug("Could not click series heading %r on %s", series, url)

            collect_models(section, series)
    else:
        log_hierarchy(
            "Currently scraping:",
            [
                menu_entry.get("equipment", ""),
                menu_entry.get("header_1", ""),
                menu_entry.get("header_2", ""),
            ],
        )

        # Flat category pages (scissor lifts, telehandlers, etc.).
        collect_models("", "")

        # Some pages expose one model card at a time via h6 nav clicks.
        for heading in browser.list_series_headings(driver):
            if parser._looks_like_series(heading):
                browser.click_series_heading(driver, heading)
                browser.scroll_page(driver, pause=0.2)
                browser.click_load_more_buttons(driver)
                time.sleep(1.5)
                collect_models("", heading)
                continue

            browser.click_series_heading(driver, heading)
            browser.scroll_page(driver, pause=0.2)
            time.sleep(1.0)
            collect_models("", "")

        # Tabbed layouts (Dumpers and Forklifts).
        tab_labels = browser.list_tab_buttons(driver)
        if len(tab_labels) >= 2:
            for tab in tab_labels:
                if browser.click_tab_button(driver, tab):
                    browser.scroll_page(driver, pause=0.2)
                    browser.click_load_more_buttons(driver)
                    time.sleep(1.5)
                    collect_models("", tab)

    return rows


def _dedupe_menu_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for row in rows:
        url = row["url"]
        if url in seen:
            continue
        seen.add(url)
        unique.append(row)
    return unique


def run_scraper(
    headless: bool = True,
    max_pages: int | None = None,
    resume: bool = False,
) -> list[dict]:
    """
    Execute the full two-phase scrape and return all model rows.

    Parameters
    ----------
    headless:
        Run Chrome without a visible window.
    max_pages:
        Optional cap on category pages visited (useful for smoke tests).
    resume:
        Load existing checkpoint rows and skip already-seen model URLs.
    """
    start_time = time.monotonic()
    all_rows: list[dict] = []
    visited_model_urls: set[str] = set()
    visited_category_urls: set[str] = set()
    pending_checkpoint: list[dict] = []
    pages_visited = 0
    models_saved = 0

    if resume:
        all_rows, visited_model_urls = load_checkpoint()

    driver = browser.build_driver(headless=headless)

    try:
        menu_rows = phase1_map_equipment_menu(driver)
        if not menu_rows:
            logger.error("No equipment menu entries discovered — aborting.")
            return all_rows

        listing_urls = expand_shallow_menu_entries(driver, _dedupe_menu_rows(menu_rows))
        logger.info("Phase 1 expanded to %d listing URLs", len(listing_urls))

        for menu_entry in listing_urls:
            if max_pages is not None and pages_visited >= max_pages:
                logger.info("Reached max_pages=%s — stopping.", max_pages)
                break

            category_url = menu_entry["url"]
            if category_url in visited_category_urls:
                continue
            visited_category_urls.add(category_url)

            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    new_rows = phase2_scrape_category(driver, menu_entry)
                    pages_visited += 1
                    break
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Category scrape failed (%d/%d) for %s: %s",
                        attempt,
                        MAX_RETRIES,
                        category_url,
                        exc,
                    )
                    if attempt == MAX_RETRIES:
                        new_rows = []
                    else:
                        random_delay()
            else:
                new_rows = []

            for row in new_rows:
                product_url = row["product_url"]
                if product_url in visited_model_urls:
                    continue
                visited_model_urls.add(product_url)
                all_rows.append(row)
                pending_checkpoint.append(row)
                models_saved += 1

                if len(pending_checkpoint) >= CHECKPOINT_EVERY:
                    fieldnames = _checkpoint_fieldnames(all_rows)
                    append_to_checkpoint(pending_checkpoint, fieldnames)
                    pending_checkpoint = []
                    logger.info("Saved %d models...", models_saved)

            random_delay()

        if pending_checkpoint:
            fieldnames = _checkpoint_fieldnames(all_rows)
            append_to_checkpoint(pending_checkpoint, fieldnames)
            logger.info("Saved %d models...", models_saved)

    except KeyboardInterrupt:
        logger.warning("Interrupted by user — flushing checkpoint…")
        if pending_checkpoint:
            fieldnames = _checkpoint_fieldnames(all_rows)
            append_to_checkpoint(pending_checkpoint, fieldnames)

    finally:
        try:
            driver.quit()
            logger.info("Chrome session closed")
        except Exception:
            pass

    elapsed = int(time.monotonic() - start_time)
    hours, remainder = divmod(elapsed, 3600)
    minutes, seconds = divmod(remainder, 60)

    print("\n" + "=" * 60)
    print("SCRAPE COMPLETE")
    print("=" * 60)
    print(f"  Listing URLs visited : {pages_visited}")
    print(f"  Total models scraped : {models_saved}")
    print(f"  Time taken           : {hours:02d}:{minutes:02d}:{seconds:02d}")
    print("=" * 60)

    return all_rows
