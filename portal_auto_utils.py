import os
PORTAL_USERNAME = os.environ.get("PURE_PORTAL_USERNAME", "")
PORTAL_PASSWORD = os.environ.get("PURE_PORTAL_PASSWORD", "")

def automate_full_workflow(driver, wait, metadata, search_query):
    """
    Full workflow: login, search for record, select, and fill fields.
    Update element selectors as needed for your portal.
    """

    # 1. Login if needed
    login_if_needed(driver, wait)

    # 2. Click 'Research outputs' in the sidebar
    try:
        print("Clicking 'Research outputs'...")
        # The sidebar item likely has text 'Research outputs'. Use XPath to find it by visible text.
        research_outputs_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//span[text()='Research outputs']")))
        research_outputs_btn.click()
        time.sleep(1)
    except Exception as e:
        print(f"Error clicking 'Research outputs': {e}")
        return

    # 3. Search for the record (by title only)
    try:
        print("Searching for record by title...")
        # Always use the record title for the search
        record_title = metadata.get("title", "")
        if not record_title:
            print("No title found in metadata for search.")
            return
        search_box = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@placeholder='Search for research output...']")))
        search_box.clear()
        search_box.send_keys(record_title)
        # Wait for the search button to be clickable
        try:
            search_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[normalize-space()='Search']")))
        except Exception:
            # Try alternative: input[type=submit] with value 'Search'
            search_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@type='submit' and @value='Search']")))
        # Scroll into view just in case
        driver.execute_script("arguments[0].scrollIntoView(true);", search_btn)
        time.sleep(0.5)
        search_btn.click()
        print("Clicked the Search button.")
        time.sleep(2)
    except Exception as e:
        print(f"Error during search: {e}")
        return

    # 3. Wait for and select the correct record from results
    try:
        print("Waiting for search results...")
        # Update selector for the result row/cell
        result_row = wait.until(EC.element_to_be_clickable((By.XPATH, "//table//tr[1]")))
        result_row.click()
    except Exception as e:
        print(f"Error selecting search result: {e}")
        return

    # 4. Wait for the edit form to load
    try:
        print("Waiting for edit form...")
        wait.until(EC.presence_of_element_located((By.XPATH, "//input | //textarea")))
    except Exception as e:
        print(f"Error waiting for edit form: {e}")
        return

    # 5. Fill in the fields
    try:
        print("Filling in fields...")
        fill_form_fields(driver, wait, metadata)
        print("Fields filled. Please review and submit manually if needed.")
    except Exception as e:
        print(f"Error filling form: {e}")
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


# --- Start Selenium with or without user profile ---
def start_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    wait = WebDriverWait(driver, 15)
    print(f"Navigating to {PORTAL_URL} ...")
    driver.get(PORTAL_URL)
    print("Navigation attempted. Check your browser window.")
    return driver, wait

def login_if_needed(driver, wait, username=None, password=None):
    try:
        username_field = wait.until(EC.presence_of_element_located((By.ID, "username")))
        password_field = driver.find_element(By.ID, "password")
        user = username or PORTAL_USERNAME
        pwd = password or PORTAL_PASSWORD
        if user and pwd:
            username_field.clear()
            username_field.send_keys(user)
            password_field.clear()
            password_field.send_keys(pwd)
            password_field.send_keys(Keys.RETURN)
            print("Login submitted.")
            wait.until(EC.presence_of_element_located((By.XPATH, "//span[text()='Research outputs']")))
    except Exception:
        print("Login form not detected or already logged in.")

def fill_form_fields(driver, wait, metadata):
    try:
        wait.until(EC.presence_of_element_located((By.XPATH, "//input | //textarea")))
    except Exception:
        print("Form fields not found. Check if login or navigation failed.")
        return
    try:
        # Example: update selectors to match your form
        driver.find_element(By.NAME, "peer_reviewed").send_keys(metadata.get("peer_reviewed", ""))
        driver.find_element(By.NAME, "publication_status").send_keys(metadata.get("publication_status", ""))
        driver.find_element(By.NAME, "title").send_keys(metadata.get("title", ""))
        driver.find_element(By.NAME, "abstract").send_keys(metadata.get("abstract", ""))
        driver.find_element(By.NAME, "num_pages").send_keys(metadata.get("num_pages", ""))
        # ...add more fields as needed...
        print("Form fields populated.")
    except Exception as e:
        print(f"Error filling form: {e}")
