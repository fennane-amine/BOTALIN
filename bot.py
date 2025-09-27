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
# prefer env var for security; fallback to the value you asked earlier
SENDER_PASS = os.environ.get("SENDER_PASS") or "usdd czjy zsnq iael"
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL") or "fennane.mohamedamine@gmail.com"

WAIT_TIMEOUT = int(os.environ.get("WAIT_TIMEOUT", 12))
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("1", "true", "yes")
SEEN_FILE = "offers_seen.json"
CANDIDATURES_FILE = "candidature_statuses.json"
MAX_RUN_SECONDS = int(os.environ.get("MAX_RUN_SECONDS", 300))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 15))

# Matching criteria
MAX_PRICE = int(os.environ.get("MAX_PRICE", 550))
WANTED_TYPOLOGY_KEY = os.environ.get("WANTED_TYPOLOGY_KEY", "T2")
MIN_AREA_M2 = int(os.environ.get("MIN_AREA_M2", 40))

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


def load_candidature_statuses():
    try:
        with open(CANDIDATURES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_seen(seen_set):
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen_set), f, ensure_ascii=False, indent=2)
        logging.info(f"Saved {len(seen_set)} seen offers to {SEEN_FILE}")
    except Exception as e:
        logging.warning(f"Failed to save seen file: {e}")


def save_candidature_statuses(d):
    try:
        with open(CANDIDATURES_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        logging.info(f"Saved candidature statuses to {CANDIDATURES_FILE}")
    except Exception as e:
        logging.warning(f"Failed saving candidature statuses: {e}")


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


def parse_area_from_typ(typ_text):
    """
    Extract area number (int m2) from typology like '40m2 | T2' or '40 m2 | T2'
    """
    if not typ_text:
        return None
    m = re.search(r"(\d{1,3})\s*m", typ_text.replace("\u00A0", " "), re.IGNORECASE)
    if m:
        try:
            return int(m.group(1))
        except:
            return None
    return None


def handle_cookie_banner(driver, timeout=5):
    """
    Essaie de fermer la pop-in cookies si elle est présente.
    On tente plusieurs sélecteurs connus.
    """
    selectors = [
        "//button[contains(., 'Accepter tous les cookies')]",
        "//button[contains(., 'Tout accepter')]",
        "//button[contains(., 'Accepter')]",
        "//button[contains(., 'Autoriser')]",
        "//button[contains(., 'Accept all')]",
        "//button[contains(@class,'cookie')]",
        "//a[contains(.,'Accepter')]",
        "button.tarteaucitronAllAll"  # fallback
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
    """
    Tente de fermer les modales / overlays qui peuvent empêcher les clicks.
    """
    candidates = [
        "//button[contains(@class,'close') or contains(.,'Fermer') or contains(.,'Close')]",
        "//button[contains(@aria-label,'Close')]",
        "//div[contains(@class,'overlay')]//button",
        "//button[contains(.,'Refuser') or contains(.,'Refuser tous les cookies')]",
        "//span[contains(@class,'cookie')]//a[contains(.,'Refuser')]",
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
    # support both app-offer-card and direct containers
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
            # fallback: use c itself
            normalized.append(c)
    return normalized


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
    area = parse_area_from_typ(typ)
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
        # sometimes the logged-in landing page is 'Mes candidatures' or another dashboard
        # check for presence of Mon compte / Mes candidatures etc.
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
        # wait login form - use a flexible selector (site uses formcontrolname attributes)
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form.global-form")))
        mail_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="mail"], input[type="email"], input[name="email"]')))
        pwd_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="password"], input[type="password"], input[name="password"]')))
        mail_input.clear()
        mail_input.send_keys(EMAIL)
        pwd_input.clear()
        pwd_input.send_keys(PASSWORD)

        # Try a robust set of selectors for the login button
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

        # After login, the app is SPA — wait for an element indicating offers loaded or header
        # we relax the condition: presence of .offer-sections or links 'Les offres' or 'Mes candidatures'
        WebDriverWait(driver, WAIT_TIMEOUT).until(
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
        # save screenshot to debug failed login states
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


# ---------- Confirmation modal helper ----------
def handle_confirmation_modal_yes(driver, timeout=4):
    """
    Detecte une pop-in de confirmation de type PDialog avec boutons 'Oui' / 'Non'
    et clique sur 'Oui' si présent. Retourne True si clique effectué.
    """
    try:
        # common selectors for the dialog content/button area
        # look for buttons with text 'Oui' or class matching 'btn-outline-primary' (as in sample)
        xpath_yes_text = "//button[normalize-space(.)='Oui' or contains(normalize-space(.),'Oui')]"
        css_yes_class = "button.btn.btn-13.btn-outline-primary"
        # try xpath first
        try:
            yes_btn = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.XPATH, xpath_yes_text)))
            try:
                driver.execute_script("arguments[0].scrollIntoView(true);", yes_btn)
                yes_btn.click()
            except Exception:
                driver.execute_script("arguments[0].click();", yes_btn)
            logging.info("Clicked modal 'Oui' button.")
            return True
        except Exception:
            # try class selector
            try:
                yes_btn = WebDriverWait(driver, 1).until(EC.element_to_be_clickable((By.CSS_SELECTOR, css_yes_class)))
                try:
                    driver.execute_script("arguments[0].scrollIntoView(true);", yes_btn)
                    yes_btn.click()
                except Exception:
                    driver.execute_script("arguments[0].click();", yes_btn)
                logging.info("Clicked modal 'Oui' button (by class).")
                return True
            except Exception:
                # try any button inside p-dialog with text Oui
                try:
                    dialog_yes = driver.find_element(By.XPATH, "//p-dialog//button[normalize-space(.)='Oui']")
                    driver.execute_script("arguments[0].click();", dialog_yes)
                    logging.info("Clicked modal 'Oui' (p-dialog).")
                    return True
                except Exception:
                    return False
    except Exception as e:
        logging.debug(f"handle_confirmation_modal_yes error: {e}")
        return False


