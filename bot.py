# bot.py - version 1ère offre uniquement avec init_driver corrigé
import os
import time
import json
import re
import logging
from datetime import datetime, timedelta
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import stat

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------- CONFIG ----------
BASE_URL = "https://al-in.fr/#/connexion-demandeur"
EMAIL = os.environ.get("EMAIL")
PASSWORD = os.environ.get("PASSWORD")
MAX_RUN_SECONDS = int(os.environ.get("MAX_RUN_SECONDS", 300))  # default 5 minutes
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 15))
WAIT_TIMEOUT = 10  # explicit waits: 10s
SEEN_FILE = "offers_seen.json"
COOKIES_FILE = "session_cookies.json"
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("1", "true", "yes")

if not EMAIL or not PASSWORD:
    logging.error("EMAIL and PASSWORD environment variables must be set.")
    raise SystemExit(1)

# ---------- Helpers ----------
def save_cookies(driver):
    try:
        cookies = driver.get_cookies()
        with open(COOKIES_FILE, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        logging.info(f"Saved {len(cookies)} cookies to {COOKIES_FILE}")
    except Exception as e:
        logging.warning(f"Failed to save cookies: {e}")

def load_cookies_to_driver(driver):
    if not os.path.exists(COOKIES_FILE):
        logging.info("No cookies file found.")
        return False
    try:
        with open(COOKIES_FILE, "r", encoding="utf-8") as f:
            cookies = json.load(f)
    except Exception as e:
        logging.warning(f"Failed to read cookies file: {e}")
        return False
    if not cookies:
        logging.info("Cookies file empty.")
        return False
    try:
        driver.get("https://al-in.fr")
    except Exception:
        pass
    added = 0
    for c in cookies:
        cookie = dict(c)
        cookie.pop("sameSite", None)
        cookie.pop("hostOnly", None)
        if "expiry" in cookie:
            try:
                cookie["expiry"] = int(cookie["expiry"])
            except Exception:
                cookie.pop("expiry", None)
        cookie.pop("domain", None)
        try:
            driver.add_cookie(cookie)
            added += 1
        except Exception as e:
            logging.debug(f"Failed to add cookie {cookie.get('name')}: {e}")
    logging.info(f"Attempted to load cookies into browser: added {added} cookies")
    try:
        driver.refresh()
    except Exception:
        pass
    return added > 0

def parse_price(price_text):
    if not price_text:
        return None
    m = re.search(r"(\d{1,3}(?:[ \u00A0]\d{3})*|\d+)\s*€", price_text.replace("\u00A0", " "))
    if not m:
        return None
    num = m.group(1).replace(" ", "")
    try:
        return int(num)
    except:
        return None

def find_section_button(driver, name):
    xpath = f"//div[contains(@class,'offer-sections')]//div[contains(normalize-space(.),'{name}')]"
    try:
        return WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.element_to_be_clickable((By.XPATH, xpath))
        )
    except TimeoutException:
        return None

def get_offer_cards_in_current_section(driver):
    try:
        container = WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-list-container"))
        )
    except TimeoutException:
        return []
    cards = container.find_elements(By.CSS_SELECTOR, "app-offer-card, .offer-card-container")
    normalized = []
    for c in cards:
        try:
            classes = (c.get_attribute("class") or "")
            if "offer-card-container" in classes:
                normalized.append(c)
            else:
                inner = c.find_element(By.CSS_SELECTOR, ".offer-card-container")
                normalized.append(inner)
        except Exception:
            continue
    return normalized

def extract_offer_info(card):
    try:
        img = card.find_element(By.CSS_SELECTOR, ".offer-image img")
        img_src = img.get_attribute("src")
    except Exception:
        img_src = None
    try:
        price_el = card.find_element(By.CSS_SELECTOR, ".price")
        price_text = price_el.text.strip()
        price = parse_price(price_text)
    except Exception:
        price_text = ""
        price = None
    try:
        typ = card.find_element(By.CSS_SELECTOR, ".typology").text.strip()
    except Exception:
        typ = ""
    try:
        loc = card.find_element(By.CSS_SELECTOR, ".location").text.strip()
    except Exception:
        loc = ""
    uid = img_src or f"{loc}|{price_text}|{typ}"
    return {"uid": uid, "img_src": img_src, "price": price, "price_text": price_text, "typ": typ, "loc": loc}

