# bot.py - watcher + apply + ranking check + email notifications
# IMPORTANT: Put real credentials into environment variables.

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
    StaleElementReferenceException
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------- GLOBAL CONFIG ----------
BASE_URL = "https://al-in.fr/#/connexion-demandeur"

# Timers optimized
WAIT_TIMEOUT = int(os.environ.get("WAIT_TIMEOUT", "25"))
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
        "min_price": 700,
        "max_price": 850,
        "min_area": 45,
        "wanted_typ": "T2",
        "section_scope": ["Communes demandées", "Communes limitrophes"],
        "seen_file": "offers_seen_account1.json",
        "cand_file": "candidatures_status_account1.json",
    },
    {
        "name": "account2",
        "email_env": "EMAIL_2",
        "pass_env": "PASSWORD_2",
        "min_price": 0,
        "max_price": 900,
        "min_area": 0,
        "wanted_typ": "T4|T5",
        "section_scope": ["Communes demandées"],
        "seen_file": "offers_seen_account2.json",
        "cand_file": "candidatures_status_account2.json",
    }
]

# ---------- HELPERS ----------

def send_email(subject: str, body: str) -> bool:
    if not SENDER_EMAIL or not SENDER_PASS or not RECIPIENT_EMAIL:
        return False
    msg = EmailMessage()
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as s:
            s.ehlo()
            s.starttls()
            s.login(SENDER_EMAIL, SENDER_PASS)
            s.send_message(msg)
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
    except Exception:
        pass

def parse_price(price_text: str):
    if not price_text: return None
    cleaned = price_text.replace("\u00A0", " ").strip()
    m = re.search(r"^(\d+(?:[ ]\d+)*)", cleaned)
    if m:
        try: return int(m.group(1).replace(" ", ""))
        except: pass
    return None

def parse_area_from_typology(typ_text: str):
    if not typ_text: return None
    cleaned = typ_text.replace("\u00A0", " ").replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*m", cleaned, re.IGNORECASE)
    if m:
        try: return float(m.group(1))
        except: pass
    return None

# ---------- UI HELPERS ----------

def handle_cookie_banner(driver, timeout=3):
    selectors = [
        "button[data-cookiefirst-action='accept']",
        "//button[contains(., 'Accepter tous les cookies')]",
    ]
    for s in selectors:
        try:
            if s.startswith("//"):
                el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.XPATH, s)))
            else:
                el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.CSS_SELECTOR, s)))
            try: el.click()
            except: driver.execute_script("arguments[0].click();", el)
            logging.info("✅ Bannière cookies acceptée.")
            time.sleep(0.5)
            return True
        except Exception:
            continue
    return False

def close_overlays(driver):
    try:
        overlays = driver.find_elements(By.CSS_SELECTOR, ".p-dialog-header-close-icon, button.close, .modal-close")
        for ov in overlays:
            if ov.is_displayed():
                driver.execute_script("arguments[0].click();", ov)
    except:
        pass

def progressive_scroll_container_to_bottom(driver, container, max_attempts=5, pause=0.5):
    try:
        last_height = driver.execute_script("return arguments[0].scrollHeight", container)
        for _ in range(max_attempts):
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", container)
            time.sleep(pause)
            new_height = driver.execute_script("return arguments[0].scrollHeight", container)
            if new_height == last_height:
                break
            last_height = new_height
    except:
        pass

# ---------- DRIVER ----------

def init_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-gpu")
    options.page_load_strategy = 'eager'
    options.add_argument(f"--user-data-dir=/tmp/chrome_user_data_{os.getpid()}")

    try:
        driver = webdriver.Chrome(options=options)
    except:
        driver_path = ChromeDriverManager().install()
        try:
            st = os.stat(driver_path)
            os.chmod(driver_path, st.st_mode | 0o111)
        except: pass
        service = Service(driver_path)
        driver = webdriver.Chrome(service=service, options=options)
    
    driver.set_page_load_timeout(60)
    return driver

# ---------- LOGIN / LOGOUT ----------

def perform_login(driver, wait, email, password):
    try:
        driver.get(BASE_URL)
    except TimeoutException:
        driver.execute_script("window.stop();")

    handle_cookie_banner(driver, timeout=3)
    
    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form.global-form")))
        
        mail_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="mail"]')))
        mail_input.clear()
        mail_input.send_keys(email)
        
        pwd_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="password"]')))
        pwd_input.clear()
        pwd_input.send_keys(password)
        
        btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.btnCreate")))
        driver.execute_script("arguments[0].scrollIntoView(true);", btn)
        time.sleep(0.5)
        
        try:
            btn.click()
        except:
            driver.execute_script("arguments[0].click();", btn)
            
        logging.info("Clicked login. Waiting for next page...")

        WebDriverWait(driver, 30).until(EC.any_of(
             EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-sections")),
             EC.presence_of_element_located((By.CSS_SELECTOR, ".tdb-s-candidature")),
             EC.url_contains("offre"),
             EC.url_contains("candidature")
        ))
        
        logging.info("Login successful.")
        handle_cookie_banner(driver, timeout=1)
        close_overlays(driver)
        return True

    except Exception as e:
        logging.error(f"Login failed: {e}")
        return False

