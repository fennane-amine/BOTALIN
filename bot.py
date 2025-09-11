import json
import logging
import os
import tempfile
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# ------------------ CONFIG ------------------
LOGIN_URL = "https://example.com/login"  # Remplace par ton URL de login
OFFERS_URL = "https://example.com/offers"  # Remplace par l'URL des offres
EMAIL = "ton_email@example.com"
PASSWORD = "ton_mot_de_passe"
TEST_MODE = True  # True = n'applique qu'à la première offre

COOKIES_FILE = "session_cookies.json"
OFFERS_SEEN_FILE = "offers_seen.json"

# ------------------ LOGGING ------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ------------------ DRIVER ------------------
def init_driver():
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")

    # Utiliser un dossier temporaire unique pour éviter conflit user-data-dir
    temp_dir = tempfile.mkdtemp()
    options.add_argument(f"--user-data-dir={temp_dir}")

    driver = webdriver.Chrome(options=options)
    return driver

# ------------------ COOKIES ------------------
def load_cookies(driver):
    if os.path.exists(COOKIES_FILE):
        logging.info(f"Attempting to load cookies from {COOKIES_FILE}")
        with open(COOKIES_FILE, "r") as f:
            cookies = json.load(f)
        driver.get(LOGIN_URL)  # Nécessaire pour charger les cookies sur le domaine correct
        for cookie in cookies:
            driver.add_cookie(cookie)
        logging.info(f"Added {len(cookies)} cookies")
        return True
    return False

def save_cookies(driver):
    cookies = driver.get_cookies()
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f)
    logging.info(f"Saved {len(cookies)} cookies to {COOKIES_FILE}")

# ------------------ LOGIN ------------------
def perform_login(driver):
    logging.info("Performing full login with credentials")
    driver.get(LOGIN_URL)
    time.sleep(2)  # attendre que la page charge

    # Remplace ces sélecteurs par ceux de ton formulaire
    driver.find_element(By.NAME, "email").send_keys(EMAIL)
    driver.find_element(By.NAME, "password").send_keys(PASSWORD)
    driver.find_element(By.XPATH, "//button[@type='submit']").click()

    # Attendre que le login réussisse
    WebDriverWait(driver, 10).until(
        EC.url_changes(LOGIN_URL)
    )
    logging.info("Login successful")
    save_cookies(driver)

# ------------------ OFFERS ------------------
def fetch_offers(driver):
    logging.info("Fetching offers")
    driver.get(OFFERS_URL)
    time.sleep(2)  # attendre le chargement initial
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "app-offer-card"))
    )

    offer_elements = driver.find_elements(By.CSS_SELECTOR, "app-offer-card")
    logging.info(f"Found {len(offer_elements)} offers")
    offers = []

    for el in offer_elements:
        try:
            price = el.find_element(By.CSS_SELECTOR, ".price").text.strip()
            typology = el.find_element(By.CSS_SELECTOR, ".typology").text.strip()
            location = el.find_element(By.CSS_SELECTOR, ".location").text.strip()
            offers.append({
                "price": price,
                "typology": typology,
                "location": location
            })
        except:
            continue
    return offers

def apply_to_offer(driver, offer):
    logging.info(f"Applying to offer: {offer['location']} - {offer['typology']} - {offer['price']}")
    # Ici, ajoute ton code pour cliquer sur le bouton postuler ou remplir le formulaire
    # Exemple :
    # el.find_element(By.CSS_SELECTOR, ".apply-button").click()
    time.sleep(1)  # Simule un petit délai

# ------------------ MAIN ------------------
def run_bot():
    logging.info("Starting bot")
    offers_seen = []
    if os.path.exists(OFFERS_SEEN_FILE):
        with open(OFFERS_SEEN_FILE, "r") as f:
            offers_seen = json.load(f)
        logging.info(f"Loaded {len(offers_seen)} seen offers")

    driver = init_driver()

    try:
        if not load_cookies(driver):
            perform_login(driver)
        else:
            driver.get(OFFERS_URL)
            logging.info("Loaded cookies, logged in")

        offers = fetch_offers(driver)

        if not offers:
            logging.warning("No offers found")
            return

        for i, offer in enumerate(offers):
            key = f"{offer['location']}|{offer['typology']}|{offer['price']}"
            if key in offers_seen:
                continue

            apply_to_offer(driver, offer)
            offers_seen.append(key)

            if TEST_MODE:
                logging.info("TEST MODE: only applying to first new offer")
                break

    finally:
        driver.quit()
        with open(OFFERS_SEEN_FILE, "w") as f:
            json.dump(offers_seen, f)
        logging.info("Bot finished run")

# ------------------ RUN ------------------
if __name__ == "__main__":
    run_bot()
