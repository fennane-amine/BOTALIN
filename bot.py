import os
import time
import logging
import smtplib
from email.mime.text import MIMEText
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ----------------- CONFIG -----------------
BASE_URL = "https://al-in.fr/#/connexion-demandeur"
EMAIL = os.environ.get("EMAIL")
PASSWORD = os.environ.get("PASSWORD")
WAIT_TIMEOUT = 10
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("1", "true", "yes")

if not EMAIL or not PASSWORD:
    logging.error("EMAIL and PASSWORD environment variables must be set.")
    raise SystemExit(1)

# Mail jetable (directement en dur)
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SENDER_EMAIL = "tesstedsgstsredr@gmail.com"
SENDER_PASSWORD = "tesstedsgstsredr@gmail.com1212"
RECEIVER_EMAIL = "fennane.mohamedamine@gmail.com"

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ----------------- SELENIUM DRIVER -----------------
def init_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    logging.info("Starting bot")
    driver = webdriver.Chrome(options=options)
    return driver

# ----------------- MAIL FUNCTION -----------------
def send_email(subject, body):
    try:
        msg = MIMEText(body)
        msg["From"] = SENDER_EMAIL
        msg["To"] = RECEIVER_EMAIL
        msg["Subject"] = subject

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())

        logging.info(f"Email sent to {RECEIVER_EMAIL}")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")

# ----------------- BOT ACTIONS -----------------
def login(driver):
    driver.get(BASE_URL)
    WebDriverWait(driver, WAIT_TIMEOUT).until(EC.presence_of_element_located((By.NAME, "username")))
    driver.find_element(By.NAME, "username").send_keys(EMAIL)
    driver.find_element(By.NAME, "password").send_keys(PASSWORD)
    driver.find_element(By.CSS_SELECTOR, "button[type='submit']").click()
    WebDriverWait(driver, WAIT_TIMEOUT).until(EC.presence_of_element_located((By.CLASS_NAME, "offers")))
    logging.info("Login successful.")

def apply_first_offer(driver):
    offers = driver.find_elements(By.CLASS_NAME, "offer-card-container")
    if not offers:
        logging.info("No offers found.")
        return None

    offer = offers[0]
    img_src = offer.find_element(By.TAG_NAME, "img").get_attribute("src")
    price_text = offer.find_element(By.CLASS_NAME, "price").text.strip()
    typ = offer.find_element(By.CLASS_NAME, "typology").text.strip()
    loc = offer.find_element(By.CLASS_NAME, "location").text.strip()

    offer_data = {
        "img_src": img_src,
        "price_text": price_text,
        "typ": typ,
        "loc": loc
    }

    logging.info(f"Applying to first offer: {offer_data}")

    ActionChains(driver).move_to_element(offer).click().perform()
    WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Je postule')]"))).click()
    logging.info("Clicked 'Je postule'")

    WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Confirmer')]"))).click()
    logging.info("Clicked 'Confirmer'")

    WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Ok')]"))).click()
    logging.info("Clicked 'Ok'")

    time.sleep(2)
    try:
        result_text = driver.find_element(By.CLASS_NAME, "swal2-html-container").text
    except:
        result_text = "Application sent (no confirmation text found)."

    logging.info(f"Application result: {result_text}")
    return f"Offer: {offer_data}\n\nResult: {result_text}"

# ----------------- MAIN -----------------
def main():
    driver = None
    try:
        driver = init_driver()
        login(driver)
        result = apply_first_offer(driver)

        if result:
            send_email("Bot application finished ✅", result)
            logging.info("Bot finished successfully (applied to 1st offer).")
        else:
            send_email("Bot application finished ⚠️", "No offers found.")
    finally:
        if driver:
            driver.quit()

if __name__ == "__main__":
    main()
