"""
Selenium browser helpers: driver setup, navigation, scrolling, and overlays.
"""

from __future__ import annotations

import time
from typing import Any

from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import (
    ElementNotInteractableException,
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)

from webdriver_manager.chrome import ChromeDriverManager

from config import (
    BASE_URL,
    JS_POLL_TIMEOUT,
    MAX_RETRIES,
    PAGE_TIMEOUT,
    USER_AGENT,
    WAIT_TIMEOUT,
)
from utils import logger, random_delay, save_error_screenshot


def build_driver(headless: bool = True) -> webdriver.Chrome:
    """Create a Chrome WebDriver configured for anti-bot resilience."""
    options = ChromeOptions()
    if headless:
        options.add_argument("--headless=new")

    options.add_argument("--start-maximized")
    options.add_argument("--window-size=1440,900")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-popup-blocking")
    options.add_argument(f"--user-agent={USER_AGENT}")
    options.add_argument("--lang=en-US,en;q=0.9")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    service = ChromeService(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
    )
    driver.set_page_load_timeout(PAGE_TIMEOUT)
    driver.implicitly_wait(0)
    logger.info("Chrome launched (headless=%s)", headless)
    return driver


def dismiss_overlays(driver: webdriver.Chrome) -> None:
    """Dismiss cookie banners, region selectors, and modal pop-ups."""
    selectors = [
        "#onetrust-accept-btn-handler",
        "[data-cy='cookie-accept-btn']",
        "button[id*='accept']",
        "button[id*='cookie']",
        "button[class*='accept']",
        "button[aria-label*='Accept']",
        "button[aria-label*='Close']",
        "[data-cy='close-button']",
        ".modal-close",
    ]
    for selector in selectors:
        try:
            button = driver.find_element(By.CSS_SELECTOR, selector)
            if button.is_displayed():
                button.click()
                logger.debug("Dismissed overlay via %s", selector)
                time.sleep(0.4)
        except (NoSuchElementException, ElementNotInteractableException):
            pass
        except Exception:
            pass


def goto_page(driver: webdriver.Chrome, url: str) -> str | None:
    """Navigate to *url* with retries and return rendered HTML."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            driver.get(url)
            WebDriverWait(driver, WAIT_TIMEOUT).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            dismiss_overlays(driver)
            random_delay()
            return driver.page_source
        except TimeoutException as exc:
            logger.warning("Timeout (%d/%d) loading %s: %s", attempt, MAX_RETRIES, url, exc)
            save_error_screenshot(driver, label=f"timeout_{attempt}")
        except WebDriverException as exc:
            logger.warning("WebDriver error (%d/%d) on %s: %s", attempt, MAX_RETRIES, url, exc)
            save_error_screenshot(driver, label=f"webdriver_{attempt}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Error (%d/%d) on %s: %s", attempt, MAX_RETRIES, url, exc)
            save_error_screenshot(driver, label=f"error_{attempt}")
        time.sleep(2 * attempt)

    logger.error("Giving up on URL after %d attempts: %s", MAX_RETRIES, url)
    return None


def scroll_page(driver: webdriver.Chrome, pause: float = 0.25) -> None:
    """Scroll through the page to trigger lazy-loaded React content."""
    try:
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.2)
        step = 600
        position = 0
        total_height = int(driver.execute_script("return document.body.scrollHeight") or 3000)
        while position < total_height:
            driver.execute_script(f"window.scrollTo(0, {position});")
            time.sleep(pause)
            position += step
            total_height = int(driver.execute_script("return document.body.scrollHeight") or total_height)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.3)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Scroll failed (non-critical): %s", exc)


def wait_for_links(driver: webdriver.Chrome, href_fragment: str, timeout: int = JS_POLL_TIMEOUT) -> bool:
    """Poll the live DOM until links containing *href_fragment* appear."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        count = driver.execute_script(
            """
            const fragment = arguments[0];
            let total = 0;
            document.querySelectorAll('a[href]').forEach((anchor) => {
                if (anchor.href && anchor.href.includes(fragment)) total += 1;
            });
            return total;
            """,
            href_fragment,
        )
        if count and int(count) > 0:
            return True
        time.sleep(0.8)
    return False