# ---------- Candidatures monitoring ----------
def check_and_notify_candidatures(driver, wait):
    """
    Clique sur 'Mes candidatures' et scanne les candidatures présentes.
    Envoie un email si le statut d'une candidature a changé depuis le dernier run.
    Garde l'état dans candidature_statuses.json
    """
    statuses = load_candidature_statuses()
    changed = False
    sent_emails = 0

    # try to open Mes candidatures
    try:
        # find a link/button to 'Mes candidatures'
        try:
            cand_btn = driver.find_element(By.XPATH, "//*[contains(text(),'Mes candidatures') or contains(.,'Mes candidatures')]")
            try:
                driver.execute_script("arguments[0].scrollIntoView(true);", cand_btn)
                cand_btn.click()
            except Exception:
                driver.execute_script("arguments[0].click();", cand_btn)
        except Exception:
            # fallback: look for profile/dashboard links or direct route
            try:
                driver.get("https://al-in.fr/#/mes-candidatures")
            except Exception:
                pass

        # wait for candidature cards
        time.sleep(1.0)
        try:
            cards = driver.find_elements(By.CSS_SELECTOR, ".tdb-s-candidature, .info-candidatures, .tdb-s-candidature.col-12")
            if not cards:
                # try a broader selector
                cards = driver.find_elements(By.CSS_SELECTOR, ".info-candidatures, .tdb-s-candidature")
        except Exception:
            cards = []

        # parse each candidature
        for el in cards:
            try:
                # get identity: typ + loc + price
                try:
                    typ = el.find_element(By.CSS_SELECTOR, ".title, .tdb-s-title, .big").text.strip()
                except Exception:
                    typ = ""
                try:
                    loc = el.find_element(By.CSS_SELECTOR, ".title").text.strip()
                except Exception:
                    loc = ""
                # price & area may be inside the title; find price span
                try:
                    price_text = el.find_element(By.XPATH, ".//div[contains(@class,'title')]/span[contains(.,'€')]|.//div[contains(.,'€')]").text.strip()
                except Exception:
                    price_text = ""
                uid = f"{typ}|{price_text}"

                # Try to determine status:
                status = None
                try:
                    # Prefer the 'Statut de la demande' data span
                    status_span = el.find_element(By.XPATH, ".//*[contains(.,'Statut de la demande')]/following::span[1]")
                    status = status_span.text.strip()
                except Exception:
                    pass
                if not status:
                    # fallback: look for .data inside card
                    try:
                        data_spans = el.find_elements(By.CSS_SELECTOR, ".data, .text_picto_vert")
                        for ds in data_spans:
                            txt = ds.text.strip()
                            if txt:
                                # prefer short statuses like 'En attente', 'Validation', etc.
                                status = txt
                                break
                    except Exception:
                        status = None
                if not status:
                    # fallback: steps current
                    try:
                        step = el.find_element(By.CSS_SELECTOR, ".steps .current .step-title")
                        status = step.text.strip()
                    except Exception:
                        status = "unknown"

                # send email if new or changed
                prev = statuses.get(uid)
                if prev != status:
                    # update and send email
                    subject = f"BOTALIN - Candidature statut mis à jour: {status}"
                    body = f"Candidature: {uid}\nNouveau statut: {status}\nAncien statut: {prev}\n\nElement text snapshot:\n{(el.text[:1000] if el is not None else '')}"
                    send_email(subject, body)
                    statuses[uid] = status
                    changed = True
                    sent_emails += 1
            except Exception as e:
                logging.debug(f"Error parsing candidature card: {e}")
                continue

        if changed:
            save_candidature_statuses(statuses)
            logging.info(f"Notified {sent_emails} candidature status changes.")
        else:
            logging.info("No candidature status changes detected.")
    except Exception as e:
        logging.debug(f"Error while checking candidatures: {e}")
    return


