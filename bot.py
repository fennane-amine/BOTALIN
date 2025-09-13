# bot.py - watcher + apply + email notifications
import os
import time
import json
import re
import logging
import smtplib
import stat
from datetime import datetime, timedelta
from email.message import EmailMessage

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------- CONFIG ----------
BASE_URL = "https://al-in.fr/#/connexion-demandeur"
# Credentials for site - MUST be set (or hardcode here if you insist)
EMAIL = os.environ.get("EMAIL") or "mohamed-amine.fennane@epita.fr"
PASSWORD = os.environ.get("PASSWORD") or "&9.Mnq.6F8'M/wm{"

# SMTP (you asked to hardcode the test account)
SENDER_EMAIL = "tesstedsgstsredr@gmail.com"
SENDER_PASS = "tesstedsgstsredr@gmail.com1212"   # provided by you (jetable)
RECIPIENT_EMAIL = "fennane.mohamedamine@gmail.com"

WAIT_TIMEOUT = 12
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("1", "true", "yes")
SEEN_FILE = "offers_seen.json"
MAX_RUN_SECONDS = int(os.environ.get("MAX_RUN_SECONDS", 300))  # default 5 minutes
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 15))

# Matching criteria
MAX_PRICE = 650
WANTED_TYPOLOGY_KEY = "T2"

# Retry / scroll settings
CLICK_RETRIES = 5
SCROLL_PAUSE = 0.6
CONTAINER_SCROLL_ATTEMPTS = 30

# Safety checks
if not EMAIL or not PASSWORD:
    logging.error("EMAIL and PASSWORD must be set (site credentials).")
    raise SystemExit(1)


# ---------- Helpers ----------
def send_email(subject: str, body: str):
    """Send notification email (simple SMTP TLS)."""
    try:
        msg = EmailMessage()
        msg["From"] = SENDER_EMAIL
        msg["To"] = RECIPIENT_EMAIL
        msg["Subject"] = subject
        msg.set_content(body)

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
            s.starttls()
            s.login(SENDER_EMAIL, SENDER_PASS)
            s.send_message(msg)
        logging.info("Notification email sent.")
    except Exception as e:
        logging.warning(f"Failed to send notification email: {e}")


def load_seen():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data)
    except Exception:
        return set()


def save_seen(seen_set):
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen_set), f, ensure_ascii=False, indent=2)
        logging.info(f"Saved {len(seen_set)} seen offers to {SEEN_FILE}")
    except Exception as e:
        logging.warning(f"Failed to save seen file: {e}")


def parse_price(price_text):
    if not price_text:
        return None
    # match first occurrence of number + €
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
        return WebDriverWait(driver, WAIT_TIMEOUT).until(EC.element_to_be_clickable((By.XPATH, xpath)))
    except TimeoutException:
        return None


def get_offer_cards_in_current_section(driver):
    # ensures the container is present and returns .offer-card-container elements
    try:
        container = WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-list-container"))
        )
    except TimeoutException:
        return []
    return container.find_elements(By.CSS_SELECTOR, ".offer-card-container")


def click_element(driver, el):
    # robust click: scroll into view, normal click, fallback to JS click
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


def extract_offer_info(card):
    try:
        img = card.find_element(By.CSS_SELECTOR, ".offer-image img")
        img_src = img.get_attribute("src")
    except Exception:
        img_src = None
    try:
        price_text = card.find_element(By.CSS_SELECTOR, ".price").text.strip()
    except Exception:
        price_text = ""
    try:
        typ = card.find_element(By.CSS_SELECTOR, ".typology").text.strip()
    except Exception:
        typ = ""
    try:
        loc = card.find_element(By.CSS_SELECTOR, ".location").text.strip()
    except Exception:
        loc = ""
    uid = img_src or f"{loc}|{price_text}|{typ}"
    price = parse_price(price_text)
    return {"uid": uid, "img_src": img_src, "price_text": price_text, "price": price, "typ": typ, "loc": loc}


