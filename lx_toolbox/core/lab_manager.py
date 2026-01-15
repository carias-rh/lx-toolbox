import time
import os
import re
import logging
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
# Assuming WebDriver exceptions like TimeoutException might be caught
from selenium.common.exceptions import TimeoutException 

from .base_selenium_driver import BaseSeleniumDriver
from ..utils.config_manager import ConfigManager
from ..utils.helpers import step_logger, reset_step_counter

class LabManager:
    # External service verification field IDs (from third-party login pages)
    _GITHUB_VERIFY_FIELD = "app_totp"
    
    def __init__(self, config: ConfigManager, browser_name: str = None, is_headless: bool = None):
        self.config = config
        self.logger = step_logger # Alias for convenience

        _browser_name = browser_name if browser_name else self.config.get("General", "default_selenium_driver", "firefox")
        _is_headless = is_headless if is_headless is not None else self.config.get("General", "debug_mode", False) == False # Assuming debug_mode=True means not headless for lab ops
        
        self.selenium_driver = BaseSeleniumDriver(
            browser_name=_browser_name,
            is_headless=_is_headless,
            config_manager=self.config
        )
        self.driver = self.selenium_driver.get_driver()
        self.wait = self.selenium_driver.wait # Convenience

    def _prompt_for_manual_login(self, message: str = None):
        """
        Prompt the user to complete authentication manually.
        Waits until the user presses Enter in the CLI.
        
        Args:
            message: Optional custom message to display
        """
        if message:
            print(f"\n{'='*60}")
            print(message)
            print(f"{'='*60}")
        else:
            print(f"\n{'='*60}")
            print("Manual authentication required.")
            print("Please complete the login in the browser.")
            print(f"{'='*60}")
        
        input("Press Enter once you have completed the login...")

    def _get_credentials(self, environment: str):
        """
        Helper to fetch credentials for a given environment.
        
        Returns:
            Tuple of (username, password, auth_helper_cmd)
        """
        if environment == "rol":
            username = self.config.get("Credentials", "RH_USERNAME")
            password = self.config.get("Credentials", "RH_PASSWORD")
            auth_helper = self.config.get("Credentials", "RH_AUTH_HELPER")
            return username, password, auth_helper
        elif environment == "rol-stage":
            username = self.config.get("Credentials", "GITHUB_USERNAME")
            password = self.config.get("Credentials", "GITHUB_PASSWORD")
            auth_helper = self.config.get("Credentials", "GITHUB_AUTH_HELPER")
            return username, password, auth_helper
        elif environment == "china":
            username = self.config.get("Credentials", "CHINA_USERNAME")
            password = self.config.get("Credentials", "CHINA_PASSWORD")
            return username, password, None
        else:
            raise ValueError(f"Unknown environment for credentials: {environment}")

    def _get_auth_token(self, auth_helper: str) -> str:
        """Execute auth helper command and return the token."""
        if not auth_helper:
            return ""
        try:
            return os.popen(auth_helper).read().replace('\n', '')
        except Exception as e:
            logging.getLogger(__name__).debug(f"Auth helper returned empty: {e}")
            return ""

    def login(self, environment: str):
        """
        Login to the specified environment.
        
        If credentials are configured, they will be autofilled.
        If credentials are not available, the user will be prompted to complete
        authentication manually in the browser.
        
        Args:
            environment: The target environment (rol, rol-stage, china)
        """
        self.logger(f"Login into '{environment}' environment")
        base_url = self.config.get_lab_base_url(environment)
        if not base_url:
            raise ValueError(f"Base URL for environment '{environment}' not configured.")

        # Navigate to a generic course page to trigger login
        self.selenium_driver.go_to_url(base_url + "rh124-9.3")

        username, password, auth_helper = self._get_credentials(environment)

        try:
            if environment == "rol":
                self.selenium_driver.accept_trustarc_cookies(timeout=5)
                
                if username:
                    self.wait.until(EC.element_to_be_clickable(
                        (By.XPATH, "/html/body/div[1]/main/div/div/div[1]/div[2]/div[2]/div/section[1]/form/div[1]/input")
                    )).send_keys(f"{username}@redhat.com")
                    self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="login-show-step2"]'))).click()
                    
                    self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="username"]'))).send_keys(username)
                    
                    if password:
                        # Build full credential string
                        auth_token = self._get_auth_token(auth_helper)
                        full_credential = str(password).replace('\n', '') + str(auth_token)
                        self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="password"]'))).send_keys(full_credential)
                        self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="submit"]'))).click()
                    else:
                        self._prompt_for_manual_login(
                            "Username autofilled. Please enter your password and complete authentication."
                        )
                else:
                    self._prompt_for_manual_login(
                        "Credentials not configured. Please complete the login manually."
                    )

            elif environment == "rol-stage":
                self.selenium_driver.accept_trustarc_cookies(timeout=1)
                self.wait.until(EC.element_to_be_clickable((By.XPATH, '/html/body/div/div[2]/div/div/div[2]/ul/a/span'))).click()
                
                if username:
                    self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="login_field"]'))).send_keys(username)
                    
                    if password:
                        self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="password"]'))).send_keys(password)
                        self.wait.until(EC.element_to_be_clickable((By.XPATH, '//input[@type="submit"]'))).click()
                        
                        # Handle additional verification if needed
                        if auth_helper:
                            try:
                                verify_field = self.wait.until(EC.element_to_be_clickable(
                                    (By.XPATH, f'//*[@id="{self._GITHUB_VERIFY_FIELD}"]')
                                ))
                                verify_field.click()
                                auth_token = self._get_auth_token(auth_helper)
                                if auth_token:
                                    verify_field.send_keys(auth_token)
                            except TimeoutException:
                                pass
                        else:
                            try:
                                WebDriverWait(self.driver, 3).until(
                                    EC.presence_of_element_located(
                                        (By.XPATH, f'//*[@id="{self._GITHUB_VERIFY_FIELD}"]')
                                    )
                                )
                                self._prompt_for_manual_login(
                                    "Additional verification required. Please complete it in the browser."
                                )
                            except TimeoutException:
                                pass
                    else:
                        self._prompt_for_manual_login(
                            "Username autofilled. Please enter your password and complete authentication."
                        )
                else:
                    self._prompt_for_manual_login(
                        "Credentials not configured. Please complete the login manually."
                    )

            elif environment == "china":
                china_login_url = self.config.get_lab_base_url("china").replace("courses/", "login/local")
                self.selenium_driver.go_to_url(china_login_url)
                self.selenium_driver.accept_trustarc_cookies()
                
                if username:
                    self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="username"]'))).send_keys(username)
                    
                    if password:
                        self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="password"]'))).send_keys(password)
                        self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="login_button"]'))).click()
                    else:
                        self._prompt_for_manual_login(
                            "Username autofilled. Please enter your password and complete authentication."
                        )
                else:
                    self._prompt_for_manual_login(
                        "Credentials not configured. Please complete the login manually."
                    )
            
            self.wait_for_site_to_be_ready(environment)
        except Exception as e:
            pass

    def wait_for_site_to_be_ready(self, environment: str, timeout: int = 5):
        self.driver.execute_script("document.body.style.zoom = '0.70'")
        self.driver.execute_script("window.scrollTo(0, 0);")
        self.logger("Waiting for site to be ready...")
        try:
            self.selenium_driver.accept_trustarc_cookies() # Try again in case it reappeared
            if environment == "rol" or environment == "china":
                # Example element, this should be a reliable element indicating logged-in state
                self.wait.until(EC.presence_of_element_located((By.XPATH, '/html/body/div[1]/div[1]/header/div[2]/div/nav[2]/button[4]'))) 
            elif environment == "rol-stage":
                 # Example element for ROL stage (e.g., avatar)
                self.wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="avatar"]')))
        except TimeoutException:
            # self.selenium_driver.driver.save_screenshot(f"{environment}_site_not_ready.png")
            time.sleep(0.5) # Short pause from original script
            try:
                self.selenium_driver.accept_trustarc_cookies()
                # Re-attempt the original wait, could make this recursive with a depth limit
                if environment == "rol" or environment == "china":
                    self.wait.until(EC.presence_of_element_located((By.XPATH, '/html/body/div[1]/div[1]/header/div[2]/div/nav[2]/button[4]')))
                elif environment == "rol-stage":
                    self.wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="avatar"]')))
            except Exception as retry_err:
                logging.getLogger(__name__).warning(f"Site ready check retry failed for {environment}: {retry_err}")
        except Exception as e:
            logging.getLogger(__name__).warning(f"Site ready check failed for {environment}: {e}")

    def go_to_course(self, course_id: str, chapter_section: str = "pr01", environment: str = "rol"):
        self.logger(f"Navigating to course: {course_id} {chapter_section} in {environment}")
        base_url = self.config.get_lab_base_url(environment)
        if not base_url:
            raise ValueError(f"Base URL for environment '{environment}' not configured.")
        self.selenium_driver.go_to_url(f"{base_url}{course_id}/pages/{chapter_section}")
        self.wait_for_site_to_be_ready(environment) # Ensure page is loaded after navigation

    def disable_video_player(self):
        """Disable video player to avoid issues with page interactions."""
        try:
            video_btn = self.driver.find_element(By.XPATH, '//button[@id="HUD__dock-item__btn--video-player"]')
            if video_btn.get_attribute("aria-pressed") == "true":
                video_btn.click()
                self.logger("Disabled video player")
        except Exception:
            pass

    def dismiss_active_alerts(self):
        """Dismiss any active PF5 alerts on the page."""
        try:
            alert = self.driver.find_element(By.XPATH, '//div[contains(@class, "pf-v5-c-alert") and contains(@class, "labs-alert")]')
            if alert:
                dismiss_btn = alert.find_element(By.XPATH, './/button[contains(@class, "pf-m-link") and contains(text(), "Dismiss")]')
                dismiss_btn.click()
                self.logger("Dismissed active alert")
                time.sleep(0.3)
        except Exception:
            pass

    def select_lab_environment_tab(self, tab_name: str):
        """Selects a tab like 'index', 'course', or 'lab'."""
        
        # Map tab names to both old and new interface selectors
        tab_selectors = {
            "index": {
                "old": "1",
                "new": "Course"  # Based on the HTML you provided
            },
            "course": {
                "old": "2", 
                "new": "Course"
            },
            "lab-environment": {
                "old": "8",
                "new": "Lab Environment"
            }
        }
        
        tab_config = tab_selectors.get(tab_name.lower())
        if not tab_config:
            raise ValueError(f"Invalid tab name: {tab_name}. Expected one of {list(tab_selectors.keys())}")

        # Try new PF5 interface first, then fall back to old interface
        success = False
        
        # Method 1: Try new PF5 interface (by text content)
        try:
            self.driver.execute_script("document.body.style.zoom = '0.70'")
            self.driver.execute_script("window.scrollTo(0, 0);")

            new_tab_xpath = f'//button[@role="tab" and .//span[contains(text(), "{tab_config["new"]}")]]'
            tab_element = WebDriverWait(self.driver, 20).until(EC.element_to_be_clickable((By.XPATH, new_tab_xpath)))
            tab_element.click()
            
            # Verify tab is selected in new interface
            WebDriverWait(self.driver, 20).until(
                lambda d: d.find_element(By.XPATH, new_tab_xpath).get_attribute("aria-selected") == "true",
                message=f"Tab {tab_name} did not become selected in new interface."
            )
            time.sleep(0.2)
            success = True
            
        except TimeoutException:
            self.logger(f"New PF5 interface not found for tab {tab_name}, trying old interface...")
        
        # Method 2: Try old interface if new one failed
        if not success:
            try:
                old_tab_xpath = f'//*[@id="course-tabs-tab-{tab_config["old"]}"]'
                tab_element = self.wait.until(EC.element_to_be_clickable((By.XPATH, old_tab_xpath)))
                tab_element.click()
                
                # Verify tab is selected
                WebDriverWait(self.driver, 10).until(
                    lambda d: d.find_element(By.XPATH, old_tab_xpath).get_attribute("aria-selected") == "true",
                    message=f"Tab {tab_name} did not become selected in old interface."
                )
                time.sleep(0.2)
                success = True
                
            except TimeoutException:
                # Common recovery step
                self.selenium_driver.accept_trustarc_cookies()
                time.sleep(1)
                
                # Retry old interface
                try:
                    tab_element = self.wait.until(EC.element_to_be_clickable((By.XPATH, old_tab_xpath)))
                    tab_element.click()
                    WebDriverWait(self.driver, 10).until(
                        lambda d: d.find_element(By.XPATH, old_tab_xpath).get_attribute("aria-selected") == "true"
                    )
                    success = True
                except Exception as e:
                    self.logger(f"Failed to select tab {tab_name} after retry: {e}")
                    raise
        
        if not success:
            raise Exception(f"Could not select tab '{tab_name}' in either new or old interface")
            
    def _get_lab_action_button(self, action_texts: list[str], timeout: int = 1):
        """Helper to find a lab action button (Create, Start, Stop, Delete)."""
        # Combined logic for finding create/delete or start/stop buttons
        # XPATH from original: //*[@id="tab-course-lab-environment"]//*[@type="button"][contains(text(), "Action")]
        # This might need refinement if button positions are key (first vs second)
        for text in action_texts:
            try:
                button_xpath = f'//*[@id="tab-course-lab-environment"]//*[@type="button"][contains(text(), "{text}")]'
                button = WebDriverWait(self.driver, timeout).until(
                    EC.presence_of_element_located((By.XPATH, button_xpath))
                )
                return button, button.text 
            except TimeoutException:
                continue # Try next text
        return None, None


    def check_lab_status(self) -> tuple[str | None, str | None]:
        """
        Checks the status of the lab by looking for Create/Delete/Start/Stop buttons.
        Returns a tuple: (primary_action, secondary_action) e.g., ("CREATE", None) or ("DELETE", "STARTING")
        This simplifies the original check_lab_status_button which took 'first' or 'second'.
        We now look for specific keywords.
        """
        self.select_lab_environment_tab("lab-environment")

        # Check for Create/Creating or Delete/Deleting button (usually primary)
        create_delete_button, create_delete_text = self._get_lab_action_button(["Create", "Creating", "Delete", "Deleting"])
        
        # Check for Start/Starting or Stop/Stopping button (usually secondary or alternative primary)
        start_stop_button, start_stop_text = self._get_lab_action_button(["Start", "Starting", "Stop", "Stopping"])

        primary_status = None
        secondary_status = None

        if create_delete_button:
            primary_status = create_delete_text.upper()
        
        if start_stop_button:
            # If no create/delete button found, start/stop is the primary
            if not primary_status:
                primary_status = start_stop_text.upper()
            else: # Otherwise it's a secondary status indicator
                secondary_status = start_stop_text.upper()
        
        #self.logger(f"Lab status: Primary='{primary_status}', Secondary='{secondary_status}'")
        return primary_status, secondary_status


    def create_lab(self, course_id: str):
        self.logger(f"Creating lab for course: {course_id}")
        self.select_lab_environment_tab("lab-environment")
        try:
            create_button, _ = self._get_lab_action_button(["Create"])
            if create_button:
                create_button.click()
                # Add wait for lab creation, e.g., wait for status to change or a specific element.
                WebDriverWait(self.driver, 60).until(
                    lambda d: self._get_lab_action_button(["Creating", "Delete", "Starting", "Stop"])[0] is not None, # Wait until create is done
                    message="Lab did not appear to start creating or finish creating." 
                )
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to create lab {course_id}: {e}")
            # self.selenium_driver.driver.save_screenshot(f"create_lab_error_{course_id}.png")
            raise
    
    def start_lab(self, course_id: str):
        self.logger(f"Starting lab for course: {course_id}")
        self.select_lab_environment_tab("lab-environment")
        try:
            start_button, _ = self._get_lab_action_button(["Start"])
            if start_button:
                start_button.click()
                # Add wait for lab start
                time.sleep(5)
                WebDriverWait(self.driver, 60).until(
                    lambda d: self._get_lab_action_button(["Stop", "Starting"])[0] is not None,
                    message="Lab did not appear to start."
                )
            else:
                logging.getLogger(__name__).error(f"Start lab button not found for {course_id}. Lab might be running or in another state.")
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to start lab {course_id}: {e}")
            # self.selenium_driver.driver.save_screenshot(f"start_lab_error_{course_id}.png")
            raise

    def stop_lab(self, course_id: str):
        self.logger(f"Stopping lab for course: {course_id}")
        self.select_lab_environment_tab("lab-environment")
        try:
            stop_button, _ = self._get_lab_action_button(["Stop"])
            if stop_button:
                stop_button.click()
                # Confirm stop in dialog
                confirm_button_xpath = '//*[@role="dialog"]//*[@type="button"][contains(text(), "Stop")]'
                confirm_stop = self.wait.until(EC.element_to_be_clickable((By.XPATH, confirm_button_xpath)))
                confirm_stop.click()
                # Add wait for lab stop
                time.sleep(5)
                WebDriverWait(self.driver, 30).until(
                    lambda d: self._get_lab_action_button(["Start", "Stopping"])[0] is not None,
                    message="Lab did not appear to stop."
                )
            else:
                logging.getLogger(__name__).error(f"Stop lab button not found for {course_id}.")
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to stop lab {course_id}: {e}")
            # self.selenium_driver.driver.save_screenshot(f"stop_lab_error_{course_id}.png")
            raise
            
    def delete_lab(self, course_id: str):
        self.logger(f"Deleting lab for course: {course_id}")
        self.select_lab_environment_tab("lab-environment")
        try:
            delete_button, _ = self._get_lab_action_button(["Delete"])
            if delete_button:
                delete_button.click()
                # Confirm delete in dialog
                confirm_button_xpath = '//*[@role="dialog"]//*[@type="button"][contains(text(), "Delete")]'
                confirm_delete = self.wait.until(EC.element_to_be_clickable((By.XPATH, confirm_button_xpath)))
                confirm_delete.click()
                # Add wait for lab deletion
                # Original: time.sleep(20)
                WebDriverWait(self.driver, 60).until(
                    lambda d: self._get_lab_action_button(["Create"])[0] is not None, # Wait until delete is done (Create becomes available)
                    message="Lab did not appear to be deleted."
                )
            else:
                logging.getLogger(__name__).error(f"Delete lab button not found for {course_id}.")
        except Exception as e:
            logging.getLogger(__name__).error(f"Failed to delete lab {course_id}: {e}")
            # self.selenium_driver.driver.save_screenshot(f"delete_lab_error_{course_id}.png")
            raise
            
    def recreate_lab(self, course_id: str, environment: str):
        self.logger(f"Recreating lab for course: {course_id} in {environment}")
        self.go_to_course(course_id, environment=environment) # Ensure on correct page
        self.select_lab_environment_tab("lab-environment")
        
        primary_status, secondary_status = self.check_lab_status()

        if primary_status in ["STOP", "START"]: # Corresponds to STOP or START button being the main one
            self.delete_lab(course_id)
            # Wait for delete to complete and create button to be available
            self.wait.until(lambda d: self._get_lab_action_button(["Create"])[0] is not None, message="Create button not available after delete.")
            self.create_lab(course_id)
        elif primary_status == "CREATE":
            self.create_lab(course_id)
        elif primary_status in ["DELETING", "CREATING"]:
             # Wait until a stable state (Create or Stop/Start button appears)
             WebDriverWait(self.driver, 300).until(
                 lambda d: self._get_lab_action_button(["Create", "Start", "Stop"])[0] is not None,
                 message = f"Lab did not stabilize from {primary_status} state."
             )
             # Recurse or re-check status and act
             self.recreate_lab(course_id, environment) # Call again to re-evaluate
        else:
            logging.getLogger(__name__).error(f"Cannot determine action for lab status: Primary='{primary_status}', Secondary='{secondary_status}'. Recreating might fail.")
            # Fallback to trying delete then create
            try:
                self.delete_lab(course_id)
                self.wait.until(lambda d: self._get_lab_action_button(["Create"])[0] is not None)
            except Exception as e:
                logging.getLogger(__name__).error(f"Delete failed during recreate, attempting create anyway: {e}")
            self.create_lab(course_id)

        self.increase_autostop(course_id)
        self.increase_lifespan(course_id)
        self.logger(f"Lab {course_id} recreate sequence finished.")


    def _click_lab_adjustment_button(self, course_id: str, button_xpath_part: str, times: int, description: str):
        self.logger(f"{description} for course {course_id} ({times} times)")
        self.select_lab_environment_tab("lab-environment")
        try:

            self.driver.execute_script("document.body.style.zoom = '0.70'")
            # Wait until lab is in a state where adjustments can be made (e.g., running)
            # The original script checked for "CREATING" or "STARTING" states before clicking.
            # This implies we should wait until those are done.
            WebDriverWait(self.driver, 300).until(EC.presence_of_element_located((By.XPATH, '//button[text()="Open Console"]')))
            
            # Scroll to bottom of the lab environment tab to ensure all controls/buttons are visible
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(0.3)
            
            button_xpath = f'//*[@id="tab-course-lab-environment"]/div/table/tr[{button_xpath_part}]/td[2]/button'
            WebDriverWait(self.driver, 3).until(EC.visibility_of_element_located((By.XPATH, button_xpath)))
            adj_button = self.wait.until(EC.element_to_be_clickable((By.XPATH, button_xpath)))
            for _ in range(times):
                adj_button.click()
                time.sleep(0.1) # Small pause between clicks
        except TimeoutException:
            logging.getLogger(__name__).error(f"Timeout finding {description} button for {course_id}. Lab might not be ready or button not found.")
            # self.selenium_driver.driver.save_screenshot(f"{description.lower().replace(' ','_')}_timeout_{course_id}.png")
            # Pass for now as in original script
        except Exception as e:
            pass

    def increase_autostop(self, course_id: str, times: int = 4):
        self._click_lab_adjustment_button(course_id, "1", times, "Increasing auto-stop")

    def increase_lifespan(self, course_id: str, times: int = 5):
        self._click_lab_adjustment_button(course_id, "2", times, "Increasing auto-destroy (lifespan)")

    def impersonate_user(self, impersonate_username: str, current_course_id: str, environment: str):
        self.logger(f"Impersonating user '{impersonate_username}'")
        if not impersonate_username:
            logging.getLogger(__name__).error("No impersonation username provided.")
            return

        try:
            self.driver.refresh() # Refresh current page
            self.wait_for_site_to_be_ready(environment) # Ensure site is loaded

            # Click on Switch user (text might vary by platform/language)
            # Using a more general XPath that looks for the text "Switch user" within a button or link
            switch_user_button = self.wait.until(EC.element_to_be_clickable(
                (By.XPATH, '//*[(self::button or self::a) and normalize-space(.)="Switch user"] | //*[text()="Switch user"]')
            ))
            switch_user_button.click()
            
            # Introduce username
            username_field = self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="formInlineUsername"]')))
            username_field.send_keys(impersonate_username)
            
            # Click on switch button (text might vary)
            confirm_switch_button = self.wait.until(EC.element_to_be_clickable((By.XPATH, "//button[normalize-space(.)='Switch User']")))
            confirm_switch_button.click()
            
            # Wait for a confirmation element or redirection. Original used a generic div.
            # This wait might need to be more specific, e.g., waiting for the username to change in the UI.
            self.wait.until(EC.staleness_of(username_field)) # Wait for old elements to go stale
            self.wait_for_site_to_be_ready(environment) # Wait for page to reload as new user

            # Re-navigate to the course after impersonation
            if current_course_id:
                self.go_to_course(current_course_id, environment=environment)
            self.select_lab_environment_tab("lab-environment") # Go to lab tab by default after impersonation

        except Exception as e:
            logging.getLogger(__name__).error(f"An exception occurred while impersonating {impersonate_username}: {e}")
            # self.selenium_driver.driver.save_screenshot(f"impersonate_error_{impersonate_username}.png")
            # Don't re-raise, allow script to continue if impersonation fails but is not critical path for *all* ops

    # --- QA and command execution methods ---
    
    # Tab handles for switching between course page and console
    _course_tab_handle = None
    _console_tab_handle = None

    def open_workstation_console(self, course_id: str, setup_environment_style: str = None):
        """
        Opens the workstation console for a course and optionally sets up the environment.
        
        Args:
            course_id: The course identifier
            setup_environment_style: Optional style for environment setup (e.g., "rgdacosta")
        """
        self.logger(f"Opening workstation console for course: {course_id}")
        self.select_lab_environment_tab("lab-environment")
        
        # Store the course tab handle before opening console
        self._course_tab_handle = self.driver.current_window_handle
                
        # Scroll to top and zoom out to ensure button is visible
        self.driver.execute_script("document.body.style.zoom = '0.70'")
        self.driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.5)
        
        # Wait for workstation button using original XPath
        workstation_button_xpath = "//*[text()='workstation']/../td[3]/button"
        workstation_button = self.wait.until(
            EC.presence_of_element_located((By.XPATH, workstation_button_xpath))
        )
        
        # Scroll the button into view and use JavaScript click to avoid interception
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", workstation_button)
        time.sleep(0.3)
        self.driver.execute_script("arguments[0].click();", workstation_button)
        
        # Wait for new window/tab and switch to it
        WebDriverWait(self.driver, 30).until(EC.number_of_windows_to_be(2))
        handles = self.driver.window_handles
        # Find the new tab (not the course tab)
        for handle in handles:
            if handle != self._course_tab_handle:
                self._console_tab_handle = handle
                break
        self.driver.switch_to.window(self._console_tab_handle)

        # Open virtual keyboard in the console
        try:
            show_keyboard_button = self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="showKeyboard"]')))
            show_keyboard_button.click()
        except TimeoutException:
            logging.getLogger(__name__).warning("Could not find 'Show Keyboard' button. Console UI might have changed.")

        # Store the send text button reference for reuse
        try:
            self._send_text_option_button = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="showSendTextDialog"]'))
            )
        except TimeoutException:
            self._send_text_option_button = None
            logging.getLogger(__name__).warning("Could not find 'Send Text' button.")
        
        if setup_environment_style == "rgdacosta":
            self._setup_environment_rgdacosta_style()
        elif setup_environment_style:
            logging.getLogger(__name__).warning(f"Unknown environment setup style: {setup_environment_style}")

    def switch_to_course_tab(self):
        """Switch to the course page tab."""
        if self._course_tab_handle:
            self.driver.switch_to.window(self._course_tab_handle)
        else:
            # Fallback: assume first tab is course tab
            handles = self.driver.window_handles
            if handles:
                self.driver.switch_to.window(handles[0])
                self._course_tab_handle = handles[0]

    def switch_to_console_tab(self):
        """Switch to the workstation console tab."""
        if self._console_tab_handle:
            self.driver.switch_to.window(self._console_tab_handle)
        else:
            # Fallback: assume second tab is console tab
            handles = self.driver.window_handles
            if len(handles) > 1:
                self.driver.switch_to.window(handles[1])
                self._console_tab_handle = handles[1]

    def _click_virtual_keyboard_key(self, key_name: str, timeout: int = 10):
        """
        Click a key on the virtual keyboard.
        
        Args:
            key_name: The name of the key (e.g., "Enter", "Tab", "Esc", "Alt", "F2")
            timeout: Maximum time to wait for the key to be clickable
        """
        try:
            key_xpath = f'//div[@id="keyboard"]//div[text()="{key_name}"]'
            key_element = WebDriverWait(self.driver, timeout).until(
                EC.element_to_be_clickable((By.XPATH, key_xpath))
            )
            key_element.click()
            time.sleep(0.3)
        except TimeoutException:
            logging.getLogger(__name__).error(f"Could not find virtual keyboard key: {key_name}")
            raise

    def _setup_environment_rgdacosta_style(self):
        """
        Sets up the lab environment in 'rgdacosta' style.
        This involves logging in as student, opening a terminal, and running setup commands.
        """
        self.logger("Setting up lab environment 'rgdacosta' style!")
        
        # Wait for the console to fully load
        time.sleep(60)
        self.logger("Waiting for console to be ready...")
        time.sleep(10)
        
        # Click enter to select student user
        self._click_virtual_keyboard_key("Enter")
        self._click_virtual_keyboard_key("Tab")
        self._click_virtual_keyboard_key("Enter")
        
        # Enter password for student user and press enter
        self.introduce_command_to_console('student', auto_enter=True)
        time.sleep(17)
        
        # Open a terminal with ALT + F2
        self._click_virtual_keyboard_key("Esc")
        self._click_virtual_keyboard_key("Alt")
        self._click_virtual_keyboard_key("F2")
        self._click_virtual_keyboard_key("Alt")
        time.sleep(0.5)
        
        self.introduce_command_to_console('gnome-terminal', auto_enter=True)
        
        # Clone the rgdacosta repository
        time.sleep(1.5)
        self.introduce_command_to_console('git clone https://gitlab.com/rgdacosta/classroom_env.git', auto_enter=True)
        time.sleep(3)
        
        # Run the ansible playbook
        self.introduce_command_to_console('cd classroom_env; ansible-playbook playbook.yml', auto_enter=True)
        time.sleep(20)
        
        # Open another terminal with ALT + F2
        self._click_virtual_keyboard_key("Esc")
        self._click_virtual_keyboard_key("Alt")
        self._click_virtual_keyboard_key("F2")
        self._click_virtual_keyboard_key("Alt")
        
        self.introduce_command_to_console('gnome-terminal', auto_enter=True)
        
        self.logger("Lab environment 'rgdacosta' style setup completed.")

    def _wait_for_command_to_paste(self, command: str):
        """Wait a proportional time based on the command length for pasting to complete."""
        time.sleep(len(command) * 0.1)

    def introduce_command_to_console(self, command: str, auto_enter: bool = True):
        """
        Introduces a command to the console using the text dialog.
        
        Args:
            command: The command to send
            auto_enter: Whether to press Enter after sending the command
        """
        if not command:
            return

        try:
            # Open text dialog
            if hasattr(self, '_send_text_option_button') and self._send_text_option_button:
                self._send_text_option_button.click()
            else:
                send_text_dialog_button = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH, '//*[@id="showSendTextDialog"]'))
                )
                send_text_dialog_button.click()

            # Paste command into text box
            text_input_area = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="sendTextInput"]'))
            )
            text_input_area.send_keys(command)

            # Click Send button to send the command
            send_text_button = self.wait.until(
                EC.element_to_be_clickable((By.XPATH, '//*[@id="sendTextButton"]'))
            )
            send_text_button.click()
            
            # Wait proportional to command length
            self._wait_for_command_to_paste(command)

            # Click Enter on virtual keyboard
            if auto_enter:
                # Using the specific XPath from the original implementation
                enter_key_xpath = '/html/body/div[9]/div/div/div[3]/div/div[1]/div[3]/div[13]/div/div'
                try:
                    enter_key = WebDriverWait(self.driver, 10).until(
                        EC.element_to_be_clickable((By.XPATH, enter_key_xpath))
                    )
                    enter_key.click()
                except TimeoutException:
                    # Fallback to generiCheckinc Enter key search
                    try:
                        self._click_virtual_keyboard_key("Enter")
                    except Exception as e_enter:
                        self.logger(f"Could not press Enter key: {e_enter}")

        except Exception as e:
            self.logger(f"Error introducing command '{command[:50]}...': {e}")

    def click_on_show_solution_buttons(self):
        """Click all 'Show Solution' buttons on the current page."""
        try:
            while True:
                try:
                    show_solution_button = WebDriverWait(self.driver, 0.5).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[text()='Show Solution']"))
                    )
                    show_solution_button.click()
                    time.sleep(0.5)
                except TimeoutException:
                    # No more show solution buttons, break the loop
                    break
        except Exception:
            pass

    def get_exercise_commands(self, course_id: str, chapter_section: str, environment: str) -> list[str]:
        """
        Gets commands from an exercise by navigating to the course page and extracting userinput elements.
        Switches to course tab, fetches commands, then switches back to console tab.
        
        Args:
            course_id: The course identifier (e.g., "rh124-9.3")
            chapter_section: The chapter and section (e.g., "ch01s02")
            environment: The target environment
            
        Returns:
            List of commands extracted from the exercise
        """
        self.logger(f"Getting commands from exercise {chapter_section}")
        
        # Switch to course tab to fetch commands
        self.switch_to_course_tab()
        
        # Navigate to the specific exercise page
        base_url = self.config.get_lab_base_url(environment)
        exercise_url = f"{base_url}{course_id}/pages/{chapter_section}"
        self.selenium_driver.go_to_url(exercise_url)
        
        self.select_lab_environment_tab("course")
        time.sleep(4)

        # Click on all the show solution buttons until there are no more
        self.click_on_show_solution_buttons()

        # Get commands directly from the online platform
        commands_list = []
        try:
            # Try the old interface first
            userinput_elements = WebDriverWait(self.driver, 10).until(
                EC.presence_of_all_elements_located(
                    (By.XPATH, '//*[@id="course-tabs-pane-2"]//strong[@class="userinput"]')
                )
            )
            for element in userinput_elements:
                text = element.text.strip()
                if text:
                    commands_list.append(text)
        except TimeoutException:
            # Try the new PF5 interface
            try:
                userinput_elements = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_all_elements_located(
                        (By.XPATH, '//strong[@class="userinput"]')
                    )
                )
                for element in userinput_elements:
                    text = element.text.strip()
                    if text:
                        commands_list.append(text)
            except TimeoutException:
                self.logger("Could not find any userinput elements on the page.")
        except Exception as e:
            logging.getLogger(__name__).error(f"Exception while fetching commands: {e}")

        commands = "\n".join(commands_list)
        print("\n")
        print(commands)
        print("#####################################")

        # Switch back to console tab for command execution
        self.switch_to_console_tab()

        return commands_list

    # Keywords used to identify exercise/lab sections
    EXERCISE_KEYWORDS = ["Guided Exercise:", "Lab:"]
    
    # Keywords used to exclude theory sections (same as link_checker)
    EXCLUDED_THEORY_KEYWORDS = [
        "Guided Exercise:",
        "Lab:",
        "Quiz:",
        "Summary",
        "Review:",
        "Preface:",
        "About This Course",
        "Orientation to the Classroom",
        "Comprehensive Review",
        "Course Objectives",
        "Course Overview"
    ]

    def get_course_sections(self, course_id: str, environment: str, 
                           section_type: str = "exercises") -> list[dict]:
        """
        Gets sections from a course's Table of Contents.
        
        Args:
            course_id: The course identifier (e.g., "rh124-9.3")
            environment: The target environment
            section_type: Type of sections to fetch:
                - "exercises": Only Guided Exercises and Labs
                - "theory": Only theory sections (excludes exercises, summaries, etc.)
                - "all": All sections
            
        Returns:
            List of dicts with 'title', 'url', 'chapter_section' keys
        """
        type_desc = {
            "exercises": "Guided Exercises and Labs",
            "theory": "Theory sections",
            "all": "All sections"
        }.get(section_type, section_type)
        
        self.logger(f"Getting {type_desc} for course {course_id}")
        
        # Ensure we're on the course tab (not the console tab)
        self.switch_to_course_tab()
        
        # Navigate to course
        base_url = self.config.get_lab_base_url(environment)
        course_url = f"{base_url}{course_id}"
        
        if course_id not in self.driver.current_url:
            self.selenium_driver.go_to_url(course_url)
            time.sleep(3)

        sections = []

        try:
            # Wait for any backdrop/modal overlay to disappear
            try:
                WebDriverWait(self.driver, 15).until(
                    EC.invisibility_of_element_located((By.XPATH, '//div[contains(@class, "pf-v5-c-backdrop")]'))
                )
            except TimeoutException:
                self.logger("  ⚠ Backdrop overlay still present, attempting to continue...")
                try:
                    self.driver.execute_script("document.body.click();")
                    time.sleep(0.5)
                except:
                    pass
            
            # Click on "Toggle Table of Contents panel" button to open TOC
            toc_button = self.wait.until(EC.element_to_be_clickable(
                (By.XPATH, '//button[contains(@aria-label, "Table of Contents") or contains(@aria-label, "Toggle Table of Contents")]')
            ))
            
            # Check if TOC is already open
            try:
                toc_region = self.driver.find_element(By.XPATH, '//div[contains(@class, "ToC")] | //div[@aria-label="Table of contents"]')
                if not toc_region.is_displayed():
                    self.driver.execute_script("arguments[0].click();", toc_button)
                    time.sleep(1)
            except:
                self.driver.execute_script("arguments[0].click();", toc_button)
                time.sleep(1)
            
            # Click on "Expand all" toggle switch to show all chapters
            try:
                expand_all_selectors = [
                    '//label[contains(@class, "pf-v5-c-switch") and .//span[contains(text(), "Expand all")]]',
                    '//span[contains(@class, "pf-v5-c-switch__label") and contains(text(), "Expand all")]/..',
                    '//input[following-sibling::*[contains(text(), "Expand all")]]',
                    '//button[contains(text(), "Expand all")]',
                    '//*[contains(text(), "Expand all")]/ancestor::label[contains(@class, "switch")]//input',
                ]
                
                expand_all = None
                for selector in expand_all_selectors:
                    try:
                        expand_all = self.driver.find_element(By.XPATH, selector)
                        if expand_all.is_displayed():
                            break
                    except:
                        continue
                
                if expand_all and expand_all.is_displayed():
                    is_checked = expand_all.get_attribute('aria-checked') == 'true' or expand_all.get_attribute('checked')
                    if not is_checked:
                        self.driver.execute_script("arguments[0].click();", expand_all)
                        time.sleep(1)
                        self.logger("  Clicked 'Expand all' toggle")
                else:
                    self.logger("  'Expand all' toggle not found, expanding chapters manually...")
            except Exception as e:
                logging.getLogger(__name__).debug(f"Could not use 'Expand all' toggle: {e}")
            
            # Expand any collapsed chapter accordions
            try:
                collapsed_chapters = self.driver.find_elements(
                    By.XPATH,
                    '//button[contains(@class, "pf-v5-c-accordion__toggle") and @aria-expanded="false"]'
                )
                
                if collapsed_chapters:
                    self.logger(f"  Expanding {len(collapsed_chapters)} collapsed chapters...")
                    for btn in collapsed_chapters:
                        try:
                            self.driver.execute_script("arguments[0].click();", btn)
                            time.sleep(0.3)
                        except:
                            continue
                    time.sleep(0.5)
            except Exception as e:
                logging.getLogger(__name__).debug(f"Error expanding chapters: {e}")
            
            # Get all section links from TOC
            section_links = self.driver.find_elements(
                By.XPATH,
                '//a[contains(@href, "/pages/") and @data-analytics-id="toc-link-ole-lp"]'
            )
            
            # If no links found with specific data-analytics-id, try broader search
            if not section_links:
                section_links = self.driver.find_elements(
                    By.XPATH,
                    '//div[contains(@class, "ToC")]//a[contains(@href, "/pages/")]'
                )
            
            # If still no links, try even broader search
            if not section_links:
                section_links = self.driver.find_elements(
                    By.XPATH,
                    '//a[contains(@href, "/pages/")]'
                )
            
            # Process and filter sections based on section_type
            seen_sections = set()
            for link in section_links:
                try:
                    url = link.get_attribute('href')
                    title = link.text.strip()
                    
                    if not url or not title or '/pages/' not in url:
                        continue
                    
                    # Extract chapter_section from URL
                    try:
                        chapter_section = str(re.findall(r"ch[0-9]*s[0-9]*", url)[0])
                    except (IndexError, TypeError):
                        continue
                    
                    # Skip duplicates
                    if chapter_section in seen_sections:
                        continue
                    
                    # Filter based on section_type
                    is_exercise = any(kw in title for kw in self.EXERCISE_KEYWORDS)
                    is_excluded_theory = any(kw in title for kw in self.EXCLUDED_THEORY_KEYWORDS)
                    
                    include = False
                    if section_type == "exercises":
                        include = is_exercise
                    elif section_type == "theory":
                        include = not is_excluded_theory
                    elif section_type == "all":
                        include = True
                    
                    if include:
                        seen_sections.add(chapter_section)
                        sections.append({
                            'title': title,
                            'url': url,
                            'chapter_section': chapter_section
                        })
                        print(f"{title} -> {chapter_section}")
                        
                except:
                    continue
            
            self.logger(f"Found {len(sections)} {type_desc}")
            
        except TimeoutException:
            self.logger(f"Timeout waiting for TOC in course {course_id}")
        except Exception as e:
            self.logger(f"Error getting course sections: {e}")

        return sections

    def get_guided_exercises_and_labs(self, course_id: str, start_from: str, environment: str) -> list[str]:
        """
        Gets the list of Guided Exercises and Labs for a course.
        Convenience wrapper around get_course_sections that returns just chapter_section identifiers.
        
        Args:
            course_id: The course identifier (e.g., "rh124-9.3")
            start_from: The chapter_section to start from (unused, kept for API compatibility)
            environment: The target environment
            
        Returns:
            List of chapter_section identifiers (e.g., ["ch01s02", "ch01s03", ...])
        """
        sections = self.get_course_sections(course_id, environment, section_type="exercises")
        return [s['chapter_section'] for s in sections]

    def _multiline_command(self, command: str) -> bool:
        """
        Check if a command ends with an odd number of backslashes (line continuation).
        
        Args:
            command: The command to check
            
        Returns:
            True if the command continues on the next line, False otherwise
        """
        stripped_command = command.rstrip()
        if not stripped_command:
            return False
        trailing_backslashes = re.search(r'\\+$', stripped_command)
        if not trailing_backslashes:
            return False
        return len(trailing_backslashes.group(0)) % 2 == 1

    def _merge_command_fragments(self, previous_command: str, current_fragment: str) -> str | None:
        """
        Attempt to merge command fragments that might be split across lines.
        
        Args:
            previous_command: The previous command
            current_fragment: The current fragment to potentially merge
            
        Returns:
            The merged command if fragments should be merged, None otherwise
        """
        previous = previous_command.rstrip()
        current = current_fragment.lstrip()

        if not previous or not current:
            return None

        # Check for unclosed quotes
        if previous.count("'") % 2 == 1:
            return f"{previous}{current}"
        if previous.count('"') % 2 == 1:
            return f"{previous}{current}"

        # Check for assignment continuation
        if previous.endswith('='):
            return f"{previous}{current}"

        # Check for path continuation
        if previous.endswith('/'):
            if '/' in current and ' ' not in current:
                return f"{previous}{current}"

        # Check for flag continuation
        if current.startswith('--') or (current.startswith('-') and len(current) > 1 and current[1] != ' '):
            return f"{previous} {current}"
        
        # Check for pipe/redirect continuation
        if current.startswith(('|', '>', '<')):
            return f"{previous} {current}"
        
        # Check for logical operator continuation
        if current.startswith(('&&', '||')):
            return f"{previous} {current}"

        return None

    def filter_commands_list(self, commands: list[str] | str) -> list[str]:
        """
        Filters and composes multi-line commands from a list or string of commands.
        
        Args:
            commands: Either a list of command strings or a newline-separated string
            
        Returns:
            List of filtered and composed commands
        """
        self.logger("Filtering command list...")
        
        # Handle string input
        if isinstance(commands, str):
            commands_array = commands.split("\n")
        else:
            commands_array = commands
            
        composed_command = ''
        filtered_commands_array = []

        for raw_command in commands_array:
            command = raw_command.strip()
            if command == '':
                continue

            # Check for multiline command (ends with backslash)
            if self._multiline_command(command):
                line_without_backslash = re.sub(r'\\+$', '', command.rstrip())
                composed_command = f"{composed_command}{line_without_backslash.rstrip()} "
                continue

            # If we have a composed command pending, complete it
            if composed_command:
                composed_command = f"{composed_command}{command}"
                filtered_commands_array.append(composed_command.strip())
                composed_command = ''
                continue

            # Try to merge with previous command
            if filtered_commands_array:
                merged_command = self._merge_command_fragments(filtered_commands_array[-1], command)
                if merged_command is not None:
                    filtered_commands_array[-1] = merged_command.strip()
                    continue

            filtered_commands_array.append(command)

        # Handle any remaining composed command
        if composed_command:
            filtered_commands_array.append(composed_command.strip())

        return filtered_commands_array

    def _prompt_user_to_continue(self, custom_message: str = ""):
        """
        Prompts for user input to continue the execution of the QA.
        
        Args:
            custom_message: Optional message to display to the user
        """
        print("")
        input(f"Press Enter to continue {custom_message}\n")

    def _handle_special_command(self, command: str) -> bool:
        """
        Handles special commands that require specific treatment.
        
        Args:
            command: The command to handle
            
        Returns:
            True if the command was handled specially, False otherwise
        """
        
        # Lab start/setup commands
        if re.match(r"lab .*start", command) or re.match(r"lab .*setup", command):
            command = "date; time " + command
            self.introduce_command_to_console(command, auto_enter=True)
            self._prompt_user_to_continue("with the exercise.")
            return True
            
        # Lab grade commands
        if re.match(r"lab .*grade", command):
            command = "date; time " + command
            self.introduce_command_to_console(command, auto_enter=True)
            self._prompt_user_to_continue("with the exercise.")
            return True
            
        # Lab finish commands
        if re.match(r"lab .*finish", command):
            command = "date; time " + command
            self.introduce_command_to_console(command, auto_enter=True)
            print("##############  Exercise completed ##############")
            return True
            
        # SSH commands (excluding sshd, keygen, copy-id)
        if "ssh" in command and "sshd" not in command and "keygen" not in command and "copy-id" not in command:
            self.introduce_command_to_console(command, auto_enter=True)
            self._prompt_user_to_continue("after the ssh.")
            return True
            
        # Ansible commands
        if "ansible" in command:
            self._prompt_user_to_continue("if you did review/create the playbook.")
            self.introduce_command_to_console(command, auto_enter=True)
            self._prompt_user_to_continue("if playbook finished")
            return True
            
        # Skip output lines (ok=, failed=)
        if "ok=" in command or "failed=" in command:
            print("skipping output")
            return True
            
        # Podman build commands
        if "podman build" in command:
            self._prompt_user_to_continue("if the Containerfile is ready to build.")
            self.introduce_command_to_console(command, auto_enter=True)
            self._prompt_user_to_continue("if podman build finished.")
            return True
            
        # Vim/vi commands
        if "vim " in command or "vi " in command:
            self.introduce_command_to_console(command, auto_enter=True)
            self._prompt_user_to_continue("if you did review/create the file.")
            return True
            
        # Enter command
        if "Enter" in command:
            self.introduce_command_to_console("\n", auto_enter=True)
            return True
            
        # Less command (replace with cat)
        if "less " in command:
            command = command.replace("less ", "cat ")
            self.introduce_command_to_console(command, auto_enter=True)
            return True
            
        # Systemctl status commands
        if "systemctl status" in command:
            self.introduce_command_to_console(command, auto_enter=True)
            self.introduce_command_to_console("q\n", auto_enter=True)
            return True
            
        # Systemctl restart / daemon-reload commands
        if "systemctl restart" in command or "daemon-reload" in command:
            self._prompt_user_to_continue(
                "if you made sure that the new configuration is in place to 'systemctl restart service.'"
            )
            self.introduce_command_to_console(command, auto_enter=True)
            return True
            
        # Journalctl commands
        if "journalctl" in command:
            self.introduce_command_to_console(command, auto_enter=True)
            self.introduce_command_to_console(" \n", auto_enter=True)
            return True
            
        # Ping commands (excluding ansible)
        if "ping" in command and "ansible" not in command:
            if "-c" not in command:
                command = command + " -c2"
            self.introduce_command_to_console(command, auto_enter=True)
            return True
            
        # Yum/dnf install/reinstall/remove commands
        if any(x in command for x in ["yum install", "yum reinstall", "yum remove", "dnf install"]):
            if "-y" not in command:
                command = command + " -y"
            self.introduce_command_to_console(command, auto_enter=True)
            self._prompt_user_to_continue("when the installation has finished.")
            return True
            
        # Podman login commands
        if "podman login registry.redhat.io" in command:
            self.introduce_command_to_console(command, auto_enter=True)
            self._prompt_user_to_continue("when login is completed.")
            return True
            
        # VG restore commands
        if "vgcfgrestore -f" in command:
            self._prompt_user_to_continue("when you have selected the desired .vg file.")
            self.introduce_command_to_console(command, auto_enter=True)
            return True
            
        # /etc/hosts or /etc/resolv.conf commands
        if "/etc/hosts" in command or "/etc/resolv.conf" in command:
            self._prompt_user_to_continue("when you have reviewed/fixed the /etc/hosts or /etc/resolv.conf files.")
            self.introduce_command_to_console(command, auto_enter=True)
            return True
            
        # iSCSI discovery commands
        if "iscsiadm -m discovery" in command:
            self.introduce_command_to_console(command, auto_enter=True)
            self._prompt_user_to_continue("when discovery command has executed.")
            return True
            
        # OC edit commands
        if "oc edit" in command:
            self.introduce_command_to_console(command, auto_enter=True)
            self._prompt_user_to_continue("when the object edit has been saved.")
            return True
            
        # OC create/apply with -f or -k commands
        if ("oc create" in command or "oc apply" in command) and (" -f " in command or " -k " in command):
            self._prompt_user_to_continue("when the yaml file has been saved.")
            self.introduce_command_to_console(command, auto_enter=True)
            return True
            
        # OC patch commands
        if "oc patch" in command:
            self._prompt_user_to_continue("when the yaml file has been saved.")
            self.introduce_command_to_console(command, auto_enter=True)
            return True
            
        # OC logs / podman logs commands
        if "oc logs" in command or "podman logs" in command:
            try:
                suffix_match = re.findall(r"-\w+-\w+$", command)
                if suffix_match:
                    suffix = suffix_match[0]
                    self.introduce_command_to_console(re.split(str(suffix), command)[0], auto_enter=False)
                    self._prompt_user_to_continue(". Use TAB to complete the container/pod name.\n")
                else:
                    self.introduce_command_to_console(command, auto_enter=True)
            except Exception:
                self.introduce_command_to_console(command, auto_enter=True)
            return True
            
        # Watch commands or oc get with -w
        if "watch" in command or ("oc get" in command and " -w " in command):
            self.introduce_command_to_console(command, auto_enter=True)
            self._prompt_user_to_continue("when you finished using the watch command.")
            return True

        # Command was not handled specially
        return False

    def run_full_course_qa(self, course_id: str, environment: str, start_from: str = None):
        """
        Runs QA on all Guided Exercises and Labs in a course, optionally starting from a specific exercise.
        
        Note: This method assumes the lab environment is already set up (logged in, lab started, 
        workstation console open). Call this after performing those setup steps.
        
        Args:
            course_id: The course identifier (e.g., "rh124-9.3")
            environment: The target environment
            start_from: Optional chapter_section to start from (e.g., "ch01s02"). 
                       If not provided, starts from the first guided exercise.
        """
        self.logger(f"Starting full course QA for {course_id}")
        
        # Get list of exercises and labs (switches to course tab internally)
        exercises = self.get_guided_exercises_and_labs(course_id, start_from, environment)
        
        if not exercises:
            self.logger("No exercises or labs found for this course.")
            
        
        # Filter exercises to start from the specified chapter_section
        if start_from:
            if start_from in exercises:
                start_index = exercises.index(start_from)
                exercises = exercises[start_index:]
                self.logger(f"Starting from {start_from} ({len(exercises)} exercises/labs remaining)")
            else:
                self.logger(f"Warning: {start_from} not found in exercises list. Starting from the beginning.")
        
        self.logger(f"Found {len(exercises)} exercises/labs to QA")
        
        # Run QA on each exercise
        for chapter_section in exercises:
            try:
                self._run_qa_on_exercise(course_id, chapter_section, environment)
            except Exception as e:
                logging.getLogger(__name__).error(f"Error during QA of {chapter_section}: {e}")
                self._prompt_user_to_continue(f"after handling error in {chapter_section}.")
        
        self.logger(f"Completed full course QA for {course_id}")

    def _run_qa_on_exercise(self, course_id: str, chapter_section: str, environment: str):
        """
        Internal method to run QA on a specific exercise by executing commands.
        
        Args:
            course_id: The course identifier (e.g., "rh124-9.3")
            chapter_section: The chapter and section (e.g., "ch01s02")
            environment: The target environment
        """
        self.logger(f"Starting QA for {course_id} - {chapter_section}")
        
        commands = self.get_exercise_commands(course_id, chapter_section, environment)
        filtered_commands = self.filter_commands_list(commands)
        
        if not filtered_commands:
            self.logger(f"No commands found for {chapter_section}, skipping to next exercise.")
            return

        self.logger(f"Executing {len(filtered_commands)} commands...")

        for i, command in enumerate(filtered_commands):
            if command == '':
                continue
                
            print(f"Introducing: {command}")
            if i + 1 < len(filtered_commands):
                print('-------------------------------------------------')
            
            # Check if this is a special command that needs specific handling
            if not self._handle_special_command(command):
                # Regular command - just execute it
                self.introduce_command_to_console(command, auto_enter=True)

            # Standard time for command to execute
            command_delay = self.config.get("QA", "command_delay_seconds", 3)
            if isinstance(command_delay, str):
                command_delay = int(command_delay)
            time.sleep(command_delay)

        self.logger(f"Finished QA for {course_id} - {chapter_section}")


    def close_browser(self):
        self.logger("Closing browser.")
        self.selenium_driver.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close_browser()


