# bot.py - watcher + apply + candidature status tracking + email notifications
# Full merged version requested by user.
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

# site login (prefer env)
EMAIL = os.environ.get("EMAIL") or "mohamed-amine.fennane@epita.fr"
PASSWORD = os.environ.get("PASSWORD") or "&9.Mnq.6F8'M/wm{"

# SMTP / notification (prefer env)
SENDER_EMAIL = os.environ.get("SENDER_EMAIL") or "tesstedsgstsredr@gmail.com"
SENDER_PASS = os.environ.get("SENDER_PASS") or "usdd czjy zsnq iael"  # prefer env var
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL") or "fennane.mohamedamine@gmail.com"

# Timing & runtime
WAIT_TIMEOUT = int(os.environ.get("WAIT_TIMEOUT", 12))
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("1", "true", "yes")
SEEN_FILE = "offers_seen.json"
CANDIDATURES_FILE = "candidatures_status.json"
MAX_RUN_SECONDS = int(os.environ.get("MAX_RUN_SECONDS", 300))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 15))

# Matching criteria
MAX_PRICE = int(os.environ.get("MAX_PRICE", 600))  # user requested 600 / later said 600 -> keep configurable
MIN_AREA = int(os.environ.get("MIN_AREA", 40))    # user requested min 40 m2
WANTED_TYPOLOGY_KEY = os.environ.get("WANTED_TYPOLOGY_KEY", "T2")

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


# ---------- Helpers (files, parsing) ----------
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


def load_candidature_statuses():
    try:
        with open(CANDIDATURES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}  # uid -> {status: str, rank: int/None, cand_count: int/None, last_notified: iso}


def save_candidature_statuses(data):
    try:
        with open(CANDIDATURES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logging.info(f"Saved {len(data)} candidature statuses to {CANDIDATURES_FILE}")
    except Exception as e:
        logging.warning(f"Failed to save candidature statuses: {e}")


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


def parse_area_from_typology(typ_text):
    # expected formats: "45m2 | T2" or " 40m2 | T2"
    if not typ_text:
        return None
    m = re.search(r"(\d{1,3})\s*m(?:2|²)?", typ_text.replace("\u00A0", " "))
    if m:
        try:
            return int(m.group(1))
        except:
            return None
    return None


# ---------- UI helpers (cookies / overlays) ----------
def handle_cookie_banner(driver, timeout=5):
    """
    Try to close cookie banner/popins with a list of probable selectors.
    """
    selectors = [
        "//button[contains(., 'Accepter tous les cookies')]",
        "//button[contains(., 'Tout accepter')]",
        "//button[contains(., 'Accepter')]",
        "//button[contains(., 'Autoriser')]",
        "//button[contains(., 'Accept all')]",
        "//button[contains(@class,'cookie')]",
        "//a[contains(.,'Accepter')]",
        "//button[contains(.,'Refuser') and contains(.,'cookies')]"  # some sites
    ]
    for s in selectors:
        try:
            el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.XPATH, s)))
            try:
                el.click()
            except Exception:
                driver.execute_script("arguments[0].click();", el)
            logging.info("✅ Bannière cookies acceptée automatiquement.")
            return True
        except Exception:
            continue
    logging.debug("No cookie banner detected or couldn't close it.")
    return False


def close_overlays(driver):
    """
    Try to close typical overlays / modals to avoid click interception.
    """
    try:
        # try generic close buttons
        candidates = driver.find_elements(By.XPATH, "//button[contains(@class,'close') or contains(.,'Fermer') or contains(.,'Close') or contains(.,'Non') or contains(.,'Annuler')]")
        for el in candidates:
            try:
                if el.is_displayed():
                    driver.execute_script("arguments[0].click();", el)
                    time.sleep(0.15)
            except Exception:
                continue
    except Exception:
        pass


# ---------- Offer UI helpers ----------
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
    area = parse_area_from_typology(typ)
    uid = img_src or f"{loc}|{price_text}|{typ}"
    price = parse_price(price_text)
    return {"uid": uid, "img_src": img_src, "price_text": price_text, "price": price, "typ": typ, "loc": loc, "area": area}


