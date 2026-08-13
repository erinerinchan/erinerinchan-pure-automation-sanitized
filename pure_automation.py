# pure_automation.py
# Browser automation for HKUST Pure portal — fills metadata and validates records.
# Prerequisites: pip install selenium webdriver-manager
#
# How it works:
#   1. Launches Chrome with a dedicated persistent profile (~/.pure_chrome_profile)
#      so your login session persists between runs.
#   2. Clicks "Research outputs" in the left sidebar.
#   3. Types the record title in the search bar and waits for results.
#   4. Clicks the matching result to open the editor page.
#   5. Fills empty fields with metadata from the APIs.
#   6. Changes status from "For validation" to "Validated" and clicks Save.

import os
import re
import sys
import time

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait, Select
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.common.exceptions import (
        TimeoutException,
        NoSuchElementException,
        StaleElementReferenceException,
        WebDriverException,
        ElementNotInteractableException,
        ElementClickInterceptedException,
    )
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

try:
    from webdriver_manager.chrome import ChromeDriverManager
    HAS_WDM = True
except ImportError:
    HAS_WDM = False

try:
    from rapidfuzz import fuzz as rf_fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False


PURE_BASE = "https://researchportal.hkust.edu.hk"
PURE_WORKSPACE_URL = f"{PURE_BASE}/admin/workspace.xhtml?uid=3"
CHROME_PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".pure_chrome_profile")


