import os
import time
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import ElementClickInterceptedException, TimeoutException

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

EMAIL = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")

URL = "https://www.al-in.fr/login"  # adapte si nécessaire
SECTIONS = ["Communes demandées", "Communes limitrophes", "Autres communes du département"]

def init_driver():
    logging.info("Init webdriver via Selenium Manager")
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    driver = webdriver.Chrome(options=options)
    logging.info("Selenium Manager Chrome OK")
    return driver

def login(driver):
    driver.get(URL)
    logging.info("Performing login")
    wait = WebDriverWait(driver, 15)
    wait.until(EC.presence_of_element_located((By.NAME, "email"))).send_keys(EMAIL)
    driver.find_element(By.NAME, "password").send_keys(PASSWORD)
    driver.find_element(By.XPATH, "//button[contains(text(),'Se connecter')]").click()
    # attendre que la page principale se charge
    time.sleep(5)
    logging.info("Login seems successful")

def scroll_section(driver, section_name):
    logging.info(f"Selecting section: {section_name}")
    try:
        # cliquer sur la section
        section_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, f"//div[contains(@class,'section') and contains(text(),'{section_name}')]"))
        )
        section_button.click()
        time.sleep(2)
    except (ElementClickInterceptedException, TimeoutException):
        logging.warning(f"Cannot select '{section_name}'")
        return []

    # récupérer toutes les cartes dans la section
    cards = driver.find_elements(By.XPATH, "//div[contains(@class,'offer-card-container')]")
    logging.info(f"Found {len(cards)} cards in section {section_name}")
    return cards

def apply_first_offer(driver, card):
    logging.info("Opening first offer")
    ActionChains(driver).move_to_element(card).click().perform()
    time.sleep(3)

    # cliquer sur "Je postule"
    postule_btn = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Je postule')]"))
    )
    postule_btn.click()
    logging.info("Clicked 'Je postule'")
    time.sleep(2)

    # cliquer sur "Confirmer"
    confirm_btn = WebDriverWait(driver, 10).until(
        EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Confirmer')]"))
    )
    confirm_btn.click()
    logging.info("Clicked 'Confirmer'")
    time.sleep(3)

    # vérifier le popin final
    try:
        final_text = driver.find_element(By.XPATH, "//div[contains(@class,'title-modal') and contains(text(),'Ce logement a bien été ajouté')]")
        logging.info("Candidature envoyée avec succès !")
        logging.info(final_text.text)
    except:
        logging.warning("Impossible de vérifier le popin final")

def main():
    driver = init_driver()
    try:
        login(driver)
        all_cards = []
        for section in SECTIONS:
            cards = scroll_section(driver, section)
            all_cards.extend(cards)
        if not all_cards:
            logging.info("Aucune offre trouvée")
            return

        # prendre la première offre
        apply_first_offer(driver, all_cards[0])

    finally:
        logging.info("Fermeture du navigateur dans 5s")
        time.sleep(5)
        driver.quit()

if __name__ == "__main__":
    main()
