# bot.py - watcher + apply + email notifications (status-change notifications ONLY, deduped)
# - Sends email only when a candidature's status changes (or optionally on first-run if SEND_ON_FIRST_RUN=true)
# - Keeps persistent record of candidature statuses in candidatures_status.json
# - Retains apply behaviour and the "cancel if rank != 1" best-effort check
# - Minimal changes to previous structure; improved status extraction to avoid picking dates
#
# USAGE:
# - Set site credentials in environment: EMAIL, PASSWORD
# - Set SMTP creds in environment: SENDER_EMAIL, SENDER_PASS (recommended)
# - Optionally set SEND_ON_FIRST_RUN=true to get notifications on the first run (default: false)
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
from selenium.common.exceptions import (
    TimeoutException,
    ElementClickInterceptedException,
    WebDriverException,
    NoSuchElementException,
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------- CONFIG ----------
BASE_URL = "https://al-in.fr/#/connexion-demandeur"
# site login (keep as env)
EMAIL = os.environ.get("EMAIL") or "mohamed-amine.fennane@epita.fr"
PASSWORD = os.environ.get("PASSWORD") or "&9.Mnq.6F8'M/wm{"

# SMTP / notification (use env vars when possible)
SENDER_EMAIL = os.environ.get("SENDER_EMAIL") or "tesstedsgstsredr@gmail.com"
SENDER_PASS = os.environ.get("SENDER_PASS") or "usdd czjy zsnq iael"  # prefer env var
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL") or "fennane.mohamedamine@gmail.com"

# whether to send notifications on first run (when no candidatures_status file exists)
SEND_ON_FIRST_RUN = os.environ.get("SEND_ON_FIRST_RUN", "false").lower() in ("1", "true", "yes")

WAIT_TIMEOUT = int(os.environ.get("WAIT_TIMEOUT", 12))
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("1", "true", "yes")
SEEN_FILE = "offers_seen.json"
CANDIDATURES_STATUS_FILE = "candidatures_status.json"
MAX_RUN_SECONDS = int(os.environ.get("MAX_RUN_SECONDS", 300))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 15))

# Matching criteria
MAX_PRICE = int(os.environ.get("MAX_PRICE", 600))  # default 600
WANTED_TYPOLOGY_KEY = os.environ.get("WANTED_TYPOLOGY_KEY", "T2")
MIN_AREA_M2 = int(os.environ.get("MIN_AREA_M2", 40))  # default 40m2

# Scrolling / retries
CLICK_RETRIES = int(os.environ.get("CLICK_RETRIES", 5))
SCROLL_PAUSE = float(os.environ.get("SCROLL_PAUSE", 0.6))
CONTAINER_SCROLL_ATTEMPTS = int(os.environ.get("CONTAINER_SCROLL_ATTEMPTS", 30))

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


def save_seen(seen_set):
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen_set), f, ensure_ascii=False, indent=2)
        logging.info(f"Saved {len(seen_set)} seen offers to {SEEN_FILE}")
    except Exception as e:
        logging.warning(f"Failed to save seen file: {e}")


def load_candidatures_statuses():
    try:
        with open(CANDIDATURES_STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_candidatures_statuses(d):
    try:
        with open(CANDIDATURES_STATUS_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        logging.info(f"Saved {len(d)} candidature statuses to {CANDIDATURES_STATUS_FILE}")
    except Exception as e:
        logging.warning(f"Failed to save candidature status file: {e}")


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


def parse_area(typ_text):
    """
    Extract integer m2 from typ_text like '45m2' or '45 m2' or '45m²' etc.
    """
    if not typ_text:
        return None
    m = re.search(r"(\d{2,3})\s*m", typ_text.replace("²", "").replace("M", "m"))
    if not m:
        return None
    try:
        return int(m.group(1))
    except:
        return None


# ---------- Cookie / overlays ----------
def handle_cookie_banner(driver, timeout=5):
    """
    Essaie de fermer la pop-in cookies si elle est présente
    """
    selectors = [
        "//button[contains(., 'Accepter tous les cookies')]",
        "//button[contains(., 'Tout accepter')]",
        "//button[contains(., 'Accepter')]",
        "//button[contains(., 'Autoriser')]",
        "//button[contains(., 'Accept all')]",
        "//button[contains(@class,'cookie')]",
        "//a[contains(.,'Accepter')]",
        "button.tarteaucitronAllAll"
    ]
    for s in selectors:
        try:
            if s.startswith("//"):
                el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.XPATH, s)))
            else:
                el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.CSS_SELECTOR, s)))
            try:
                el.click()
            except Exception:
                driver.execute_script("arguments[0].click();", el)
            logging.info("✅ Bannière cookies acceptée automatiquement.")
            return True
        except TimeoutException:
            continue
        except Exception:
            continue
    logging.debug("ℹ️ Pas de bannière cookies détectée ou impossibilité de la fermer automatiquement.")
    return False


