# bot.py - aggressive final scroll version
import os
import time
import json
import re
import logging
import stat
from datetime import datetime, timedelta

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from webdriver_manager.chrome import ChromeDriverManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ---------- CONFIG ----------
BASE_URL = "https://al-in.fr/#/connexion-demandeur"
EMAIL = os.environ.get("EMAIL")
PASSWORD = os.environ.get("PASSWORD")
MAX_RUN_SECONDS = int(os.environ.get("MAX_RUN_SECONDS", 300))  # default 5 minutes
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", 15))
WAIT_TIMEOUT = 10  # explicit waits: 10s
SEEN_FILE = "offers_seen.json"
COOKIES_FILE = "session_cookies.json"
HEADLESS = os.environ.get("HEADLESS", "true").lower() in ("1", "true", "yes")

if not EMAIL or not PASSWORD:
    logging.error("EMAIL and PASSWORD environment variables must be set.")
    raise SystemExit(1)


# ---------- Helpers ----------
def load_seen():
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_seen(seen_set):
    try:
        with open(SEEN_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen_set), f, ensure_ascii=False, indent=2)
        logging.info(f"Saved {len(seen_set)} seen offers to {SEEN_FILE}")
    except Exception as e:
        logging.warning(f"Failed to save seen file: {e}")


def save_cookies(driver):
    try:
        cookies = driver.get_cookies()
        with open(COOKIES_FILE, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
        logging.info(f"Saved {len(cookies)} cookies to {COOKIES_FILE}")
    except Exception as e:
        logging.warning(f"Failed to save cookies: {e}")


def load_cookies_to_driver(driver):
    if not os.path.exists(COOKIES_FILE):
        logging.info("No cookies file found.")
        return False
    try:
        with open(COOKIES_FILE, "r", encoding="utf-8") as f:
            cookies = json.load(f)
    except Exception as e:
        logging.warning(f"Failed to read cookies file: {e}")
        return False

    if not cookies:
        logging.info("Cookies file empty.")
        return False

    try:
        driver.get("https://al-in.fr")
    except Exception:
        pass

    added = 0
    for c in cookies:
        cookie = dict(c)
        cookie.pop("sameSite", None)
        cookie.pop("hostOnly", None)
        if "expiry" in cookie:
            try:
                cookie["expiry"] = int(cookie["expiry"])
            except Exception:
                cookie.pop("expiry", None)
        cookie.pop("domain", None)
        try:
            driver.add_cookie(cookie)
            added += 1
        except Exception as e:
            logging.debug(f"Failed to add cookie {cookie.get('name')}: {e}")
    logging.info(f"Attempted to load cookies into browser: added {added} cookies")
    try:
        driver.refresh()
    except Exception:
        pass
    return added > 0


def parse_price(price_text):
    if not price_text:
        return None
    m = re.search(r"(\d{1,3}(?:[ \u00A0]\d{3})*|\d+)\s*€", price_text.replace("\u00A0", " "))
    if not m:
        return None
    num = m.group(1).replace(" ", "")
    try:
        return int(num)
    except:
        return None


def find_section_button(driver, name):
    xpath = f"//div[contains(@class,'offer-sections')]//div[contains(normalize-space(.),'{name}')]"
    try:
        return WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.element_to_be_clickable((By.XPATH, xpath))
        )
    except TimeoutException:
        return None


def get_offer_cards_in_current_section(driver):
    try:
        container = WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-list-container"))
        )
    except TimeoutException:
        return []
    cards = container.find_elements(By.CSS_SELECTOR, "app-offer-card, .offer-card-container")
    normalized = []
    for c in cards:
        try:
            classes = (c.get_attribute("class") or "")
            if "offer-card-container" in classes:
                normalized.append(c)
            else:
                inner = c.find_element(By.CSS_SELECTOR, ".offer-card-container")
                normalized.append(inner)
        except Exception:
            continue
    return normalized


def extract_offer_info(card):
    try:
        img = card.find_element(By.CSS_SELECTOR, ".offer-image img")
        img_src = img.get_attribute("src")
    except Exception:
        img_src = None
    try:
        price_el = card.find_element(By.CSS_SELECTOR, ".price")
        price_text = price_el.text.strip()
        price = parse_price(price_text)
    except Exception:
        price_text = ""
        price = None
    try:
        typ = card.find_element(By.CSS_SELECTOR, ".typology").text.strip()
    except Exception:
        typ = ""
    try:
        loc = card.find_element(By.CSS_SELECTOR, ".location").text.strip()
    except Exception:
        loc = ""
    uid = img_src or f"{loc}|{price_text}|{typ}"
    return {"uid": uid, "img_src": img_src, "price": price, "price_text": price_text, "typ": typ, "loc": loc}


