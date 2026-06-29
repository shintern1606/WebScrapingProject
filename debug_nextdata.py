"""
debug_nextdata.py
-----------------
Extract and fully inspect the __NEXT_DATA__ JSON blob from boom-lifts.
This will reveal the exact structure of the category/section/series/model data.
"""
import time
import json
import re
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup

TARGET = "https://www.jlg.com/en/equipment/boom-lifts"

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--window-size=1440,900")
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")
opts.add_argument("--disable-blink-features=AutomationControlled")
opts.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
opts.add_experimental_option("excludeSwitches", ["enable-automation"])
opts.add_experimental_option("useAutomationExtension", False)

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()), options=opts
)
driver.execute_cdp_cmd(
    "Page.addScriptToEvaluateOnNewDocument",
    {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
)
driver.set_page_load_timeout(60)

driver.get(TARGET)
WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
time.sleep(3)

html = driver.page_source
soup = BeautifulSoup(html, "lxml")

next_data_tag = soup.find("script", {"id": "__NEXT_DATA__"})
if not next_data_tag:
    print("ERROR: No __NEXT_DATA__ found!")
    driver.quit()
    exit(1)

data = json.loads(next_data_tag.string)

# Save full JSON for inspection
with open("/tmp/next_data_boom_lifts.json", "w") as f:
    json.dump(data, f, indent=2)
print("Full JSON saved to /tmp/next_data_boom_lifts.json")
print(f"Top-level keys: {list(data.keys())}")

# Drill into props > pageProps
props = data.get("props", {})
print(f"\nprops keys: {list(props.keys())}")

page_props = props.get("pageProps", {})
print(f"\npageProps keys: {list(page_props.keys())}")

# Look for any field containing category/product links
def find_all_urls(obj, depth=0, path=""):
    """Recursively find all strings that look like /en/equipment/ URLs."""
    results = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            results.extend(find_all_urls(v, depth+1, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            results.extend(find_all_urls(v, depth+1, f"{path}[{i}]"))
    elif isinstance(obj, str):
        if "/en/equipment/" in obj and len(obj) < 200:
            segs = [s for s in obj.split("/") if s]
            results.append((len(segs), obj, path))
    return results

print("\nSearching for /en/equipment/ URLs in __NEXT_DATA__...")
urls = find_all_urls(data)
urls.sort()
print(f"Found {len(urls)} equipment URLs:")
for depth, url, path in urls:
    print(f"  depth={depth}  url={url!r:60s}  path={path[:80]}")

driver.quit()
print("\nDone.")