def close_overlays(driver):
    candidates = [
        "//button[contains(@class,'close') or contains(.,'Fermer') or contains(.,'Close')]",
        "//button[contains(@aria-label,'Close')]",
        "//div[contains(@class,'overlay')]//button",
        "//button[contains(.,'Refuser') or contains(.,'Refuser tous les cookies')]",
    ]
    for sel in candidates:
        try:
            els = driver.find_elements(By.XPATH, sel)
            for el in els:
                try:
                    if el.is_displayed():
                        driver.execute_script("arguments[0].click();", el)
                        time.sleep(0.2)
                except Exception:
                    continue
        except Exception:
            continue


# ---------- Offer list helpers ----------
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
            normalized.append(c)
    return normalized


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
    area = parse_area(typ)
    return {"uid": uid, "img_src": img_src, "price_text": price_text, "price": price, "typ": typ, "loc": loc, "area": area}


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

    # try Selenium Manager first
    try:
        logging.info("Trying Selenium Manager (webdriver.Chrome(options=...))")
        driver = webdriver.Chrome(options=options)
        logging.info("Selenium Manager initialized Chrome successfully.")
        return driver
    except Exception as e:
        logging.warning(f"Selenium Manager failed: {e}. Falling back to webdriver-manager.")

    # fallback to webdriver-manager binary
    try:
        driver_path = ChromeDriverManager().install()
        if os.path.isdir(driver_path):
            found = None
            for root, dirs, files in os.walk(driver_path):
                for f in files:
                    if f.lower().startswith("chromedriver"):
                        found = os.path.join(root, f)
                        break
                if found:
                    break
            if found:
                driver_path = found
        try:
            st = os.stat(driver_path)
            os.chmod(driver_path, st.st_mode | stat.S_IEXEC)
        except Exception:
            pass
        service = Service(driver_path)
        driver = webdriver.Chrome(service=service, options=options)
        logging.info("Chrome initialized with webdriver-manager chromedriver.")
        return driver
    except Exception as e:
        logging.error(f"Failed to initialize Chrome driver: {e}")
        raise


# ---------- Login ----------
def is_logged_in(driver):
    try:
        driver.find_element(By.CSS_SELECTOR, ".offer-sections")
        return True
    except Exception:
        try:
            driver.find_element(By.XPATH, "//*[contains(text(),'Mes candidatures') or contains(text(),'Mon compte') or contains(text(),'Bienvenue')]")
            return True
        except Exception:
            return False