# ---------- Scrolling utilities ----------
def progressive_scroll_container_to_bottom(driver, container, max_attempts=CONTAINER_SCROLL_ATTEMPTS, pause=SCROLL_PAUSE):
    """
    Try multiple scroll approaches to fully load offers inside container.
    """
    prev_counts = []
    attempt = 0
    while attempt < max_attempts:
        try:
            # set to bottom
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)
        except Exception:
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            except Exception:
                pass

        # small stepped scroll to help lazy loads
        try:
            js = """
            const c = arguments[0];
            const steps = 6;
            for (let i=0;i<steps;i++){
              c.scrollTop = c.scrollTop + Math.round(c.clientHeight/steps);
            }
            return true;
            """
            driver.execute_script(js, container)
        except Exception:
            pass

        time.sleep(pause)
        cards = get_offer_cards_in_current_section(driver)
        cur_count = len(cards)
        prev_counts.append(cur_count)
        if len(prev_counts) > 6:
            prev_counts.pop(0)
        if len(prev_counts) >= 4 and all(x == prev_counts[0] for x in prev_counts):
            logging.debug(f"Container scroll stable after {attempt+1} attempts with {cur_count} cards.")
            break
        attempt += 1
    return


# ---------- Driver init ----------
def init_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")

    # avoid "user data directory is already in use" by using unique temp profile
    options.add_argument(f"--user-data-dir=/tmp/chrome_user_data_{os.getpid()}")

    try:
        logging.info("Trying Selenium Manager (webdriver.Chrome(options=...))")
        driver = webdriver.Chrome(options=options)
        logging.info("Selenium Manager initialized Chrome successfully.")
        return driver
    except Exception as e:
        logging.warning(f"Selenium Manager failed: {e}. Falling back to webdriver-manager.")

    # fallback to webdriver-manager
    driver_path = ChromeDriverManager().install()
    if os.path.isdir(driver_path):
        for root, _, files in os.walk(driver_path):
            for f in files:
                if f.lower().startswith("chromedriver"):
                    driver_path = os.path.join(root, f)
                    break
    try:
        st = os.stat(driver_path)
        os.chmod(driver_path, st.st_mode | stat.S_IEXEC)
    except Exception:
        pass
    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    return driver


# ---------- Login ----------
def is_logged_in(driver):
    try:
        driver.find_element(By.CSS_SELECTOR, ".offer-sections")
        return True
    except Exception:
        return False


def perform_login(driver, wait):
    driver.get(BASE_URL)
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form.global-form")))
        mail_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="mail"]')))
        pwd_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="password"]')))
        mail_input.clear()
        mail_input.send_keys(EMAIL)
        pwd_input.clear()
        pwd_input.send_keys(PASSWORD)
        btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Je me connecte') or contains(.,'JE ME CONNECTE')]")))
        btn.click()
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-sections")))
        logging.info("Login successful.")
        return True
    except Exception as e:
        logging.error(f"Login failed: {e}")
        return False


def ensure_logged_in(driver, wait):
    if is_logged_in(driver):
        return True
    return perform_login(driver, wait)


# ---------- Apply attempt (robust) ----------
def robust_click_apply_flow(driver, wait):
    """
    On the offer detail page try to click 'Je postule', 'Confirmer', 'Ok' robustly.
    Returns tuple (applied_bool, reason_str)
    """
    # Wait short to allow any overlays to settle
    time.sleep(0.5)

    # Look for postule button with retries
    for attempt in range(CLICK_RETRIES):
        try:
            apply_btn = wait.until(EC.element_to_be_clickable((
                By.XPATH, "//button[contains(.,'Je postule') or contains(.,'JE POSTULE') or contains(.,'Je postuler')]"
            )))
            # try scrolling around before click if overlay covers it
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'}); window.scrollBy(0, -80);", apply_btn)
            except Exception:
                pass
            try:
                apply_btn.click()
            except ElementClickInterceptedException:
                # fallback: JS click
                try:
                    driver.execute_script("arguments[0].click();", apply_btn)
                except Exception as e:
                    logging.debug(f"JS click on apply failed: {e}")
                    raise
            logging.info("Clicked 'Je postule'")
            break
        except Exception as e:
            logging.debug(f"Attempt {attempt+1} click 'Je postule' failed: {e}")
            # try a little scroll in the container & window
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(0.2)
                driver.execute_script("window.scrollTo(0, 0);")
            except Exception:
                pass
            time.sleep(0.7)
    else:
        return False, "apply_click_failed"

    # Confirm if exists
    try:
        confirm_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Confirmer')]")))
        try:
            click_element(driver, confirm_btn)
        except Exception:
            logging.debug("Confirm click fallback")
        logging.info("Clicked 'Confirmer'")
    except TimeoutException:
        logging.info("No 'Confirmer' button found (maybe not required).")

    # Close final modal with Ok if present
    try:
        ok_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[normalize-space(.)='Ok' or contains(.,'OK') or contains(.,'Ok')]")))
        try:
            click_element(driver, ok_btn)
        except Exception:
            pass
        logging.info("Clicked 'Ok'")
    except TimeoutException:
        logging.debug("No final OK button found.")

    # Check result text
    try:
        txt = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".text_picto_vert")), )
        val = txt.text.strip()
        logging.info("Application result text found.")
        return True, val
    except TimeoutException:
        logging.debug("No application result text found.")
        return True, "applied_but_no_text"


