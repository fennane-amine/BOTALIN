import time
import logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

LOGIN_URL = "https://candidat.pole-emploi.fr/espacepersonnel/"
SEARCH_URL = "https://candidat.pole-emploi.fr/offres/recherche?lieux=75112"

USERNAME = "mohamed-amine.fennane@epita.fr"      # ⚠️ à remplacer
PASSWORD = "&9.Mnq.6F8'M/wm{"   # ⚠️ à remplacer


def init_driver():
    options = Options()
    options.add_argument("--start-maximized")
    driver = webdriver.Chrome(options=options)
    return driver


def login(driver):
    driver.get(LOGIN_URL)
    logging.info("Page login ouverte")

    # attendre le champ identifiant
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.ID, "identifiant"))
    )
    driver.find_element(By.ID, "identifiant").send_keys(USERNAME)
    driver.find_element(By.ID, "mdp").send_keys(PASSWORD)

    driver.find_element(By.ID, "submit").click()
    logging.info("Login soumis")

    # attendre redirection
    WebDriverWait(driver, 20).until(
        EC.url_contains("candidat.pole-emploi.fr/espacepersonnel")
    )
    logging.info("Connexion réussie")


def scroll_to_communes(driver):
    driver.get(SEARCH_URL)
    logging.info("Recherche chargée")

    # scroller pour que la section soit visible
    time.sleep(3)
    driver.execute_script("window.scrollBy(0, 1000);")
    logging.info("Scrolled vers Communes limitrophes")
    time.sleep(3)


def select_first_offer(driver):
    try:
        offers = driver.find_elements(By.CSS_SELECTOR, "li.result")
        if not offers:
            logging.error("Aucune offre trouvée dans Communes limitrophes")
            return None
        offers[0].click()
        logging.info("Première offre cliquée")
        return True
    except Exception as e:
        logging.error(f"Erreur sélection offre: {e}")
        return None


def apply_to_offer(driver):
    try:
        WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.LINK_TEXT, "Je postule"))
        ).click()
        logging.info("Bouton 'Je postule' cliqué")

        # attendre la suite du formulaire
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "form"))
        )

        # ici on simule clic sur "Continuer"
        continuer = driver.find_elements(By.XPATH, "//button[contains(text(),'Continuer')]")
        if continuer:
            continuer[0].click()
            logging.info("Étape 'Continuer' passée")

        # attendre bouton confirmer
        confirmer = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Confirmer')]"))
        )
        confirmer.click()
        logging.info("Candidature confirmée ✅")

    except Exception as e:
        logging.error(f"Erreur dans le process de candidature: {e}")


def main():
    logging.info("Starting bot")
    driver = init_driver()
    try:
        login(driver)
        scroll_to_communes(driver)
        if select_first_offer(driver):
            apply_to_offer(driver)
        else:
            logging.warning("Pas d’offre pour postuler.")
    finally:
        time.sleep(5)
        driver.quit()
        logging.info("Bot terminé")


if __name__ == "__main__":
    main()