# ---------- Robust apply flow ----------
def robust_click_apply_flow(driver, wait):
    """
    Attempts to click Apply button robustly, confirm, and fetch result text.
    Returns (applied_bool, result_text_or_reason)
    """
    # ensure banners/overlays closed before clicking
    close_overlays(driver)
    handle_cookie_banner(driver)

    for attempt in range(1, CLICK_RETRIES + 1):
        try:
            # try the main apply button selectors
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

            # try to bring it into view & click
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'}); window.scrollBy(0, -80);", apply_btn)
            except Exception:
                pass
            try:
                apply_btn.click()
            except ElementClickInterceptedException:
                # common cause: overlay or fixed footer; try to close overlays then JS click
                logging.debug("ElementClickInterceptedException on apply click; trying to close overlays and JS click.")
                close_overlays(driver)
                time.sleep(0.2)
                try:
                    driver.execute_script("arguments[0].click();", apply_btn)
                except Exception as e:
                    logging.debug(f"JS click also failed: {e}")
                    raise
            except Exception as e:
                # fallback JS click
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
            # attempt small scrolls to reveal button
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
        confirm_btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Confirmer')]")))
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
        ok_btn = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, "//button[normalize-space(.)='Ok' or contains(.,'OK') or contains(.,'Ok')]")))
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
        txt = WebDriverWait(driver, WAIT_TIMEOUT).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".text_picto_vert")))
        val = txt.text.strip()
        logging.info("Application result text found.")
        return True, val
    except Exception:
        logging.debug("No application result text found after apply.")
        return True, "applied_but_no_text"


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
        # typology must contain wanted type
        if WANTED_TYPOLOGY_KEY.upper() not in info.get("typ", "").upper():
            continue
        # area constraint
        if info.get("area") is None or info.get("area") < MIN_AREA_M2:
            continue
        if info.get("price") > MAX_PRICE:
            continue
        found.append((card, info))
    return found


def try_parse_rank_from_application_page(driver):
    """
    After opening application detail, attempt to parse user's rank (1-based).
    Tries multiple heuristics.
    Returns int rank if found, else None.
    """
    try:
        # look for text like "Position" and a nearby text containing index like "Vous êtes 2" or "Position : 2"
        elems = driver.find_elements(By.XPATH, "//*[contains(.,'Position') or contains(.,'rang') or contains(.,'Position ')]")
        for e in elems:
            txt = e.text or ""
            m = re.search(r"(\bposition\b[:\s]*|\brang\b[:\s]*|\bVous êtes\b[:\s]*)?(\d{1,3})", txt, re.IGNORECASE)
            if m:
                try:
                    return int(m.group(2))
                except:
                    continue
        # fallback: find any number in element with class 'position' or 'data' near 'Position'
        try:
            pos_el = driver.find_element(By.XPATH, "//*[contains(.,'Position')]/following::*[1]")
            m = re.search(r"(\d{1,3})", (pos_el.text or ""))
            if m:
                return int(m.group(1))
        except Exception:
            pass
    except Exception:
        pass
    return None