def perform_login(driver, wait):
    driver.get(BASE_URL)
    # try to accept cookie banner immediately if present
    try:
        handle_cookie_banner(driver, timeout=3)
    except Exception:
        pass

    try:
        # wait login form - flexible selectors
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form.global-form")))
        mail_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="mail"], input[type="email"], input[name="email"]')))
        pwd_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="password"], input[type="password"], input[name="password"]')))
        mail_input.clear()
        mail_input.send_keys(EMAIL)
        pwd_input.clear()
        pwd_input.send_keys(PASSWORD)

        # Try robust login button selectors
        btn_selectors = [
            (By.CSS_SELECTOR, "button.btnCreate"),
            (By.XPATH, "//button[contains(.,'Je me connecte') or contains(.,'JE ME CONNECTE') or contains(.,'Se connecter')]"),
            (By.XPATH, "//button[contains(.,'JE ME CONNECTE') or contains(.,'Je me connecte')]"),
        ]
        clicked = False
        for sel in btn_selectors:
            try:
                btn = wait.until(EC.element_to_be_clickable(sel))
                driver.execute_script("arguments[0].scrollIntoView(true);", btn)
                try:
                    btn.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", btn)
                clicked = True
                break
            except TimeoutException:
                continue
            except Exception:
                continue
        if not clicked:
            logging.error("Login button not found during perform_login.")
            driver.save_screenshot("login_error.png")
            return False

        # After clicking, accept cookies again if overlay reappears
        try:
            handle_cookie_banner(driver, timeout=2)
        except Exception:
            pass

        # Wait for offers page or 'Les offres' / 'Mes candidatures' link to be present
        wait.until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-sections")),
                EC.presence_of_element_located((By.XPATH, "//a[contains(.,'Les offres')]")),
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(),'Mes candidatures')]")),
            )
        )
        logging.info("Login successful (post-submit checks).")

        # ensure we are on the offers page: click "Les offres" if present
        try:
            offres_btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, "//a[contains(.,'Les offres')]")))
            try:
                driver.execute_script("arguments[0].scrollIntoView(true);", offres_btn)
                offres_btn.click()
                logging.info("Clicked 'Les offres' to navigate to offers page.")
            except Exception:
                try:
                    driver.execute_script("arguments[0].click();", offres_btn)
                    logging.info("Clicked 'Les offres' (JS click).")
                except Exception:
                    logging.debug("Could not click 'Les offres' (maybe already on offers page).")
        except Exception:
            logging.debug("'Les offres' button not found or not clickable; continuing.")

        # final handle cookies/overlays
        try:
            handle_cookie_banner(driver, timeout=1)
            close_overlays(driver)
        except Exception:
            pass

        return True
    except Exception as e:
        try:
            driver.save_screenshot("login_error.png")
            logging.info("Saved screenshot login_error.png for debugging.")
        except Exception:
            pass
        logging.error(f"Login failed: {e}")
        return False


def ensure_logged_in(driver, wait):
    if is_logged_in(driver):
        return True
    logging.info("Not logged in. Performing full login with credentials.")
    ok = perform_login(driver, wait)
    if not ok:
        logging.error("Full login failed.")
    return ok


# ---------- Robust apply flow ----------
def robust_click_apply_flow(driver, wait):
    """
    Attempts to click Apply button robustly, confirm, and fetch result text.
    Returns (applied_bool, result_text_or_reason)
    """
    close_overlays(driver)
    handle_cookie_banner(driver)

    for attempt in range(1, CLICK_RETRIES + 1):
        try:
            apply_selectors = [
                (By.XPATH, "//button[contains(.,'Je postule') or contains(.,'JE POSTULE') or contains(.,'Je postuler')]"),
                (By.CSS_SELECTOR, ".btn.btn-secondary.hi-check-round"),
                (By.XPATH, "//button[contains(.,'Postuler') or contains(.,'Postulez')]"),
            ]
            apply_btn = None
            for sel in apply_selectors:
                try:
                    apply_btn = wait.until(EC.element_to_be_clickable(sel))
                    if apply_btn:
                        break
                except Exception:
                    continue
            if not apply_btn:
                logging.debug(f"Attempt {attempt}: Apply button not found yet; scrolling and retrying.")
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(0.4)
                driver.execute_script("window.scrollTo(0, 0);")
                time.sleep(0.6)
                continue

            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'}); window.scrollBy(0, -80);", apply_btn)
            except Exception:
                pass
            try:
                apply_btn.click()
            except ElementClickInterceptedException:
                close_overlays(driver)
                time.sleep(0.2)
                try:
                    driver.execute_script("arguments[0].click();", apply_btn)
                except Exception as e:
                    logging.debug(f"JS click also failed: {e}")
                    raise
            except Exception as e:
                try:
                    driver.execute_script("arguments[0].click();", apply_btn)
                except Exception:
                    logging.debug(f"Both normal and JS clicks failed on apply: {e}")
                    raise
            logging.info("Clicked 'Je postule'")
            break
        except Exception as e:
            logging.debug(f"Attempt {attempt} click 'Je postule' failed: {e}")
            time.sleep(0.7)
            try:
                driver.execute_script("window.scrollBy(0, 120);")
                time.sleep(0.15)
                driver.execute_script("window.scrollBy(0, -120);")
            except Exception:
                pass
    else:
        logging.error("All attempts to click 'Je postule' failed.")
        return False, "apply_click_failed"

    # Confirm if present
    try:
        confirm_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Confirmer')]")), timeout=WAIT_TIMEOUT)
        try:
            click_element(driver, confirm_btn)
            logging.info("Clicked 'Confirmer'")
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", confirm_btn)
                logging.info("Clicked 'Confirmer' (JS)")
            except Exception:
                logging.debug("Could not click 'Confirmer' (ignored).")
    except Exception:
        logging.debug("No 'Confirmer' button found (maybe not required).")

    # OK button sometimes shown
    try:
        ok_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[normalize-space(.)='Ok' or contains(.,'OK') or contains(.,'Ok')]")), timeout=5)
        try:
            click_element(driver, ok_btn)
            logging.info("Clicked 'Ok'")
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", ok_btn)
            except Exception:
                logging.debug("Could not click 'Ok' (ignored).")
    except Exception:
        logging.debug("No final OK button found.")

    # read result text
    try:
        txt = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".text_picto_vert")), timeout=WAIT_TIMEOUT)
        val = txt.text.strip()
        logging.info("Application result text found.")
        return True, val
    except Exception:
        logging.debug("No application result text found after apply.")
        return True, "applied_but_no_text"


