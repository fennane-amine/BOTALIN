import json
import logging
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# ----------------- CONFIG -----------------
LOGIN_URL = "https://www.al-in.fr/login"
OFFERS_URL = "https://www.al-in.fr/offres"
USERNAME = "mohamed-amine.fennane@epita.fr"
PASSWORD = "&9.Mnq.6F8'M/wm{"
COOKIES_FILE = "session_cookies.json"
OFFERS_SEEN_FILE = "offers_seen.json"

# ----------------- LOGGING -----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# ----------------- DRIVER -----------------
def init_driver():
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-blink-features=AutomationControlled")
    # options.add_argument("--headless=new")  # Décommenter si tu veux headless
    driver = webdriver.Chrome(options=options)
    return driver

# ----------------- COOKIE MANAGEMENT -----------------
def load_cookies(driver):
    try:
        with open(COOKIES_FILE, "r") as f:
            cookies = json.load(f)
        for cookie in cookies:
            driver.add_cookie(cookie)
        logging.info(f"Attempted to load cookies: added {len(cookies)} cookies")
        return True
    except FileNotFoundError:
        logging.info("No cookies file found")
        return False

def save_cookies(driver):
    cookies = driver.get_cookies()
    with open(COOKIES_FILE, "w") as f:
        json.dump(cookies, f)
    logging.info(f"Saved {len(cookies)} cookies to {COOKIES_FILE}")

# ----------------- LOGIN -----------------
def login(driver):
    driver.get(LOGIN_URL)
    time.sleep(2)  # Attendre que la page charge
    # Si cookies valides, on devrait être connecté
    if load_cookies(driver):
        driver.refresh()
        time.sleep(2)
        if "login" not in driver.current_url.lower():
            logging.info("Logged in using cookies")
            return
    # Sinon login complet
    driver.get(LOGIN_URL)
    time.sleep(2)
    driver.find_element(By.NAME, "email").send_keys(USERNAME)
    driver.find_element(By.NAME, "password").send_keys(PASSWORD)
    driver.find_element(By.XPATH, "//button[contains(text(),'Se connecter')]").click()
    time.sleep(5)
    save_cookies(driver)
    logging.info("Login completed and cookies saved")

# ----------------- OFFERS -----------------
def load_seen_offers():
    try:
        with open(OFFERS_SEEN_FILE, "r") as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()

def save_seen_offers(offers_seen):
    with open(OFFERS_SEEN_FILE, "w") as f:
        json.dump(list(offers_seen), f)

def fetch_offers(driver):
    driver.get(OFFERS_URL)
    time.sleep(3)  # attendre que la page charge complètement

    offers = driver.find_elements(By.CSS_SELECTOR, "app-offer-card .offer-card-container")
    logging.info(f"Found {len(offers)} offers on page")
    return offers

def apply_to_offer(driver, offer_element):
    try:
        apply_button = offer_element.find_element(By.XPATH, ".//button[contains(text(),'Postuler')]")
        apply_button.click()
        time.sleep(1)
        logging.info("Applied to an offer")
    except:
        logging.warning("Apply button not found or already applied")

# ----------------- BOT -----------------
def run_bot():
    logging.info("Starting bot")
    driver = init_driver()
    offers_seen = load_seen_offers()

    try:
        login(driver)
        offers = fetch_offers(driver)

        new_offers = 0
        for offer in offers:
            offer_id = offer.get_attribute("outerHTML")  # tu peux aussi utiliser un id si présent
            if offer_id not in offers_seen:
                apply_to_offer(driver, offer)
                offers_seen.add(offer_id)
                new_offers += 1

        save_seen_offers(offers_seen)
        logging.info(f"Processed {len(offers)} offers, applied to {new_offers} new offers")

    finally:
        driver.quit()
        logging.info("Bot finished run")

# ----------------- ENTRY POINT -----------------
if __name__ == "__main__":
    run_bot()
