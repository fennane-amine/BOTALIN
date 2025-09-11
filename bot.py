# bot.py (complet, version robuste avec safe_click / loader wait / aggressive scroll)
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
from selenium.common.exceptions import (
    TimeoutException,
    ElementClickInterceptedException,
    StaleElementReferenceException,
)
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver import ActionChains

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


# ---------- Low-level click & loading helpers ----------
def wait_for_no_loading(driver, timeout=12, poll=0.4):
    """
    Wait until likely loading overlays are gone.
    """
    end = time.time() + timeout
    while time.time() < end:
        try:
            loaders = driver.find_elements(By.CSS_SELECTOR, ".loading, .spinner, .overlay, .p-overlay")
            visible = False
            for l in loaders:
                try:
                    if l.is_displayed():
                        visible = True
                        break
                except Exception:
                    continue
            if not visible:
                return True
        except Exception:
            return True
        time.sleep(poll)
    return False


def safe_click(driver, element, timeout_wait_no_loading=12):
    """
    Robust click: wait loader gone, try normal click retries, JS click, ActionChains fallback.
    """
    wait_for_no_loading(driver, timeout=timeout_wait_no_loading)

    # try normal click a few times
    for attempt in range(3):
        try:
            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
        except Exception:
            pass
        try:
            element.click()
            return True
        except ElementClickInterceptedException:
            # overlay likely present: wait and retry
            wait_for_no_loading(driver, timeout=2)
            time.sleep(0.2)
            continue
        except StaleElementReferenceException:
            return False
        except Exception:
            break

    # JS click fallback
    try:
        driver.execute_script("arguments[0].click();", element)
        return True
    except Exception:
        pass

    # ActionChains fallback
    try:
        actions = ActionChains(driver)
        actions.move_to_element(element).click().perform()
        return True
    except Exception:
        return False


def click_section(driver, section_name, wait_timeout=12):
    """
    Find and safely click a section button. Returns True if clicked or already selected.
    """
    xpath = f"//div[contains(@class,'offer-sections')]//div[contains(normalize-space(.),'{section_name}')]"
    try:
        btn = WebDriverWait(driver, wait_timeout).until(
            EC.presence_of_element_located((By.XPATH, xpath))
        )
    except TimeoutException:
        return False

    try:
        cls = btn.get_attribute("class") or ""
        if "selected" in cls:
            wait_for_no_loading(driver, timeout=4)
            return True
    except Exception:
        pass

    ok = safe_click(driver, btn, timeout_wait_no_loading=wait_timeout)
    if ok:
        time.sleep(0.9)
    else:
        try:
            driver.execute_script("window.scrollBy(0, -120);")
        except Exception:
            pass
        wait_for_no_loading(driver, timeout=2)
        ok = safe_click(driver, btn, timeout_wait_no_loading=wait_timeout)
        if ok:
            time.sleep(0.9)
    return ok


def get_offer_cards_in_current_section(driver, min_expected=1, timeout=8, poll=0.5):
    """
    Wait for .offer-list-container and return normalized offer-card elements.
    Tries scrolling the container to trigger lazy load until min_expected found or timeout.
    """
    try:
        container = WebDriverWait(driver, WAIT_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-list-container"))
        )
    except TimeoutException:
        return []

    attempts = 0
    last_count = -1
    elapsed = 0.0
    while elapsed < timeout:
        try:
            cards = container.find_elements(By.CSS_SELECTOR, "app-offer-card, .offer-card-container")
        except Exception:
            cards = []
        normalized = []
        for c in cards:
            try:
                if "offer-card-container" in (c.get_attribute("class") or ""):
                    normalized.append(c)
                else:
                    inner = c.find_element(By.CSS_SELECTOR, ".offer-card-container")
                    normalized.append(inner)
            except Exception:
                continue
        cur = len(normalized)
        if cur >= min_expected:
            return normalized
        # scroll to bottom of container to trigger load
        try:
            driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight;", container)
        except Exception:
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            except Exception:
                pass
        time.sleep(poll)
        elapsed += poll
        if cur == last_count:
            attempts += 1
        else:
            attempts = 0
        last_count = cur
        # slight extra condition: if we've tried a few times with no change, break
        if attempts >= 3:
            break

    return normalized


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


