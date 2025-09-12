import os
import json
import time
import logging
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# ---------- Config ----------
WAIT_TIMEOUT = 15
COOKIES_FILE = "session_cookies.json"
SEEN_FILE = "offers_seen.json"
EMAIL = os.getenv("BOT_EMAIL")
PASSWORD = os.getenv("BOT_PASSWORD")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# ---------- Driver ----------
def init_driver():
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--headless=new")

    logging.info("Trying Selenium Manager (webdriver.Chrome(options=...))")
    driver = webdriver.Chrome(options=options)  # Selenium Manager gère tout
    logging.info("Selenium Manager initialized Chrome successfully.")
    return driver

# ---------- Cookies ----------
def save_cookies(driver):
    cookies = driver.get_cookies()
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f)
    logging.info(f"Saved {len(cookies)} cookies to {COOKIES_FILE}")

def load_cookies(driver):
    if not os.path.exists(COOKIES_FILE):
        return False
    try:
        with open(COOKIES_FILE, "r") as f:
            cookies = json.load(f)
        driver.get("https://www.leboncoin.fr")
        for c in cookies:
            driver.add_cookie(c)
        logging.info(f"Attempted to load cookies into browser: added {len(cookies)} cookies")
        return True
    except Exception as e:
        logging.warning(f"Could not load cookies: {e}")
        return False

# ---------- Seen ----------
def load_seen():
    if os.path.exists(SEEN_FILE):
        try:
            with open(SEEN_FILE, "r") as f:
                return set(json.load(f))
        except:
            return set()
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)
    logging.info(f"Saved {len(seen)} seen offers to {SEEN_FILE}")

# ---------- Login ----------
def ensure_logged_in(driver, wait):
    driver.get("https://www.leboncoin.fr/")
    time.sleep(2)

    # Charger cookies si dispo
    if load_cookies(driver):
        driver.refresh()
        time.sleep(2)

    # Vérifier si connecté
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='compte']")))
        logging.info("Already logged in via cookies")
        return True
    except TimeoutException:
        logging.info("Not logged in. Performing full login.")

    # Sinon, login manuel
    try:
        login_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "a[data-qa-id='header-login-button']")))
        login_btn.click()
        email_input = wait.until(EC.presence_of_element_located((By.NAME, "email")))
        pwd_input = driver.find_element(By.NAME, "password")
        email_input.send_keys(EMAIL)
        pwd_input.send_keys(PASSWORD)
        submit_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
        submit_btn.click()
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='compte']")))
        logging.info("Login successful.")
        save_cookies(driver)
        return True
    except Exception as e:
        logging.error(f"Login failed: {e}")
        return False

# ---------- Helpers ----------
def find_section_button(driver, section_text):
    try:
        return driver.find_element(By.XPATH, f"//button[contains(., '{section_text}')]")
    except:
        return None

def get_offer_cards_in_current_section(driver):
    try:
        return driver.find_elements(By.CSS_SELECTOR, "a[data-test-id='ad']")
    except:
        return []

def extract_offer_info(card):
    try:
        title = card.get_attribute("title") or "No title"
        link = card.get_attribute("href") or "No link"
        return f"{title} | {link}"
    except:
        return "Unknown offer"

def click_element(driver, element):
    driver.execute_script("arguments[0].click();", element)

# ---------- Main ----------
def main():
    logging.info("Starting bot")
    seen = load_seen()
    logging.info(f"Loaded {len(seen)} seen offers (from {SEEN_FILE} if present)")

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

        # ---------- TEST APPLY MODE ----------
        logging.info("TEST APPLY MODE: selecting section 'Autres communes du département'")
        btn = find_section_button(driver, "Autres communes du département")
        if btn:
            click_element(driver, btn)
            time.sleep(2)
            cards = get_offer_cards_in_current_section(driver)
            if not cards:
                logging.error("No offers found in 'Autres communes du département' for TEST APPLY.")
                return

            # prendre la première offre
            test_card = cards[0]
            info = extract_offer_info(test_card)
            logging.info(f"TEST APPLY on first card: {info}")

            try:
                img_el = test_card.find_element(By.CSS_SELECTOR, "img")
                click_element(driver, img_el)
            except Exception as e:
                logging.warning(f"Could not open offer detail in TEST APPLY: {e}")
                return

            try:
                apply_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Je postule')]"))
                )
                apply_btn.click()
                logging.info("Clicked 'Je postule' (TEST APPLY)")
            except Exception as e:
                logging.error(f"Apply button not found in TEST APPLY: {e}")
                return

            try:
                confirm_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Confirmer')]"))
                )
                confirm_btn.click()
                logging.info("Clicked 'Confirmer' (TEST APPLY)")
            except TimeoutException:
                logging.warning("Confirm button not found in TEST APPLY (maybe not required)")

            try:
                ok_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[normalize-space(.)='Ok']"))
                )
                ok_btn.click()
                logging.info("Clicked 'Ok' (TEST APPLY)")
            except TimeoutException:
                logging.warning("Ok button not found in TEST APPLY (maybe not required)")

            try:
                txt = WebDriverWait(driver, WAIT_TIMEOUT).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".text_picto_vert"))
                )
                val = txt.text.strip()
                logging.info(f"TEST APPLY result text: {val}")
            except TimeoutException:
                logging.warning("Result text not found in TEST APPLY")

            save_cookies(driver)
            logging.info("TEST APPLY MODE finished successfully.")
            return
        else:
            logging.error("Section 'Autres communes du département' not found for TEST APPLY.")
            return
        # ---------- END TEST APPLY MODE ----------

    finally:
        driver.quit()

if __name__ == "__main__":
    main()