def cancel_candidature_if_not_first(driver, wait):
    """
    If on an application detail page, try to find user's rank and cancel if rank != 1.
    Returns True if cancelled, False if not cancelled or not applicable.
    """
    try:
        rank = try_parse_rank_from_application_page(driver)
        logging.info(f"Parsed rank: {rank}")
        if rank is None:
            logging.info("Could not determine rank — will not cancel automatically.")
            return False
        if rank == 1:
            logging.info("Rank is 1 — not cancelling application.")
            return False

        # find 'Annuler cette candidature' link/button inside page
        try:
            ann_btn = driver.find_element(By.XPATH, "//*[contains(.,'Annuler cette candidature') or contains(.,'Annuler la candidature') or contains(.,'Annuler')]")
            try:
                driver.execute_script("arguments[0].scrollIntoView(true);", ann_btn)
                ann_btn.click()
                logging.info("Clicked 'Annuler cette candidature' button.")
            except Exception:
                driver.execute_script("arguments[0].click();", ann_btn)
        except Exception:
            # fallback find by class tool-link hi-cross-round
            try:
                ann_btn = driver.find_element(By.CSS_SELECTOR, ".tool-link.hi-cross-round, .annuler a, .tool-link")
                driver.execute_script("arguments[0].click();", ann_btn)
                logging.info("Clicked 'Annuler' fallback button.")
            except Exception:
                logging.info("No cancel button found to click.")
                return False

        # handle confirmation modal (Oui / Non)
        try:
            handled = handle_confirmation_modal_yes(driver, timeout=4)
            if handled:
                logging.info("Confirmation modal accepted (Oui).")
            else:
                # as fallback, try to find generic OK/Confirm buttons
                try:
                    ok_btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'OK') or contains(.,'Ok') or contains(.,'Confirmer')]")))
                    try:
                        driver.execute_script("arguments[0].click();", ok_btn)
                        logging.info("Clicked generic OK/Confirmer after cancel.")
                    except Exception:
                        ok_btn.click()
                except Exception:
                    logging.debug("No confirmation modal or OK found after cancel.")
        except Exception as e:
            logging.debug(f"Error handling confirmation modal after cancel: {e}")

        send_email("BOTALIN - Candidature annulée", f"Candidature annulée automatiquement car rang={rank} != 1")
        return True
    except Exception as e:
        logging.debug(f"Error during cancel check: {e}")
        return False


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

        # After login, click on Mes candidatures and notify status changes
        try:
            check_and_notify_candidatures(driver, wait)
        except Exception as e:
            logging.debug(f"Candidature check failed: {e}")

        sections_priority = [
            "Communes demandées",
            "Communes limitrophes",
            "Autres communes du département"
        ]

        selected_card = None
        selected_info = None

        # check Communes demandées first, then others
        matches = find_matching_offers_in_section(driver, wait, seen, sections_priority[0])
        if matches:
            selected_card, selected_info = matches[0]
            logging.info("Selected first matching offer in 'Communes demandées'")
        else:
            for sect in sections_priority[1:]:
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

        # ensure overlays/cookies closed before attempting to apply
        close_overlays(driver)
        handle_cookie_banner(driver)

        applied, result = robust_click_apply_flow(driver, wait)
        if applied:
            # After applying, open "Mes candidatures" to check rank and cancel if rank !=1
            try:
                # small wait to ensure application is registered in UI
                time.sleep(1.2)
                # go to Mes candidatures
                try:
                    mc_btn = driver.find_element(By.XPATH, "//*[contains(text(),'Mes candidatures') or contains(.,'Mes candidatures')]")
                    try:
                        driver.execute_script("arguments[0].scrollIntoView(true);", mc_btn)
                        mc_btn.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", mc_btn)
                except Exception:
                    try:
                        driver.get("https://al-in.fr/#/mes-candidatures")
                    except Exception:
                        pass
                time.sleep(1.0)
                # try to open the application detail that matches the offer we just applied to
                # attempt to find a 'Voir l'annonce' button near a card that contains the same loc or typ or price
                cand_cards = driver.find_elements(By.CSS_SELECTOR, ".tdb-s-candidature, .info-candidatures, .tdb-s-candidature.col-12")
                opened = False
                for cc in cand_cards:
                    try:
                        text = cc.text or ""
                        if info.get("loc") and info["loc"] in text or info.get("price_text") and info["price_text"] in text:
                            # click Voir l'annonce if present
                            try:
                                voir_btn = cc.find_element(By.XPATH, ".//button[contains(.,'Voir l'annonce') or contains(.,'Voir lannonce') or contains(.,'Voir l')]")
                                try:
                                    driver.execute_script("arguments[0].scrollIntoView(true);", voir_btn)
                                    voir_btn.click()
                                    opened = True
                                    break
                                except Exception:
                                    driver.execute_script("arguments[0].click();", voir_btn)
                                    opened = True
                                    break
                            except Exception:
                                # click card itself
                                try:
                                    driver.execute_script("arguments[0].click();", cc)
                                    opened = True
                                    break
                                except Exception:
                                    continue
                    except Exception:
                        continue

                if not opened:
                    logging.debug("Could not open specific candidature detail; will attempt generic cancel check.")
                time.sleep(0.8)
                cancelled = cancel_candidature_if_not_first(driver, wait)
                if cancelled:
                    logging.info("Application was cancelled due to rank !=1.")
                    send_email("BOTALIN - Application cancelled", f"Application to {info} cancelled because rank != 1")
                else:
                    logging.info("Application left in place (either rank==1 or rank unknown).")

            except Exception as e:
                logging.debug(f"Post-apply candidature/rank check failed: {e}")

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