# ---------- scrolling utilities ----------
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
        # wait login form - flexible selectors
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form.global-form")))
        mail_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="mail"], input[type="email"], input[name="email"]')))
        pwd_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="password"], input[type="password"], input[name="password"]')))
        mail_input.clear()
        mail_input.send_keys(EMAIL)
        pwd_input.clear()
        pwd_input.send_keys(PASSWORD)

        # Try robust set of login button selectors
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

        # Wait for presence of offers or candidatures link to confirm login
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

    apply_selectors = [
        (By.XPATH, "//button[contains(.,'Je postule') or contains(.,'JE POSTULE') or contains(.,'Je postuler')]"),
        (By.CSS_SELECTOR, ".btn.btn-secondary.hi-check-round"),
        (By.XPATH, "//button[contains(.,'Postuler') or contains(.,'Postulez')]"),
    ]

    for attempt in range(1, CLICK_RETRIES + 1):
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
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(0.2)
                driver.execute_script("window.scrollTo(0, 0);")
            except Exception:
                pass
            time.sleep(0.6)
            continue

        # try to bring it into view & click
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'}); window.scrollBy(0,-80);", apply_btn)
        except Exception:
            pass
        try:
            apply_btn.click()
        except ElementClickInterceptedException:
            logging.debug("ElementClickInterceptedException on apply click; trying to close overlays and JS click.")
            close_overlays(driver)
            time.sleep(0.2)
            try:
                driver.execute_script("arguments[0].click();", apply_btn)
            except Exception as e:
                logging.debug(f"JS click also failed: {e}")
                # try a small scroll and retry
                try:
                    driver.execute_script("window.scrollBy(0,120);")
                    time.sleep(0.2)
                    driver.execute_script("arguments[0].click();", apply_btn)
                except Exception:
                    logging.debug("apply click retries failed after interception.")
                    # fallback to continue loop to retry later
                    time.sleep(0.6)
                    continue
        except Exception as e:
            logging.debug(f"apply normal click failed: {e}; trying JS click")
            try:
                driver.execute_script("arguments[0].click();", apply_btn)
            except Exception as e2:
                logging.debug(f"JS click failed too: {e2}")
                time.sleep(0.6)
                continue

        logging.info("Clicked 'Je postule'")
        break
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


# ---------- Mes candidatures helpers ----------
def goto_mes_candidatures(driver, wait):
    # try several selectors
    selectors = [
        (By.XPATH, "//a[contains(.,'Mes candidatures')]"),
        (By.XPATH, "//a[contains(.,'Mes candidatures') or contains(.,'Mes demandes')]"),
        (By.XPATH, "//a[contains(.,'Mon compte')]/following::a[contains(.,'Mes candidatures')]"),
        (By.XPATH, "//a[contains(.,'Candidatures')]")
    ]
    for sel in selectors:
        try:
            el = WebDriverWait(driver, 3).until(EC.element_to_be_clickable(sel))
            try:
                el.click()
            except Exception:
                driver.execute_script("arguments[0].click();", el)
            # wait for candidatures list
            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".tdb-s-candidature, .tdb-s-candidature, .info-candidatures")), timeout=6)
            except Exception:
                pass
            time.sleep(0.6)
            return True
        except Exception:
            continue
    # fallback: try direct URL fragment if known (some pages use #/mes-candidatures)
    try:
        driver.get("https://al-in.fr/#/mes-candidatures")
        time.sleep(1.2)
        return True
    except Exception:
        return False


def scan_mes_candidatures_page(driver):
    """
    Parse all candidature blocks from the candidatures page and return a list of dicts:
    {uid, title_text_snapshot, statu_text, candidatures_count (int or None), rank (int or None)}
    """
    results = []
    try:
        blocks = driver.find_elements(By.CSS_SELECTOR, ".tdb-s-candidature, .info-candidatures, .tdb-s-candidature")
        for b in blocks:
            try:
                title_el = b.find_element(By.CSS_SELECTOR, ".title")
                title_text = title_el.text.strip()
            except Exception:
                title_text = b.text.strip()[:200]
            # find position/candidature count
            candid_count = None
            try:
                pos_div = b.find_element(By.XPATH, ".//div[contains(@class,'text_picto_vert') or contains(.,'candidatures déposées')]")
                candid_text = pos_div.text.strip()
                m = re.search(r"(\d{1,4})\s+candidature", candid_text.replace("\u00A0", " "))
                if m:
                    candid_count = int(m.group(1))
            except Exception:
                # maybe there is a dedicated "Position" box
                try:
                    pos_div2 = b.find_element(By.XPATH, ".//*[contains(text(),'Position')]/following::*[1]")
                    candid_text = pos_div2.text.strip()
                    m = re.search(r"(\d{1,4})", candid_text)
                    if m:
                        candid_count = int(m.group(1))
                except Exception:
                    candid_count = None
            # status field
            statu = None
            try:
                statu_el = b.find_element(By.XPATH, ".//*[contains(.,'Statut de la demande')]/following::*[1]")
                statu = statu_el.text.strip()
            except Exception:
                # fallback: look for any .data red element near "Statut"
                try:
                    statu_el2 = b.find_element(By.CSS_SELECTOR, ".data")
                    statu = statu_el2.text.strip()
                except Exception:
                    statu = None
            # rank: try to find if there is a "rang" or number near "Position" or similar
            rank = None
            try:
                # sometimes there's text like "Vous êtes en position N"
                txt = b.text
                m_rank = re.search(r"position\s+(\d{1,3})", txt, re.IGNORECASE)
                if m_rank:
                    rank = int(m_rank.group(1))
            except Exception:
                pass

            # derive uid: use title_text as uid key (unique enough) or image src if present
            uid = title_text
            try:
                img = b.find_element(By.CSS_SELECTOR, ".offer-card-container .offer-image img")
                uid = img.get_attribute("src") or uid
            except Exception:
                pass

            results.append({"uid": uid, "title_snapshot": title_text, "status": statu, "cand_count": candid_count, "rank": rank})
    except Exception:
        # fallback: try to parse main page as text
        try:
            body = driver.find_element(By.TAG_NAME, "body").text
            results.append({"uid": "page_text_snapshot", "title_snapshot": body[:800], "status": None, "cand_count": None, "rank": None})
        except Exception:
            pass
    return results


