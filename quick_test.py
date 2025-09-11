# quick_test.py
# Quick dry-run test reusing functions from bot.py (no changes to bot.py required)
# - opens browser via init_driver()
# - ensure_logged_in (uses EMAIL/PASSWORD from env as in bot.py)
# - clicks Communes demandées (or limitrophes)
# - opens first offer and clicks "Je postule" (dry-run: does NOT click Confirmer/Ok)
# - saves cookies and exits

import logging
import time
import sys

# import functions from your existing bot.py
try:
    from bot import (
        init_driver,
        ensure_logged_in,
        click_section,
        get_offer_cards_in_current_section,
        safe_click,
        extract_offer_info,
        save_cookies,
    )
except Exception as e:
    logging.error("Failed to import from bot.py: %s", e)
    sys.exit(1)

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
WAIT = 10

def open_first_offer_and_click_postule(driver):
    # Try to get cards in current section
    cards = get_offer_cards_in_current_section(driver, min_expected=0, timeout=6, poll=0.4)
    if not cards:
        logging.info("No cards found in this section.")
        return False

    card = cards[0]
    info = extract_offer_info(card)
    logging.info("First offer found -> price=%s typ=%s loc=%s", info.get("price_text"), info.get("typ"), info.get("loc"))

    # click image to open
    try:
        img = card.find_element(By.CSS_SELECTOR, ".offer-image img")
    except Exception as e:
        logging.error("Could not find image element on card: %s", e)
        return False

    ok = safe_click(driver, img)
    if not ok:
        logging.error("Failed to open offer detail by clicking image.")
        return False

    # wait short time for page/modal to render
    time.sleep(1.0)

    # find Je postule button; don't confirm further (dry-run)
    try:
        apply_btn = WebDriverWait(driver, WAIT).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Je postule') or contains(.,'JE POSTULE') or contains(.,'Je postuler')]")),
            )
        logging.info("'Je postule' button located")
    except TimeoutException:
        logging.error("'Je postule' button not found on offer page (dry-run stops here).")
        return False

    # click Je postule (dry-run)
    if safe_click(driver, apply_btn):
        logging.info("Clicked 'Je postule' (dry-run). NOT confirming.")
        return True
    else:
        logging.error("Failed to click 'Je postule'.")
        return False

def main():
    driver = init_driver()
    try:
        # Ensure logged in (uses same logic and env as bot.py)
        if not ensure_logged_in(driver, WebDriverWait(driver, WAIT)):
            logging.error("ensure_logged_in failed; aborting test.")
            return

        # prefer Communes demandées
        logging.info("Trying section: Communes demandées")
        if not click_section(driver, "Communes demandées"):
            logging.info("Communes demandées unavailable or empty; trying Communes limitrophes")
            click_section(driver, "Communes limitrophes")

        # open first offer and click 'Je postule' (dry-run)
        success = open_first_offer_and_click_postule(driver)
        if success:
            logging.info("Dry-run successful: 'Je postule' clicked (no confirmation).")
        else:
            logging.info("Dry-run did not find/apply any offer.")

    finally:
        try:
            save_cookies(driver)
        except Exception as e:
            logging.warning("Could not save cookies: %s", e)
        try:
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()