def click_element(driver, el):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
    except Exception:
        pass
    try:
        el.click()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", el)
            return True
        except Exception as e:
            logging.debug(f"JS click failed: {e}")
            return False

# ---------- Driver init ----------
def init_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")

    # Correct WebDriver manager usage
    driver_path = ChromeDriverManager(version="latest", cache_valid_range=1).install()
    if os.path.isdir(driver_path):
        for root, dirs, files in os.walk(driver_path):
            for f in files:
                if f.startswith("chromedriver") and not f.endswith(".txt") and not f.endswith(".LICENSE"):
                    driver_path = os.path.join(root, f)
                    break
    st = os.stat(driver_path)
    os.chmod(driver_path, st.st_mode | stat.S_IEXEC)
    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    return driver

# ---------- Login utilities ----------
def is_logged_in(driver):
    try:
        driver.find_element(By.CSS_SELECTOR, ".offer-sections")
        return True
    except Exception:
        return False

def perform_login(driver, wait):
    try:
        driver.get(BASE_URL)
    except Exception:
        pass
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form.global-form")))
        mail_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="mail"]')))
        pwd_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="password"]')))
        mail_input.clear()
        mail_input.send_keys(EMAIL)
        pwd_input.clear()
        pwd_input.send_keys(PASSWORD)
        btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'JE ME CONNECTE') or contains(.,'Je me connecte')]")))
        btn.click()
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-sections")))
        logging.info("Login successful.")
        save_cookies(driver)
        return True
    except Exception as e:
        logging.error(f"Login failed: {e}")
        return False

def ensure_logged_in(driver, wait):
    if is_logged_in(driver):
        return True
    logging.info("Not logged in, trying cookies...")
    loaded = load_cookies_to_driver(driver)
    if loaded and is_logged_in(driver):
        logging.info("Logged in via cookies")
        return True
    return perform_login(driver, wait)

# ---------- Main ----------
def main():
    logging.info("Starting bot")
    try:
        driver = init_driver()
    except Exception as e:
        logging.error(f"Driver init failed: {e}")
        return
    wait = WebDriverWait(driver, WAIT_TIMEOUT)
    try:
        if not ensure_logged_in(driver, wait):
            logging.error("Could not authenticate; stopping run.")
            return

        section_names = [
            "Communes demandées",
            "Communes limitrophes",
            "Autres communes du département"
        ]

        # ----- parcours des sections et première offre -----
        applied = False
        for sect in section_names:
            logging.info(f"Selecting section '{sect}'")
            btn = find_section_button(driver, sect)
            if not btn:
                logging.warning(f"Section '{sect}' not found; skipping")
                continue
            click_element(driver, btn)
            time.sleep(2)  # wait content load
            cards = get_offer_cards_in_current_section(driver)
            logging.info(f"{len(cards)} offers found in section '{sect}'")
            for card in cards:
                info = extract_offer_info(card)
                logging.info(f"Checking offer {info['uid']} with price {info['price']}")
                if info['price'] is not None and info['price'] <= 600:
                    logging.info(f"Applying to first suitable offer {info['uid']}")
                    click_element(driver, card)
                    time.sleep(2)
                    try:
                        apply_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'POSTULER')]")))
                        click_element(driver, apply_btn)
                        logging.info("Application submitted.")
                    except Exception as e:
                        logging.warning(f"Failed to click POSTULER: {e}")
                    applied = True
                    break
            if applied:
                break
        if not applied:
            logging.info("No offer applied this run.")

    finally:
        driver.quit()
        logging.info("Bot finished.")

if __name__ == "__main__":
    main()
