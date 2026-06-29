"""
debug_inspect.py
----------------
Diagnostic script: open jlg.com/en/equipment/boom-lifts with Selenium,
wait for JS hydration, and print all <a href> links found on the page
to understand what depth-4 links look like in the live DOM.
"""
import time
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
opts.add_experimental_option("excludeSwitches", ["enable-automation"])

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()), options=opts
)
driver.set_page_load_timeout(60)

print(f"Navigating to {TARGET} ...")
driver.get(TARGET)
WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
print("DOM ready. Scrolling to trigger JS hydration...")

# Scroll down in steps
for pos in range(0, 5000, 400):
    driver.execute_script(f"window.scrollTo(0, {pos});")
    time.sleep(0.25)
driver.execute_script("window.scrollTo(0, 0);")

# Wait and collect links progressively
for wait_s in [2, 5, 10, 20, 30]:
    time.sleep(2)
    html  = driver.page_source
    soup  = BeautifulSoup(html, "lxml")
    links = [
        (a.get_text(strip=True), a["href"])
        for a in soup.find_all("a", href=True)
        if "/en/equipment/" in a["href"]
    ]
    print(f"\n--- After ~{wait_s}s total wait: {len(links)} /en/equipment/ links ---")
    for name, href in links[:30]:
        segs = [s for s in href.split("/") if s]
        depth = len(segs)
        print(f"  depth={depth}  name={name!r:40s}  href={href}")

# Also dump all link depths as histogram
all_links = driver.find_elements(By.CSS_SELECTOR, "a[href]")
depth_hist = {}
for a in all_links:
    href = a.get_attribute("href") or ""
    if "/en/equipment/" in href:
        segs = [s for s in href.replace("https://www.jlg.com","").split("/") if s]
        d = len(segs)
        depth_hist[d] = depth_hist.get(d, 0) + 1
print("\n--- Depth histogram for /en/equipment/ links ---")
for d, count in sorted(depth_hist.items()):
    print(f"  depth {d}: {count} links")

driver.quit()
print("\nDone.")
