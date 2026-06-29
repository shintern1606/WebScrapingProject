"""
Kawasaki Product Catalog Scraper (Production Grade)
===================================================

Recursively crawls the Kawasaki US catalog and exports every product
to Excel with its full hierarchy.

FIXES APPLIED:
1. Improved Leaf Detection: Uses URL depth and page content rather than "no links".
2. Path Sanitization: Filters out "Learn More", "View Models" from category names.
3. Automatic Driver: Added webdriver-manager for seamless setup.
4. In-scope Filtering: Stricter URL segment matching to avoid side-crawling.
"""

import json
import logging
import os
import time
import re
from urllib.parse import urljoin, urlparse, urlunparse

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    StaleElementReferenceException,
)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ============================================================================
# CONFIGURATION
# ============================================================================

BASE_DOMAIN = "www.kawasaki.com"
START_URL = "https://www.kawasaki.com/en-us/"

# Only follow links whose path starts with one of these.
ALLOWED_PATH_PREFIXES = [
    "/en-us/motorcycle",
    "/en-us/atv",
    "/en-us/side-x-side",
    "/en-us/watercraft", # Corrected from jet-ski to match real URL
    "/en-us/electrification",
]

# Skip these keywords to avoid noise
EXCLUDED_PATH_KEYWORDS = [
    "about", "dealer", "financing", "blog", "news", "careers", "support",
    "legal", "privacy", "terms", "account", "cart", "search", "sitemap",
    "compare", "build-and-price", "warranty", "recall", "press", "sweepstakes",
    "apparel", "accessor", "parts", "brochure", "promotion", "gallery",
    "offers", "specifications", "features", "maintenance",
]

# Generic buttons to skip when building hierarchy names
NOISY_ANCHOR_TEXT = {
    "view models", "learn more", "see details", "view specs", "details",
    "explore", "view gallery", "shop now", "view all", "legendary performance",
}

HEADLESS = False  # Set to True for production
PAGE_LOAD_TIMEOUT = 30
REQUEST_DELAY = 1.5
MAX_RETRIES = 3
CHECKPOINT_FILE = "kawasaki_crawl_checkpoint.json"
OUTPUT_EXCEL = "kawasaki_catalog.xlsx"
LOG_FILE = "kawasaki_crawl.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ============================================================================
# UTILITIES
# ============================================================================

def build_driver() -> webdriver.Chrome:
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)
    return driver

def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    # lowercase the path and strip trailing slash
    path = parsed.path.lower().rstrip("/") or "/"
    # Rebuild without query/fragment
    return urlunparse(parsed._replace(path=path, query="", fragment=""))

def get_url_depth(url: str) -> int:
    path = urlparse(url).path.strip("/")
    return len(path.split("/")) if path else 0

