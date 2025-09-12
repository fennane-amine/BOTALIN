import time
import logging
import smtplib
from email.mime.text import MIMEText
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# 📌 Config logs
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# 📌 Config email
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
EMAIL_SENDER = "tesstedsgstsredr@gmail.com"
EMAIL_PASSWORD = "tesstedsgstsredr@gmail.com1212"
EMAIL_RECEIVER = "fennane.mohamedamine@gmail.com"


def send_email(subject, body):
    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = EMAIL_SENDER
        msg["To"] = EMAIL_RECEIVER

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())

        logging.info("✅ Email sent successfully")
    except Exception as e:
        logging.error(f"❌ Failed to send email: {e}")


def init_driver():
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")

    logging.info("Trying Selenium Manager (webdriver.Chrome(options=...))")
    driver = webdriver.Chrome(options=options)
    return driver


def login(driver):
    driver.get("https://al-in.fr/locataire/connexion")

    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.NAME, "email"))
    ).send_keys("ton.email@login.com")

    driver.find_element(By.NAME, "password").send_keys("tonpassword")
    driver.find_element(By.CSS_SELECTOR, "button[type=submit]").click()

    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CLASS_NAME, "navbar"))
    )
    logging.info("Login successful.")


def find_and_apply(driver):
    offers = driver.find_elements(By.CSS_SELECTOR, "div.offer-card-container")

    for offer in offers:
        try:
            loc = offer.find_element(By.CSS_SELECTOR, "div.location").text.strip()
            if "Choisy-le-Roi" in loc:
                price_text = offer.find_element(By.CSS_SELECTOR, "div.price").text.strip()
                typ = offer.find_element(By.CSS_SELECTOR, "div.typology").text.strip()
                img_src = offer.find_element(By.CSS_SELECTOR, "img").get_attribute("src")

                target_offer = {
                    "uid": img_src,
                    "img_src": img_src,
                    "price_text": price_text,
                    "typ": typ,
                    "loc": loc,
                }

                logging.info(f"Found target offer: {target_offer}")

                # clique bouton "Je postule"
                apply_btn = offer.find_element(By.CSS_SELECTOR, "button.btn.btn-secondary")
                driver.execute_script("arguments[0].scrollIntoView(true);", apply_btn)
                time.sleep(1)
                driver.execute_script("arguments[0].click();", apply_btn)
                logging.info("Clicked 'Je postule'")

                # confirmer
                confirm_btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Confirmer')]"))
                )
                confirm_btn.click()
                logging.info("Clicked 'Confirmer'")

                # ok final
                ok_btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Ok')]"))
                )
                ok_btn.click()
                logging.info("Clicked 'Ok'")

                # ✅ send email notification
                send_email(
                    subject="Bot AL-IN : Candidature envoyée ✅",
                    body=f"L'offre suivante a été traitée :\n\n{target_offer}"
                )

                return
        except Exception as e:
            logging.error(f"Erreur en traitant une offre: {e}")


def main():
    logging.info("Starting bot")
    driver = init_driver()
    try:
        login(driver)
        find_and_apply(driver)
        logging.info("Bot finished successfully.")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
