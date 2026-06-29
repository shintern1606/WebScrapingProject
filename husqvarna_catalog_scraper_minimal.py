import time
from itertools import zip_longest
from urllib.parse import urljoin
import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

BASE_URL = "https://www.husqvarna.com"
HOMEPAGE_URL = BASE_URL + "/us/"

def start_browser():
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)

def close_popups(driver):
    try:
        WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
        ).click()
    except TimeoutException:
        pass

def get_menu_links(driver):
    driver.get(HOMEPAGE_URL)
    close_popups(driver)
    sidebar = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "[data-depth='2']"))
    )
    name_tags = sidebar.find_elements(By.TAG_NAME, "p")
    category_names = [t.get_attribute("textContent").strip() for t in name_tags]
    category_names = [n for n in category_names if n]
    all_panels = driver.find_elements(By.CSS_SELECTOR, "[data-depth='3']")
    links = []
    for header1_name, panel in zip_longest(category_names, all_panels):
        if header1_name is None or panel is None:
            continue
        soup = BeautifulSoup(panel.get_attribute("innerHTML"), "html.parser")
        for header2_block in soup.select("div[id^='link-group-title_']"):
            header2_name = header2_block.get_text(strip=True)
            link_list = header2_block.find_next("ul")
            if link_list is None:
                continue
            for a_tag in link_list.find_all("a", href=True):
                links.append({
                    "header1": header1_name,
                    "header2": header2_name,
                    "header3": a_tag.get_text(strip=True),
                    "url": urljoin(BASE_URL, a_tag["href"]),
                })
    return links

def click_load_more_until_done(driver):
    previous_count = -1
    for _ in range(20):
        current_count = len(driver.find_elements(By.CSS_SELECTOR, "[data-product-sku]"))
        if current_count == previous_count:
            break
        previous_count = current_count
        try:
            load_more = driver.find_element(By.XPATH, "//button[.//span[text()='Load more']]")
        except NoSuchElementException:
            break
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", load_more)
        time.sleep(0.5)
        try:
            WebDriverWait(driver, 5).until(EC.element_to_be_clickable(
                (By.XPATH, "//button[.//span[text()='Load more']]")
            ))
            load_more.click()
        except Exception:
            driver.execute_script("arguments[0].click();", load_more)
        time.sleep(1.5)

def get_products_on_page(driver, category_url):
    driver.get(category_url)
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "[data-product-sku]"))
        )
    except TimeoutException:
        return []
    click_load_more_until_done(driver)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    results = []
    for card in soup.select("[data-product-sku]"):
        name_tag = card.select_one("h3")
        link_tag = card.select_one("a[href]")
        if name_tag and link_tag:
            results.append((name_tag.get_text(strip=True), urljoin(BASE_URL, link_tag["href"])))
    return results

def save_progress(all_rows, filename):
    df = pd.DataFrame(all_rows)
    if not df.empty:
        df = df.drop_duplicates(subset=["Product URL"])
    try:
        df.to_excel(filename, index=False)
    except PermissionError:
        fallback = filename.replace(".xlsx", "_new.xlsx")
        df.to_excel(fallback, index=False)
    return df

def main():
    driver = start_browser()
    all_rows = []
    try:
        menu_links = get_menu_links(driver)
        for i, link in enumerate(menu_links, start=1):
            try:
                products = get_products_on_page(driver, link["url"])
            except Exception:
                products = []
            for model_name, product_url in products:
                all_rows.append({
                    "Header 1": link["header1"],
                    "Header 2": link["header2"],
                    "Header 3": link["header3"],
                    "Product Model Name": model_name,
                    "Product URL": product_url,
                })
            if i % 10 == 0:
                save_progress(all_rows, "husqvarna_catalog_new_partial.xlsx")
    finally:
        driver.quit()
    save_progress(all_rows, "husqvarna_catalog_new.xlsx")

if __name__ == "__main__":
    main()