def is_allowed(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc and parsed.netloc != BASE_DOMAIN:
        return False
    path = parsed.path.lower()
    if not any(path.startswith(prefix) for prefix in ALLOWED_PATH_PREFIXES):
        return False
    if any(kw in path for kw in EXCLUDED_PATH_KEYWORDS):
        return False
    return True

# ============================================================================
# CRAWLER ENGINE
# ============================================================================

class KawasakiScraper:
    def __init__(self):
        self.state = self.load_checkpoint()
        self.visited = set(self.state["visited"])
        self.stack = self.state["stack"]  
        self.products = self.state["products"]
        self.driver = None

    def load_checkpoint(self):
        if os.path.exists(CHECKPOINT_FILE):
            try:
                with open(CHECKPOINT_FILE, "r") as f:
                    data = json.load(f)
                    # Support old checkpoint format if needed
                    if "visited" not in data: data["visited"] = []
                    if "stack" not in data: data["stack"] = [[START_URL, []]]
                    if "products" not in data: data["products"] = []
                    return data
            except Exception:
                pass
        return {"visited": [], "stack": [[START_URL, []]], "products": []}

    def save_checkpoint(self):
        with open(CHECKPOINT_FILE, "w") as f:
            json.dump({
                "visited": list(self.visited),
                "stack": self.stack,
                "products": self.products
            }, f, indent=2)

    def wait_for_ready(self):
        try:
            WebDriverWait(self.driver, 15).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
        except:
            pass

    def is_product_page(self, url: str) -> bool:
        """
        Detects if a page is a specific product (Model).
        Kawasaki leaf nodes are usually depth 5 or 6 and have a 'MSRP'.
        """
        depth = get_url_depth(url)
        if depth < 5:
            return False
            
        try:
            source = self.driver.page_source.lower()
            # If it's a 'Build & Price' page, it's a sub-page of a product, skip recording as a separate row
            if "build-your-kawasaki" in url:
                return False
            
            # Key indicators of a product leaf node
            if "msrp" in source or "specs" in source or "specifications" in source:
                return True
        except:
            pass
        return False

    def extract_links(self, current_url: str):
        links = []
        try:
            anchors = self.driver.find_elements(By.TAG_NAME, "a")
        except:
            return []

        current_depth = get_url_depth(current_url)
        current_path_prefix = urlparse(current_url).path.lower().rstrip("/")

        for a in anchors:
            try:
                href = a.get_attribute("href")
                if not href: continue
                
                full_url = normalize_url(urljoin(current_url, href))
                if not is_allowed(full_url) or full_url in self.visited:
                    continue
                
                child_path = urlparse(full_url).path.lower()
                
                # STRICT HIERARCHY RULE:
                # 1. Child must be deeper than parent (more segments)
                # 2. Child URL must start with Parent URL (nesting)
                # Exception: Home page (depth 2 for /en-us/)
                if current_depth >= 2:
                    if not child_path.startswith(current_path_prefix):
                        continue
                    if get_url_depth(full_url) <= current_depth:
                        continue

                text = (a.text or a.get_attribute("aria-label") or "").strip()
                if not text or text.lower() in NOISY_ANCHOR_TEXT:
                    text = full_url.rstrip("/").split("/")[-1].replace("-", " ").title()
                
                links.append((full_url, text))
            except (StaleElementReferenceException, Exception):
                continue
        
        deduped = {}
        for url, text in links:
            if url not in deduped:
                deduped[url] = text
        return list(deduped.items())

    def run(self):
        log.info("Initializing browser...")
        self.driver = build_driver()
        
        try:
            while self.stack:
                url, path = self.stack.pop()
                if url in self.visited: continue
                
                log.info(f"Visiting: {url} | Path Depth: {len(path)} | Products: {len(self.products)}")
                
                success = False
                for attempt in range(MAX_RETRIES):
                    try:
                        self.driver.get(url)
                        self.wait_for_ready()
                        # Test if driver is still alive
                        _ = self.driver.current_url
                        success = True
                        break
                    except Exception as e:
                        log.warning(f"Connection lost or error on {url}. Attempt {attempt+1}. Error: {e}")
                        try: self.driver.quit()
                        except: pass
                        time.sleep(5)
                        self.driver = build_driver() # Restart driver
                
                if not success:
                    log.error(f"Abandoning {url} after retries.")
                    self.visited.add(url)
                    continue

                self.visited.add(url)
                time.sleep(REQUEST_DELAY)

                # Check for product
                if self.is_product_page(url):
                    # Clean up model name
                    model_name = path[-1] if path else url.split("/")[-1].replace("-", " ").title()
                    # Final check: Don't add if URL already in products
                    if not any(p["model_url"] == url for p in self.products):
                        self.products.append({
                            "path": path,
                            "model_name": model_name,
                            "model_url": url
                        })
                        log.info(f"  [PRODUCT FOUND] {model_name}")

                # Find sub-links (Only deeper ones)
                children = self.extract_links(url)
                # Sort children to maintain some order
                children.sort(key=lambda x: x[0])
                for child_url, child_text in reversed(children):
                    if child_url not in self.visited:
                        new_path = path + [child_text]
                        self.stack.append([child_url, new_path])

                # Checkpoint more frequently (every 5 pages)
                if len(self.visited) % 5 == 0:
                    self.save_checkpoint()
                    log.info("  [CHECKPOINT] Progress saved.")

        except Exception as global_e:
            log.error(f"UNEXPECTED FATAL ERROR: {global_e}")
            self.save_checkpoint()
        finally:
            self.save_checkpoint()
            if self.driver:
                try: self.driver.quit()
                except: pass
            log.info("Crawl session finished or interrupted.")
        
        return self.products

# ============================================================================
# EXPORT
# ============================================================================

def export_to_excel(products):
    if not products:
        log.warning("No product data to export.")
        return

    log.info("Processing data for Excel...")
    # Deduplicate products by URL
    df_raw = pd.DataFrame(products)
    df_raw = df_raw.drop_duplicates(subset=["model_url"])

    # Determine max category depth
    max_depth = 0
    for p in products:
        max_depth = max(max_depth, len(p["path"]))

    rows = []
    for _, p in df_raw.iterrows():
        path = p["path"]
        model_name = p["model_name"]
        model_url = p["model_url"]
        
        # Padded category list
        # If path is [Motorcycle, Ninja, Sport, Ninja 650]
        # Categories are [Motorcycle, Ninja, Sport]
        categories = path[:-1] if len(path) > 1 else path
        padded_path = categories + [""] * (max_depth - 1 - len(categories))
        
        rows.append(padded_path + [model_name, model_url])

    # Dynamic column names
    col_names = ["Category"] + [f"Sub-Category {i}" for i in range(1, max_depth - 1)] + ["Model Name", "Model URL"]
    
    # Handle edge case where max_depth is 1
    if max_depth <= 1:
        col_names = ["Model Name", "Model URL"]
        rows = [[p["model_name"], p["model_url"]] for _, p in df_raw.iterrows()]

    df = pd.DataFrame(rows, columns=col_names[:len(rows[0])])
    df.to_excel(OUTPUT_EXCEL, index=False)
    log.info(f"Successfully exported {len(df)} products to {OUTPUT_EXCEL}")

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    scraper = KawasakiScraper()
    results = scraper.run()
    export_to_excel(results)