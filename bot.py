# test_apply_first_offer.py
"""
Test script: se loguer, cliquer "Communes demandées", ouvrir la 1ère offre, tenter "Je postule".
By default APPLY is False -> le script n'enverra PAS la confirmation finale.
Set APPLY=true to actually click 'Confirmer' and 'Ok'.
Env variables used: EMAIL, PASSWORD, APPLY (true/false), HEADLESS (true/false)
"""
import os
import time
import logging
import stat
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

EMAIL = os.environ.get("EMAIL")
PASSWORD = os.environ.get("PASSWORD")
APPLY = os.environ.get("APPLY", "false").lower() in ("1", "true", "yes")
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("1", "true", "yes")
WAIT = 10

if not EMAIL or not PASSWORD:
    logging.error("Set EMAIL and PASSWORD environment variables before running.")
    raise SystemExit(1)


def init_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    try:
        logging.info("Init webdriver via Selenium Manager")
        driver = webdriver.Chrome(options=options)
        logging.info("Selenium Manager Chrome OK")
        return driver
    except Exception as e:
        logging.warning("Selenium Manager failed, using webdriver-manager: %s", e)
    # fallback
    path = ChromeDriverManager().install()
    try:
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IEXEC)
    except Exception:
        pass
    service = Service(path)
    driver = webdriver.Chrome(service=service, options=options)
    return driver


def save_cookies(driver, fname="session_cookies.json"):
    try:
        import json
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(driver.get_cookies(), f, ensure_ascii=False, indent=2)
        logging.info("Saved cookies to %s", fname)
    except Exception as e:
        logging.warning("Saving cookies failed: %s", e)


def load_cookies(driver, fname="session_cookies.json"):
    import json
    if not os.path.exists(fname):
        return False
    try:
        with open(fname, "r", encoding="utf-8") as f:
            cookies = json.load(f)
    except Exception as e:
        logging.warning("Failed to load cookies: %s", e)
        return False
    driver.get("https://al-in.fr")
    added = 0
    for c in cookies:
        ck = dict(c)
        ck.pop("sameSite", None)
        ck.pop("hostOnly", None)
        ck.pop("domain", None)
        if "expiry" in ck:
            try:
                ck["expiry"] = int(ck["expiry"])
            except Exception:
                ck.pop("expiry", None)
        try:
            driver.add_cookie(ck)
            added += 1
        except Exception:
            continue
    if added:
        logging.info("Loaded %d cookies", added)
        driver.refresh()
        return True
    return False


def wait_presence(driver, by, sel, timeout=WAIT):
    return WebDriverWait(driver, timeout).until(EC.presence_of_element_located((by, sel)))


def wait_clickable(driver, by, sel, timeout=WAIT):
    return WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((by, sel)))


def is_logged_in(driver):
    try:
        driver.find_element(By.CSS_SELECTOR, ".offer-sections")
        return True
    except Exception:
        return False


def do_login(driver):
    logging.info("Performing login")
    driver.get("https://al-in.fr/#/connexion-demandeur")
    try:
        wait_presence(driver, By.CSS_SELECTOR, "form.global-form", timeout=WAIT)
        mail = wait_presence(driver, By.CSS_SELECTOR, 'input[formcontrolname="mail"]')
        pwd = wait_presence(driver, By.CSS_SELECTOR, 'input[formcontrolname="password"]')
        mail.clear()
        mail.send_keys(EMAIL)
        pwd.clear()
        pwd.send_keys(PASSWORD)
        # click button Je me connecte (text variation tolerant)
        try:
            btn = wait_clickable(driver, By.XPATH, "//button[contains(translate(., 'abcdefghijklmnopqrstuvwxyz','ABCDEFGHIJKLMNOPQRSTUVWXYZ'),'JE ME CONNECTE') or contains(.,'Je me connecte')]", timeout=WAIT)
        except Exception:
            btn = wait_clickable(driver, By.XPATH, "//button[contains(.,'JE ME CONNECTE') or contains(.,'Je me connecte') or contains(.,'JE ME CONNECTER')]", timeout=WAIT)
        btn.click()
        # wait for the main offer sections to appear
        wait_presence(driver, By.CSS_SELECTOR, ".offer-sections", timeout=WAIT+5)
        logging.info("Login seems successful")
        save_cookies(driver)
        return True
    except Exception as e:
        logging.error("Login failed: %s", e)
        return False


def click_section(driver, section_name):
    logging.info("Selecting section: %s", section_name)
    xpath = f"//div[contains(@class,'offer-sections')]//div[contains(normalize-space(.),'{section_name}')]"
    try:
        btn = wait_clickable(driver, By.XPATH, xpath, timeout=WAIT)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
        btn.click()
        time.sleep(1.0)
        return True
    except Exception as e:
        logging.warning("Section button not found/clickable: %s", e)
        return False