# ---------- Matching & find ----------
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
        # enforce minimum area
        area = info.get("area")
        if area is None or area < MIN_AREA_M2:
            continue
        found.append((card, info))
    return found


# ---------- Candidature monitoring ----------
def extract_candidature_info(cand_elem):
    """
    From a candidature element, extract a uid and current status text and (attempt) rank.
    Return dict with keys: uid, header_text, status_text, rank (int or None), full_text
    """
    text = cand_elem.text or ""
    # header/title
    header_text = ""
    try:
        header = cand_elem.find_element(By.CSS_SELECTOR, ".title")
        header_text = header.text.strip()
    except Exception:
        # fallback: first non-empty line
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        header_text = lines[0] if lines else ""

    # 1) Prefer step-based active/current step title (e.g., "Je postule", "Validations")
    status_text = None
    try:
        candidates = [
            ".//div[contains(@class,'steps')]//div[contains(@class,'a-step') and (contains(@class,'current') or contains(@class,'active') )]//div[contains(@class,'step-title')]",
            ".//div[contains(@class,'steps')]//div[contains(@class,'a-step') and contains(@class,'current')]//div[contains(@class,'step-title')]",
            ".//div[contains(@class,'steps')]//div[contains(@class,'a-step')]//div[contains(@class,'step-title') and contains(@class,'current')]",
        ]
        for xp in candidates:
            try:
                el = cand_elem.find_element(By.XPATH, xp)
                txt = el.text.strip()
                if txt:
                    status_text = txt
                    break
            except Exception:
                continue
    except Exception:
        pass

    # 2) If not found, search for "Statut de la demande" label and pick the following line (strict)
    if not status_text:
        try:
            label_elems = cand_elem.find_elements(By.XPATH, ".//*[contains(normalize-space(.),'Statut de la demande')]")
            for label in label_elems:
                try:
                    parent = label.find_element(By.XPATH, "..")
                    spans = parent.find_elements(By.XPATH, ".//span")
                    for s in spans:
                        stext = s.text.strip()
                        if stext and not re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", stext):
                            status_text = stext
                            break
                    if status_text:
                        break
                except Exception:
                    continue
            if not status_text:
                lines = [l.strip() for l in text.splitlines() if l.strip()]
                for i, line in enumerate(lines):
                    if "Statut de la demande" in line:
                        if i + 1 < len(lines):
                            cand_line = lines[i + 1]
                            if not re.match(r"\d{1,2}/\d{1,2}/\d{2,4}", cand_line):
                                status_text = cand_line
                        break
        except Exception:
            pass

    # 3) Another fallback: try to pick .data.red but avoid picking date or counts
    if not status_text:
        try:
            data_spans = cand_elem.find_elements(By.CSS_SELECTOR, ".data, .data.red")
            for s in data_spans:
                st = s.text.strip()
                if not st:
                    continue
                if "candidat" in st.lower() or "candidatures" in st.lower():
                    continue
                if re.match(r"\d{1,2}/\d{1,2}/\d{2,4}", st):
                    continue
                status_text = st
                break
        except Exception:
            pass

    if not status_text:
        status_text = "Unknown"

    # attempt to parse rank (search in text for 'Position' or 'position' and a number)
    rank = None
    try:
        m = re.search(r"Position\s*\n?\s*[:\-]?\s*(\d{1,4})", text, re.IGNORECASE)
        if not m:
            m = re.search(r"position[^\d]*(\d{1,4})", text, re.IGNORECASE)
        if m:
            rank = int(m.group(1))
    except Exception:
        rank = None

    uid = header_text or (text.splitlines()[0] if text else str(hash(text)))
    return {"uid": uid, "header": header_text, "status": status_text, "rank": rank, "full_text": text}


