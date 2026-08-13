import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# --- CONFIGURATION ---
CHROME_PROFILE_PATH = r"C:\Users\lberin\AppData\Local\Google\Chrome\User Data"  # Update if your profile is elsewhere
PROFILE_NAME = "Default"  # Change if you use a different Chrome profile
PORTAL_URL = "https://researchportal.hkust.edu.hk/admin/workspace.xhtml?uid=6"
USERNAME = "your_username"  # Optional: only if login is needed
PASSWORD = "your_password"  # Optional: only if login is needed

# --- START SELENIUM WITH EXISTING PROFILE ---
options = webdriver.ChromeOptions()
options.add_argument(f"--user-data-dir={CHROME_PROFILE_PATH}")
options.add_argument(f"--profile-directory={PROFILE_NAME}")
options.add_argument("--start-maximized")

# Optional: make Selenium less detectable
options.add_experimental_option("excludeSwitches", ["enable-automation"])
options.add_experimental_option('useAutomationExtension', False)

# Start driver
service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service, options=options)
wait = WebDriverWait(driver, 30)

def login_if_needed():
    # If login page detected, fill in credentials
    try:
        # Update selectors as needed for your login form
        username_field = wait.until(EC.presence_of_element_located((By.ID, "username")))
        password_field = driver.find_element(By.ID, "password")
        username_field.clear()
        username_field.send_keys(USERNAME)
        password_field.clear()
        password_field.send_keys(PASSWORD)
        password_field.send_keys(Keys.RETURN)
        print("Login submitted.")
        time.sleep(3)
    except Exception:
        print("Login form not detected or already logged in.")

def fill_form_fields():
    # Wait for the form to load (update selector as needed)
    try:
        wait.until(EC.presence_of_element_located((By.XPATH, "//input | //textarea")))
    except Exception:
        print("Form fields not found. Check if login or navigation failed.")
        return

    # --- EXAMPLES: Update these selectors to match your form ---
    try:
        # Peer-reviewed
        peer_reviewed = driver.find_element(By.NAME, "peer_reviewed")
        peer_reviewed.click()  # or .send_keys("Yes")

        # Publication status
        pub_status = driver.find_element(By.NAME, "publication_status")
        pub_status.send_keys("Published (online/print date distinguished)")

        # Title
        title_field = driver.find_element(By.NAME, "title")
        title_field.clear()
        title_field.send_keys("Your Title Here")

        # Abstract
        abstract_field = driver.find_element(By.NAME, "abstract")
        abstract_field.clear()
        abstract_field.send_keys("Your abstract here.")

        # Number of pages
        pages_field = driver.find_element(By.NAME, "num_pages")
        pages_field.clear()
        pages_field.send_keys("10")

        # ...repeat for all other fields as needed...

        print("Form fields populated.")
    except Exception as e:
        print(f"Error filling form: {e}")

if __name__ == "__main__":
    try:
        driver.get(PORTAL_URL)
        time.sleep(3)
        login_if_needed()
        time.sleep(3)
        fill_form_fields()
        # Optionally: driver.quit() when done
    except Exception as e:
        print(f"Automation failed: {e}")
        driver.quit()
