import time
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ------------------ CONFIG ------------------
EMAIL = "mohamed-amine.fennane@epita.fr"
PASSWORD = "&9.Mnq.6F8'M/wm{"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ------------------ INIT DRIVER ------------------
def init_driver():
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    driver = webdriver.Chrome(options=options)
    return driver

# ------------------ LOGIN ------------------
def login(driver):
    logging.info("Opening login page...")
    driver.get("https://example-login-page.com")  # <- mettre l'URL de login réel
    WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "email"))).send_keys(EMAIL)
    driver.find_element(By.NAME, "password").send_keys(PASSWORD)
    driver.find_element(By.XPATH, "//button[contains(text(),'Se connecter')]").click()
    logging.info("Login attempted")
    time.sleep(3)  # attente pour chargement complet

# ------------------ NAVIGATION ------------------
def go_to_communes_limitrophes(driver):
    logging.info("Navigating to 'Communes limitrophes' section...")
    try:
        section_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//div[contains(text(),'Communes limitrophes')]"))
        )
        section_btn.click()
        logging.info("'Communes limitrophes' section opened")
        time.sleep(2)
    except Exception as e:
        logging.error("Failed to open 'Communes limitrophes': %s", e)

# ------------------ POSTULE FIRST OFFER ------------------
def postule_first_offer(driver):
    try:
        # Scroll pour charger les offres
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)

        # Cherche la première offre dans la section
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

        # Pop-up final OK
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
        go_to_communes_limitrophes(driver)
        postule_first_offer(driver)
    finally:
        logging.info("Closing browser...")
        driver.quit()

if __name__ == "__main__":
    main()
