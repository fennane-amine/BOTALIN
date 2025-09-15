# bot.py - watcher + apply + email notifications (priority: Communes demandées, max price 600)
# USAGE:
# - Set site credentials in environment: EMAIL, PASSWORD
# - Set SMTP creds in environment: SENDER_EMAIL, SENDER_PASS (recommended)
# - Optionally set HEADLESS, MAX_RUN_SECONDS, POLL_INTERVAL
#
# Gmail notes:
# - If using a Gmail account for SENDER_EMAIL, you must create an App Password (account with 2FA),
#   then set that app password in SENDER_PASS. Without that Gmail will refuse (535 BadCredentials).
# - Alternatively use a transactional email provider (SendGrid/Mailgun/etc.)
#
# This script will NOT crash if SMTP auth fails: it logs the problem and continues.

import os
import time
import json
import re
import logging
import stat
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException, WebDriverException, NoSuchElementException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------- CONFIG ----------
BASE_URL = "https://al-in.fr/#/connexion-demandeur"
# site login (keep as env)
EMAIL = os.environ.get("EMAIL") or "mohamed-amine.fennane@epita.fr"
PASSWORD = os.environ.get("PASSWORD") or "&9.Mnq.6F8'M/wm{"

# SMTP / notification
SENDER_EMAIL = os.environ.get("SENDER_EMAIL") or "tesstedsgstsredr@gmail.com"
SENDER_PASS = "usdd czjy zsnq iael" or "tesstedsgstsredr@gmail.com1212"  # prefer env var for security
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL") or "fennane.mohamedamine@gmail.com"

WAIT_TIMEOUT = 12
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("1", "true", "yes")
SEEN_FILE = "offers_seen.json"
MAX_RUN_SECONDS = int(os.environ.get("MAX_RUN_SECONDS", 300))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 15))

# Matching criteria
MAX_PRICE = 600
WANTED_TYPOLOGY_KEY = "T2"

# Scrolling / retries
CLICK_RETRIES = 5
SCROLL_PAUSE = 0.6
CONTAINER_SCROLL_ATTEMPTS = 30

# Basic sanity
if not EMAIL or not PASSWORD:
    logging.error("Site EMAIL and PASSWORD must be set.")
    raise SystemExit(1)


# ---------- Email helper (robust) ----------
def send_email(subject: str, body: str):
    """Send email via SMTP TLS. On failure logs error and returns False."""
    msg = EmailMessage()
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject
    msg.set_content(body)

    # Try TLS (587) first, then SSL(465) fallback
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
            s.ehlo()
            s.starttls()
            s.login(SENDER_EMAIL, SENDER_PASS)
            s.send_message(msg)
        logging.info("Notification email sent via smtp.gmail.com:587")
        return True
    except smtplib.SMTPAuthenticationError as e:
        logging.warning(f"SMTP auth failed (TLS): {e}")
    except Exception as e:
        logging.warning(f"SMTP TLS send failed: {e}")

    # fallback to SSL port 465
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
            s.login(SENDER_EMAIL, SENDER_PASS)
            s.send_message(msg)
        logging.info("Notification email sent via smtp.gmail.com:465")
        return True
    except smtplib.SMTPAuthenticationError as e:
        logging.warning(f"SMTP auth failed (SSL): {e}")
    except Exception as e:
        logging.warning(f"SMTP SSL send failed: {e}")

    logging.error("All attempts to send notification email failed. Check SMTP credentials / app password.")
    return False


# ---------- Helpers ----------
def load_seen():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def handle_cookie_banner(driver, timeout=5):
    """
    Essaie de fermer la pop-in cookies si elle est présente
    """
    try:
        # Attendre que la bannière apparaisse
        cookie_button = WebDriverWait(driver, timeout).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Accepter tous les cookies') or contains(., 'Tout accepter')]"))
        )
        cookie_button.click()
        print("✅ Bannière cookies acceptée automatiquement.")
    except TimeoutException:
        print("ℹ️ Pas de bannière cookies détectée.")
    except NoSuchElementException:
        print("ℹ️ Aucun bouton de cookies trouvé.")

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
    try:
        container = WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-list-container"))
        )
    except TimeoutException:
        return []
    return container.find_elements(By.CSS_SELECTOR, ".offer-card-container")


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
    prev_counts = []
    attempt = 0
    while attempt < max_attempts:
        try:
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)
        except Exception:
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            except Exception:
                pass

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
    # unique profile to avoid "user data directory is already in use"
    options.add_argument(f"--user-data-dir=/tmp/chrome_user_data_{os.getpid()}")

    try:
        logging.info("Trying Selenium Manager (webdriver.Chrome(options=...))")
        driver = webdriver.Chrome(options=options)
        logging.info("Selenium Manager initialized Chrome successfully.")
        return driver
    except Exception as e:
        logging.warning(f"Selenium Manager failed: {e}. Falling back to webdriver-manager.")

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

        # Email et mot de passe
        mail_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[formcontrolname='mail']")))
        pwd_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "input[formcontrolname='password']")))

        mail_input.clear()
        mail_input.send_keys(EMAIL)
        pwd_input.clear()
        pwd_input.send_keys(PASSWORD)

        # Bouton login robuste (par classe btnCreate)
        btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.btnCreate")))
        driver.execute_script("arguments[0].scrollIntoView(true);", btn)
        btn.click()

        handle_cookie_banner(driver)

        # Attendre que la page offres charge
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-sections")))
        logging.info("Login successful.")
        return True

    except Exception as e:
        driver.save_screenshot("login_error.png")  # 👈 pour debug
        logging.error(f"Login failed: {e}")
        return False

