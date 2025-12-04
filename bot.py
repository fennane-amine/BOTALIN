# bot.py - watcher + apply + candidature status tracking + email notifications for 2 accounts
# IMPORTANT: Put real credentials into environment variables, NOT in the code.

import os
import time
import json
import re
import logging
from datetime import datetime
from email.message import EmailMessage
import smtplib

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import (
    TimeoutException,
    ElementClickInterceptedException,
    NoSuchElementException,
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------- GLOBAL CONFIG ----------
BASE_URL = "https://al-in.fr/#/connexion-demandeur"

WAIT_TIMEOUT = int(os.environ.get("WAIT_TIMEOUT", "12"))
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("1", "true", "yes")
MAX_RUN_SECONDS = int(os.environ.get("MAX_RUN_SECONDS", "300"))

# SMTP notification config
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "tesstedsgstsredr@gmail.com")
SENDER_PASS = os.environ.get("SENDER_PASS", "usdd czjy zsnq iael")
# UPDATED: Added second email recipient
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "fennane.mohamedamine@gmail.com, abdelhakim.fennane@sncf.fr")

# ---------- ACCOUNT DEFINITIONS ----------
ACCOUNTS = [
    {
        "name": "account1",
        "email_env": "mohamed-amine.fennane@epita.fr",
        "pass_env": "&9.Mnq.6F8'M/wm{",
        "max_price": 800,                           # Max 800€
        "min_area": 45,                             # Min 45m2
        "wanted_typ": "T2",                         # T2 only
        "section_scope": ["Communes demandées"],    # Only Communes demandées
        "seen_file": "offers_seen_account1.json",
        "cand_file": "candidatures_status_account1.json",
    },
    {
        "name": "account2",
        "email_env": "abdelhakim.fennane@sncf.fr",
        "pass_env": "Youssef2017*@",
        "max_price": 900,                           # Max 900€
        "min_area": 0,                              # No specific min area
        "wanted_typ": "T4|T5",                      # T4 or T5
        "section_scope": ["Communes demandées"],    # Only Communes demandées
        "seen_file": "offers_seen_account2.json",
        "cand_file": "candidatures_status_account2.json",
    }
]

# ---------- GENERAL SETTINGS ----------
CLICK_RETRIES = int(os.environ.get("CLICK_RETRIES", "5"))
SCROLL_PAUSE = float(os.environ.get("SCROLL_PAUSE", "0.6"))
CONTAINER_SCROLL_ATTEMPTS = int(os.environ.get("CONTAINER_SCROLL_ATTEMPTS", "30"))

# ---------- HELPERS: file / parsing / email ----------

def send_email(subject: str, body: str) -> bool:
    """Send notification email via Gmail TLS (try TLS then SSL)."""
    if not SENDER_EMAIL or not SENDER_PASS or not RECIPIENT_EMAIL:
        logging.warning("SMTP not configured: skip send_email.")
        return False
    msg = EmailMessage()
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject
    msg.set_content(body)
    # TLS
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
            s.ehlo()
            s.starttls()
            s.login(SENDER_EMAIL, SENDER_PASS)
            s.send_message(msg)
        logging.info(f"Notification email sent to {RECIPIENT_EMAIL} via smtp.gmail.com:587")
        return True
    except smtplib.SMTPAuthenticationError as e:
        logging.warning(f"SMTP auth failed (TLS): {e}")
    except Exception as e:
        logging.warning(f"SMTP TLS send failed: {e}")
    # SSL fallback
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
            s.login(SENDER_EMAIL, SENDER_PASS)
            s.send_message(msg)
        logging.info(f"Notification email sent to {RECIPIENT_EMAIL} via smtp.gmail.com:465")
        return True
    except Exception as e:
        logging.warning(f"SMTP SSL send failed: {e}")
    logging.error("All attempts to send notification email failed.")
    return False

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logging.info(f"Saved {path}")
    except Exception as e:
        logging.warning(f"Failed to save {path}: {e}")

def parse_price(price_text: str):
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

def parse_area_from_typology(typ_text: str):
    if not typ_text:
        return None
    m = re.search(r"(\d{1,3})\s*m", typ_text.replace("\u00A0", " "))
    if m:
        try:
            return int(m.group(1))
        except:
            return None
    return None

# ---------- UI helpers: cookies, overlays, clicks, scrolling ----------

