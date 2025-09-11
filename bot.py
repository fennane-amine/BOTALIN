# bot_first_offer.py
import os
import time
import logging
from datetime import datetime, timedelta

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

BASE_URL = "https://al-in.fr/#/connexion-demandeur"
EMAIL = os.environ.get("EMAIL")
PASSWORD = os.environ.get("PASSWORD")
MAX_RUN_SECONDS = int(os.environ.get("MAX_RUN_SECONDS", 300))
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("1", "true", "yes")

if not EMAIL or not PASSWORD:
    logging.error("EMAIL and PASSWORD must be set")
    raise SystemExit(1)

def init_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    return driver

def perform_login(driver):
    driver.get(BASE_URL)
    wait = WebDriverWait(driver, 10)
    mail_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="mail"]')))
    pwd_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="password"]')))
    mail_input.send_keys(EMAIL)
    pwd_input.send_keys(PASSWORD)
    login_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'JE ME CONNECTE') or contains(.,'Je me connecte')]")))
    login_btn.click()
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-sections")))

def apply_first_offer(driver):
    wait = WebDriverWait(driver, 10)
    # prend la première section affichée
    first_section = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-sections .section")))
    try:
        first_section.click()
    except:
        pass
    # attend la liste d'offres
    cards = wait.until(EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".offer-card-container")))
    if not cards:
        logging.info("Aucune offre trouvée")
        return
    first_offer = cards[0]
    try:
        # clique sur l'image pour ouvrir l'offre
        first_offer.find_element(By.CSS_SELECTOR, ".offer-image img").click()
    except:
        pass
    time.sleep(1)
    try:
        apply_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Je postule') or contains(.,'JE POSTULE') or contains(.,'Je postuler')]")))
        apply_btn.click()
    except:
        logging.info("Bouton postuler introuvable")
        return
    try:
        confirm_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Confirmer')]")))
        confirm_btn.click()
    except:
        pass
    try:
        ok_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[normalize-space(.)='Ok' or contains(.,'Ok') or contains(.,'OK')]")))
        ok_btn.click()
    except:
        pass
    logging.info("Première offre testée/appliquée")

def main():
    driver = init_driver()
    perform_login(driver)
    apply_first_offer(driver)
    driver.quit()

if __name__ == "__main__":
    main()
