import time
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    TimeoutException,
    NoSuchElementException,
)


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def init_driver():
    logging.info("Starting bot")
    options = webdriver.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--start-maximized")
    driver = webdriver.Chrome(options=options)
    return driver


def login(driver):
    driver.get("https://al-in.fr/login")
    wait = WebDriverWait(driver, 20)

    try:
        username = wait.until(EC.presence_of_element_located((By.ID, "username")))
        password = driver.find_element(By.ID, "password")

        username.send_keys("ton_email")
        password.send_keys("ton_mot_de_passe")

        driver.find_element(By.ID, "kc-login").click()
        logging.info("Login successful.")
    except TimeoutException:
        logging.error("Login page did not load properly.")
        driver.quit()
        raise


def apply_to_offer(driver, offer):
    logging.info(f"Applying to first offer: {offer}")
    wait = WebDriverWait(driver, 15)

    try:
        apply_btn = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button.btn.btn-secondary.hi-check-round")))

        # Tentative normale
        try:
            apply_btn.click()
        except ElementClickInterceptedException:
            logging.warning("Click intercepted, trying workaround with scroll + ActionChains...")
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", apply_btn)
            time.sleep(1)
            try:
                apply_btn.click()
            except ElementClickInterceptedException:
                ActionChains(driver).move_to_element(apply_btn).click().perform()

        logging.info("Application sent successfully!")

    except TimeoutException:
        logging.error("Apply button not found or not clickable.")
    except NoSuchElementException:
        logging.error("Apply button element not found.")


def main():
    driver = init_driver()
    try:
        login(driver)

        offer = {
            'uid': 'https://api.al-in.fr/sassets/2704995b1fabd3e880ddfe7687bdc614/68b6e35404c04941a5f3f3f1-asset-1756816212-31ucs9uc.JPG',
            'img_src': 'https://api.al-in.fr/sassets/2704995b1fabd3e880ddfe7687bdc614/68b6e35404c04941a5f3f3f1-asset-1756816212-31ucs9uc.JPG',
            'price_text': '776 € (650 € Hors charge)',
            'typ': '34m2 | T1',
            'loc': 'Paris (75018)'
        }

        apply_to_offer(driver, offer)

    finally:
        time.sleep(5)
        driver.quit()


if __name__ == "__main__":
    main()
