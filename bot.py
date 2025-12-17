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
    StaleElementReferenceException
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------- GLOBAL CONFIG ----------
BASE_URL = "https://al-in.fr/#/connexion-demandeur"

WAIT_TIMEOUT = int(os.environ.get("WAIT_TIMEOUT", "15"))
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("1", "true", "yes")
MAX_RUN_SECONDS = int(os.environ.get("MAX_RUN_SECONDS", "300"))

# SMTP notification config
SENDER_EMAIL = os.environ.get("SENDER_EMAIL")
SENDER_PASS = os.environ.get("SENDER_PASS")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "fennane.mohamedamine@gmail.com, abdelhakim.fennane@sncf.fr")

# ---------- ACCOUNT DEFINITIONS ----------
ACCOUNTS = [
    {
        "name": "account1",
        "email_env": "EMAIL_1",
        "pass_env": "PASSWORD_1",
        "max_price": 800,
        "min_area": 45,
        "wanted_typ": "T2",
        "section_scope": ["Communes demandées"],
        "seen_file": "offers_seen_account1.json",
        "cand_file": "candidatures_status_account1.json",
    },
    {
        "name": "account2",
        "email_env": "EMAIL_2",
        "pass_env": "PASSWORD_2",
        "max_price": 900,
        "min_area": 0,
        "wanted_typ": "T4|T5",
        "section_scope": ["Communes demandées"],
        "seen_file": "offers_seen_account2.json",
        "cand_file": "candidatures_status_account2.json",
    }
]

# ---------- GENERAL SETTINGS ----------
CLICK_RETRIES = int(os.environ.get("CLICK_RETRIES", "5"))
SCROLL_PAUSE = float(os.environ.get("SCROLL_PAUSE", "0.8"))
CONTAINER_SCROLL_ATTEMPTS = int(os.environ.get("CONTAINER_SCROLL_ATTEMPTS", "20"))

# ---------- HELPERS: file / parsing / email ----------

def send_email(subject: str, body: str) -> bool:
    """Send notification email via Gmail TLS."""
    if not SENDER_EMAIL or not SENDER_PASS or not RECIPIENT_EMAIL:
        logging.warning("SMTP not configured (check secrets): skip send_email.")
        return False
    msg = EmailMessage()
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject
    msg.set_content(body)
    
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as s:
            s.ehlo()
            s.starttls()
            s.login(SENDER_EMAIL, SENDER_PASS)
            s.send_message(msg)
        logging.info(f"Notification email sent to recipients.")
        return True
    except Exception as e:
        logging.warning(f"SMTP send failed: {e}")
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
    except Exception as e:
        logging.warning(f"Failed to save {path}: {e}")

def parse_price(price_text: str):
    if not price_text:
        return None
    # Handle "583 € (531 € Hors charge)" -> extract 583
    # Clean string first
    cleaned = price_text.replace("\u00A0", " ").strip()
    m = re.search(r"^(\d+(?:[ ]\d+)*)", cleaned)
    if m:
        try:
            return int(m.group(1).replace(" ", ""))
        except:
            pass
    return None

def parse_area_from_typology(typ_text: str):
    if not typ_text:
        return None
    # Handle "24m2 | T1"
    cleaned = typ_text.replace("\u00A0", " ").replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*m", cleaned, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except:
            pass
    return None

# ---------- UI helpers ----------

def handle_cookie_banner(driver, timeout=3):
    selectors = [
        "//button[contains(., 'Accepter tous les cookies')]",
        "//button[contains(., 'Tout accepter')]",
        "//button[contains(., 'Accepter')]",
        "//button[contains(@class,'cookie')]",
    ]
    for s in selectors:
        try:
            el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.XPATH, s)))
            driver.execute_script("arguments[0].click();", el)
            logging.info("✅ Bannière cookies acceptée.")
            return True
        except Exception:
            continue
    return False

def close_overlays(driver):
    # Try generic close buttons often found in overlays
    try:
        overlays = driver.find_elements(By.CSS_SELECTOR, ".p-dialog-header-close-icon, button.close, .modal-close")
        for ov in overlays:
            if ov.is_displayed():
                driver.execute_script("arguments[0].click();", ov)
                time.sleep(0.2)
    except:
        pass