def click_element(driver, el):
    try:
        driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
    except Exception:
        pass
    try:
        el.click()
        return True
    except Exception:
        try:
            driver.execute_script("arguments[0].click();", el)
            return True
        except Exception as e:
            logging.debug(f"JS click failed: {e}")
            return False


# ---------- Driver init ----------
def init_driver():
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")

    try:
        logging.info("Trying Selenium Manager (webdriver.Chrome(options=...))")
        driver = webdriver.Chrome(options=options)
        logging.info("Selenium Manager initialized Chrome successfully.")
        return driver
    except Exception as e:
        logging.warning(f"Selenium Manager failed: {e}. Falling back to webdriver-manager.")

    try:
        logging.info("Using webdriver-manager to download/find chromedriver")
        driver_path = ChromeDriverManager().install()
        if os.path.isdir(driver_path):
            found = None
            for root, dirs, files in os.walk(driver_path):
                for f in files:
                    if f.lower().startswith("chromedriver"):
                        found = os.path.join(root, f)
                        break
                if found:
                    break
            if not found:
                raise RuntimeError(f"No chromedriver binary found in {driver_path}")
            driver_path = found
        try:
            st = os.stat(driver_path)
            os.chmod(driver_path, st.st_mode | stat.S_IEXEC)
        except Exception as e:
            logging.warning(f"chmod on chromedriver failed: {e}")
        service = Service(driver_path)
        driver = webdriver.Chrome(service=service, options=options)
        logging.info("Chrome initialized with webdriver-manager chromedriver.")
        return driver
    except Exception as e:
        logging.error(f"Failed to initialize Chrome driver: {e}")
        raise


# ---------- Final aggressive baseline (force scroll to page bottom) ----------
def _get_displayed_count_for_section(driver, sect_name, timeout=12, poll=0.5):
    end = time.time() + timeout
    while time.time() < end:
        try:
            sections = driver.find_elements(By.CSS_SELECTOR, ".offer-sections .section")
            for s in sections:
                try:
                    full_text = s.text.strip()
                    spans = s.find_elements(By.TAG_NAME, "span")
                    if spans:
                        txt = spans[-1].text.strip()
                        if txt.isdigit() and sect_name in full_text:
                            return int(txt)
                    m = re.search(r"(\d+)", full_text)
                    if m and sect_name in full_text:
                        return int(m.group(1))
                except Exception:
                    continue
        except Exception:
            pass
        time.sleep(poll)
    return None


def _progressive_scroll_container_to_bottom(driver, container, max_attempts=30, pause=0.8):
    prev_counts = []
    attempt = 0
    while attempt < max_attempts:
        try:
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)
        except Exception:
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            except Exception:
                pass
        try:
            js = """
            const c = arguments[0];
            const steps = 6;
            const delay = 40;
            for (let i=0;i<steps;i++){
              c.scrollTop = c.scrollTop + Math.round(c.clientHeight/steps);
            }
            return true;
            """
            driver.execute_script(js, container)
        except Exception:
            pass
        time.sleep(pause)
        cards = get_offer_cards_in_current_section(driver)
        cur_count = len(cards)
        prev_counts.append(cur_count)
        if len(prev_counts) > 5:
            prev_counts.pop(0)
        if len(prev_counts) >= 4 and all(x == prev_counts[0] for x in prev_counts):
            break
        attempt += 1
    return


def gather_existing_offers(driver, max_scroll_attempts=40, scroll_pause=0.8):
    try:
        driver.execute_script("window.scrollTo(0,0);")
    except Exception:
        pass

    found_uids = set()
    section_names = [
        "Communes demandées",
        "Communes limitrophes",
        "Autres communes du département"
    ]

    displayed_counts = {}
    for sect in section_names:
        displayed_counts[sect] = _get_displayed_count_for_section(driver, sect, timeout=12, poll=0.5)

    for sect in section_names:
        btn = find_section_button(driver, sect)
        if not btn:
            continue
        click_element(driver, btn)
        time.sleep(1.0)
        try:
            container = WebDriverWait(driver, WAIT_TIMEOUT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-list-container"))
            )
        except TimeoutException:
            continue

        for i in range(3):
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            except Exception:
                pass
            time.sleep(0.4)

        _progressive_scroll_container_to_bottom(driver, container, max_attempts=max_scroll_attempts, pause=scroll_pause)

        for i in range(4):
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            except Exception:
                pass
            time.sleep(scroll_pause)

        prev_counts = []
        attempts = 0
        while attempts < 6:
            cards = get_offer_cards_in_current_section(driver)
            cur_count = len(cards)
            prev_counts.append(cur_count)
            if len(prev_counts) > 4:
                prev_counts.pop(0)
            if len(prev_counts) >= 3 and prev_counts[-1] == prev_counts[-2] == prev_counts[-3]:
                break
            time.sleep(0.6)
            attempts += 1

        cards = get_offer_cards_in_current_section(driver)
        for card in cards:
            info = extract_offer_info(card)
            if info["uid"]:
                found_uids.add(info["uid"])
        # --- stop after first section processed to speed up baseline ---
        break

    return found_uids


