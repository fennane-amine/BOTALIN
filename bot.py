import os
import time
import logging
import smtplib
from email.mime.text import MIMEText
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, ElementClickInterceptedException

# ----------------- CONFIG -----------------
BASE_URL = "https://al-in.fr/#/connexion-demandeur"
EMAIL = os.environ.get("EMAIL")
PASSWORD = os.environ.get("PASSWORD")
WAIT_TIMEOUT = 15
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("1", "true", "yes")

# Email notification config
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = "tesstedsgstsredr@gmail.com"
SENDER_PASSWORD = "tesstedsgstsredr@gmail.com1212"
RECEIVER_EMAIL = "fennane.mohamedamine@gmail.com"

# ----------------- LOGGING -----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ----------------- EMAIL -----------------
def send_email(subject, body):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = SENDER_EMAIL
        msg["To"] = RECEIVER_EMAIL

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        server.quit()
        logging.info("Notification email sent.")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")

# ----------------- SELENIUM INIT -----------------
def init_driver():
    options = webdriver.ChromeOptions()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    driver = webdriver.Chrome(options=options)
    return driver

# ----------------- LOGIN -----------------
def login(driver):
    driver.get(BASE_URL)
    try:
        # Champ Email
        mail_input = WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.XPATH, "//input[@formcontrolname='mail']"))
        )
        mail_input.clear()
        mail_input.send_keys(EMAIL)

        # Champ Mot de passe
        pwd_input = driver.find_element(By.XPATH, "//input[@formcontrolname='password']")
        pwd_input.clear()
        pwd_input.send_keys(PASSWORD)

        # Bouton "JE ME CONNECTE"
        login_btn = driver.find_element(By.XPATH, "//button[contains(., 'JE ME CONNECTE')]")
        login_btn.click()

        WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.XPATH, "//div[contains(text(),'Mes informations')]"))
        )
        logging.info("Login successful.")
    except TimeoutException:
        logging.error("Login failed - Timeout.")
        driver.quit()
        raise

# ----------------- APPLY TO FIRST OFFER -----------------
def apply_first_offer(driver):
    try:
        # Trouver la première carte offre
        first_offer = WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-card-container"))
        )
        img = first_offer.find_element(By.CSS_SELECTOR, "img").get_attribute("src")
        price = first_offer.find_element(By.CSS_SELECTOR, ".price").text.strip()
        typ = first_offer.find_element(By.CSS_SELECTOR, ".typology").text.strip()
        loc = first_offer.find_element(By.CSS_SELECTOR, ".location").text.strip()

        offer_data = {
            "uid": img,
            "img_src": img,
            "price_text": price,
            "typ": typ,
            "loc": loc
        }
        logging.info(f"Applying to first offer: {offer_data}")

        # Bouton "Je postule"
        apply_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Je postule')]"))
        )
        driver.execute_script("arguments[0].click();", apply_btn)
        logging.info("Clicked 'Je postule'")

        # Sélection automatique de la première commune si demandée
        try:
            commune_select = WebDriverWait(driver, 3).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "select"))
            )
            first_option = commune_select.find_elements(By.TAG_NAME, "option")[1]  # skip "Sélectionnez..."
            first_option.click()
            logging.info("Selected first commune.")
        except TimeoutException:
            logging.info("No commune selection required.")

        # Bouton "Confirmer"
        confirm_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Confirmer')]"))
        )
        driver.execute_script("arguments[0].click();", confirm_btn)
        logging.info("Clicked 'Confirmer'")

        # Bouton "Ok"
        ok_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Ok')]"))
        )
        driver.execute_script("arguments[0].click();", ok_btn)
        logging.info("Clicked 'Ok'")

        # Message résultat
        try:
            result_msg = driver.find_element(By.CSS_SELECTOR, ".swal2-html-container").text
            logging.info(f"Application result: {result_msg}")
        except NoSuchElementException:
            logging.info("Application result: No message found.")

        # Envoi mail
        send_email("AL-IN Bot - Application done", f"Successfully applied to:\n{offer_data}")

    except Exception as e:
        logging.error(f"Failed to apply: {e}")

# ----------------- MAIN -----------------
def main():
    logging.info("Starting bot")
    driver = init_driver()
    try:
        login(driver)
        time.sleep(2)
        apply_first_offer(driver)
        logging.info("Bot finished successfully (applied to 1st offer).")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