def progressive_scroll_container_to_bottom(driver, container, max_attempts=CONTAINER_SCROLL_ATTEMPTS, pause=SCROLL_PAUSE):
    # Scroll logic tailored for al-in lists
    try:
        last_height = driver.execute_script("return arguments[0].scrollHeight", container)
        for _ in range(max_attempts):
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", container)
            time.sleep(pause)
            new_height = driver.execute_script("return arguments[0].scrollHeight", container)
            if new_height == last_height:
                break
            last_height = new_height
    except Exception:
        pass

# ---------- Selenium driver init ----------

def init_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.add_argument(f"--user-data-dir=/tmp/chrome_user_data_{os.getpid()}")

    try:
        driver = webdriver.Chrome(options=options)
        return driver
    except Exception:
        driver_path = ChromeDriverManager().install()
        try:
            st = os.stat(driver_path)
            os.chmod(driver_path, st.st_mode | 0o111)
        except:
            pass
        service = Service(driver_path)
        driver = webdriver.Chrome(service=service, options=options)
        return driver

# ---------- Login flow (HTML based) ----------

def perform_login(driver, wait, email, password):
    driver.get(BASE_URL)
    handle_cookie_banner(driver, timeout=2)
    
    try:
        # Wait for form
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form.global-form")))
        
        # Input: formcontrolname="mail"
        mail_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="mail"]')))
        mail_input.clear()
        mail_input.send_keys(email)
        
        # Input: formcontrolname="password"
        pwd_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="password"]')))
        pwd_input.clear()
        pwd_input.send_keys(password)
        
        # Button: class contains 'btnCreate'
        btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.btnCreate")))
        driver.execute_script("arguments[0].scrollIntoView(true);", btn)
        time.sleep(0.5)
        
        try:
            btn.click()
        except:
            driver.execute_script("arguments[0].click();", btn)
            
        # Wait for redirection to dashboard (offers or candidatures)
        # We look for "offer-sections" or "Mes candidatures" text
        WebDriverWait(driver, 20).until(EC.any_of(
             EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-sections")),
             EC.presence_of_element_located((By.XPATH, "//a[contains(@href,'mes-candidatures')]")),
             EC.url_contains("offre")
        ))
        
        logging.info("Login successful.")
        handle_cookie_banner(driver, timeout=1)
        close_overlays(driver)
        return True

    except Exception as e:
        logging.error(f"Login failed: {e}")
        try:
            driver.save_screenshot("login_error.png")
        except:
            pass
        return False

def ensure_logged_in(driver, wait, email, password):
    # Quick check if already logged in
    try:
        driver.find_element(By.CSS_SELECTOR, ".offer-sections")
        return True
    except:
        pass
    
    logging.info("Not logged in or session expired. Logging in...")
    return perform_login(driver, wait, email, password)

# ---------- Offers Parsing (HTML based) ----------

def find_section_button(driver, name):
    # Sections: "Communes demandées", etc.
    # HTML: <div class="section"> Communes demandées <span>0</span></div>
    xpath = f"//div[contains(@class,'section') and contains(., '{name}')]"
    try:
        return WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, xpath)))
    except TimeoutException:
        return None

def extract_offer_from_card(card):
    # Based on HTML: app-offer-card -> offer-card-container
    info = {"uid": None, "price": None, "typ": None, "loc": None, "area": None, "raw_price": "", "img_src": None}
    try:
        # Image (used as UID)
        try:
            img_el = card.find_element(By.CSS_SELECTOR, ".offer-image img")
            info["img_src"] = img_el.get_attribute("src")
        except:
            pass
        
        # Price: <div class="price"> 583 € <span>(531 € Hors charge)</span></div>
        try:
            price_el = card.find_element(By.CSS_SELECTOR, ".price")
            info["raw_price"] = price_el.text.strip()
            info["price"] = parse_price(info["raw_price"])
        except:
            pass
            
        # Typology: <div class="typology"> 24m2 <span> | T1</span></div>
        try:
            typ_el = card.find_element(By.CSS_SELECTOR, ".typology")
            info["typ"] = typ_el.text.strip()
            info["area"] = parse_area_from_typology(info["typ"])
        except:
            pass
            
        # Location: <div class="location">Paris (75010)</div>
        try:
            loc_el = card.find_element(By.CSS_SELECTOR, ".location")
            info["loc"] = loc_el.text.strip()
        except:
            pass
        
        # UID strategy: image src OR composite key
        if info["img_src"] and "assets/img" not in info["img_src"]:
            info["uid"] = info["img_src"]
        else:
            # Fallback UID
            info["uid"] = f"{info['loc']}-{info['price']}-{info['typ']}"
            
    except StaleElementReferenceException:
        return None
        
    return info