def handle_cookie_banner(driver, timeout=3):
    selectors = [
        "//button[contains(., 'Accepter tous les cookies')]",
        "//button[contains(., 'Tout accepter')]",
        "//button[contains(., 'Accepter')]",
        "//button[contains(., 'Autoriser')]",
        "//button[contains(., 'Accept all')]",
        "//button[contains(@class,'cookie')]",
        "//a[contains(.,'Accepter')]",
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
    return False

def close_overlays(driver):
    try:
        # generic closes
        candidates = driver.find_elements(By.XPATH, "//button[contains(@class,'close') or contains(.,'Fermer') or contains(.,'Non') or contains(.,'Annuler') or contains(.,'OK')]")
        for el in candidates:
            try:
                if el.is_displayed():
                    driver.execute_script("arguments[0].click();", el)
                    time.sleep(0.15)
            except Exception:
                continue
    except Exception:
        pass

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
            break
        attempt += 1
    return

# ---------- Selenium driver init ----------

def init_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument(f"--user-data-dir=/tmp/chrome_user_data_{os.getpid()}")

    # try direct
    try:
        driver = webdriver.Chrome(options=options)
        logging.info("Selenium Manager initialized Chrome successfully.")
        return driver
    except Exception as e:
        logging.warning(f"Selenium Manager failed: {e}. Falling back to webdriver-manager.")

    driver_path = ChromeDriverManager().install()
    try:
        st = os.stat(driver_path)
        os.chmod(driver_path, st.st_mode | 0o111)
    except Exception:
        pass
    service = Service(driver_path)
    driver = webdriver.Chrome(service=service, options=options)
    return driver

# ---------- Login flow (robust) ----------

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

def perform_login(driver, wait, email, password):
    driver.get(BASE_URL)
    handle_cookie_banner(driver, timeout=2)
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form.global-form")))
        mail_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="mail"], input[type="email"], input[name="email"]')))
        pwd_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="password"], input[type="password"], input[name="password"]')))
        mail_input.clear()
        mail_input.send_keys(email)
        pwd_input.clear()
        pwd_input.send_keys(password)

        # robust click on login
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
            driver.save_screenshot("login_error.png")
            logging.error("Login button not found")
            return False

        # wait either offers or candidatures presence
        wait.until(EC.any_of(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-sections")),
            EC.presence_of_element_located((By.XPATH, "//a[contains(.,'Les offres')]")),
            EC.presence_of_element_located((By.XPATH, "//*[contains(text(),'Mes candidatures')]")),
        ), timeout=15)
        # ensure on offers page if possible
        try:
            offres_btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, "//a[contains(.,'Les offres')]")))
            try:
                driver.execute_script("arguments[0].click();", offres_btn)
            except:
                pass
        except:
            pass

        handle_cookie_banner(driver, timeout=1)
        close_overlays(driver)
        logging.info("Login successful (post-submit checks).")
        return True
    except Exception as e:
        try:
            driver.save_screenshot("login_error.png")
            logging.info("Saved screenshot login_error.png for debugging.")
        except:
            pass
        logging.error(f"Login failed: {e}")
        return False

def ensure_logged_in(driver, wait, email, password):
    if is_logged_in(driver):
        return True
    logging.info("Not logged in. Performing full login.")
    return perform_login(driver, wait, email, password)

# ---------- Offer helpers ----------

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

# ---------- Apply flow (robust) ----------