def cancel_candidature_if_not_rank1(driver, wait, uid_key):
    """
    On mes candidatures page, find candidature matching uid_key and if rank exists and !=1, cancel it.
    Returns True if cancelled, False otherwise.
    """
    try:
        blocks = driver.find_elements(By.CSS_SELECTOR, ".tdb-s-candidature, .info-candidatures")
        for b in blocks:
            try:
                title_el = b.find_element(By.CSS_SELECTOR, ".title")
                title_text = title_el.text.strip()
            except Exception:
                title_text = b.text.strip()[:200]
            # match by uid_key presence in title or image src
            matched = False
            try:
                img = b.find_element(By.CSS_SELECTOR, ".offer-image img")
                if img and uid_key in (img.get_attribute("src") or ""):
                    matched = True
            except Exception:
                pass
            if not matched and uid_key in title_text:
                matched = True
            if not matched:
                continue

            # find rank
            rank = None
            try:
                txt = b.text
                m_rank = re.search(r"position\s+(\d{1,3})", txt, re.IGNORECASE)
                if m_rank:
                    rank = int(m_rank.group(1))
            except Exception:
                pass

            if rank is None:
                logging.debug("No rank found for matched candidature; will not auto-cancel.")
                return False

            if rank != 1:
                # click cancel: look for 'Annuler cette candidature' link/button
                try:
                    cancel_el = b.find_element(By.XPATH, ".//a[contains(.,'Annuler') or contains(.,'Annuler cette candidature') or contains(.,'Annuler la candidature')]")
                    try:
                        cancel_el.click()
                    except Exception:
                        driver.execute_script("arguments[0].click();", cancel_el)
                    time.sleep(0.4)
                    # popin: click 'Oui'
                    try:
                        yes_btn = WebDriverWait(driver, 4).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Oui') or contains(.,'Confirmer') or contains(.,'OK')]")))
                        try:
                            yes_btn.click()
                        except Exception:
                            driver.execute_script("arguments[0].click();", yes_btn)
                        logging.info(f"Cancelled candidature for uid_key={uid_key} because rank={rank} != 1")
                        return True
                    except Exception:
                        # Try to click dialog close or confirm differently
                        try:
                            confirm = driver.find_element(By.XPATH, "//button[contains(.,'Oui') or contains(.,'OK') or contains(.,'Confirmer')]")
                            driver.execute_script("arguments[0].click();", confirm)
                            logging.info(f"Cancelled candidature for uid_key={uid_key} (fallback confirmation).")
                            return True
                        except Exception:
                            logging.warning("Could not confirm cancellation pop-in.")
                            return False
                except Exception:
                    logging.warning("Cancel link/button not found for candidature despite rank!=1.")
                    return False
            else:
                logging.info("Rank is 1 -> do not cancel.")
                return False
    except Exception:
        pass
    return False


# ---------- Finding matching offers ----------
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
        progressive_scroll_container_to_bottom(driver, container, max_attempts=8, pause=0.4)  # smaller efforts for speed
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
        # area check
        area = info.get("area")
        if area is None or area < MIN_AREA:
            continue
        found.append((card, info))
    return found


