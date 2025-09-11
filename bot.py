# bot.py
import os
import time
import json
import re
import logging
import stat
from datetime import datetime, timedelta

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    ElementClickInterceptedException,
    WebDriverException,
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# webdriver-manager imported for fallback
from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------- CONFIG ----------
BASE_URL = "https://al-in.fr/#/connexion-demandeur"
EMAIL = os.environ.get("EMAIL")
PASSWORD = os.environ.get("PASSWORD")
# default run time per execution (seconds). Change via env MAX_RUN_SECONDS
MAX_RUN_SECONDS = int(os.environ.get("MAX_RUN_SECONDS", 300))  # default 5 minutes
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 15))  # seconds between checks
WAIT_TIMEOUT = 10  # explicit waits set to 10s (as requested)
SEEN_FILE = "offers_seen.json"
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("1", "true", "yes")

if not EMAIL or not PASSWORD:
    logging.error("EMAIL and PASSWORD environment variables must be set.")
    raise SystemExit(1)


# ---------- Helpers ----------
def load_seen():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(seen_set):
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen_set), f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.warning(f"Failed to save seen file: {e}")


def parse_price(price_text):
    """
    Extract integer euros from strings like '448 € (288 € Hors charge)' or ' 946 € '
    """
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
    xpath = f"//div[contains(@class,'offer-sections')]//div[contains(.,'{name}')]"
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
            # if this element is the container directly
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
        WebDriverWait(driver, WAIT_TIMEOUT).until(EC.element_to_be_clickable((By.XPATH, ".")))
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


# ---------- Driver init (Selenium Manager first, fallback to webdriver-manager) ----------
def init_driver():
    options = Options()
    if HEADLESS:
        # headless new mode
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    # optional: reduce detection by setting a standard user-agent if needed
    # options.add_argument("user-agent=Mozilla/5.0 (X11; Linux x86_64) ...")

    # Try Selenium Manager (automatic) first
    try:
        logging.info("Trying Selenium Manager (webdriver.Chrome(options=...))")
        driver = webdriver.Chrome(options=options)
        logging.info("Selenium Manager initialized Chrome successfully.")
        return driver
    except Exception as e:
        logging.warning(f"Selenium Manager failed: {e}. Falling back to webdriver-manager approach.")

    # Fallback to webdriver-manager robust approach
    try:
        logging.info("Using webdriver-manager to download/find chromedriver")
        driver_path = ChromeDriverManager().install()  # may return folder or path

        # If returned path is a directory, search for chromedriver binary inside
        if os.path.isdir(driver_path):
            found = None
            for root, dirs, files in os.walk(driver_path):
                for f in files:
                    # accept files that start with chromedriver (handles platform variations)
                    if f.lower().startswith("chromedriver"):
                        found = os.path.join(root, f)
                        break
                if found:
                    break
            if not found:
                raise RuntimeError(f"No chromedriver binary found in {driver_path}")
            driver_path = found

        # ensure executable
        try:
            st = os.stat(driver_path)
            os.chmod(driver_path, st.st_mode | stat.S_IEXEC)
        except Exception as e:
            logging.warning(f"chmod on chromedriver failed: {e}")

        service = Service(driver_path)
        driver = webdriver.Chrome(service=service, options=options)
        logging.info("Chrome initialized with webdriver-manager chromedriver.")
        return driver
    except Exception as e:
        logging.error(f"Failed to initialize Chrome driver: {e}")
        raise


