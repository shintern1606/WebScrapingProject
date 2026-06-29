"""
debug_inspect2.py
-----------------
Deeper diagnostic: dump the raw HTML and check for:
1. __NEXT_DATA__ script tag (server-side rendered props)
2. Any data-cy or product card markers
3. What the page title says (blocked vs real content)
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

print(f"Navigating to {TARGET} ...")
driver.get(TARGET)
WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
time.sleep(5)

html  = driver.page_source
soup  = BeautifulSoup(html, "lxml")

# Check title
title = soup.find("title")
print(f"\nPage title: {title.text if title else 'N/A'}")

# Check for __NEXT_DATA__
next_data_tag = soup.find("script", {"id": "__NEXT_DATA__"})
if next_data_tag:
    print("\nFound __NEXT_DATA__ — parsing for category URLs...")
    try:
        data = json.loads(next_data_tag.string or "{}")
        raw  = json.dumps(data)
        # Find all /en/equipment/ paths in the JSON
        paths = list(set(re.findall(r'/en/equipment/[^"\'\\s]+', raw)))
        paths.sort()
        print(f"Found {len(paths)} /en/equipment/ paths in __NEXT_DATA__:")
        for p in paths[:50]:
            depth = len([s for s in p.split("/") if s])
            print(f"  depth={depth}  {p}")
    except Exception as e:
        print(f"  Could not parse __NEXT_DATA__: {e}")
else:
    print("\n__NEXT_DATA__ NOT found.")
    # Look for any script tags
    scripts = soup.find_all("script")
    print(f"Found {len(scripts)} script tags")
    for s in scripts[:5]:
        if s.string:
            print(f"  Script snippet: {s.string[:200]}")

# Check data-cy attributes (JLG React components)
cy_elements = soup.find_all(attrs={"data-cy": True})
print(f"\nFound {len(cy_elements)} data-cy elements")
for el in cy_elements[:20]:
    cy = el.get("data-cy")
    href = el.get("href", "")
    print(f"  data-cy={cy!r:50s}  href={href}")

# Check for any product card or category markers
card_classes = ["card", "product", "category", "equipment", "sitecore-slider-item"]
for cls in card_classes:
    found = soup.find_all(class_=re.compile(cls, re.I))
    if found:
        print(f"\nElements with class containing '{cls}': {len(found)}")
        for el in found[:3]:
            print(f"  {el.name}  href={el.get('href', '')}  class={el.get('class', '')}")

print("\n--- Page length:", len(html), "bytes ---")
driver.quit()
print("\nDone.")
