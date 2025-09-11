import time
import tempfile
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# -----------------------------
# CONFIG
# -----------------------------
EMAIL = "mohamed-amine.fennane@epita.fr"
PASSWORD = "&9.Mnq.6F8'M/wm{"
APPLY_REAL = True  # True = envoie réel de la candidature

# -----------------------------
# LOGGING
# -----------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# -----------------------------
# INIT DRIVER
# -----------------------------
def init_driver():
    options = Options()
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    # Profil temporaire pour éviter conflit de session
    user_data_dir = tempfile.mkdtemp()
    options.add_argument(f"--user-data-dir={user_data_dir}")
    driver = webdriver.Chrome(options=options)
    return driver

# -----------------------------
# LOGIN
# -----------------------------
def login(driver):
    logging.info("Opening login page...")
    driver.get("https://example.com/login")  # mettre l'URL réelle de login
    try:
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.NAME, "email"))).send_keys(EMAIL)
        driver.find_element(By.NAME, "password").send_keys(PASSWORD)
        driver.find_element(By.XPATH, "//button[contains(text(),'Se connecter')]").click()
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "dashboard")))
        logging.info("Login successful")
    except Exception as e:
        logging.error(f"Login failed: {e}")
        driver.quit()

# -----------------------------
# SCROLL
# -----------------------------
def scroll_to_bottom(driver):
    driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
    time.sleep(2)

# -----------------------------
# SELECT FIRST OFFER IN COMMUNES LIMITROPHES
# -----------------------------
def apply_first_offer(driver):
    try:
        # Cliquer sur section "Communes limitrophes"
        section = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//div[contains(text(),'Communes limitrophes')]"))
        )
        section.click()
        time.sleep(2)

        # Récupérer la première offre
        first_offer = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, ".offer-card"))
        )
        first_offer.click()
        time.sleep(2)

        # Cliquer sur "Je postule"
        apply_button = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Je postule')]"))
        )
        apply_button.click()
        logging.info("Clicked on 'Je postule'")

        # Si mode réel, confirmer candidature
        if APPLY_REAL:
            confirm_button = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Confirmer')]"))
            )
            confirm_button.click()
            logging.info("Application sent successfully!")

        time.sleep(2)

    except Exception as e:
        logging.error(f"Error during offer application: {e}")

# -----------------------------
# MAIN
# -----------------------------
def main():
    driver = init_driver()
    try:
        login(driver)
        scroll_to_bottom(driver)
        apply_first_offer(driver)
    finally:
        logging.info("Closing browser...")
        driver.quit()

# -----------------------------
# RUN
# -----------------------------
if __name__ == "__main__":
    main()