def process_candidatures_and_notify(driver, wait, candid_statuses, send_notifications=True, send_on_first_run=False):
    """
    Navigate to 'Mes candidatures' and read current candidatures.
    For each candidature, if status differs from saved status, send email (once) and update saved map.
    Deduplicates by uid within the run so you don't get multiple emails.
    Behavior:
      - If send_notifications == False -> do not send any emails, just return the map
      - If send_notifications == True:
          - send an email only when previous status exists and prev != current
          - or when prev is None AND send_on_first_run == True
    """
    # Navigate to 'Mes candidatures' page if possible
    try:
        try:
            mc = driver.find_element(By.XPATH, "//a[contains(.,'Mes candidatures') or contains(.,'Mes candidatures')]")
            try:
                click_element(driver, mc)
            except Exception:
                try:
                    driver.execute_script("arguments[0].click();", mc)
                except Exception:
                    pass
        except Exception:
            try:
                driver.get("https://al-in.fr/#/mes-candidatures")
            except Exception:
                pass
        time.sleep(1)
    except Exception:
        pass

    time.sleep(0.6)
    close_overlays(driver)
    handle_cookie_banner(driver)

    # gather candidature elements
    cand_elems = []
    try:
        cand_elems = driver.find_elements(By.CSS_SELECTOR, ".tdb-s-candidature, .info-candidatures")
    except Exception:
        cand_elems = []

    if not cand_elems:
        try:
            cand_elems = driver.find_elements(By.CSS_SELECTOR, ".tdb-s-candidature")
        except Exception:
            cand_elems = []

    current_map = dict(candid_statuses)
    processed_uids = set()
    changed_items = []

    for elem in cand_elems:
        try:
            info = extract_candidature_info(elem)
            uid = info["uid"]
            if uid in processed_uids:
                continue
            processed_uids.add(uid)

            status = info["status"]
            rank = info.get("rank")
            prev = candid_statuses.get(uid)

            should_send = False
            if send_notifications:
                if prev is not None:
                    if prev != status:
                        should_send = True
                else:
                    # prev is None
                    if send_on_first_run:
                        should_send = True

            if should_send:
                subject = f"BOTALIN - Candidature statut mis à jour: {status}"
                body_lines = [
                    f"Candidature: {info['header'] or uid}",
                    "",
                    f"Element text snapshot:",
                    info["full_text"],
                    "",
                    f"Nouveau statut: {status}",
                    f"Ancien statut: {prev}",
                ]
                body = "\n".join(body_lines)
                ok = send_email(subject, body)
                if ok:
                    logging.info(f"Sent candidature status change email for uid={uid} status={status}")
                else:
                    logging.warning(f"Failed sending candidature status email for uid={uid}")
                current_map[uid] = status
                changed_items.append((uid, prev, status, rank))
            else:
                # still persist new candidatures (so no repeated "None->status" emails unless send_on_first_run True)
                if prev is None:
                    current_map[uid] = status
        except Exception as e:
            logging.debug(f"Failed to process candidature element: {e}")
            continue

    if changed_items:
        save_candidatures_statuses(current_map)

    # If no saved statuses existed before (first-run), ensure we save the initial map to avoid repeated emails later
    if not candid_statuses and current_map:
        save_candidatures_statuses(current_map)

    return current_map, changed_items