# Example Usage (illustrative)
# if __name__ == '__main__':
#     # Setup ConfigManager (ensure config files are in expected relative paths or adjust)
#     # Assuming lx-toolbox is the project root and this script is run from there or PYTHONPATH is set.
#     # If running this file directly for testing, paths in ConfigManager need to be relative to *this file's location*
#     # or absolute.
#     # For package use, relative from project root is fine for ConfigManager defaults.
#     config_path = os.path.join(os.path.dirname(__file__), '..', '..', 'config', 'config.ini.example')
#     env_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env.example') # Use example if no .env
    
#     if not os.path.exists(env_path.replace(".example","")): # If real .env doesn't exist, copy example
#         if os.path.exists(env_path):
#             import shutil
#             shutil.copy(env_path, env_path.replace(".example",""))

#     cfg = ConfigManager(config_file_path=config_path, env_file_path=env_path.replace(".example",""))
    
#     # Ensure you have geckodriver/chromedriver in PATH
#     # And update .env with actual credentials for testing login
    
#     lab_env_to_test = cfg.get("General", "default_lab_environment", "rol")
#     course_to_test = "rh124-9.3" # Example course

#     try:
#         with LabManager(config=cfg) as lab_ops:
#             reset_step_counter()
            
#             # 1. Login
#             lab_ops.login(environment=lab_env_to_test)
            
