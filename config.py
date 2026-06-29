"""Central configuration for the JLG equipment scraper."""

from pathlib import Path

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"
LOGS_DIR = BASE_DIR / "logs"
SCREENSHOTS_DIR = LOGS_DIR / "screenshots"

BASE_URL = "https://www.jlg.com/en"
EQUIPMENT_URL = "https://www.jlg.com/en/equipment"
EXCEL_PATH = OUTPUT_DIR / "jlg_equipment.xlsx"
CHECKPOINT_CSV = OUTPUT_DIR / "checkpoint.csv"
LOG_FILE = LOGS_DIR / "scraper.log"

PAGE_TIMEOUT = 60
WAIT_TIMEOUT = 40
JS_POLL_TIMEOUT = 35
CHECKPOINT_EVERY = 25
MAX_RETRIES = 3
DELAY_MIN = 1.5
DELAY_MAX = 3.5

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

SKIP_URL_FRAGMENTS = (
    "/financing",
    "/accessories",
    "/attachments",
    "/parts",
    "/equipment-selector",
    "/content/",
)

# Equipment types surfaced in the mega-menu (Header 1 / Equipment column).
EQUIPMENT_TYPES = {
    "boom-lifts",
    "scissor-lifts",
    "telehandlers",
    "low-level-access",
    "vertical-lifts",
    "stock-pickers",
    "dumpersandforklifts",
    "jlg-used-equipment",
}

# Top-level menu labels that are not product listing roots.
SKIP_MENU_CATEGORIES = frozenset(
    {
        "all equipment",
        "equipment selector",
        "financing",
    }
)
