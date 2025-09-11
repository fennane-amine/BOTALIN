import os
import json
import time
import logging
import tempfile
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException, TimeoutException

# -------------------------------
# Configuration logging
# -------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# -------------------------------
# Credentials
# -------------------------------
USERNAME = os.environ.get("BOT_USERNAME", "votre_email@example.com")
PASSWORD = os.environ.get("BOT_PASSWORD", "votre_mot_de_passe")

# -------------------------------
# Fichiers de cookies et offres vues
# -------------------------------
COOKIES_FILE = "session_cookies.json"
SEEN_OFFERS_FILE = "offers_seen.json"

# -------------------------------
# Init driver
# -------------------------------
def init_driver():
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    # options.add_argument("--headless=new")  # décommenter si besoin en CI/CD

    # Profil temporaire unique pour éviter conflit de session
    unique_profile = f"/tmp/chrome_profile_{int(time.time()*1000)}"
    options.add_argument(f"--user-data-dir={unique_profile}")

    driver = webdriver.Chrome(options=options)
    return driver

# -------------------------------
# Gestion des cookies
# -------------------------------
def load_cookies(driver):
    if os.path.exists(COOKIES_FILE):
        try:
            with open(COOKIES_FILE, "r") as f:
                cookies = json.load(f)
            for cookie in cookies:
                driver.add_cookie(cookie)
            logging.info(f"Loaded {len(cookies)} cookies")
        except Exception as e:
            logging.warning(f"Failed to load cookies: {e}")

def save_cookies(driver):
    cookies = driver.get_cookies()
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f)
    logging.info(f"Saved {len(cookies)} cookies")

# -------------------------------
# Gestion des offres vues
# -------------------------------
def load_seen_offers():
    if os.path.exists(SEEN_OFFERS_FILE):
        with open(SEEN_OFFERS_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen_offers(seen_offers):
    with open(SEEN_OFFERS_FILE, "w") as f:
        json.dump(list(seen_offers), f)
    logging.info(f"Saved {len(seen_offers)} seen offers")

# -------------------------------
# Login
# -------------------------------
def login(driver):
    driver.get("https://example.com/login")  # changer l'URL
    time.sleep(2)

    # Tenter de charger les cookies
    load_cookies(driver)
    driver.refresh()
    time.sleep(2)

    # Vérifier si connecté
    if "login" in driver.current_url.lower():
        logging.info("Performing full login")
        driver.find_element(By.ID, "email").send_keys(USERNAME)
        driver.find_element(By.ID, "password").send_keys(PASSWORD)
        driver.find_element(By.ID, "submit-login").click()
        time.sleep(5)
        save_cookies(driver)
        logging.info("Login successful")
    else:
        logging.info("Already logged in via cookies")

# -------------------------------
# Collecte et application offres
# -------------------------------
def get_offers(driver):
    driver.get("https://example.com/offers")  # changer l'URL
    time.sleep(3)
    offers = driver.find_elements(By.CSS_SELECTOR, "app-offer-card")
    logging.info(f"Found {len(offers)} offers on the page")
    return offers

def process_offers(driver, seen_offers):
    offers = get_offers(driver)
    for offer in offers:
        try:
            location = offer.find_element(By.CSS_SELECTOR, ".location").text.strip()
            price = offer.find_element(By.CSS_SELECTOR, ".price").text.strip()
            offer_id = f"{location}-{price}"

            if offer_id in seen_offers:
                continue

            seen_offers.add(offer_id)
            logging.info(f"Applying to offer: {offer_id}")

            # Cliquer sur postuler si disponible
            try:
                apply_button = offer.find_element(By.CSS_SELECTOR, "button.apply")
                apply_button.click()
                logging.info("Application submitted")
                time.sleep(2)
            except NoSuchElementException:
                logging.info("No apply button, skipping")
        except Exception as e:
            logging.warning(f"Failed to process offer: {e}")

# -------------------------------
# Main bot
# -------------------------------
def run_bot():
    logging.info("Starting bot")
    seen_offers = load_seen_offers()

    driver = init_driver()
    try:
        login(driver)
        process_offers(driver, seen_offers)
    finally:
        save_seen_offers(seen_offers)
        driver.quit()
        logging.info("Bot finished run")

# -------------------------------
# Lancement
# -------------------------------
if __name__ == "__main__":
    run_bot()
