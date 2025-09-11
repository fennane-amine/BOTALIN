import json
import logging
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

# -------------------- CONFIG --------------------
USERNAME = "TON_EMAIL"
PASSWORD = "TON_MOT_DE_PASSE"
LOGIN_URL = "https://example.com/login"
OFFERS_URL = "https://example.com/offers"
COOKIES_FILE = "session_cookies.json"
OFFERS_SEEN_FILE = "offers_seen.json"
WAIT_TIMEOUT = 10

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# -------------------- UTILITAIRES --------------------
def load_seen_offers():
    try:
        with open(OFFERS_SEEN_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_seen_offers(seen):
    with open(OFFERS_SEEN_FILE, "w") as f:
        json.dump(seen, f)

def save_cookies(driver):
    cookies = driver.get_cookies()
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f)
    logging.info(f"Saved {len(cookies)} cookies to {COOKIES_FILE}")

def load_cookies(driver):
    try:
        with open(COOKIES_FILE, "r") as f:
            cookies = json.load(f)
        for cookie in cookies:
            driver.add_cookie(cookie)
        logging.info(f"Loaded {len(cookies)} cookies into browser")
    except FileNotFoundError:
        logging.info("No cookies file found, will login manually")

# -------------------- SELENIUM SETUP --------------------
def init_driver():
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    driver = webdriver.Chrome(options=options)
    return driver

# -------------------- LOGIN --------------------
def login(driver):
    driver.get(LOGIN_URL)
    load_cookies(driver)
    driver.get(OFFERS_URL)

    # Vérifier si déjà logué
    try:
        WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-list-container"))
        )
        logging.info("Already logged in via cookies.")
        return
    except TimeoutException:
        logging.info("Not logged in. Performing full login...")

    # Remplir email / mot de passe
    driver.get(LOGIN_URL)
    WebDriverWait(driver, WAIT_TIMEOUT).until(EC.presence_of_element_located((By.NAME, "email"))).send_keys(USERNAME)
    driver.find_element(By.NAME, "password").send_keys(PASSWORD)
    driver.find_element(By.CSS_SELECTOR, "button[type=submit]").click()

    # Attendre que la page des offres soit chargée
    WebDriverWait(driver, WAIT_TIMEOUT).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-list-container"))
    )
    logging.info("Login successful.")
    save_cookies(driver)

# -------------------- RÉCUPÉRER LES OFFRES --------------------
def get_offer_cards_in_current_section(driver):
    try:
        container = WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-list-container"))
        )

        # Scroll pour charger toutes les offres
        driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)
        time.sleep(1)  # attendre que les cartes se chargent

        cards = container.find_elements(By.CSS_SELECTOR, "app-offer-card")
        logging.info(f"Detected {len(cards)} offer cards in current section")
        return cards

    except TimeoutException:
        logging.warning("No offers found in this section.")
        return []

# -------------------- POSTULER À UNE OFFRE --------------------
def apply_to_offer(card):
    try:
        # Cliquer sur l'offre
        card.click()
        time.sleep(1)  # attendre le chargement du formulaire

        # Exemple : cliquer sur le bouton "Postuler"
        apply_button = WebDriverWait(card.parent, WAIT_TIMEOUT).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button.apply"))
        )
        apply_button.click()
        logging.info("Applied to offer successfully.")
    except Exception as e:
        logging.error(f"Failed to apply to offer: {e}")

# -------------------- BOT PRINCIPAL --------------------
def run_bot():
    logging.info("Starting bot")
    seen_offers = load_seen_offers()
    driver = init_driver()

    try:
        login(driver)
        driver.get(OFFERS_URL)
        cards = get_offer_cards_in_current_section(driver)

        # Appliquer à la première offre non vue
        for card in cards:
            offer_id = card.get_attribute("outerHTML")[:50]  # ou utiliser un ID unique si dispo
            if offer_id not in seen_offers:
                apply_to_offer(card)
                seen_offers.append(offer_id)
                save_seen_offers(seen_offers)
                break
        else:
            logging.info("No new offers to apply for.")

    finally:
        logging.info("Bot finished run.")
        driver.quit()

# -------------------- LANCEMENT --------------------
if __name__ == "__main__":
    run_bot()
