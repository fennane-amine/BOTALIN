import time
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# Config logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

EMAIL = "mohamed-amine.fennane@epita.fr"
PASSWORD = "&9.Mnq.6F8'M/wm{"

URL_LOGIN = "https://www.al-in.fr/login"
URL_COMMUNES = "https://www.al-in.fr/communes-demandes"

def init_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(f"--user-data-dir=/tmp/selenium_profile_{int(time.time())}")
    driver = webdriver.Chrome(options=options)
    driver.maximize_window()
    return driver

def login(driver):
    driver.get(URL_LOGIN)
    logging.info("Opening login page...")
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "email"))).send_keys(EMAIL)
    driver.find_element(By.NAME, "password").send_keys(PASSWORD)
    driver.find_element(By.XPATH, "//button[contains(text(),'Se connecter')]").click()
    logging.info("Login submitted, waiting for dashboard...")
    WebDriverWait(driver, 15).until(EC.url_contains("dashboard"))
    logging.info("Login successful!")

def go_to_communes(driver):
    driver.get(URL_COMMUNES)
    logging.info("Navigated to Communes demandées page...")
    time.sleep(3)  # wait for possible loading

def select_first_offer(driver):
    try:
        offer_card = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, ".offer-card-container"))
        )
        offer_card.click()
        logging.info("Clicked on the first offer")
        time.sleep(2)
    except Exception as e:
        logging.error("No offer found or cannot click: %s", e)
        return False
    return True

def apply_to_offer(driver):
    try:
        btn_postule = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Je postule')]"))
        )
        btn_postule.click()
        logging.info("Clicked 'Je postule'")

        btn_confirmer = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Confirmer')]"))
        )
        btn_confirmer.click()
        logging.info("Clicked 'Confirmer'")

        # Final popup OK
        btn_ok = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Ok')]"))
        )
        btn_ok.click()
        logging.info("Application confirmed!")

    except Exception as e:
        logging.error("Error during application process: %s", e)

def main():
    driver = init_driver()
    try:
        login(driver)
        go_to_communes(driver)
        if select_first_offer(driver):
            apply_to_offer(driver)
        else:
            logging.info("No offer to apply to.")
    finally:
        logging.info("Closing browser...")
        driver.quit()

if __name__ == "__main__":
    main()