def robust_click_apply_flow(driver, wait):
    close_overlays(driver)
    handle_cookie_banner(driver)
    apply_selectors = [
        (By.XPATH, "//button[contains(.,'Je postule') or contains(.,'JE POSTULE') or contains(.,'Je postuler')]"),
        (By.CSS_SELECTOR, ".btn.btn-secondary.hi-check-round"),
        (By.XPATH, "//button[contains(.,'Postuler') or contains(.,'Postulez')]"),
    ]
    apply_btn = None
    for attempt in range(1, CLICK_RETRIES + 1):
        for sel in apply_selectors:
            try:
                apply_btn = wait.until(EC.element_to_be_clickable(sel))
                if apply_btn:
                    break
            except Exception:
                continue
        if not apply_btn:
            logging.debug(f"Attempt {attempt}: apply button not found -> scrolling")
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(0.2)
                driver.execute_script("window.scrollTo(0, 0);")
            except:
                pass
            time.sleep(0.6)
            continue
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'}); window.scrollBy(0,-80);", apply_btn)
        except:
            pass
        try:
            apply_btn.click()
        except ElementClickInterceptedException:
            close_overlays(driver)
            time.sleep(0.2)
            try:
                driver.execute_script("arguments[0].click();", apply_btn)
            except Exception:
                pass
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", apply_btn)
            except:
                pass
        logging.info("Clicked 'Je postule'")
        break
    else:
        logging.error("All attempts to click 'Je postule' failed.")
        return False, "apply_click_failed"

    # confirm
    try:
        confirm_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Confirmer')]")), timeout=WAIT_TIMEOUT)
        try:
            click_element(driver, confirm_btn)
            logging.info("Clicked 'Confirmer'")
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", confirm_btn)
            except:
                pass
    except Exception:
        logging.debug("No 'Confirmer' button found")

    # ok
    try:
        ok_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[normalize-space(.)='Ok' or contains(.,'OK') or contains(.,'Ok')]")), timeout=5)
        try:
            click_element(driver, ok_btn)
            logging.info("Clicked 'Ok'")
        except:
            try:
                driver.execute_script("arguments[0].click();", ok_btn)
            except:
                pass
    except Exception:
        logging.debug("No final OK button found")

    # result text
    try:
        txt = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".text_picto_vert")), timeout=WAIT_TIMEOUT)
        val = txt.text.strip()
        logging.info("Application result text found.")
        return True, val
    except Exception:
        logging.debug("No application result text found after apply.")
        return True, "applied_but_no_text"

# ---------- Mes candidatures helpers ----------

def goto_mes_candidatures(driver, wait):
    selectors = [
        (By.XPATH, "//a[contains(.,'Mes candidatures')]"),
        (By.XPATH, "//a[contains(.,'Mes candidatures') or contains(.,'Mes demandes')]"),
        (By.XPATH, "//a[contains(.,'Candidatures')]")
    ]
    for sel in selectors:
        try:
            el = WebDriverWait(driver, 3).until(EC.element_to_be_clickable(sel))
            try:
                el.click()
            except:
                driver.execute_script("arguments[0].click();", el)
            time.sleep(0.6)
            return True
        except:
            continue
    try:
        driver.get("https://al-in.fr/#/mes-candidatures")
        time.sleep(1.2)
        return True
    except:
        return False

def scan_mes_candidatures_page(driver):
    results = []
    try:
        blocks = driver.find_elements(By.CSS_SELECTOR, ".tdb-s-candidature, .info-candidatures")
        for b in blocks:
            try:
                title_el = b.find_element(By.CSS_SELECTOR, ".title")
                title_text = title_el.text.strip()
            except:
                title_text = b.text.strip()[:200]
            candid_count = None
            try:
                pos_div = b.find_element(By.XPATH, ".//*[contains(.,'candidature') or contains(.,'candidatures')]")
                mc = pos_div.text.strip()
                m = re.search(r"(\d{1,4})\s+candidature", mc.replace("\u00A0", " "))
                if m:
                    candid_count = int(m.group(1))
            except:
                candid_count = None
            statu = None
            try:
                statu_el = b.find_element(By.XPATH, ".//*[contains(.,'Statut de la demande')]/following::*[1]")
                statu = statu_el.text.strip()
            except:
                try:
                    statu_el2 = b.find_element(By.CSS_SELECTOR, ".data")
                    statu = statu_el2.text.strip()
                except:
                    statu = None
            rank = None
            try:
                txt = b.text
                m_rank = re.search(r"position\s+(\d{1,3})", txt, re.IGNORECASE)
                if m_rank:
                    rank = int(m_rank.group(1))
            except:
                pass
            uid = title_text
            try:
                img = b.find_element(By.CSS_SELECTOR, ".offer-image img")
                uid = img.get_attribute("src") or uid
            except:
                pass
            results.append({"uid": uid, "title_snapshot": title_text, "status": statu, "cand_count": candid_count, "rank": rank, "raw_text": b.text})
    except Exception:
        try:
            body = driver.find_element(By.TAG_NAME, "body").text
            results.append({"uid": "page_text_snapshot", "title_snapshot": body[:800], "status": None, "cand_count": None, "rank": None, "raw_text": body})
        except:
            pass
    return results

