# bot.py
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
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    ElementClickInterceptedException,
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# CONFIG (modifiable via env)
BASE_URL = "https://al-in.fr/#/connexion-demandeur"
EMAIL = os.environ.get("EMAIL")
PASSWORD = os.environ.get("PASSWORD")
# run duration per job: default 300 seconds = 5 minutes
MAX_RUN_SECONDS = int(os.environ.get("MAX_RUN_SECONDS", 300))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 15))  # seconds between checks
WAIT_TIMEOUT = 10  # explicit wait: 10 seconds as requested
SEEN_FILE = "offers_seen.json"

if not EMAIL or not PASSWORD:
    logging.error("EMAIL and PASSWORD must be provided via environment variables.")
    raise SystemExit(1)

def load_seen():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_seen(seen_set):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_set), f, ensure_ascii=False, indent=2)

def parse_price(price_text):
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
            if c.get_attribute("class") and "offer-card-container" in c.get_attribute("class"):
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
        WebDriverWait(driver, WAIT_TIMEOUT).until(EC.element_to_be_clickable((By.XPATH, ".")), 0.1)
    except Exception:
        pass
    try:
        el.click()
        return True
    except Exception as e:
        logging.debug(f"Click failed: {e}")
        try:
            driver.execute_script("arguments[0].click();", el)
            return True
        except Exception as e2:
            logging.warning(f"JS click failed: {e2}")
            return False

def main():
    logging.info("Starting bot")
    seen = load_seen()
    logging.info(f"Loaded {len(seen)} seen offers")

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    try:
        driver.get(BASE_URL)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form.global-form")))
        mail_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="mail"]')))
        pwd_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="password"]')))
        mail_input.clear()
        mail_input.send_keys(EMAIL)
        pwd_input.clear()
        pwd_input.send_keys(PASSWORD)
        try:
            btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'JE ME CONNECTE') or contains(.,'Je me connecte')]")))
            btn.click()
        except TimeoutException:
            logging.error("Login button not found")
            return

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
                btn = find_section_button(driver, sect)
                if not btn:
                    logging.warning(f"Section button '{sect}' not found; skipping")
                    continue
                try:
                    click_element(driver, btn)
                except Exception as e:
                    logging.warning(f"Could not click section {sect}: {e}")
                time.sleep(1)

                cards = get_offer_cards_in_current_section(driver)
                logging.info(f"Found {len(cards)} cards in '{sect}'")
                for card in cards:
                    info = extract_offer_info(card)
                    uid = info["uid"]
                    logging.debug(f"Offer: uid={uid} price={info['price']} typ={info['typ']} loc={info['loc']}")
                    if uid in seen:
                        continue
                    if info["price"] is None:
                        continue
                    is_t2 = "T2" in info["typ"].upper() or "| T2" in info["typ"].upper()
                    if is_t2 and info["price"] <= 600:
                        logging.info(f"Found matching new offer: {info}")
                        try:
                            img_el = card.find_element(By.CSS_SELECTOR, ".offer-image img")
                            click_element(driver, img_el)
                        except Exception as e:
                            logging.warning(f"Could not open offer detail: {e}")
                            continue

                        try:
                            apply_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                                EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Je postule') or contains(.,'JE POSTULE')]"))
                            )
                            apply_btn.click()
                            logging.info("Clicked 'Je postule'")
                        except TimeoutException:
                            logging.warning("Apply button not found")
                            try:
                                apply_btn = driver.find_element(By.CSS_SELECTOR, ".btn-secondary.hi-check-round")
                                apply_btn.click()
                            except Exception as e:
                                logging.error(f"Failed to click apply: {e}")
                                continue

                        try:
                            confirm_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                                EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Confirmer')]"))
                            )
                            confirm_btn.click()
                            logging.info("Clicked 'Confirmer'")
                        except TimeoutException:
                            logging.warning("Confirm button not found")

                        try:
                            ok_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                                EC.element_to_be_clickable((By.XPATH, "//button[normalize-space(.)='Ok' or contains(.,'Ok')]"))
                            )
                            ok_btn.click()
                            logging.info("Clicked 'Ok'")
                        except TimeoutException:
                            logging.warning("Ok button not found (maybe not required)")

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
                break
            # respect end_time precisely
            remaining = (end_time - datetime.utcnow()).total_seconds()
            if remaining <= 0:
                break
            sleep_for = min(POLL_INTERVAL, max(0, remaining))
            logging.info(f"No matching offers found. Sleeping {sleep_for}s before next poll...")
            time.sleep(sleep_for)

        logging.info("Bot finished run.")
    finally:
        try:
            driver.quit()
        except:
            pass

if __name__ == "__main__":
    main()
