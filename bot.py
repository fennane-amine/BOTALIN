# bot.py - postuler à l'offre ciblée (Choisy-le-Roi T2) dans "Autres communes du département"
import os
import time
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import stat

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------- CONFIG ----------
BASE_URL = "https://al-in.fr/#/connexion-demandeur"
EMAIL = os.environ.get("EMAIL")
PASSWORD = os.environ.get("PASSWORD")
WAIT_TIMEOUT = 10
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("1", "true", "yes")

# Mail de notification (boîte bidon)
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "tesstedsgstsredr@gmail.com"
SMTP_PASSWORD = "tesstedsgstsredr@gmail.com1212"
MAIL_TO = "fennane.mohamedamine@gmail.com"

if not EMAIL or not PASSWORD:
    logging.error("EMAIL and PASSWORD environment variables must be set.")
    raise SystemExit(1)


# ---------- Helpers ----------
def send_email(subject, body):
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_USER
        msg["To"] = MAIL_TO
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, MAIL_TO, msg.as_string())
        server.quit()
        logging.info(f"Notification email sent to {MAIL_TO}")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")


def find_section_button(driver, name):
    xpath = f"//div[contains(@class,'offer-sections')]//div[contains(normalize-space(.),'{name}')]"
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
        except Exception:
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
    return {"uid": uid, "img_src": img_src, "price_text": price_text, "typ": typ, "loc": loc}


# ---------- Driver init ----------
def init_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")

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


# ---------- Main ----------
def main():
    logging.info("Starting bot")

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

        # Aller directement à "Autres communes du département"
        section = "Autres communes du département"
        btn = find_section_button(driver, section)
        if not btn:
            logging.error(f"Section '{section}' not found.")
            return
        click_element(driver, btn)
        time.sleep(2)

        cards = get_offer_cards_in_current_section(driver)
        if not cards:
            logging.info("No offers found in section.")
            return

        # Filtrer l'offre ciblée
        target_card = None
        for card in cards:
            info = extract_offer_info(card)
            if "Choisy-le-Roi" in info["loc"] and "T2" in info["typ"]:
                target_card = card
                logging.info(f"Found target offer: {info}")
                break

        if not target_card:
            logging.info("Target offer not found, stopping.")
            return

        # Ouvrir l'offre
        try:
            img_el = target_card.find_element(By.CSS_SELECTOR, ".offer-image img")
            click_element(driver, img_el)
        except Exception as e:
            logging.warning(f"Could not open target offer detail: {e}")
            return

        # Postuler
        try:
            apply_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Je postule') or contains(.,'JE POSTULE')]"))
            )
            apply_btn.click()
            logging.info("Clicked 'Je postule'")
        except TimeoutException:
            logging.error("Apply button not found.")
            return

        try:
            confirm_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Confirmer')]"))
            )
            confirm_btn.click()
            logging.info("Clicked 'Confirmer'")
        except TimeoutException:
            pass

        try:
            ok_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                EC.element_to_be_clickable((By.XPATH, "//button[normalize-space(.)='Ok' or contains(.,'OK')]"))
            )
            ok_btn.click()
            logging.info("Clicked 'Ok'")
        except TimeoutException:
            pass

        try:
            txt = WebDriverWait(driver, WAIT_TIMEOUT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".text_picto_vert"))
            )
            result_msg = txt.text.strip()
            logging.info(f"Application result: {result_msg}")
            send_email("Bot AL-IN: Candidature envoyée", f"Candidature réussie.\n\nDétails:\n{result_msg}")
        except TimeoutException:
            logging.warning("No confirmation text found, but application may have been submitted.")
            send_email("Bot AL-IN: Candidature envoyée", "Candidature soumise mais pas de message de confirmation.")

        logging.info("Bot finished successfully (applied to target offer).")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
