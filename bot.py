# bot.py - postuler à la 1ère offre dans "Autres communes du département" + notification email
import os
import time
import json
import re
import logging
import stat
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta

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

# Email notification config (boîte jetable donnée)
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = "tesstedsgstsredr@gmail.com"
SENDER_PASSWORD = "tesstedsgstsredr@gmail.com1212"
RECEIVER_EMAIL = "fennane.mohamedamine@gmail.com"

if not EMAIL or not PASSWORD:
    logging.error("EMAIL and PASSWORD environment variables must be set.")
    raise SystemExit(1)


# ---------- Helpers ----------
def send_email(subject, body):
    """Send an email notification after application."""
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SENDER_EMAIL
        msg["To"] = RECEIVER_EMAIL

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, [RECEIVER_EMAIL], msg.as_string())
        logging.info("Notification email sent successfully.")
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
def login(driver):
    driver.get(BASE_URL)
    wait = WebDriverWait(driver, WAIT_TIMEOUT)
    try:
        # email
        mail_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[formcontrolname='mail']"))
        )
        mail_input.clear()
        mail_input.send_keys(EMAIL)

        # password
        pwd_input = driver.find_element(By.CSS_SELECTOR, "input[formcontrolname='password']")
        pwd_input.clear()
        pwd_input.send_keys(PASSWORD)

        # bouton connexion
        btn = wait.until(
            EC.element_to_be_clickable(
                (By.XPATH, "//button[contains(.,'Je me connecte') or contains(.,'JE ME CONNECTE')]")
            )
        )
        btn.click()

        # attendre que la page des offres charge
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-sections")))
        logging.info("Login successful.")
        return True
    except Exception as e:
        logging.error(f"Login failed: {e}")
        return False


# ---------- Main ----------
def main():
    logging.info("Starting bot")

    try:
        driver = init_driver()
    except Exception as e:
        logging.error(f"Driver init failed: {e}")
        return

    try:
        if not login(driver):
            logging.error("Could not authenticate; stopping run.")
            return

        # Aller à "Autres communes du département"
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

        # Prendre la 1ère offre
        first_card = cards[0]
        info = extract_offer_info(first_card)
        logging.info(f"Applying to first offer: {info}")

        try:
            img_el = first_card.find_element(By.CSS_SELECTOR, ".offer-image img")
            click_element(driver, img_el)
        except Exception as e:
            logging.warning(f"Could not open offer detail: {e}")
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
        except TimeoutException:
            result_msg = "No confirmation text found, but application may have been submitted."
            logging.warning(result_msg)

        # envoyer un mail de notification
        send_email("Bot AL-IN - Candidature envoyée", f"Résultat: {result_msg}\nOffre: {info}")

        logging.info("Bot finished successfully (applied to 1st offer).")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
