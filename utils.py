"""
Utility helpers: logging, delays, URL helpers, checkpoints, and screenshots.
"""

from __future__ import annotations

import csv
import logging
import random
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from config import (
    CHECKPOINT_CSV,
    DELAY_MAX,
    DELAY_MIN,
    LOG_FILE,
    LOGS_DIR,
    OUTPUT_DIR,
    SCREENSHOTS_DIR,
)

for directory in (OUTPUT_DIR, LOGS_DIR, SCREENSHOTS_DIR):
    directory.mkdir(parents=True, exist_ok=True)

JLG_BASE = "https://www.jlg.com"

_MODEL_RE = re.compile(
    r"^https://www\.jlg\.com/en/equipment(?:/[^/?#]+){3,}$",
    re.IGNORECASE,
)


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure the project logger for file and console output."""
    fmt = "%(asctime)s | %(levelname)-8s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    log = logging.getLogger("jlg_scraper")
    log.setLevel(level)

    if not log.handlers:
        file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(fmt, datefmt))
        log.addHandler(file_handler)

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(logging.Formatter(fmt, datefmt))
        log.addHandler(stream_handler)

    return log


logger = setup_logging()


def random_delay(lo: float = DELAY_MIN, hi: float = DELAY_MAX) -> None:
    """Sleep for a random duration to mimic human browsing."""
    time.sleep(random.uniform(lo, hi))


def normalise_url(href: str) -> str:
    """Expand relative hrefs and strip query strings / trailing slashes."""
    href = href.strip().split("?")[0].split("#")[0].rstrip("/")
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return JLG_BASE + href
    return href


def path_depth(url: str) -> int:
    """Return the number of path segments below the host."""
    path = url.replace(JLG_BASE, "")
    return len([segment for segment in path.split("/") if segment])


def is_descendant_url(child_url: str, parent_url: str) -> bool:
    """Return True when *child_url* is nested beneath *parent_url*."""
    child_path = child_url.replace(JLG_BASE, "").rstrip("/")
    parent_path = parent_url.replace(JLG_BASE, "").rstrip("/")
    return child_path.startswith(parent_path + "/")


def is_valid_model_name(name: str) -> bool:
    """Reject navigation copy mistakenly captured as a model name."""
    if not name or len(name) > 50:
        return False
    if len(name.split()) > 5:
        return False
    lowered = name.lower()
    if any(
        phrase in lowered
        for phrase in ("learn more", "our process", "your value", "why it works", "refresh your")
    ):
        return False
    return True


def is_valid_model_url(url: str, parent_url: str | None = None) -> bool:
    """Return True when *url* looks like an individual product page."""
    if not url:
        return False
    cleaned = url.strip().split("?")[0].split("#")[0].rstrip("/")
    if not _MODEL_RE.match(cleaned):
        return False
    slug = cleaned.rsplit("/", 1)[-1].lower()
    if slug in {"content", "reconditioning", "certified-used-equipment"}:
        return False
    if parent_url and not is_descendant_url(cleaned, parent_url):
        return False
    if parent_url:
        return path_depth(cleaned) > path_depth(parent_url)
    return True


def slug_to_title(slug: str) -> str:
    """Convert a URL slug such as `engine-powered` to title case."""
    return slug.replace("-", " ").title()


def save_error_screenshot(driver: Any, label: str = "error") -> None:
    """Save a screenshot when Selenium interactions fail."""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_label = re.sub(r"[^a-zA-Z0-9_-]", "_", label)[:60]
        path = SCREENSHOTS_DIR / f"{timestamp}_{safe_label}.png"
        driver.save_screenshot(str(path))
        logger.info("Screenshot saved: %s", path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not save screenshot: %s", exc)


def log_hierarchy(prefix: str, levels: list[str]) -> None:
    """Print a breadcrumb-style progress message."""
    if not levels:
        return
    lines = [prefix, *levels]
    logger.info("\n".join(lines))


CHECKPOINT_BASE_FIELDS = ["equipment_model", "product_url"]


def load_checkpoint() -> tuple[list[dict], set[str]]:
    """Load previously scraped rows and visited product URLs."""
    rows: list[dict] = []
    visited: set[str] = set()

    if not CHECKPOINT_CSV.exists():
        return rows, visited

    with open(CHECKPOINT_CSV, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows.append(row)
            visited.add(row.get("product_url", ""))

    logger.info("Resumed from checkpoint: %d rows loaded", len(rows))
    return rows, visited


def append_to_checkpoint(new_rows: list[dict], fieldnames: list[str]) -> None:
    """Append rows to the checkpoint CSV, creating headers when needed."""
    write_header = not CHECKPOINT_CSV.exists()
    with open(CHECKPOINT_CSV, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(new_rows)


class Timer:
    """Simple wall-clock timer."""

    def __init__(self) -> None:
        self._start = time.monotonic()

    def elapsed(self) -> str:
        seconds = int(time.monotonic() - self._start)
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
