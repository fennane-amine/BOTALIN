import logging
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ------------------ CONFIG ------------------
EMAIL = "mohamed-amine.fennane@epita.fr"
PASSWORD = "&9.Mnq.6F8'M/wm{"
URL_LOGIN = "https://www.al-in.fr/login"
URL_COMMUNES = "https://www.al-in.fr/mes-demandes/communes-demandees"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ------------------ INIT DRIVER ------------------
def init_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--start-maximized")
    driver = webdriver.Chrome(options=options)
    driver.implicitly_wait(5)
    return driver

# ------------------ LOGIN ------------------
def login(driver):
    driver.get(URL_LOGIN)
    logging.info("Opening login page...")

    try:
        email_input = WebDriverWait(driver, 30).until(
            EC.visibility_of_element_located((By.NAME, "email"))
        )
        email_input.send_keys(EMAIL)

        password_input = driver.find_element(By.NAME, "password")
        password_input.send_keys(PASSWORD)

        driver.find_element(By.XPATH, "//button[contains(text(),'Se connecter')]").click()
        logging.info("Login submitted, waiting for dashboard...")

        WebDriverWait(driver, 30).until(EC.url_contains("dashboard"))
        logging.info("Login successful!")

    except Exception as e:
        logging.error("Login failed: %s", e)
        driver.quit()
        raise

# ------------------ NAVIGATE TO COMMUNES ------------------
def go_to_communes(driver):
    driver.get(URL_COMMUNES)
    logging.info("Navigated to Communes demandées page")

# ------------------ POSTULE FIRST OFFER ------------------
def postule_first_offer(driver):
    try:
        # Scroll down pour s'assurer que les cartes se chargent
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)

        # Cherche la première offre
        first_offer = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, ".offer-card-container"))
        )
        logging.info("First offer found")
        first_offer.click()
        time.sleep(1)

        # Clique sur "Je postule"
        apply_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Je postule')]"))
        )
        apply_btn.click()
        logging.info("'Je postule' clicked")
        time.sleep(1)

        # Clique sur "Confirmer"
        confirm_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Confirmer')]"))
        )
        confirm_btn.click()
        logging.info("'Confirmer' clicked")
        time.sleep(2)

        # Final pop-up OK
        ok_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Ok')]"))
        )
        ok_btn.click()
        logging.info("Application confirmed successfully!")

    except Exception as e:
        logging.error("Failed to postule first offer: %s", e)

# ------------------ MAIN ------------------
def main():
    driver = init_driver()
    try:
        login(driver)
        go_to_communes(driver)
        postule_first_offer(driver)
    finally:
        logging.info("Closing browser...")
        driver.quit()

if __name__ == "__main__":
    main()