def get_first_card(driver):
    # look for the container, then the first card
    try:
        container = wait_presence(driver, By.CSS_SELECTOR, ".offer-list-container", timeout=WAIT)
    except TimeoutException:
        logging.warning("No offer-list-container found")
        return None
    # card selector: app-offer-card or .offer-card-container
    try:
        cards = container.find_elements(By.CSS_SELECTOR, "app-offer-card, .offer-card-container")
        if not cards:
            logging.info("No cards in container")
            return None
        # normalize to offer-card-container element
        for c in cards:
            try:
                if "offer-card-container" in (c.get_attribute("class") or ""):
                    return c
                inner = c.find_element(By.CSS_SELECTOR, ".offer-card-container")
                return inner
            except Exception:
                continue
    except Exception as e:
        logging.warning("Error fetching cards: %s", e)
        return None
    return None


def open_offer_click_first_and_apply(driver):
    card = get_first_card(driver)
    if not card:
        logging.info("Aucune offre trouvée dans la section.")
        return False

    # extract some info for logging
    try:
        price = card.find_element(By.CSS_SELECTOR, ".price").text.strip()
    except Exception:
        price = "n/a"
    try:
        typ = card.find_element(By.CSS_SELECTOR, ".typology").text.strip()
    except Exception:
        typ = "n/a"
    try:
        loc = card.find_element(By.CSS_SELECTOR, ".location").text.strip()
    except Exception:
        loc = "n/a"
    logging.info("First offer -> price: %s typ: %s loc: %s", price, typ, loc)

    # click the image to open detail
    try:
        img = card.find_element(By.CSS_SELECTOR, ".offer-image img")
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", img)
        img.click()
    except Exception as e:
        logging.error("Cannot click offer image: %s", e)
        return False

    # wait for apply button on offer page
    time.sleep(1.0)
    try:
        apply_btn = wait_clickable(driver, By.XPATH, "//button[contains(.,'Je postule') or contains(.,'JE POSTULE') or contains(.,'Je postuler')]", timeout=WAIT)
        logging.info("'Je postule' button found")
    except TimeoutException:
        logging.error("'Je postule' not found on offer page")
        return False

    # click Je postule
    try:
        apply_btn.click()
        logging.info("Clicked 'Je postule'")
    except Exception as e:
        logging.error("Failed clicking 'Je postule': %s", e)
        return False

    # wait for confirmation button inside modal
    time.sleep(0.8)
    try:
        confirm_btn = WebDriverWait(driver, WAIT).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Confirmer') or contains(.,'CONFIRMER')]"))
        )
        logging.info("'Confirmer' button present in modal")
    except TimeoutException:
        logging.warning("'Confirmer' not found (modal may differ). Searching alternative...")
        confirm_btn = None

    if not APPLY:
        logging.info("APPLY=false -> stopping before final confirmation (dry run).")
        return True

    # if we are allowed to apply, click confirm then OK
    if confirm_btn:
        try:
            confirm_btn.click()
            logging.info("Clicked 'Confirmer'")
        except Exception as e:
            logging.error("Failed to click 'Confirmer': %s", e)

    # wait for the success modal and Ok button
    try:
        ok_btn = WebDriverWait(driver, WAIT).until(
            EC.element_to_be_clickable((By.XPATH, "//button[normalize-space(.)='Ok' or contains(.,'Ok') or contains(.,'OK')]"))
        )
        ok_btn.click()
        logging.info("Clicked 'Ok' in success modal")
    except TimeoutException:
        logging.warning("Ok button not found after confirm. Trying to find final modal text anyway.")

    # read .text_picto_vert if present
    try:
        txt_el = WebDriverWait(driver, WAIT).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".text_picto_vert")))
        txt = txt_el.text.strip()
        logging.info("Result text found: %s", txt)
    except TimeoutException:
        logging.warning("Result text (.text_picto_vert) not found after apply.")
        txt = None

    return True


def main():
    driver = init_driver()
    try:
        # go to site and try load cookies
        driver.get("https://al-in.fr")
        loaded = False
        try:
            loaded = load_cookies(driver)
        except Exception:
            pass

        if not loaded or not is_logged_in(driver):
            ok = do_login(driver)
            if not ok:
                logging.error("Login failed - cannot proceed")
                return

        # click Communes demandées first
        if not click_section(driver, "Communes demandées"):
            logging.warning("Cannot select 'Communes demandées' - proceeding to 'Communes limitrophes'")
            click_section(driver, "Communes limitrophes")

        # try to open first offer and apply (or dry-run)
        success = open_offer_click_first_and_apply(driver)
        if success:
            logging.info("Test flow completed (APPLY=%s)", APPLY)
        else:
            logging.info("Test flow failed or no offer applied")

    finally:
        # save cookies for reuse
        try:
            save_cookies(driver)
        except Exception:
            pass
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
