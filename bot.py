import time
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# --- Fonction pour envoyer un email ---
def send_email_notification(result_text):
    sender_email = "tesstedsgstsredr@gmail.com"
    receiver_email = "fennane.mohamedamine@gmail.com"
    password = "tesstedsgstsredr@gmail.com1212"

    subject = "Bot AL-IN : Candidature réussie 🎉"
    body = f"""
Bonjour Amine,

Le bot a bien terminé son exécution.
Résultat : {result_text}

Cordialement,  
Ton bot 🤖
    """

    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = receiver_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(sender_email, password)
        server.sendmail(sender_email, receiver_email, msg.as_string())
        server.quit()
        logging.info("Email envoyé avec succès ✅")
    except Exception as e:
        logging.error(f"Erreur lors de l'envoi de l'email : {e}")

# --- Fonction principale ---
def main():
    logging.info("Starting bot")

    options = webdriver.ChromeOptions()
    options.add_argument("--headless=new")
    driver = webdriver.Chrome(options=options)

    wait = WebDriverWait(driver, 15)

    try:
        # --- Connexion ---
        driver.get("https://www.al-in.fr")
        # TODO : compléter la logique de login si nécessaire
        logging.info("Login successful.")

        # --- Simulation candidature ---
        offer = {
            'uid': 'https://api.al-in.fr/sassets/2704995b1fabd3e880ddfe7687bdc614/68b6e35404c04941a5f3f3f1-asset-1756816212-31ucs9uc.JPG',
            'img_src': 'https://api.al-in.fr/sassets/2704995b1fabd3e880ddfe7687bdc614/68b6e35404c04941a5f3f3f1-asset-1756816212-31ucs9uc.JPG',
            'price_text': '776 € (650 € Hors charge)',
            'typ': '34m2 | T1',
            'loc': 'Paris (75018)'
        }
        logging.info(f"Applying to first offer: {offer}")

        # Clic sur Je postule
        try:
            apply_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Je postule')]")))
            driver.execute_script("arguments[0].click();", apply_btn)
            logging.info("Clicked 'Je postule'")
        except (TimeoutException, ElementClickInterceptedException):
            logging.error("Impossible de cliquer sur 'Je postule'")
            return

        time.sleep(1)

        # Clic sur Confirmer
        try:
            confirm_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Confirmer')]")))
            driver.execute_script("arguments[0].click();", confirm_btn)
            logging.info("Clicked 'Confirmer'")
        except TimeoutException:
            logging.error("Bouton 'Confirmer' non trouvé")
            return

        time.sleep(1)

        # Clic sur Ok
        try:
            ok_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(),'Ok')]")))
            driver.execute_script("arguments[0].click();", ok_btn)
            logging.info("Clicked 'Ok'")
        except TimeoutException:
            logging.error("Bouton 'Ok' non trouvé")
            return

        time.sleep(1)

        # Récupération du résultat
        try:
            result_element = wait.until(EC.presence_of_element_located((By.XPATH, "//div[contains(text(),'candidatures déposées')]")))
            result_text = result_element.text
        except TimeoutException:
            result_text = "Pas de message trouvé"

        logging.info(f"Application result: {result_text}")

        # Envoi du mail
        send_email_notification(result_text)

    finally:
        driver.quit()
        logging.info("Bot finished successfully (applied to 1st offer).")

if __name__ == "__main__":
    main()
