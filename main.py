"""
Entry point for the JLG Equipment Hierarchy Scraper.

Usage
-----
    python main.py
    python main.py --headless false
    python main.py --max-pages 5
    python main.py --resume
"""

from __future__ import annotations

import argparse
import sys

from config import CHECKPOINT_CSV
from exporter import save_excel
from scraper import run_scraper
from utils import Timer, logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="JLG Equipment Hierarchy Scraper",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--headless",
        type=lambda value: value.lower() not in ("false", "0", "no"),
        default=True,
        metavar="BOOL",
        help="Run browser in headless mode (true/false)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        metavar="N",
        help="Stop after visiting N category listing pages",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=False,
        help=f"Resume from checkpoint CSV at {CHECKPOINT_CSV}",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    timer = Timer()

    print("=" * 60)
    print("  JLG Equipment Hierarchy Scraper")
    print("=" * 60)
    print(f"  headless  : {args.headless}")
    print(f"  max_pages : {args.max_pages or 'unlimited'}")
    print(f"  resume    : {args.resume}")
    print("=" * 60)

    rows: list[dict] = []
    try:
        rows = run_scraper(
            headless=args.headless,
            max_pages=args.max_pages,
            resume=args.resume,
        )
    except KeyboardInterrupt:
        logger.warning("Interrupted by user — saving collected rows…")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unhandled exception: %s", exc)
        sys.exit(1)
    finally:
        try:
            save_excel(rows)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to save Excel: %s", exc)

    print(f"\nDone in {timer.elapsed()}")


if __name__ == "__main__":
    main()