def ensure_logged_in(driver, wait):
    if is_logged_in(driver):
        return True
    return perform_login(driver, wait)


# ---------- Robust apply flow ----------
def robust_click_apply_flow(driver, wait):
    for attempt in range(CLICK_RETRIES):
        try:
            apply_btn = wait.until(EC.element_to_be_clickable((
                By.XPATH, "//button[contains(.,'Je postule') or contains(.,'JE POSTULE') or contains(.,'Je postuler')]"
            )))
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'}); window.scrollBy(0, -80);", apply_btn)
            except Exception:
                pass
            try:
                apply_btn.click()
            except ElementClickInterceptedException:
                try:
                    driver.execute_script("arguments[0].click();", apply_btn)
                except Exception as e:
                    logging.debug(f"JS click on apply failed: {e}")
                    raise
            logging.info("Clicked 'Je postule'")
            break
        except Exception as e:
            logging.debug(f"Attempt {attempt+1} click 'Je postule' failed: {e}")
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(0.2)
                driver.execute_script("window.scrollTo(0, 0);")
            except Exception:
                pass
            time.sleep(0.7)
    else:
        return False, "apply_click_failed"

    try:
        confirm_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Confirmer')]")))
        try:
            click_element(driver, confirm_btn)
        except Exception:
            logging.debug("Confirm click fallback")
        logging.info("Clicked 'Confirmer'")
    except TimeoutException:
        logging.info("No 'Confirmer' button found (maybe not required).")

    try:
        ok_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[normalize-space(.)='Ok' or contains(.,'OK') or contains(.,'Ok')]")))
        try:
            click_element(driver, ok_btn)
        except Exception:
            pass
        logging.info("Clicked 'Ok'")
    except TimeoutException:
        logging.debug("No final OK button found.")

    try:
        txt = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".text_picto_vert")), )
        val = txt.text.strip()
        logging.info("Application result text found.")
        return True, val
    except TimeoutException:
        logging.debug("No application result text found.")
        return True, "applied_but_no_text"


def find_matching_offers_in_section(driver, wait, seen, section_name):
    found = []
    btn = find_section_button(driver, section_name)
    if not btn:
        logging.debug(f"Section '{section_name}' not found.")
        return found
    click_element(driver, btn)
    time.sleep(0.6)
    try:
        container = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-list-container")))
        progressive_scroll_container_to_bottom(driver, container)
    except Exception:
        pass
    time.sleep(0.3)
    cards = get_offer_cards_in_current_section(driver)
    logging.info(f"Found {len(cards)} cards in '{section_name}' during matching check")
    for card in cards:
        info = extract_offer_info(card)
        uid = info.get("uid")
        if not uid or uid in seen:
            continue
        if info.get("price") is None:
            continue
        if WANTED_TYPOLOGY_KEY.upper() not in info.get("typ", "").upper():
            continue
        if info.get("price") > MAX_PRICE:
            continue
        found.append((card, info))
    return found


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

        sections_priority = [
            "Communes demandées",
            "Communes limitrophes",
            "Autres communes du département"
        ]

        selected_card = None
        selected_info = None

        matches = find_matching_offers_in_section(driver, wait, seen, "Communes demandées")
        if matches:
            selected_card, selected_info = matches[0]
            logging.info("Selected first matching offer in 'Communes demandées'")
        else:
            for sect in ("Communes limitrophes", "Autres communes du département"):
                matches = find_matching_offers_in_section(driver, wait, seen, sect)
                if matches:
                    selected_card, selected_info = matches[0]
                    logging.info(f"Selected first matching offer in '{sect}'")
                    break

        if not selected_card:
            logging.info("No matching new offers found during this run.")
            return

        info = selected_info
        uid = info["uid"]
        logging.info(f"Applying to selected offer: {info}")

        # open detail
        try:
            try:
                img_el = selected_card.find_element(By.CSS_SELECTOR, ".offer-image img")
                click_element(driver, img_el)
            except Exception:
                click_element(driver, selected_card)
        except Exception as e:
            logging.warning(f"Could not open offer detail: {e}")
            send_email("BOTALIN - Open offer failed", f"Failed to open offer detail: {info}\nException: {e}")
            seen.add(uid)
            save_seen(seen)
            return

        applied, result = robust_click_apply_flow(driver, wait)
        if applied:
            seen.add(uid)
            save_seen(seen)
            subject = f"BOTALIN - Applied to offer ({info['loc']})"
            body = f"Applied to: {info}\nResult: {result}"
            ok_email = send_email(subject, body)
            if not ok_email:
                logging.warning("Email notification failed; check SMTP credentials or use App Password.")
            logging.info("Applied (or attempted) and processed notification.")
        else:
            logging.error(f"Failed to click apply for offer: {info}")
            send_email("BOTALIN - Apply click failed", f"Failed to click apply button for offer: {info}")
            seen.add(uid)
            save_seen(seen)

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
