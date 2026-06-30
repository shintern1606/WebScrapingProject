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

    def extract_breadcrumbs(self) -> list[str]:
        """Extract the official hierarchy from the page breadcrumbs."""
        try:
            # Common breadcrumb selectors for Kawasaki en-us
            selectors = [
                "nav.breadcrumb ol li",
                "div.breadcrumb-container a",
                "ol[class*='breadcrumb' i] li",
                ".breadcrumb li",
            ]
            
            for sel in selectors:
                elements = self.driver.find_elements(By.CSS_SELECTOR, sel)
                if elements:
                    crumbs = [el.text.strip() for el in elements if el.text.strip()]
                    # Filter out 'Home' or 'en-us' if they appear
                    filtered = [c for c in crumbs if c.lower() not in ["home", "products", "en-us"]]
                    if filtered:
                        return filtered
        except:
            pass
        return []

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
                    # Use Breadcrumbs for the most accurate hierarchy
                    breadcrumbs = self.extract_breadcrumbs()
                    
                    # If breadcrumbs found, they are the source of truth for the path
                    current_path = breadcrumbs if breadcrumbs else path
                    model_name = current_path[-1] if current_path else url.split("/")[-1].replace("-", " ").title()
                    
                    if not any(p["model_url"] == url for p in self.products):
                        self.products.append({
                            "path": current_path,
                            "model_name": model_name,
                            "model_url": url
                        })
                        log.info(f"  [PRODUCT FOUND] {' > '.join(current_path)}")

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
    df_raw = pd.DataFrame(products)
    df_raw = df_raw.drop_duplicates(subset=["model_url"])

    # Determine max category depth (excluding model name)
    max_header_count = 0
    for p in products:
        # Subtract 1 if the model is the last item in path
        max_header_count = max(max_header_count, len(p["path"]) - 1)
    
    # Use at least 1 header if depth is small
    max_header_count = max(max_header_count, 1)

    rows = []
    for _, p in df_raw.iterrows():
        path = p["path"]
        model_name = p["model_name"]
        model_url = p["model_url"]
        
        # Extract category levels (all but the last item)
        categories = path[:-1] if len(path) > 1 else []
        
        # Pad with empty strings to match max depth
        padded_headers = categories + [""] * (max_header_count - len(categories))
        
        # Final row: [Header 1, Header 2, ..., Model Name, Model URL]
        rows.append(padded_headers + [model_name, model_url])

    # Dynamic column names: Header 1, Header 2, ..., Model Name, Model URL
    col_names = [f"Header {i+1}" for i in range(max_header_count)] + ["Product", "URL"]
    
    df = pd.DataFrame(rows, columns=col_names)
    df.to_excel(OUTPUT_EXCEL, index=False)
    log.info(f"Successfully exported {len(df)} products to {OUTPUT_EXCEL}")

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    scraper = KawasakiScraper()
    results = scraper.run()
    export_to_excel(results)