# ---------- Main ----------
def main():
    logging.info("Starting bot")
    # Load persistent data
    seen = load_seen()
    candidatures = load_candidature_statuses()
    logging.info(f"Loaded {len(seen)} seen offers (from {SEEN_FILE} if present)")
    logging.info(f"Loaded {len(candidatures)} saved candidature statuses (from {CANDIDATURES_FILE} if present)")

    # Window guard: operate during certain hours? (not enforced here, but you can add)
    start_time = datetime.utcnow()
    end_time = start_time + timedelta(seconds=MAX_RUN_SECONDS)

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

        # Ensure cookies/overlays closed and we're on offers page
        handle_cookie_banner(driver)
        close_overlays(driver)
        time.sleep(0.4)

        sections_priority = [
            "Communes demandées",
            "Communes limitrophes",
            "Autres communes du département"
        ]

        selected_card = None
        selected_info = None

        # first check priority sections for matches
        for sect in sections_priority:
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
            time.sleep(0.6)
        except Exception as e:
            logging.warning(f"Could not open offer detail: {e}")
            send_email("BOTALIN - Open offer failed", f"Failed to open offer detail: {info}\nException: {e}")
            seen.add(uid)
            save_seen(seen)
            return

        # attempt apply
        applied, result = robust_click_apply_flow(driver, wait)
        logging.info(f"Apply action returned: applied={applied}, result={result}")

        # if applied but no text, check Mes candidatures to confirm
        if applied and result == "applied_but_no_text":
            logging.info("No confirmation text after apply; checking 'Mes candidatures' to confirm deposit.")
            if goto_mes_candidatures(driver, wait):
                cand_list = scan_mes_candidatures_page(driver)
                # try to find entry by uid or by title containing loc+price
                matched_cand = None
                for c in cand_list:
                    if info['uid'] in (c.get("uid") or "") or (info['loc'] in (c.get("title_snapshot") or "") and str(info['price']) in (c.get("title_snapshot") or "")):
                        matched_cand = c
                        break
                if matched_cand:
                    result = matched_cand.get("status") or "applied_but_no_status"
                    # get rank and cand_count
                    rank = matched_cand.get("rank")
                    cand_count = matched_cand.get("cand_count")
                    logging.info(f"Found candidature on Mes candidatures: status={result}, rank={rank}, cand_count={cand_count}")
                    # if rank exists and !=1, cancel candidature
                    if rank is not None and rank != 1:
                        cancelled = cancel_candidature_if_not_rank1(driver, wait, matched_cand.get("uid") or matched_cand.get("title_snapshot"))
                        if cancelled:
                            logging.info("Candidature cancelled because rank != 1")
                            # mark as seen and return (no notification about successful apply)
                            seen.add(uid)
                            save_seen(seen)
                            return
                else:
                    logging.info("Could not find matching candidature in 'Mes candidatures' after applying.")
            else:
                logging.debug("Could not navigate to 'Mes candidatures' to check application.")

        # Post-process applied or apply failure
        if applied:
            # Add to seen and save
            seen.add(uid)
            save_seen(seen)

            # after application go to "Mes candidatures" and update statuses to send notifications only on change
            if goto_mes_candidatures(driver, wait):
                cand_list2 = scan_mes_candidatures_page(driver)
                # find the one matching
                matched = None
                for c in cand_list2:
                    if info['uid'] in (c.get("uid") or "") or (info['loc'] in (c.get("title_snapshot") or "") and str(info['price']) in (c.get("title_snapshot") or "")):
                        matched = c
                        break

                if matched:
                    uid_key = matched.get("uid") or matched.get("title_snapshot")
                    new_status = matched.get("status")
                    cand_count = matched.get("cand_count")
                    rank = matched.get("rank")
                    # fetch old entry
                    old = candidatures.get(uid_key)
                    old_status = old.get("status") if old else None
                    # Only send email if status changed (not None -> new value differs)
                    if new_status != old_status:
                        # update stored
                        candidatures[uid_key] = {"status": new_status, "rank": rank, "cand_count": cand_count, "last_notified": datetime.utcnow().isoformat()}
                        save_candidature_statuses(candidatures)
                        subject = f"BOTALIN - Candidature statut mis à jour: {new_status or 'unknown'}"
                        body = f"Candidature: {matched.get('title_snapshot')}\n\nElement text snapshot:\n{matched.get('title_snapshot')}\n\nNouveau statut: {new_status}\nAncien statut: {old_status}\nRang: {rank}\nNombre de candidatures: {cand_count}"
                        ok = send_email(subject, body)
                        if ok:
                            logging.info(f"Sent candidature status change email for uid={uid_key} status={new_status}")
                        else:
                            logging.warning("Failed to send candidature status change email.")
                    else:
                        logging.info("No change in candidature status -> no notification.")
                else:
                    logging.info("Applied but no candidature found in 'Mes candidatures' to update statuses.")
            else:
                logging.debug("Could not navigate to 'Mes candidatures' after applying.")
            logging.info("Applied (or attempted) and processed notification.")
        else:
            # apply failed entirely
            seen.add(uid)
            save_seen(seen)
            logging.error(f"Failed to apply for offer: {info}. Reason: {result}")
            send_email("BOTALIN - Apply click failed", f"Failed to click apply for offer: {info}\nReason: {result}")

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