#             # 2. Go to course
#             lab_ops.go_to_course(course_id=course_to_test, environment=lab_env_to_test)
            
#             # 3. Check lab status
#             primary_status, secondary_status = lab_ops.check_lab_status()
#             print(f"Initial Lab Status for {course_to_test}: Primary='{primary_status}', Secondary='{secondary_status}'")

#             # 4. Example: Ensure lab is created and started
#             if primary_status == "CREATE":
#                 lab_ops.create_lab(course_id=course_to_test)
#                 primary_status, _ = lab_ops.check_lab_status() # Re-check
            
#             if primary_status == "START":
#                 lab_ops.start_lab(course_id=course_to_test)
#                 primary_status, _ = lab_ops.check_lab_status() # Re-check

#             if primary_status == "STOP" or secondary_status == "STOPPING": # Assuming it's running
#                 lab_ops.increase_autostop(course_id=course_to_test, times=5)
#                 lab_ops.increase_lifespan(course_id=course_to_test, times=5)

#             # Example: Impersonate (if configured in .env)
#             # impersonate_target = cfg.get("Credentials", "IMPERSONATE_USERNAME")
#             # if impersonate_target:
#             #    lab_ops.impersonate_user(impersonate_username=impersonate_target, current_course_id=course_to_test, environment=lab_env_to_test)


#             # Example: Delete lab at the end of test
#             # lab_ops.delete_lab(course_id=course_to_test)


#             print("LabManager example operations completed.")

#     except ValueError as ve:
#         print(f"Configuration Error: {ve}")
#     except TimeoutException as te:
#         print(f"A timeout occurred during lab operations: {te}")
#     except Exception as e:
#         print(f"An unexpected error occurred: {e}")
#         import traceback
#         traceback.print_exc()