def cancel_candidature_if_rank_too_high(driver, wait, uid_key):
    try:
        blocks = driver.find_elements(By.CSS_SELECTOR, ".tdb-s-candidature, .info-candidatures")
        for b in blocks:
            try:
                title_el = b.find_element(By.CSS_SELECTOR, ".title")
                title_text = title_el.text.strip()
            except:
                title_text = b.text.strip()[:200]
            matched = False
            try:
                img = b.find_element(By.CSS_SELECTOR, ".offer-image img")
                if img and uid_key in (img.get_attribute("src") or ""):
                    matched = True
            except:
                pass
            if not matched and uid_key in title_text:
                matched = True
            if not matched:
                continue
            rank = None
            try:
                txt = b.text
                m_rank = re.search(r"position\s+(\d{1,3})", txt, re.IGNORECASE)
                if m_rank:
                    rank = int(m_rank.group(1))
            except:
                pass
            if rank is None:
                logging.debug("No rank found; not cancelling.")
                return False
            
            # UPDATED LOGIC: Cancel if rank > 10
            if rank > 10:
                try:
                    cancel_el = b.find_element(By.XPATH, ".//a[contains(.,'Annuler') or contains(.,'Annuler cette candidature')]")
                    try:
                        cancel_el.click()
                    except:
                        driver.execute_script("arguments[0].click();", cancel_el)
                    time.sleep(0.6)
                except:
                    logging.warning("Cancel element not found")
                    return False
                # confirm dialog
                try:
                    yes_btn = WebDriverWait(driver, 4).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Oui') or contains(.,'Confirmer') or contains(.,'OK')]")))
                    try:
                        yes_btn.click()
                    except:
                        driver.execute_script("arguments[0].click();", yes_btn)
                    logging.info(f"Cancelled candidature for uid_key={uid_key} because rank={rank} > 10")
                    return True
                except:
                    logging.warning("Could not confirm cancellation pop-in.")
                    return False
            else:
                logging.info(f"Rank is {rank} (<= 10) -> do not cancel.")
                return False
    except Exception:
        pass
    return False

# ---------- Finding matching offers ----------

def find_matching_offers_in_section(driver, wait, seen, section_name, criteria):
    found = []
    btn = find_section_button(driver, section_name)
    if not btn:
        logging.debug(f"Section '{section_name}' not found.")
        return found
    click_element(driver, btn)
    time.sleep(0.6)
    try:
        container = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-list-container")))
        progressive_scroll_container_to_bottom(driver, container, max_attempts=8, pause=0.35)
    except:
        pass
    time.sleep(0.3)
    cards = get_offer_cards_in_current_section(driver)
    logging.info(f"Found {len(cards)} cards in '{section_name}'")
    for card in cards:
        info = extract_offer_info(card)
        uid = info.get("uid")
        if not uid or uid in seen:
            continue
        if info.get("price") is None:
            continue
        # typology match (regex)
        typ = info.get("typ","").upper()
        if not re.search(criteria["wanted_typ"].upper(), typ):
            continue
        if info.get("price") > criteria["max_price"]:
            continue
        area = info.get("area")
        if criteria.get("min_area") is not None:
            if area is None or area < criteria["min_area"]:
                continue
        found.append((card, info))
    return found

# ---------- MAIN FLOW per account ----------