def load_category_page(driver: webdriver.Chrome, url: str) -> str | None:
    """Load a category endpoint, scroll, and wait for product cards."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            driver.get(url)
            WebDriverWait(driver, WAIT_TIMEOUT).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            dismiss_overlays(driver)
            scroll_page(driver)
            slug = url.rstrip("/").split("/")[-1]
            wait_for_links(driver, f"/{slug}/", timeout=20)
            click_load_more_buttons(driver)
            scroll_page(driver, pause=0.35)
            time.sleep(2)
            random_delay(0.5, 1.5)
            return driver.page_source
        except WebDriverException as exc:
            logger.warning("Category load failed (%d/%d) %s: %s", attempt, MAX_RETRIES, url, exc)
            save_error_screenshot(driver, label=f"category_{attempt}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Category load error (%d/%d) %s: %s", attempt, MAX_RETRIES, url, exc)
            save_error_screenshot(driver, label=f"category_err_{attempt}")
        time.sleep(2 * attempt)
    return None


def click_load_more_buttons(driver: webdriver.Chrome, max_clicks: int = 8) -> None:
    """Click any visible Load More buttons to reveal hidden cards."""
    for _ in range(max_clicks):
        clicked = driver.execute_script(
            """
            const candidates = Array.from(document.querySelectorAll('button, a'));
            for (const node of candidates) {
                const text = (node.innerText || '').trim().toLowerCase();
                if (!text.includes('load more') && !text.includes('show more')) continue;
                if (!node.offsetParent) continue;
                node.click();
                return true;
            }
            return false;
            """
        )
        if not clicked:
            break
        time.sleep(1.5)
        scroll_page(driver, pause=0.2)


def click_series_heading(driver: webdriver.Chrome, series_name: str) -> bool:
    """Click a series heading in the left navigation via JavaScript."""
    return bool(
        driver.execute_script(
            """
            const target = arguments[0];
            const headings = document.querySelectorAll('h6');
            for (const heading of headings) {
                if ((heading.innerText || '').trim() === target) {
                    heading.scrollIntoView({block: 'center'});
                    heading.click();
                    return true;
                }
            }
            return false;
            """,
            series_name,
        )
    )


def extract_models_via_js(driver: webdriver.Chrome, parent_url: str) -> list[dict[str, str]]:
    """Collect model links from the live DOM using JavaScript."""
    parent_path = parent_url.replace("https://www.jlg.com", "").rstrip("/")
    min_depth = len([segment for segment in parent_path.split("/") if segment]) + 1
    raw_models: list[dict[str, str]] = driver.execute_script(
        """
        const parentPath = arguments[0];
        const minDepth = arguments[1];
        const results = [];
        const seen = new Set();
        document.querySelectorAll('a[href]').forEach((anchor) => {
            let href = anchor.href.split('?')[0].split('#')[0].replace(/\\/$/, '');
            const relative = href.replace('https://www.jlg.com', '');
            if (!relative.startsWith(parentPath + '/')) return;
            const depth = relative.split('/').filter(Boolean).length;
            if (depth < minDepth || seen.has(href)) return;
            const slug = href.split('/').pop().toLowerCase();
            if (!slug || slug === 'content' || slug === 'en') return;
            seen.add(href);
            let name = (anchor.innerText || '').trim().replace(/\\s+/g, ' ');
            if (name.toLowerCase().startsWith('view the ')) {
                name = name.slice(9).trim();
            }
            if (!name || name.toLowerCase() === 'skip to content') {
                name = href.split('/').pop().toUpperCase();
            }
            results.push({model_name: name, model_url: href});
        });
        return results;
        """,
        parent_path,
        min_depth,
    )
    return raw_models or []


def list_series_headings(driver: webdriver.Chrome) -> list[str]:
    """Return clickable h6 headings from the left navigation."""
    headings: list[str] = driver.execute_script(
        """
        const seen = new Set();
        const results = [];
        document.querySelectorAll('h6').forEach((heading) => {
            const text = (heading.innerText || '').trim();
            if (!text || seen.has(text)) return;
            seen.add(text);
            results.push(text);
        });
        return results;
        """
    )
    return headings or []


def list_tab_buttons(driver: webdriver.Chrome) -> list[str]:
    """Return tab-style button labels used on tabbed category pages."""
    labels: list[str] = driver.execute_script(
        """
        const seen = new Set();
        const results = [];
        document.querySelectorAll('button').forEach((button) => {
            const text = (button.innerText || '').trim();
            if (!text || seen.has(text)) return;
            if (text.length > 60) return;
            seen.add(text);
            results.push(text);
        });
        return results;
        """
    )
    skip = {"menu", "accept all", "reject all", "close", "search", "english"}
    return [
        label
        for label in (labels or [])
        if label.lower() not in skip and not label.lower().startswith("view ")
    ]


def click_tab_button(driver: webdriver.Chrome, tab_name: str) -> bool:
    """Click a tab button by its visible label."""
    return bool(
        driver.execute_script(
            """
            const target = arguments[0];
            for (const button of document.querySelectorAll('button')) {
                if ((button.innerText || '').trim() === target) {
                    button.scrollIntoView({block: 'center'});
                    button.click();
                    return true;
                }
            }
            return false;
            """,
            tab_name,
        )
    )


def open_equipment_menu(driver: webdriver.Chrome) -> None:
    """Hover the Equipment navigation item to reveal the mega-menu."""
    dismiss_overlays(driver)
    equipment_button = WebDriverWait(driver, WAIT_TIMEOUT).until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "[data-cy='link-Equipment'] button, [data-cy='link-Equipment']")
        )
    )
    ActionChains(driver).move_to_element(equipment_button).perform()
    time.sleep(2.5)


def hover_menu_category(driver: webdriver.Chrome, category_name: str) -> None:
    """Hover a top-level equipment category inside the open mega-menu."""
    open_equipment_menu(driver)
    candidates = driver.find_elements(
        By.XPATH,
        f"//a[contains(@href,'/en/equipment/')][normalize-space()='{category_name}']",
    )
    if not candidates:
        candidates = driver.find_elements(
            By.XPATH,
            f"//*[self::a or self::button][contains(normalize-space(), '{category_name}')]",
        )
    if not candidates:
        logger.debug("Could not locate menu category to hover: %s", category_name)
        return

    target = candidates[0]
    ActionChains(driver).move_to_element(target).perform()
    time.sleep(1.5)
