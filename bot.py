import time
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# -------------------------------------------------------------------
# CONFIGURATION
# -------------------------------------------------------------------
EMAIL = "tesstedsgstsredr@gmail.com"
PASSWORD = "tesstedsgstsredr@gmail.com1212"
LOGIN_URL = "https://al-in.fr/auth/login"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# -------------------------------------------------------------------
# INITIALISATION DU DRIVER
# -------------------------------------------------------------------
def init_driver():
    logging.info("Starting bot")
    options = webdriver.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--headless=new")  # mode headless
    driver = webdriver.Chrome(options=options)
    driver.set_window_size(1280, 1024)
    return driver

# -------------------------------------------------------------------
# LOGIN
# -------------------------------------------------------------------
def login(driver):
    driver.get(LOGIN_URL)
    try:
        # attendre le champ email
        email_field = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email']"))
        )
        pwd_field = driver.find_element(By.CSS_SELECTOR, "input[type='password']")

        email_field.clear()
        email_field.send_keys(EMAIL)
        pwd_field.clear()
        pwd_field.send_keys(PASSWORD)

        # bouton de connexion
        login_btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
        login_btn.click()

        # attendre que les offres apparaissent après login
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.offer-card-container"))
        )
        logging.info("Login successful.")
        return True

    except TimeoutException:
        logging.error("Login failed.")
        return False

# -------------------------------------------------------------------
# CHERCHE ET POSTULE SUR UNE OFFRE
# -------------------------------------------------------------------
def search_and_apply_offer(driver):
    logging.info("Searching for offers...")

    offers = driver.find_elements(By.CSS_SELECTOR, "div.offer-card-container")
    for offer in offers:
        try:
            price_text = offer.find_element(By.CSS_SELECTOR, ".price").text
            typ = offer.find_element(By.CSS_SELECTOR, ".typology").text
            loc = offer.find_element(By.CSS_SELECTOR, ".location").text
            img = offer.find_element(By.CSS_SELECTOR, ".offer-image img").get_attribute("src")

            # 🎯 Filtre : Choisy-le-Roi
            if "Choisy-le-Roi" in loc:
                target_offer = {
                    "uid": img,
                    "img_src": img,
                    "price_text": price_text,
                    "typ": typ,
                    "loc": loc
                }
                logging.info(f"Found target offer: {target_offer}")

                # Trouver le bouton "Postuler"
                apply_btn = offer.find_element(By.CSS_SELECTOR, "button.btn-secondary")

                # ✅ Correction : scroller et forcer clic JS
                driver.execute_script("arguments[0].scrollIntoView(true);", apply_btn)
                time.sleep(1)
                driver.execute_script("arguments[0].click();", apply_btn)

                logging.info("Clicked on apply button successfully.")
                return True
        except Exception as e:
            logging.warning(f"Error processing offer: {e}")
            continue

    logging.info("No matching offer found.")
    return False

# -------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------
def main():
    driver = init_driver()
    try:
        if login(driver):
            applied = search_and_apply_offer(driver)
            if applied:
                logging.info("Application done.")
            else:
                logging.info("No application submitted.")
        else:
            logging.error("Could not log in.")
    finally:
        driver.quit()

if __name__ == "__main__":
    main()