# ---------- Baseline (aggressive) ----------
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
            for (let i=0;i<steps;i++){
              c.scrollTop = c.scrollTop + Math.round(c.clientHeight/steps);
            }
            return true;
            """
            driver.execute_script(js, container)
        except Exception:
            pass
        time.sleep(pause)
        cards = get_offer_cards_in_current_section(driver, min_expected=0, timeout=1, poll=0.2)
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
    logging.info(f"Displayed section counts (after wait): {displayed_counts}")

    for sect in section_names:
        ok = click_section(driver, sect)
        if not ok:
            logging.debug(f"Section button '{sect}' not found or not clickable during baseline.")
            continue

        # wait for container
        try:
            container = WebDriverWait(driver, WAIT_TIMEOUT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-list-container"))
            )
        except TimeoutException:
            logging.info(f"No offer-list-container present for '{sect}' during baseline.")
            continue

        # do progressive scrolls inside container
        _progressive_scroll_container_to_bottom(driver, container, max_attempts=max_scroll_attempts, pause=scroll_pause)

        # stabilization
        prev_counts = []
        attempts = 0
        while attempts < 6:
            cards = get_offer_cards_in_current_section(driver, min_expected=0, timeout=1, poll=0.2)
            cur_count = len(cards)
            prev_counts.append(cur_count)
            if len(prev_counts) > 4:
                prev_counts.pop(0)
            if len(prev_counts) >= 3 and prev_counts[-1] == prev_counts[-2] == prev_counts[-3]:
                break
            time.sleep(0.6)
            attempts += 1

        cards = get_offer_cards_in_current_section(driver, min_expected=0, timeout=1, poll=0.2)
        final_count = len(cards)
        logging.info(f"Baseline: gathered {final_count} cards in section '{sect}' (displayed_count={displayed_counts.get(sect)})")

        for card in cards:
            info = extract_offer_info(card)
            if info["uid"]:
                found_uids.add(info["uid"])

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
            # use safe search for the login button then safe click
            btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                EC.presence_of_element_located((By.XPATH, "//button[contains(.,'JE ME CONNECTE') or contains(.,'Je me connecte')]"))
            )
            safe_click(driver, btn)
        except TimeoutException:
            logging.error("Login button not found during perform_login.")
            return False
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".offer-sections")))
        logging.info("Login successful.")
        save_cookies(driver)
        return True
    except Exception as e:
        logging.error(f"Login process failed: {e}")
        return False


def ensure_logged_in(driver, wait):
    if is_logged_in(driver):
        return True
    logging.info("Not logged in. Trying to load cookies if available.")
    try:
        loaded = load_cookies_to_driver(driver)
        if loaded and is_logged_in(driver):
            logging.info("Logged in after loading cookies.")
            return True
    except Exception as e:
        logging.debug(f"Loading cookies raised: {e}")
    logging.info("Performing full login with credentials.")
    ok = perform_login(driver, wait)
    if not ok:
        logging.error("Full login failed.")
    return ok


# ---------- Main ----------
def main():
    logging.info("Starting bot")
    seen = load_seen()
    logging.info(f"Loaded {len(seen)} seen offers (from {SEEN_FILE} if present)")

    try:
        driver = init_driver()
    except Exception as e:
        logging.error(f"Driver init failed: {e}")
        return

    wait = WebDriverWait(driver, WAIT_TIMEOUT)
    try:
        if not ensure_logged_in(driver, wait):
            logging.error("Could not authenticate; stopping run.")
            return

        existing = gather_existing_offers(driver)
        if existing:
            new_count = 0
            for uid in existing:
                if uid not in seen:
                    seen.add(uid)
                    new_count += 1
            if new_count:
                save_seen(seen)
            logging.info(f"Baseline: added {new_count} current offers to seen. Total seen now: {len(seen)}")
        else:
            logging.info("Baseline scan found no offers (page maybe empty).")

        start_time = datetime.utcnow()
        end_time = start_time + timedelta(seconds=MAX_RUN_SECONDS)
        logging.info(f"Bot will run until {end_time.isoformat()} UTC (or until it finds & applies). Poll every {POLL_INTERVAL}s")

        section_names = [
            "Communes demandées",
            "Communes limitrophes",
            "Autres communes du département"
        ]

        while datetime.utcnow() < end_time:
            if not is_logged_in(driver):
                logging.warning("Session appears logged out during run. Re-authenticating...")
                if not ensure_logged_in(driver, wait):
                    logging.error("Re-authentication failed; aborting this run.")
                    break

            applied = False
            for sect in section_names:
                logging.info(f"Selecting section '{sect}'")
                if not click_section(driver, sect):
                    logging.warning(f"Section '{sect}' not found; skipping")
                    continue

                cards = get_offer_cards_in_current_section(driver)
                logging.info(f"Found {len(cards)} cards in '{sect}'")
                for card in cards:
                    info = extract_offer_info(card)
                    uid = info["uid"]
                    logging.debug(f"Offer uid={uid} price={info['price']} typ={info['typ']} loc={info['loc']}")
                    if not uid or uid in seen:
                        continue
                    if info["price"] is None:
                        continue
                    is_t2 = "T2" in info["typ"].upper() or "| T2" in info["typ"].upper()
                    if is_t2 and info["price"] <= 600:
                        logging.info(f"Found matching NEW offer: {info}")
                        try:
                            img_el = card.find_element(By.CSS_SELECTOR, ".offer-image img")
                            if not safe_click(driver, img_el):
                                logging.warning("Could not open offer detail by clicking image.")
                                continue
                        except Exception as e:
                            logging.warning(f"Could not open offer detail: {e}")
                            continue

                        # ensure logged in
                        if not is_logged_in(driver):
                            logging.warning("Session logged out just before applying. Re-authenticating...")
                            if not ensure_logged_in(driver, wait):
                                logging.error("Re-authentication failed; cannot apply.")
                                continue

                        # click Je postule (safe)
                        try:
                            apply_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                                EC.presence_of_element_located((By.XPATH, "//button[contains(.,'Je postule') or contains(.,'JE POSTULE') or contains(.,'Je postuler')]"))
                            )
                            safe_click(driver, apply_btn)
                            logging.info("Clicked 'Je postule'")
                        except TimeoutException:
                            logging.warning("Apply button not found (timeout). Trying alternative selectors.")
                            try:
                                apply_btn = driver.find_element(By.CSS_SELECTOR, ".btn-secondary.hi-check-round")
                                safe_click(driver, apply_btn)
                            except Exception as e:
                                logging.error(f"Failed to click apply: {e}")
                                continue

                        # confirm
                        try:
                            confirm_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                                EC.presence_of_element_located((By.XPATH, "//button[contains(.,'Confirmer')]"))
                            )
                            safe_click(driver, confirm_btn)
                            logging.info("Clicked 'Confirmer'")
                        except TimeoutException:
                            logging.warning("Confirm button not found (maybe not required)")

                        # ok
                        try:
                            ok_btn = WebDriverWait(driver, WAIT_TIMEOUT).until(
                                EC.presence_of_element_located((By.XPATH, "//button[normalize-space(.)='Ok' or contains(.,'Ok') or contains(.,'OK')]"))
                            )
                            safe_click(driver, ok_btn)
                            logging.info("Clicked 'Ok'")
                        except TimeoutException:
                            logging.warning("Ok button not found (maybe not required)")

                        # read result text
                        try:
                            txt = WebDriverWait(driver, WAIT_TIMEOUT).until(
                                EC.presence_of_element_located((By.CSS_SELECTOR, ".text_picto_vert"))
                            )
                            val = txt.text.strip()
                            logging.info(f"Application result text: {val}")
                            seen.add(uid)
                            save_seen(seen)
                            save_cookies(driver)
                            applied = True
                            break
                        except TimeoutException:
                            logging.warning("Result text not found; still saving seen and continuing.")
                            seen.add(uid)
                            save_seen(seen)
                            save_cookies(driver)
                            applied = True
                            break

                if applied:
                    break

            if applied:
                logging.info("Applied to an offer; stopping this run.")
                break

            remaining = (end_time - datetime.utcnow()).total_seconds()
            if remaining <= 0:
                break
            sleep_for = min(POLL_INTERVAL, max(0, remaining))
            logging.info(f"No matching offers found. Sleeping {sleep_for}s before next poll...")
            time.sleep(sleep_for)

        logging.info("Bot finished run.")
    except Exception as e:
        logging.error(f"Unhandled error in main loop: {e}")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