def ensure_logged_in(driver, wait, email, password):
    try:
        if len(driver.find_elements(By.CSS_SELECTOR, ".offer-sections")) > 0: return True
        if "candidature" in driver.current_url: return True
    except: pass
    
    logging.info("Not logged in. Logging in...")
    return perform_login(driver, wait, email, password)

def perform_logout(driver, wait):
    logging.info("Logging out...")
    try:
        # 1. SCROLL TO TOP
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1.0) 
        close_overlays(driver)
        
        # 2. Click "Mon compte"
        mon_compte_btn = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "a.lessor-nav-trigger")))
        driver.execute_script("arguments[0].click();", mon_compte_btn)
        time.sleep(1.0) 

        # 3. Click "Deconnexion"
        logout_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//a[contains(.,'Déconnexion')]")))
        driver.execute_script("arguments[0].click();", logout_btn)
        
        # 4. Wait for login form
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, "form.global-form")))
        logging.info("Logout successful.")
        return True
    except Exception as e:
        logging.warning(f"Logout failed (non-critical): {e}")
        return False

# ---------- OFFERS SEARCH ----------

def find_section_button(driver, name):
    xpath = f"//div[contains(@class,'section') and contains(., '{name}')]"
    try:
        return WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, xpath)))
    except TimeoutException:
        return None

def extract_offer_from_card(card):
    info = {"uid": None, "price": None, "typ": None, "loc": None, "area": None, "raw_price": "", "img_src": None}
    try:
        try:
            img_el = card.find_element(By.CSS_SELECTOR, ".offer-image img")
            info["img_src"] = img_el.get_attribute("src")
        except: pass
        
        try:
            price_el = card.find_element(By.CSS_SELECTOR, ".price")
            info["raw_price"] = price_el.text.strip()
            info["price"] = parse_price(info["raw_price"])
        except: pass
            
        try:
            typ_el = card.find_element(By.CSS_SELECTOR, ".typology")
            info["typ"] = typ_el.text.strip()
            info["area"] = parse_area_from_typology(info["typ"])
        except: pass
            
        try:
            loc_el = card.find_element(By.CSS_SELECTOR, ".location")
            info["loc"] = loc_el.text.strip()
        except: pass
        
        if info["img_src"] and "assets/img" not in info["img_src"]:
            info["uid"] = info["img_src"]
        else:
            info["uid"] = f"{info['loc']}-{info['price']}-{info['typ']}"
            
    except StaleElementReferenceException:
        return None
    return info

# ---------- APPLY & CANCEL ----------

def apply_to_offer(driver, wait):
    # 1. Je postule
    try:
        apply_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.btn.btn-secondary.hi-check-round")))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", apply_btn)
        time.sleep(0.3)
        apply_btn.click()
        logging.info("Clicked 'Je postule'")
    except Exception as e:
        logging.error(f"Failed to click 'Je postule': {e}")
        return False, "btn_not_found"

    # 2. Confirmer (Popin 1)
    try:
        confirm_btn = WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(@class,'btn-13') and contains(.,'Confirmer')]"))
        )
        time.sleep(0.3)
        confirm_btn.click()
        logging.info("Clicked 'Confirmer'")
    except Exception:
        return False, "confirm_failed"

    # 3. Ok (Popin 2)
    try:
        ok_btn = WebDriverWait(driver, 8).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(@class,'btn-13') and contains(.,'Ok')]"))
        )
        time.sleep(0.3)
        ok_btn.click()
        logging.info("Clicked 'Ok'")
        return True, "applied"
    except Exception:
        logging.warning("'Ok' button missed, assuming success.")
        return True, "applied_no_ok"