# ---------- Main ----------
def main():
    logging.info("Starting bot")
    seen = load_seen()
    logging.info(f"Loaded {len(seen)} seen offers (from {SEEN_FILE} if present)")

    try:
        driver = init_driver()
    except Exception as e:
        logging.error(f"Driver init failed: {e}")
        send_email("BOTALIN - Driver init failed", f"Driver init failed: {e}")
        return

    wait = WebDriverWait(driver, WAIT_TIMEOUT)
    try:
        if not ensure_logged_in(driver, wait):
            logging.error("Authentication failed; stopping.")
            send_email("BOTALIN - Login failed", "The bot could not log in with provided credentials.")
            return

        # Sections order per request
        section_names = [
            "Communes demandées",
            "Communes limitrophes",
            "Autres communes du département"
        ]

        # Aggressively gather baseline (scroll each section to bottom to load offers)
        for sect in section_names:
            logging.info(f"Baseline load for section '{sect}'")
            btn = find_section_button(driver, sect)
            if not btn:
                logging.warning(f"Section button '{sect}' not found")
                continue
            click_element(driver, btn)
            time.sleep(0.6)
            # attempt to scroll container
            try:
                container = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-list-container")))
                progressive_scroll_container_to_bottom(driver, container)
            except Exception:
                pass
            # small wait for stability
            time.sleep(0.3)

        # Now search for new offers that match
        applied_any = False
        for sect in section_names:
            logging.info(f"Selecting section '{sect}'")
            btn = find_section_button(driver, sect)
            if not btn:
                logging.warning(f"Section '{sect}' not found; skipping")
                continue
            click_element(driver, btn)
            time.sleep(0.8)

            # ensure container scrolled so we have all cards
            try:
                container = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-list-container")))
                progressive_scroll_container_to_bottom(driver, container)
            except Exception:
                pass

            cards = get_offer_cards_in_current_section(driver)
            logging.info(f"Found {len(cards)} cards in '{sect}'")

            # iterate through cards and find first new matching T2 <= MAX_PRICE
            for card in cards:
                info = extract_offer_info(card)
                uid = info["uid"]
                if not uid:
                    continue
                if uid in seen:
                    continue
                # price parse
                if info["price"] is None:
                    continue
                if WANTED_TYPOLOGY_KEY.upper() not in info["typ"].upper():
                    continue
                if info["price"] > MAX_PRICE:
                    continue

                # found candidate => try to apply to it
                logging.info(f"Applying to new offer: {info}")
                # open detail: click image or card
                try:
                    try:
                        img_el = card.find_element(By.CSS_SELECTOR, ".offer-image img")
                        click_element(driver, img_el)
                    except Exception:
                        # fallback: click the card itself
                        click_element(driver, card)
                except Exception as e:
                    logging.warning(f"Could not open offer detail: {e}")
                    send_email("BOTALIN - Open offer failed", f"Failed to open offer detail: {info}\nException: {e}")
                    seen.add(uid)
                    save_seen(seen)
                    continue

                # robust apply flow (with retries and scrolls)
                applied, result = robust_click_apply_flow(driver, wait)
                if applied:
                    # success path: save seen, notify
                    seen.add(uid)
                    save_seen(seen)
                    subject = f"BOTALIN - Applied to offer ({info['loc']})"
                    body = f"Applied to: {info}\nResult: {result}"
                    send_email(subject, body)
                    logging.info("Applied and notified by email.")
                    applied_any = True
                    break
                else:
                    # failure on clicking apply
                    logging.error(f"Failed to click apply for offer: {info}")
                    send_email("BOTALIN - Apply click failed", f"Failed to click apply button for offer: {info}")
                    # mark as seen to avoid retrying same failing offer repeatedly
                    seen.add(uid)
                    save_seen(seen)
                    # continue to next offer
            if applied_any:
                break

        if not applied_any:
            logging.info("No matching new offers found during this run.")
    except Exception as e:
        logging.error(f"Unhandled exception in main: {e}")
        send_email("BOTALIN - Unhandled error", f"Unhandled exception: {e}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