class PureAutomation:
    """Automate HKUST Pure portal form-filling via Selenium."""

    def __init__(self):
        self.driver = None
        self.wait = None

    # ------------------------------------------------------------------
    # Browser lifecycle
    # ------------------------------------------------------------------

    def start_browser(self, headless=False, user_data_dir=None):
        """Start Chrome with a persistent user profile for session persistence. Always use the same profile unless overridden."""
        import os
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from webdriver_manager.chrome import ChromeDriverManager
        chrome_options = Options()
        if headless:
            chrome_options.add_argument('--headless')
        # Always use the same user data directory for persistent login
        if user_data_dir is None:
            user_data_dir = os.path.abspath('selenium_profile')
        chrome_options.add_argument(f'--user-data-dir={user_data_dir}')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option('excludeSwitches', ['enable-automation'])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        service = Service(ChromeDriverManager().install())
        try:
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            self.driver.set_window_size(1400, 1000)
            print(f"[INFO] Chrome started with persistent profile at '{user_data_dir}'. Login will be remembered across runs.")
        except Exception as e:
            print(f"[ERROR] Chrome could not start: {e}")
            print("[HINT] Try updating Chrome and ChromeDriver. If the problem persists, delete the 'selenium_profile' folder and try again.")
            raise

    def ensure_logged_in(self):
        """Navigate to Pure and wait for the user to log in if needed."""
        self.driver.get(PURE_WORKSPACE_URL)
        time.sleep(4)

        # Check for SSO / login redirect
        current = self.driver.current_url.lower()
        if any(kw in current for kw in ("login", "sso", "cas", "auth", "adfs", "saml")):
            print("\n  You need to log in to Pure.")
            print("  Please complete login in the browser window.")
            print("  Waiting (up to 5 minutes)...")
            for _ in range(300):
                time.sleep(1)
                current = self.driver.current_url.lower()
                if "workspace" in current or "admin/editor" in current:
                    print("  Login detected!")
                    time.sleep(3)
                    return True
            print("  Login timed out.")
            return False

        if "workspace" in current or "admin" in current:
            print("  Already logged into Pure.")
            return True

        print(f"  Unexpected page: {self.driver.current_url}")
        print("  Please log in manually. Waiting...")
        for _ in range(300):
            time.sleep(1)
            current = self.driver.current_url.lower()
            if "workspace" in current or "admin" in current:
                print("  Login detected!")
                time.sleep(3)
                return True
        return False

    def close(self):
        """Quit the browser."""
        if self.driver:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _wait_for_ajax(self, timeout=10):
        """Wait for PrimeFaces / JSF AJAX queue to drain."""
        try:
            WebDriverWait(self.driver, timeout).until(
                lambda d: d.execute_script(
                    "return (typeof PrimeFaces === 'undefined' || "
                    "PrimeFaces.ajax.Queue.isEmpty === undefined || "
                    "PrimeFaces.ajax.Queue.isEmpty());"
                )
            )
        except (TimeoutException, WebDriverException):
            pass
        time.sleep(0.5)

    def _scroll_to(self, element):
        """Scroll an element into the visible viewport."""
        self.driver.execute_script(
            "arguments[0].scrollIntoView({behavior:'auto',block:'center'});",
            element,
        )
        time.sleep(0.4)

    def _click(self, element):
        """Click with multiple fallbacks for PrimeFaces elements."""
        self._scroll_to(element)
        try:
            element.click()
        except (ElementClickInterceptedException, ElementNotInteractableException):
            try:
                ActionChains(self.driver).move_to_element(element).click().perform()
            except Exception:
                self.driver.execute_script("arguments[0].click();", element)
        time.sleep(0.3)

    def _set_value(self, element, text):
        """Clear a field and type new text, with a JS fallback."""
        self._scroll_to(element)
        try:
            element.click()
            time.sleep(0.2)
            element.send_keys(Keys.CONTROL, "a")
            time.sleep(0.1)
            element.send_keys(text)
        except (ElementNotInteractableException, WebDriverException):
            # JS fallback
            self.driver.execute_script(
                "arguments[0].value = arguments[1];"
                "arguments[0].dispatchEvent(new Event('change',{bubbles:true}));"
                "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));",
                element, text,
            )
        time.sleep(0.3)

    def _get_value(self, element):
        """Read current value of a field."""
        return (element.get_attribute("value") or element.text or "").strip()

    def _find_field(self, label_text, field_tags=("textarea", "input")):
        """Locate a form field by its visible label text.

        Tries several strategies to cope with JSF/PrimeFaces markup.
        """
        # Strategy 1: <label for="id"> → element by id
        try:
            labels = self.driver.find_elements(
                By.XPATH,
                f"//label[contains(normalize-space(.),'{label_text}')]",
            )
            for lbl in labels:
                for_id = lbl.get_attribute("for")
                if for_id:
                    try:
                        el = self.driver.find_element(By.ID, for_id)
                        if el.tag_name.lower() in field_tags and el.is_displayed():
                            return el
                    except NoSuchElementException:
                        pass
        except Exception:
            pass

        # Strategy 2: span/label/div containing text → walk up to parent, find field
        try:
            label_elems = self.driver.find_elements(
                By.XPATH,
                f"//*[self::label or self::span or self::div]"
                f"[contains(normalize-space(.),'{label_text}')]",
            )
            for lbl in label_elems:
                ancestor = lbl
                for _ in range(5):
                    try:
                        ancestor = ancestor.find_element(By.XPATH, "./..")
                    except Exception:
                        break
                    for tag in field_tags:
                        candidates = ancestor.find_elements(By.TAG_NAME, tag)
                        for c in candidates:
                            if c.is_displayed() and c.get_attribute("type") != "hidden":
                                return c
        except Exception:
            pass

        # Strategy 3: following-sibling / following axis
        try:
            for tag in field_tags:
                xpath = (
                    f"//*[contains(normalize-space(text()),'{label_text}')]"
                    f"/following::{tag}[1]"
                )
                try:
                    el = self.driver.find_element(By.XPATH, xpath)
                    if el.is_displayed():
                        return el
                except NoSuchElementException:
                    pass
        except Exception:
            pass

        return None

    @staticmethod
    def _fuzzy(a, b):
        if HAS_RAPIDFUZZ:
            return rf_fuzz.token_sort_ratio(a.lower(), b.lower())
        sa, sb = set(a.lower().split()), set(b.lower().split())
        if not sa or not sb:
            return 0
        return int(100 * len(sa & sb) / len(sa | sb))

    # ------------------------------------------------------------------
    # Step 1: Navigate to Research Outputs list
    # ------------------------------------------------------------------

    def go_to_portal(self, url="https://researchportal.hkust.edu.hk/admin/workspace.xhtml"):
        """Click 'Research outputs' in the left sidebar to get the search list."""
        print(f"[INFO] Navigating to Pure portal: {url}")
        self.driver.get(url)
        self._wait_for_ajax(10)
        print(f"[DEBUG] Current URL after navigation: {self.driver.current_url}")

    def go_to_research_outputs(self):
        """Click 'Research outputs' in the left sidebar to get the search list."""
        print("\n  Navigating to Research outputs...")

        # Make sure we're on the workspace/admin page
        current = self.driver.current_url.lower()
        if "workspace" not in current and "admin" not in current:
            self.driver.get(PURE_WORKSPACE_URL)
            time.sleep(4)
            self._wait_for_ajax()

        # Click "Research outputs" in the sidebar
        found = False
        for by, sel in [
            (By.XPATH, "//a[normalize-space(text())='Research outputs']"),
            (By.XPATH, "//span[normalize-space(text())='Research outputs']/ancestor::a"),
            (By.LINK_TEXT, "Research outputs"),
            (By.PARTIAL_LINK_TEXT, "Research outputs"),
            (By.XPATH, "//*[contains(@class,'sidebar')]//a[contains(text(),'Research output')]"),
            (By.XPATH, "//nav//a[contains(text(),'Research output')]"),
        ]:
            try:
                elem = WebDriverWait(self.driver, 8).until(
                    EC.element_to_be_clickable((by, sel))
                )
                self._click(elem)
                found = True
                break
            except (TimeoutException, NoSuchElementException):
                continue

        if not found:
            print("  Could not find sidebar link — navigating directly...")
            self.driver.get(PURE_WORKSPACE_URL)

        time.sleep(4)
        self._wait_for_ajax()
        print("  On Research outputs page.")
        return True

    # ------------------------------------------------------------------
    # Step 2: Search for the record by title
    # ------------------------------------------------------------------

    def search_record(self, query):
        """Type a query into the research-output search bar and submit."""
        print(f"\n  Searching for: {query[:80]}...")

        # Locate the search input at the top of the Research outputs list
        search_input = None
        for by, sel in [
            (By.CSS_SELECTOR, "input[placeholder*='Search for research output']"),
            (By.CSS_SELECTOR, "input[placeholder*='search']"),
            (By.XPATH, "//input[contains(@placeholder,'earch')]"),
            (By.CSS_SELECTOR, "input[type='text'][role='searchbox']"),
            (By.CSS_SELECTOR, "input[type='search']"),
            (By.CSS_SELECTOR, "input.search-input"),
            (By.XPATH, "//input[@type='text'][ancestor::*[contains(@class,'search')]]"),
        ]:
            try:
                search_input = WebDriverWait(self.driver, 8).until(
                    EC.presence_of_element_located((by, sel))
                )
                if search_input and search_input.is_displayed():
                    break
                search_input = None
            except TimeoutException:
                continue

        if not search_input:
            print("  ERROR: Could not locate the search field.")
            return False

        # Clear and type the query
        self._scroll_to(search_input)
        search_input.click()
        time.sleep(0.3)
        search_input.send_keys(Keys.CONTROL, "a")
        time.sleep(0.1)
        search_input.send_keys(query)
        time.sleep(1)

        # Click the "Search" button
        search_clicked = False
        for by, sel in [
            (By.XPATH, "//button[normalize-space(text())='Search']"),
            (By.XPATH, "//input[@type='submit' and @value='Search']"),
            (By.CSS_SELECTOR, "button[title='Search']"),
            (By.XPATH, "//button[contains(@class,'search')]"),
            (By.XPATH, "//button[contains(@aria-label,'Search')]"),
        ]:
            try:
                btn = self.driver.find_element(by, sel)
                if btn.is_displayed():
                    self._click(btn)
                    search_clicked = True
                    break
            except NoSuchElementException:
                continue

        if not search_clicked:
            search_input.send_keys(Keys.RETURN)

        # Wait for results to load
        time.sleep(5)
        self._wait_for_ajax(15)

        # Print the result count if visible
        try:
            result_text = self.driver.find_element(
                By.XPATH, "//*[contains(text(),'result')]"
            ).text
            print(f"  {result_text.strip()}")
        except NoSuchElementException:
            pass

        print("  Search completed.")
        return True

    # ------------------------------------------------------------------
    # Step 3: Click the matching result to open the editor
    # ------------------------------------------------------------------

    def click_result(self, result_index=0):
        """Click the search result whose title best matches *expected_title*."""
        time.sleep(2)

        # Gather all plausible record links from the results list
        links = []
        for by, sel in [
            (By.XPATH, "//a[contains(@href,'editor')]"),
            (By.XPATH, "//a[contains(@href,'researchoutput')]"),
            (By.CSS_SELECTOR, "a.link"),
            (By.CSS_SELECTOR, ".result-container a"),
            (By.CSS_SELECTOR, "[class*='result'] a"),
        ]:
            try:
                links.extend(self.driver.find_elements(by, sel))
            except Exception:
                pass

        # De-duplicate by (href, text)
        seen = set()
        unique = []
        for lnk in links:
            try:
                href = lnk.get_attribute("href") or ""
                text = lnk.text.strip()
                key = (href, text)
                if key not in seen and text and len(text) > 15:
                    seen.add(key)
                    unique.append(lnk)
            except StaleElementReferenceException:
                continue

        if not unique:
            print("  ERROR: No search results found on the page.")
            return False

        # Score each link against expected title; pick best
        best, best_score = unique[0], 0
        if expected_title:
            for lnk in unique:
                try:
                    score = self._fuzzy(expected_title, lnk.text.strip())
                    if score > best_score:
                        best, best_score = lnk, score
                except StaleElementReferenceException:
                    continue

        try:
            label = best.text.strip()[:90]
        except StaleElementReferenceException:
            label = "(stale)"

        print(f"  Clicking result (match score {best_score}): {label}")
        self._click(best)

        # Click the result and wait for the editor to appear
        print("  Clicked search result, waiting for editor window...")
        time.sleep(2)
        self._wait_for_ajax(10)

        # Handle possible new window/tab
        main_window = self.driver.current_window_handle
        all_windows = self.driver.window_handles
        if len(all_windows) > 1:
            for handle in all_windows:
                if handle != main_window:
                    print("  Switching to new editor window/tab.")
                    self.driver.switch_to.window(handle)
                    break

        # Handle possible iframe/modal
        try:
            iframes = self.driver.find_elements(By.TAG_NAME, "iframe")
            for iframe in iframes:
                self.driver.switch_to.frame(iframe)
                if "editor" in self.driver.current_url.lower() or "contributiontojournaleditor" in self.driver.current_url.lower():
                    print("  Switched to editor iframe.")
                    break
                self.driver.switch_to.default_content()
        except Exception as e:
            print(f"  [WARN] Could not switch to iframe: {e}")

        # Wait for editor fields to appear
        try:
            WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(),'Title of the contribution') or contains(text(),'Title')]/ancestor::*[self::div or self::section or self::form][1]"))
            )
            print("  Editor fields detected. Ready to fill.")
        except Exception:
            print("  [WARN] Editor fields not detected. Proceeding anyway.")
        return True

    # ------------------------------------------------------------------
    # Step 4: Fill metadata fields on the editor page
    # ------------------------------------------------------------------

    def fill_metadata(self, metadata):
        """Fill empty metadata fields on the editor page, including advanced widgets."""
        print("\n  Filling metadata fields...")
        filled, skipped = [], []

        # --- Simple fields (text, textarea, radio, select) ---
        # First matching label wins; fallback labels handle different Pure versions
        field_map = [
            ("title", [
                "Title of the contribution in original language",
                "Title of the contribution",
                "Title",
            ], ("textarea", "input")),
            ("abstract", [
                "Abstract / Description",
                "Abstract",
            ], ("textarea",)),
            ("pages", [
                "Pages (from-to)",
                "Pages",
            ], ("input",)),
            ("num_pages", [
                "Number of pages",
            ], ("input",)),
            ("article_number", [
                "Article number",
            ], ("input",)),
        ]

        for key, label_variants, tags in field_map:
            value = metadata.get(key, "")
            if not value:
                continue
            value = str(value).strip()

            # Try each label variant until we find the field
            el = None
            for label in label_variants:
                el = self._find_field(label, tags)
                if el:
                    break

            if not el:
                skipped.append(f"{key} (field not found)")
                continue

            current = self._get_value(el)
            if current:
                skipped.append(f"{key} (already has value)")
                continue

            print(f"    Writing {key}: {value[:60]}{'...' if len(value) > 60 else ''}")
            self._set_value(el, value)
            try:
                el.send_keys(Keys.TAB)
            except Exception:
                pass
            self._wait_for_ajax(5)
            filled.append(key)

        # --- Contributors and affiliations ---
        self.fill_contributors(metadata)

        # --- Journal and ISSN ---
        self.fill_journal(metadata)

        # --- Electronic versions and links ---
        self.fill_electronic_versions(metadata)
        self.fill_other_links(metadata)

        # --- Keywords ---
        self.fill_keywords(metadata)

        # --- RGC FUNDED ---
        self.fill_rgc(metadata)

        # --- Research output classification ---
        self.fill_research_output_classification(metadata)

        # --- External publication IDs ---
        self.fill_external_ids(metadata)

        print(f"\n  Filled : {', '.join(filled) if filled else '(none — all fields already populated)'}")
        if skipped:
            print(f"  Skipped: {', '.join(skipped)}")
        return filled, skipped

    def fill_contributors(self, metadata):
        print("  Filling contributors and affiliations...")
        # Try to find the contributors section
        try:
            contributors_section = self.driver.find_element(By.XPATH, "//*[contains(text(),'Contributors')]/ancestor::*[contains(@class,'section') or contains(@class,'panel')][1]")
            # If there are no contributors, click "Add person..." and fill from metadata['authors']
            persons = contributors_section.find_elements(By.XPATH, ".//div[contains(@class,'contributor') or contains(@class,'person')]")
            if not persons and metadata.get('authors'):
                add_btn = contributors_section.find_element(By.XPATH, ".//button[contains(text(),'Add person') or contains(text(),'Add author')]")
                self._click(add_btn)
                # TODO: Implement author search and add logic
                print("    [TODO] Add authors from metadata['authors']")
            else:
                print("    Contributors already present or not detected.")
        except Exception as e:
            print(f"    [WARN] Could not fill contributors: {e}")

    def fill_journal(self, metadata):
        print("  Filling journal and ISSN...")
        try:
            journal_field = self._find_field("Journal", ("input", "textarea"))
            if journal_field and not self._get_value(journal_field) and metadata.get('journal'):
                self._set_value(journal_field, metadata['journal'])
                print(f"    Set journal: {metadata['journal']}")
            issn_field = self._find_field("ISSN", ("input",))
            if issn_field and not self._get_value(issn_field) and metadata.get('issn'):
                self._set_value(issn_field, metadata['issn'])
                print(f"    Set ISSN: {metadata['issn']}")
        except Exception as e:
            print(f"    [WARN] Could not fill journal/ISSN: {e}")

    def fill_electronic_versions(self, metadata):
        print("  Filling electronic versions (DOI, PDF, license)...")
        try:
            # Look for DOI field in the electronic versions section
            ev_section = self.driver.find_element(By.XPATH, "//*[contains(text(),'Electronic version') or contains(text(),'Final published version')]/ancestor::*[contains(@class,'section') or contains(@class,'panel')][1]")
            doi_links = ev_section.find_elements(By.XPATH, ".//a[contains(@href,'doi.org')]")
            if not doi_links and metadata.get('doi'):
                add_btn = ev_section.find_element(By.XPATH, ".//button[contains(text(),'Add electronic version') or contains(text(),'Add file') or contains(text(),'Add link')]")
                self._click(add_btn)
                # TODO: Implement DOI/link add logic
                print(f"    [TODO] Add DOI link: {metadata['doi']}")
            else:
                print("    DOI already present or not detected.")
        except Exception as e:
            print(f"    [WARN] Could not fill electronic versions: {e}")

    def fill_other_links(self, metadata):
        print("  Filling other links (Scopus, OpenAlex)...")
        try:
            links_section = self.driver.find_element(By.XPATH, "//*[contains(text(),'Other links')]/ancestor::*[contains(@class,'section') or contains(@class,'panel')][1]")
            # Check for existing links
            scopus_link = any('scopus.com' in a.get_attribute('href') for a in links_section.find_elements(By.TAG_NAME, 'a'))
            openalex_link = any('openalex.org' in a.get_attribute('href') for a in links_section.find_elements(By.TAG_NAME, 'a'))
            # Add links if missing
            if not scopus_link and metadata.get('scopus_link'):
                print(f"    [TODO] Add Scopus link: {metadata['scopus_link']}")
            if not openalex_link and metadata.get('oa_link'):
                print(f"    [TODO] Add OpenAlex link: {metadata['oa_link']}")
        except Exception as e:
            print(f"    [WARN] Could not fill other links: {e}")

    def fill_keywords(self, metadata):
        print("  Filling keywords...")
        try:
            kw_field = self._find_field("Keywords", ("input", "textarea"))
            if kw_field and not self._get_value(kw_field) and metadata.get('keywords'):
                self._set_value(kw_field, ', '.join(metadata['keywords']))
                print(f"    Set keywords: {', '.join(metadata['keywords'])}")
        except Exception as e:
            print(f"    [WARN] Could not fill keywords: {e}")

    def fill_rgc(self, metadata):
        print("  Filling RGC FUNDED...")
        try:
            rgc_field = self._find_field("RGC FUNDED", ("input", "textarea"))
            if rgc_field and not self._get_value(rgc_field) and metadata.get('rgc_funded') is not None:
                self._set_value(rgc_field, 'Yes' if metadata['rgc_funded'] else 'No')
                print(f"    Set RGC FUNDED: {'Yes' if metadata['rgc_funded'] else 'No'}")
        except Exception as e:
            print(f"    [WARN] Could not fill RGC FUNDED: {e}")

    def fill_research_output_classification(self, metadata):
        print("  Filling research output classification...")
        try:
            roc_field = self._find_field("Research output classification", ("input", "textarea"))
            if roc_field and not self._get_value(roc_field):
                self._set_value(roc_field, 'N/A')
                print("    Set research output classification: N/A")
        except Exception as e:
            print(f"    [WARN] Could not fill research output classification: {e}")

    def fill_external_ids(self, metadata):
        print("  Filling external publication IDs...")
        try:
            extid_section = self.driver.find_element(By.XPATH, "//*[contains(text(),'External publication IDs') or contains(text(),'Publication import ID')]/ancestor::*[contains(@class,'section') or contains(@class,'panel')][1]")
            # Check for Scopus and OpenAlex IDs
            scopus_id_present = any('scopus' in el.text.lower() for el in extid_section.find_elements(By.XPATH, ".//*[contains(text(),'Scopus')]") )
            openalex_id_present = any('openalex' in el.text.lower() for el in extid_section.find_elements(By.XPATH, ".//*[contains(text(),'OpenAlex')]") )
            if not scopus_id_present and metadata.get('scopus_doc_id'):
                print(f"    [TODO] Add Scopus ID: {metadata['scopus_doc_id']}")
            if not openalex_id_present and metadata.get('oa_id_short'):
                print(f"    [TODO] Add OpenAlex ID: {metadata['oa_id_short']}")
        except Exception as e:
            print(f"    [WARN] Could not fill external publication IDs: {e}")