def extract_rank_from_text(text):
    """
    Extracts rank from either "Position X" or "Il y a actuellement X candidatures"
    """
    if not text: return 999
    
    # Case 1: "Position 5"
    m = re.search(r"Position\s*[\n\r]*\s*(\d{1,3})", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    
    # Case 2: "Il y a actuellement 14 candidatures" (Implies rank approx = count)
    m2 = re.search(r"actuellement\s*(\d{1,4})\s*candidatures", text, re.IGNORECASE)
    if m2:
        return int(m2.group(1))
        
    return 999

def verify_and_cancel_new_application(driver, wait, account):
    logging.info("Verifying rank of new application...")
    try:
        if "mes-candidatures" not in driver.current_url:
            driver.get("https://al-in.fr/#/mes-candidatures")
        
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".tdb-s-candidature")))
        time.sleep(1.5) 
    except:
        logging.error("Could not load candidatures page.")
        return

    blocks = driver.find_elements(By.CSS_SELECTOR, ".tdb-s-candidature")
    if not blocks: return
    target_block = blocks[0]

    try:
        try:
            title_el = target_block.find_element(By.CSS_SELECTOR, ".title")
            title_text = title_el.text.strip()
        except:
            title_text = "Offre Inconnue"

        raw_text = target_block.text
        rank = extract_rank_from_text(raw_text)
        
        logging.info(f"New Application Rank detected: {rank}")

        if 10 < rank < 999:
            logging.warning(f"Rank {rank} > 10. Cancelling immediately.")
            try:
                # Click 'Annuler cette candidature'
                cancel_btn = target_block.find_element(By.CSS_SELECTOR, "a.tool-link.hi-cross-round")
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", cancel_btn)
                cancel_btn.click()
                
                # Confirm Cancel (Popin 2, button Oui)
                confirm = WebDriverWait(driver, 5).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "button.btn.btn-13.btn-outline-primary"))
                )
                confirm.click()
                logging.info("IMMEDIATE CANCELLATION SUCCESSFUL.")
                
                body = f"""
                Compte: {account['name']}
                Offre: {title_text}
                Action: Annulation automatique
                Raison: Rang {rank} > 10
                """
                send_email(f"BOTALIN - AUTO CANCEL ({account['name']})", body)
            except Exception as e:
                logging.error(f"Failed to auto-cancel: {e}")
        elif rank == 999:
            logging.warning("Rank could not be parsed (999). Keeping candidature to be safe.")
            body = f"""
            Compte: {account['name']}
            Offre: {title_text}
            Action: Candidature conservée (Rang illisible)
            Rang détecté: 999 (Illisible)
            
            --- TEXTE BRUT ---
            {raw_text[:600]}
            """
            send_email(f"BOTALIN - RANG ILLISIBLE ({account['name']})", body)
        else:
            logging.info(f"Rank {rank} is good (<= 10).")
            body = f"""
            Compte: {account['name']}
            Offre: {title_text}
            Action: Succès (Gardée)
            Rang: {rank}
            """
            send_email(f"BOTALIN - SUCCES ({account['name']})", body)

    except Exception as e:
        logging.error(f"Error checking rank: {e}")


# ---------- MAIN PROCESS ----------

def process_account(account):
    email = os.environ.get(account["email_env"])
    password = os.environ.get(account["pass_env"])
    
    if not email or not password:
        logging.error(f"Skipping {account['name']}: Missing credentials.")
        return

    logging.info(f"--- Starting {account['name']} ---")
    
    driver = init_driver()
    driver.delete_all_cookies() # Start clean
    wait = WebDriverWait(driver, WAIT_TIMEOUT)
    seen = set(load_json(account["seen_file"], []))
    
    try:
        if not ensure_logged_in(driver, wait, email, password):
            return
        
        # Force Navigation to Search to find NEW offers
        if "recherche-logement" not in driver.current_url:
            logging.info("Navigating to Search page...")
            driver.get("https://al-in.fr/#/recherche-logement")
            try:
                WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-list-container")))
            except: pass

        found_match = False
        target_offer = None
        
        for section_name in account["section_scope"]:
            btn = find_section_button(driver, section_name)
            if not btn: continue
            
            try:
                driver.execute_script("arguments[0].click();", btn)
                time.sleep(1.0)
            except: pass
            
            try:
                container = driver.find_element(By.CSS_SELECTOR, ".offer-list-container")
                progressive_scroll_container_to_bottom(driver, container, max_attempts=4)
            except: pass

            cards = driver.find_elements(By.CSS_SELECTOR, "app-offer-card")
            for card in cards:
                info = extract_offer_from_card(card)
                if not info or not info["uid"]: continue
                if info["uid"] in seen: continue
                
                # UPDATED CRITERIA CHECK
                if info["price"]:
                    if info["price"] > account["max_price"]: continue
                    if info["price"] < account.get("min_price", 0): continue
                
                if info["area"] and account["min_area"] > 0 and info["area"] < account["min_area"]: continue
                if not re.search(account["wanted_typ"], info["typ"] or "", re.IGNORECASE): continue
                
                target_offer = (card, info)
                found_match = True
                break
            
            if found_match: break
        
        if found_match and target_offer:
            card_elem, info = target_offer
            logging.info(f"Applying to: {info}")
            
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", card_elem)
                card_elem.click()
            except:
                driver.execute_script("arguments[0].click();", card_elem)
            
            success, reason = apply_to_offer(driver, wait)
            
            seen.add(info["uid"])
            save_json(account["seen_file"], list(seen))
            
            if success:
                verify_and_cancel_new_application(driver, wait, account)
            else:
                logging.error(f"Failed apply: {reason}")
        
        else:
            logging.info("No new matching offers found.")
        
        perform_logout(driver, wait)

    except Exception as e:
        logging.error(f"Global error for {account['name']}: {e}")
    finally:
        try: driver.quit()
        except: pass

def main():
    for account in ACCOUNTS:
        process_account(account)

if __name__ == "__main__":
    main()
