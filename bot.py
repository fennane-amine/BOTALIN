import os
import time
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")

BASE_URL = "https://www.al-in.fr/"  # Modifier si nécessaire

def init_driver():
    logging.info("Init webdriver via Selenium Manager")
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    # profil temporaire unique pour éviter le blocage
    options.add_argument(f"--user-data-dir=/tmp/chrome-user-data-{int(time.time())}")
    driver = webdriver.Chrome(options=options)
    logging.info("Selenium Manager Chrome OK")
    return driver

def login(driver):
    logging.info("Navigating to login page")
    driver.get(BASE_URL)
    # attendre que le formulaire soit visible
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "email"))
        )
    except TimeoutException:
        logging.error("Login page not loaded")
        return False

    logging.info("Filling login form")
    driver.find_element(By.NAME, "email").send_keys(EMAIL)
    driver.find_element(By.NAME, "password").send_keys(PASSWORD)
    driver.find_element(By.CSS_SELECTOR, "button[type=submit]").click()

    # attendre que la page principale charge
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "section"))
        )
    except TimeoutException:
        logging.error("Login failed or main page did not load")
        return False
    logging.info("Login successful")
    return True

def scroll_section(driver):
    # scroll pour charger toutes les offres
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

def find_first_offer(driver):
    # récupérer toutes les offres visibles
    cards = driver.find_elements(By.CSS_SELECTOR, ".offer-card-container")
    if not cards:
        return None
    return cards[0]

def apply_offer(driver, offer):
    logging.info("Opening offer")
    offer.click()
    time.sleep(2)  # attendre le chargement de la page

    try:
        btn_postule = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Je postule')]"))
        )
        btn_postule.click()
        logging.info("Clicked 'Je postule'")
    except TimeoutException:
        logging.error("'Je postule' button not found")
        return False

    # confirmation
    try:
        btn_confirm = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Confirmer')]"))
        )
        btn_confirm.click()
        logging.info("Clicked 'Confirmer'")
    except TimeoutException:
        logging.error("'Confirmer' button not found")
        return False

    # popin final ok
    try:
        btn_ok = WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.XPATH, "//button[text()='Ok']"))
        )
        btn_ok.click()
        logging.info("Clicked final 'Ok'")
    except TimeoutException:
        logging.warning("Final 'Ok' button not found")
    
    logging.info("Application process finished")
    return True

def main():
    driver = init_driver()
    if not login(driver):
        driver.quit()
        return

    sections = ["Communes demandées", "Communes limitrophes", "Autres communes du département"]
    offer_applied = False

    for sec_name in sections:
        logging.info(f"Selecting section: {sec_name}")
        try:
            section_btn = driver.find_element(By.XPATH, f"//div[contains(@class, 'section') and contains(text(), '{sec_name}')]")
            driver.execute_script("arguments[0].scrollIntoView(true);", section_btn)
            time.sleep(1)
            section_btn.click()
        except (TimeoutException, ElementClickInterceptedException):
            logging.warning(f"Cannot select section {sec_name}, skipping")
            continue

        scroll_section(driver)
        offer = find_first_offer(driver)
        if offer:
            logging.info(f"Found offer in section {sec_name}")
            if apply_offer(driver, offer):
                offer_applied = True
            break

    if not offer_applied:
        logging.info("No offer applied")

    driver.quit()

if __name__ == "__main__":
    main()