# ---------- Login utilities ----------
def is_logged_in(driver):
    try:
        driver.find_element(By.CSS_SELECTOR, ".offer-sections")
        return True
    except Exception:
        return False


def perform_login(driver, wait):
    try:
        driver.get(BASE_URL)
    except Exception:
        pass

    try:
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form.global-form")))
        mail_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="mail"]')))
        pwd_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[formcontrolname="password"]')))
        mail_input.clear()
        mail_input.send_keys(EMAIL)
        pwd_input.clear()
        pwd_input.send_keys(PASSWORD)
        try:
            btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'JE ME CONNECTE') or contains(.,'Je me connecte')]")))
            btn.click()
        except TimeoutException:
            return False
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-sections")))
        save_cookies(driver)
        return True
    except Exception:
        return False


def ensure_logged_in(driver, wait):
    if is_logged_in(driver):
        return True
    try:
        loaded = load_cookies_to_driver(driver)
        if loaded and is_logged_in(driver):
            return True
    except Exception:
        pass
    return perform_login(driver, wait)


# ---------- Main ----------
def main():
    seen = load_seen()
    try:
        driver = init_driver()
    except Exception:
        return

    wait = WebDriverWait(driver, WAIT_TIMEOUT)
    if not ensure_logged_in(driver, wait):
        return

    existing = gather_existing_offers(driver)
    for uid in existing:
        if uid not in seen:
            seen.add(uid)
    save_seen(seen)

    start_time = datetime.utcnow()
    end_time = start_time + timedelta(seconds=MAX_RUN_SECONDS)

    section_names = [
        "Communes demandées",
        "Communes limitrophes",
        "Autres communes du département"
    ]

    while datetime.utcnow() < end_time:
        if not is_logged_in(driver):
            if not ensure_logged_in(driver, wait):
                break

        applied = False
        for sect in section_names:
            btn = find_section_button(driver, sect)
            if not btn:
                continue
            click_element(driver, btn)
            time.sleep(1)
            cards = get_offer_cards_in_current_section(driver)
            for card in cards:
                info = extract_offer_info(card)
                uid = info["uid"]
                if not uid or uid in seen:
                    continue
                if info["price"] is None:
                    continue
                is_t2 = "T2" in info["typ"].upper() or "| T2" in info["typ"].upper()
                if is_t2 and info["price"] <= 600:
                    try:
                        img_el = card.find_element(By.CSS_SELECTOR, ".offer-image img")
                        click_element(driver, img_el)
                    except Exception:
                        continue

                    if not is_logged_in(driver):
                        if not ensure_logged_in(driver, wait):
                            continue

                    try:
                        apply_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                            EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Je postule') or contains(.,'JE POSTULE') or contains(.,'Je postuler')]"))
                        )
                        apply_btn.click()
                    except TimeoutException:
                        try:
                            apply_btn = driver.find_element(By.CSS_SELECTOR, ".btn-secondary.hi-check-round")
                            apply_btn.click()
                        except Exception:
                            continue

                    try:
                        confirm_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                            EC.element_to_be_clickable((By.XPATH, "//button[contains(.,'Confirmer')]"))
                        )
                        confirm_btn.click()
                    except TimeoutException:
                        pass

                    try:
                        ok_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                            EC.element_to_be_clickable((By.XPATH, "//button[normalize-space(.)='Ok' or contains(.,'Ok') or contains(.,'OK')]"))
                        )
                        ok_btn.click()
                    except TimeoutException:
                        pass

                    try:
                        txt = WebDriverWait(driver, WAIT_TIMEOUT).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, ".text_picto_vert"))
                        )
                        val = txt.text.strip()
                        seen.add(uid)
                        save_seen(seen)
                        save_cookies(driver)
                        applied = True
                        break
                    except TimeoutException:
                        seen.add(uid)
                        save_seen(seen)
                        save_cookies(driver)
                        applied = True
                        break
            # --- stop after first offer applied ---
            if applied:
                break

        if applied:
            break

        remaining = (end_time - datetime.utcnow()).total_seconds()
        if remaining <= 0:
            break
        sleep_for = min(POLL_INTERVAL, max(0, remaining))
        time.sleep(sleep_for)

    try:
        driver.quit()
    except Exception:
        pass


if __name__ == "__main__":
    main()