# ---------- Apply Flow (HTML based) ----------

def apply_to_offer(driver, wait):
    """
    Handles the 3-step application:
    1. Click 'Je postule' (Page Detail)
    2. Click 'Confirmer' (Popup 1)
    3. Click 'Ok' (Popup 2)
    """
    # 1. Click "Je postule"
    try:
        # selector: .btn.btn-secondary.hi-check-round
        apply_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.btn.btn-secondary.hi-check-round")))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", apply_btn)
        time.sleep(0.5)
        apply_btn.click()
        logging.info("Clicked 'Je postule'")
    except Exception as e:
        logging.error(f"Failed to click 'Je postule': {e}")
        return False, "btn_not_found"

    # 2. Popup 1: "Confirmer"
    # HTML: <button class="btn btn-13 btn-secondary mt10 mb10"> Confirmer </button>
    try:
        confirm_btn = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(@class,'btn-13') and contains(.,'Confirmer')]"))
        )
        time.sleep(0.5)
        confirm_btn.click()
        logging.info("Clicked 'Confirmer' in popup")
    except Exception as e:
        logging.error(f"Failed to confirm application: {e}")
        return False, "confirm_failed"

    # 3. Popup 2: "Ok" (Success)
    # HTML: <button class="btn btn-13 btn-secondary mt10 mb10"> Ok </button>
    try:
        ok_btn = WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(@class,'btn-13') and contains(.,'Ok')]"))
        )
        time.sleep(0.5)
        ok_btn.click()
        logging.info("Clicked 'Ok' (Success)")
        return True, "applied"
    except Exception:
        # If OK button didn't appear, maybe it failed or auto-closed
        logging.warning("Success 'Ok' button not found, assuming applied or error.")
        return True, "applied_no_ok"

# ---------- Candidature Status & Cancellation ----------

def check_and_cancel_candidatures(driver, wait, account):
    """
    Goes to 'Mes candidatures', checks rank. 
    If rank > 10, cancels the application.
    Updates status file.
    """
    candidatures = load_json(account["cand_file"], {})
    
    # Navigate
    try:
        driver.get("https://al-in.fr/#/mes-candidatures")
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".tdb-s-candidature")))
        time.sleep(2)
    except:
        logging.warning("Could not load 'Mes candidatures'")
        return

    # Scan blocks
    blocks = driver.find_elements(By.CSS_SELECTOR, ".tdb-s-candidature")
    logging.info(f"Checking {len(blocks)} active candidatures...")
    
    for block in blocks:
        try:
            # Extract Title/UID info
            try:
                title_el = block.find_element(By.CSS_SELECTOR, ".title")
                title_text = title_el.text.strip() # ex: T1 | Paris (75) | 583 €
            except:
                title_text = "Unknown"

            # UID Key strategy (needs to match offer uid if possible, otherwise use title)
            uid_key = title_text 
            
            # Extract Rank
            # HTML: <div class="col... al-border"> <div class="text"> Position </div> <div ... text_picto_vert"> Il y a ...</div> </div>
            # Sometimes rank is explicitly "Position 3", sometimes just "Il y a X candidatures" (which implies rank unknown or > threshold)
            rank = 999 # Default to high if unknown
            
            try:
                # Try to find specific Position text if available
                # Text content usually: "Position" then next div has number
                # Or looking for "Position X"
                full_text = block.text
                m_pos = re.search(r"Position\s*(\d+)", full_text, re.IGNORECASE)
                if m_pos:
                    rank = int(m_pos.group(1))
                else:
                    # Logic: if text says "Il y a X candidatures", we might assume rank is bad? 
                    # For safety: if we can't find explicit "Position X", we DO NOT cancel to avoid errors.
                    pass
            except:
                pass

            # Extract Status
            status = "Inconnu"
            try:
                # Look for "Statut de la demande" block
                status_block = block.find_element(By.XPATH, ".//*[contains(text(),'Statut de la demande')]/following-sibling::div/span")
                status = status_block.text.strip()
            except:
                pass
            
            # Logic: Cancel if Rank > 10
            # Only if we are SURE about the rank
            if 0 < rank <= 10:
                logging.info(f"Keep: {title_text} (Rank {rank})")
            elif rank > 10 and rank != 999:
                logging.info(f"Cancelling: {title_text} (Rank {rank} > 10)")
                # Click "Annuler cette candidature"
                # Selector: a.tool-link.hi-cross-round
                try:
                    cancel_btn = block.find_element(By.CSS_SELECTOR, "a.tool-link.hi-cross-round")
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", cancel_btn)
                    cancel_btn.click()
                    
                    # Confirm cancel popup
                    # Usually standard primeNg confirm dialog
                    confirm_cancel = WebDriverWait(driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'OK') or contains(.,'Confirmer')]"))
                    )
                    confirm_cancel.click()
                    logging.info("Cancellation confirmed.")
                    status = "Annulée"
                except Exception as e:
                    logging.error(f"Failed to cancel: {e}")

            # Save status
            # Detect changes to notify
            old_data = candidatures.get(uid_key, {})
            if old_data.get("status") != status:
                send_email(f"BOTALIN Update ({account['name']})", f"Offre: {title_text}\nNouveau statut: {status}\nRang détecté: {rank}")
            
            candidatures[uid_key] = {
                "status": status,
                "rank": rank,
                "last_check": datetime.now().isoformat()
            }

        except StaleElementReferenceException:
            continue
        except Exception as e:
            logging.error(f"Error parsing candidature block: {e}")

    save_json(account["cand_file"], candidatures)

