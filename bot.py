import time
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

import os

EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BASE_URL = "https://www.al-in.fr/login"

def init_driver():
    logging.info("Init webdriver via Selenium Manager")
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    # options.add_argument("--headless=new")  # décommenter si besoin
    driver = webdriver.Chrome(options=options)
    logging.info("Selenium Manager Chrome OK")
    return driver

def login(driver):
    logging.info("Opening login page")
    driver.get(BASE_URL)
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.NAME, "email"))
    )
    driver.find_element(By.NAME, "email").send_keys(EMAIL)
    driver.find_element(By.NAME, "password").send_keys(PASSWORD)
    driver.find_element(By.XPATH, "//button[contains(text(),'Connexion')]").click()
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CLASS_NAME, "section"))
    )
    logging.info("Login seems successful")

def scroll_and_collect_offers(driver):
    logging.info("Scrolling to load offers")
    offers = []
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        cards = driver.find_elements(By.CLASS_NAME, "offer-card-container")
        for c in cards:
            if c not in offers:
                offers.append(c)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height
    logging.info(f"Total offers found: {len(offers)}")
    return offers

def apply_first_offer(driver, offer):
    logging.info("Opening first offer")
    driver.execute_script("arguments[0].scrollIntoView(true);", offer)
    offer.click()
    # wait page load
    time.sleep(2)
    try:
        btn_postule = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Je postule')]"))
        )
        btn_postule.click()
        logging.info("Clicked 'Je postule'")
        # confirmation
        btn_confirmer = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Confirmer')]"))
        )
        btn_confirmer.click()
        logging.info("Clicked 'Confirmer'")
        # wait final popin
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Ok')]"))
        ).click()
        logging.info("Final OK clicked. Application done.")
    except Exception as e:
        logging.error(f"Error during applying: {e}")

def main():
    driver = init_driver()
    try:
        login(driver)
        time.sleep(2)
        offers = scroll_and_collect_offers(driver)
        if offers:
            apply_first_offer(driver, offers[0])
        else:
            logging.info("No offers found to apply.")
    finally:
        logging.info("Closing driver")
        driver.quit()

if __name__ == "__main__":
    main()