# ---------- Cancel candidature flow ----------
def cancel_candidature_by_element(driver, wait, cand_elem):
    """
    Click the 'Annuler cette candidature' link inside cand_elem and confirm the dialog.
    Return True if appears cancelled (best-effort).
    """
    try:
        try:
            cancel_link = cand_elem.find_element(By.XPATH, ".//a[contains(.,'Annuler') or contains(.,'Annuler cette candidature')]")
        except Exception:
            cancel_link = cand_elem.find_element(By.CSS_SELECTOR, "a.tool-link.hi-cross-round")
        click_element(driver, cancel_link)
        time.sleep(0.4)
        try:
            yes_btn = WebDriverWait(driver, 6).until(EC.element_to_be_clickable((By.XPATH, "//button[normalize-space(.)='Oui' or contains(.,'Oui')]")))
            try:
                yes_btn.click()
            except Exception:
                driver.execute_script("arguments[0].click();", yes_btn)
            time.sleep(0.6)
            logging.info("Clicked confirmation 'Oui' for cancellation.")
            return True
        except Exception:
            logging.warning("Confirmation dialog not found for cancellation.")
            return False
    except Exception as e:
        logging.warning(f"Failed to cancel candidature: {e}")
        return False


# ---------- Main ----------
def main():
    logging.info("Starting bot")
    seen = load_seen()
    candid_statuses = load_candidatures_statuses()
    initial_scan = (len(candid_statuses) == 0)
    logging.info(f"Loaded {len(seen)} seen offers (from {SEEN_FILE} if present)")
    logging.info(f"Loaded {len(candid_statuses)} saved candidature statuses (from {CANDIDATURES_STATUS_FILE} if present). initial_scan={initial_scan}")

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

        # check Communes demandées first, then others
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

        applied_uid = None
        if selected_card:
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
                selected_card = None

            if selected_card:
                applied, result = robust_click_apply_flow(driver, wait)
                if applied:
                    applied_uid = uid
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

        # --- After apply (or even if nothing applied), check 'Mes candidatures' and notify only if status changed ---
        try:
            # if initial scan, do not send notifications unless SEND_ON_FIRST_RUN True
            updated_statuses, changes = process_candidatures_and_notify(driver, wait, candid_statuses, send_notifications=not initial_scan, send_on_first_run=SEND_ON_FIRST_RUN)
            candid_statuses = updated_statuses
            if changes:
                logging.info(f"Candidature status changes detected: {changes}")
        except Exception as e:
            logging.warning(f"Failed to process candidatures: {e}")

        # If we just applied and cancel-if-rank-not-one is desired, attempt to find the new candidature and cancel if rank != 1
        if applied_uid:
            time.sleep(1)
            try:
                cand_elems = driver.find_elements(By.CSS_SELECTOR, ".tdb-s-candidature, .info-candidatures")
                for elem in cand_elems:
                    try:
                        info = extract_candidature_info(elem)
                        if info["uid"] == applied_uid or (applied_uid in info.get("full_text", "")):
                            rank = info.get("rank")
                            logging.info(f"Applied candidature found with rank={rank}")
                            if rank is not None and rank != 1:
                                ok_cancel = cancel_candidature_by_element(driver, wait, elem)
                                if ok_cancel:
                                    logging.info("Cancelled candidature because rank != 1")
                                    send_email("BOTALIN - Candidature cancelled", f"Cancelled candidature for {info['header']} because rank={rank} != 1")
                                    candid_statuses[applied_uid] = "Cancelled_by_bot"
                                    save_candidatures_statuses(candid_statuses)
                                else:
                                    logging.warning("Attempt to cancel candidature failed.")
                            break
                    except Exception:
                        continue
            except Exception as e:
                logging.debug(f"Error while searching for applied candidature to possibly cancel: {e}")

        logging.info("Bot finished run.")
    except Exception as e:
        try:
            driver.save_screenshot("unhandled_error.png")
            logging.info("Saved screenshot unhandled_error.png for debugging.")
        except Exception:
            pass
        logging.error(f"Unhandled exception in main: {e}")
        send_email("BOTALIN - Unhandled error", f"Unhandled exception: {e}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