# ---------- Main logic ----------
def main():
    logging.info("Starting bot")
    seen = load_seen()
    logging.info(f"Loaded {len(seen)} seen offers")

    driver = None
    try:
        driver = init_driver()
    except Exception as e:
        logging.error(f"Driver initialization failed: {e}")
        return

    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    try:
        driver.get(BASE_URL)
        # wait login form
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form.global-form")))
        # fill login
        mail_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="mail"]')))
        pwd_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="password"]')))
        mail_input.clear()
        mail_input.send_keys(EMAIL)
        pwd_input.clear()
        pwd_input.send_keys(PASSWORD)
        # click connect
        try:
            btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'JE ME CONNECTE') or contains(.,'Je me connecte')]")))
            btn.click()
        except TimeoutException:
            logging.error("Login button not found; aborting.")
            return

        # wait for offer sections to appear
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-sections")))

        start_time = datetime.utcnow()
        end_time = start_time + timedelta(seconds=MAX_RUN_SECONDS)
        logging.info(f"Bot will run until {end_time.isoformat()} UTC (or until it finds & applies). Poll every {POLL_INTERVAL}s")

        section_names = [
            "Communes demandées",
            "Communes limitrophes",
            "Autres communes du département"
        ]

        while datetime.utcnow() < end_time:
            applied = False
            for sect in section_names:
                logging.info(f"Selecting section '{sect}'")
                btn = find_section_button(driver, sect)
                if not btn:
                    logging.warning(f"Section '{sect}' not found; skipping")
                    continue
                click_element(driver, btn)
                time.sleep(1)  # allow DOM refresh

                cards = get_offer_cards_in_current_section(driver)
                logging.info(f"Found {len(cards)} cards in '{sect}'")
                for card in cards:
                    info = extract_offer_info(card)
                    uid = info["uid"]
                    logging.debug(f"Offer uid={uid} price={info['price']} typ={info['typ']} loc={info['loc']}")
                    if uid in seen:
                        continue
                    if info["price"] is None:
                        continue
                    is_t2 = "T2" in info["typ"].upper() or "| T2" in info["typ"].upper()
                    if is_t2 and info["price"] <= 600:
                        logging.info(f"Found matching NEW offer: {info}")
                        # open offer by clicking image
                        try:
                            img_el = card.find_element(By.CSS_SELECTOR, ".offer-image img")
                            click_element(driver, img_el)
                        except Exception as e:
                            logging.warning(f"Could not open offer detail: {e}")
                            continue

                        # apply
                        try:
                            apply_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                                EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Je postule') or contains(.,'JE POSTULE') or contains(.,'Je postuler')]"))
                            )
                            apply_btn.click()
                            logging.info("Clicked 'Je postule'")
                        except TimeoutException:
                            logging.warning("Apply button not found (timeout). Trying alternative selectors.")
                            try:
                                apply_btn = driver.find_element(By.CSS_SELECTOR, ".btn-secondary.hi-check-round")
                                apply_btn.click()
                            except Exception as e:
                                logging.error(f"Failed to click apply: {e}")
                                continue

                        # confirm
                        try:
                            confirm_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                                EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Confirmer')]"))
                            )
                            confirm_btn.click()
                            logging.info("Clicked 'Confirmer'")
                        except TimeoutException:
                            logging.warning("Confirm button not found (maybe not required)")

                        # ok
                        try:
                            ok_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                                EC.element_to_be_clickable((By.XPATH, "//button[normalize-space(.)='Ok' or contains(.,'Ok') or contains(.,'OK')]"))
                            )
                            ok_btn.click()
                            logging.info("Clicked 'Ok'")
                        except TimeoutException:
                            logging.warning("Ok button not found (maybe not required)")

                        # read result text
                        try:
                            txt = WebDriverWait(driver, WAIT_TIMEOUT).until(
                                EC.presence_of_element_located((By.CSS_SELECTOR, ".text_picto_vert"))
                            )
                            val = txt.text.strip()
                            logging.info(f"Application result text: {val}")
                            seen.add(uid)
                            save_seen(seen)
                            applied = True
                            break
                        except TimeoutException:
                            logging.warning("Result text not found; still saving seen and exiting.")
                            seen.add(uid)
                            save_seen(seen)
                            applied = True
                            break

                if applied:
                    break

            if applied:
                logging.info("Applied to an offer; stopping this run.")
                break

            # no match found in all sections; sleep until next poll or until end_time
            remaining = (end_time - datetime.utcnow()).total_seconds()
            if remaining <= 0:
                break
            sleep_for = min(POLL_INTERVAL, max(0, remaining))
            logging.info(f"No matching offers found. Sleeping {sleep_for}s before next poll...")
            time.sleep(sleep_for)

        logging.info("Bot finished run.")
    except Exception as e:
        logging.error(f"Unhandled error in main loop: {e}")
    finally:
        try:
            if driver:
                driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
