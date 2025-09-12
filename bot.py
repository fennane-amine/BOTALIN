import time
import logging
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --- CONFIG LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# --- CREDENTIALS (fake ones you gave) ---
EMAIL = "tesstedsgstsredr@gmail.com"
PASSWORD = "tesstedsgstsredr@gmail.com1212"

# --- SELENIUM SETUP ---
def init_driver():
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    driver = webdriver.Chrome(options=options)
    return driver

# --- LOGIN ---
def login(driver):
    driver.get("https://www.al-in.fr/")
    logging.info("Page loaded, logging in...")

    # bouton "Se connecter"
    WebDriverWait(driver, 15).until(
        EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Se connecter')]"))
    ).click()

    # champ email
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.NAME, "email"))
    ).send_keys(EMAIL)

    # champ password
    driver.find_element(By.NAME, "password").send_keys(PASSWORD)

    # bouton "Valider"
    driver.find_element(By.XPATH, "//button[contains(., 'Valider')]").click()

    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.XPATH, "//div[contains(., 'Mon compte')]"))
    )
    logging.info("Login successful.")

# --- SCRAPER ---
def find_offer(driver):
    offers = driver.find_elements(By.CSS_SELECTOR, ".offer-card-container")
    for offer in offers:
        try:
            img = offer.find_element(By.CSS_SELECTOR, ".offer-image img").get_attribute("src")
            price_text = offer.find_element(By.CSS_SELECTOR, ".price").text.strip()
            typ = offer.find_element(By.CSS_SELECTOR, ".typology").text.strip()
            loc = offer.find_element(By.CSS_SELECTOR, ".location").text.strip()

            # 🎯 FILTRE : Choisy-le-Roi (94600)
            if "Choisy-le-Roi" in loc:
                offer_data = {
                    "uid": img,
                    "img_src": img,
                    "price_text": price_text,
                    "typ": typ,
                    "loc": loc,
                }
                logging.info(f"Found target offer: {offer_data}")
                return offer
        except Exception as e:
            logging.warning(f"Error parsing offer: {e}")
    return None

# --- APPLY ---
def apply_to_offer(driver, offer):
    try:
        apply_btn = offer.find_element(By.XPATH, ".//button[contains(@class, 'btn-secondary')]")

        # ✅ Correction : clic via JavaScript
        driver.execute_script("arguments[0].scrollIntoView(true);", apply_btn)
        time.sleep(1)
        driver.execute_script("arguments[0].click();", apply_btn)

        logging.info("Clicked on apply button successfully.")
    except Exception as e:
        logging.error(f"Error while clicking apply: {e}")

# --- MAIN ---
def main():
    logging.info("Starting bot")
    driver = init_driver()
    try:
        login(driver)
        time.sleep(3)

        offer = find_offer(driver)
        if offer:
            apply_to_offer(driver, offer)
        else:
            logging.info("No matching offer found.")
    finally:
        time.sleep(5)
        driver.quit()

if __name__ == "__main__":
    main()