def process_account(account):
    # read account credentials from env
    email = os.environ.get(account["email_env"])
    password = os.environ.get(account["pass_env"])
    if not email or not password:
        logging.error(f"Credentials for {account['name']} not provided in env vars. Skipping.")
        return

    seen = set(load_json(account["seen_file"], []))
    candidatures = load_json(account["cand_file"], {})  # uid_key -> {status, rank, cand_count, last_notified}

    driver = init_driver()
    wait = WebDriverWait(driver, WAIT_TIMEOUT)

    try:
        if not ensure_logged_in(driver, wait, email, password):
            logging.error(f"Authentication failed for {account['name']}; stopping account run.")
            send_email(f"BOTALIN - Login failed ({account['name']})", f"The bot could not log in for account {account['name']}.")
            driver.quit()
            return

        # prepare UI
        handle_cookie_banner(driver)
        close_overlays(driver)
        time.sleep(0.4)

        # priority sections from account config
        selected_card = None
        selected_info = None
        for sect in account["section_scope"]:
            matches = find_matching_offers_in_section(driver, wait, seen, sect, account)
            if matches:
                selected_card, selected_info = matches[0]
                logging.info(f"[{account['name']}] Selected first matching offer in '{sect}'")
                break

        if not selected_card:
            logging.info(f"[{account['name']}] No matching new offers found this run.")
            driver.quit()
            return

        info = selected_info
        uid = info["uid"]
        logging.info(f"[{account['name']}] Applying to: {info}")

        # open detail
        try:
            try:
                img_el = selected_card.find_element(By.CSS_SELECTOR, ".offer-image img")
                click_element(driver, img_el)
            except Exception:
                click_element(driver, selected_card)
            time.sleep(0.6)
        except Exception as e:
            logging.warning(f"[{account['name']}] Could not open offer detail: {e}")
            send_email(f"BOTALIN - Open offer failed ({account['name']})", f"Failed to open offer detail: {info}\nException: {e}")
            seen.add(uid)
            save_json(account["seen_file"], list(seen))
            driver.quit()
            return

        applied, result = robust_click_apply_flow(driver, wait)
        logging.info(f"[{account['name']}] Apply result: {applied}, {result}")

        # If applied but no confirmation text, check mes candidatures
        if applied and result == "applied_but_no_text":
            if goto_mes_candidatures(driver, wait):
                cand_list = scan_mes_candidatures_page(driver)
                matched_cand = None
                for c in cand_list:
                    if info['uid'] in (c.get("uid") or "") or (info['loc'] in (c.get("title_snapshot") or "") and str(info['price']) in (c.get("title_snapshot") or "")):
                        matched_cand = c
                        break
                if matched_cand:
                    result = matched_cand.get("status") or result
                    rank = matched_cand.get("rank")
                    
                    # cancellation rule
                    if rank is not None and rank > 10:
                        cancelled = cancel_candidature_if_rank_too_high(driver, wait, matched_cand.get("uid") or matched_cand.get("title_snapshot"))
                        if cancelled:
                            seen.add(uid)
                            save_json(account["seen_file"], list(seen))
                            driver.quit()
                            return

        # Post-process
        if applied:
            seen.add(uid)
            save_json(account["seen_file"], list(seen))

            # update candidatures statuses and notify only on change
            if goto_mes_candidatures(driver, wait):
                cand_list2 = scan_mes_candidatures_page(driver)
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
                    old = candidatures.get(uid_key)
                    old_status = old.get("status") if old else None
                    if new_status != old_status:
                        # store and notify once
                        candidatures[uid_key] = {"status": new_status, "rank": rank, "cand_count": cand_count, "last_notified": datetime.utcnow().isoformat()}
                        save_json(account["cand_file"], candidatures)
                        subject = f"BOTALIN - Candidature statut mis à jour ({account['name']}): {new_status or 'unknown'}"
                        body = f"Candidature: {matched.get('title_snapshot')}\n\nText snapshot:\n{matched.get('raw_text')}\n\nNouveau statut: {new_status}\nAncien statut: {old_status}\nRang: {rank}\nNombre de candidatures: {cand_count}"
                        send_email(subject, body)
                        logging.info(f"[{account['name']}] Sent candidature status change email for uid={uid_key} status={new_status}")
                    else:
                        logging.info(f"[{account['name']}] No change in candidature status -> no notification.")
                else:
                    logging.info(f"[{account['name']}] Applied but no matching candidature found in 'Mes candidatures'.")
            else:
                logging.debug(f"[{account['name']}] Could not navigate to 'Mes candidatures' after applying.")
        else:
            seen.add(uid)
            save_json(account["seen_file"], list(seen))
            logging.error(f"[{account['name']}] Apply failed: {result}")
            send_email(f"BOTALIN - Apply click failed ({account['name']})", f"Failed to apply for offer: {info}\nReason: {result}")

    except Exception as e:
        try:
            driver.save_screenshot(f"unhandled_{account['name']}.png")
        except:
            pass
        logging.error(f"[{account['name']}] Unhandled exception: {e}")
        send_email(f"BOTALIN - Unhandled error ({account['name']})", f"Unhandled exception: {e}")
    finally:
        try:
            driver.quit()
        except:
            pass

# ---------- ENTRY POINT ----------

def main():
    for account in ACCOUNTS:
        process_account(account)

if __name__ == "__main__":
    main()