# ---------- MAIN PROCESS ----------

def process_account(account):
    email = os.environ.get(account["email_env"])
    password = os.environ.get(account["pass_env"])
    
    if not email or not password:
        logging.error(f"Skipping {account['name']}: Missing credentials.")
        return

    logging.info(f"--- Starting {account['name']} ---")
    seen = set(load_json(account["seen_file"], []))
    
    driver = init_driver()
    wait = WebDriverWait(driver, WAIT_TIMEOUT)
    
    try:
        if not ensure_logged_in(driver, wait, email, password):
            return

        # 1. Search for offers
        found_match = False
        target_offer = None
        
        # Go to offers page if not there
        if "offre" not in driver.current_url:
            driver.get("https://al-in.fr/#/recherche-logement")
            time.sleep(2)

        for section_name in account["section_scope"]:
            btn = find_section_button(driver, section_name)
            if not btn:
                continue
                
            # Click section
            try:
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(1.5)
            except:
                pass
            
            # Scroll container
            try:
                container = driver.find_element(By.CSS_SELECTOR, ".offer-list-container")
                progressive_scroll_container_to_bottom(driver, container, max_attempts=5)
            except:
                pass

            # Scan cards
            cards = driver.find_elements(By.CSS_SELECTOR, "app-offer-card")
            for card in cards:
                info = extract_offer_from_card(card)
                if not info or not info["uid"]: continue
                
                if info["uid"] in seen:
                    continue
                
                # Check criteria
                if info["price"] and info["price"] > account["max_price"]: continue
                if info["area"] and account["min_area"] > 0 and info["area"] < account["min_area"]: continue
                
                # Typology regex check
                if not re.search(account["wanted_typ"], info["typ"] or "", re.IGNORECASE):
                    continue
                
                # FOUND!
                target_offer = (card, info)
                found_match = True
                break
            
            if found_match:
                break
        
        # 2. Apply if found
        if found_match and target_offer:
            card_elem, info = target_offer
            logging.info(f"Applying to: {info}")
            
            # Click card to open detail
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", card_elem)
                card_elem.click()
            except:
                driver.execute_script("arguments[0].click();", card_elem)
            
            # Application flow
            success, reason = apply_to_offer(driver, wait)
            
            # Mark seen regardless of result to avoid loops
            seen.add(info["uid"])
            save_json(account["seen_file"], list(seen))
            
            if success:
                send_email(f"BOTALIN APPLIED ({account['name']})", f"Applied to {info}")
            else:
                logging.error(f"Failed apply: {reason}")
        
        else:
            logging.info("No new matching offers found.")

        # 3. Check & Cancel logic
        check_and_cancel_candidatures(driver, wait, account)

    except Exception as e:
        logging.error(f"Global error for {account['name']}: {e}")
        try:
            driver.save_screenshot(f"error_{account['name']}.png")
        except:
            pass
    finally:
        try:
            driver.quit()
        except:
            pass

def main():
    for account in ACCOUNTS:
        process_account(account)

if __name__ == "__main__":
    main()
