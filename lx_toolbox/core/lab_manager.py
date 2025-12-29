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

    def _get_credentials(self, environment: str):
        """Helper to fetch credentials for a given environment."""
        # This is a simplified example. Actual credential names might vary.
        # Assumes .env keys like RH_USERNAME, RH_PIN, GITHUB_USERNAME, GITHUB_PASSWORD
        if environment == "rol":
            username = self.config.get("Credentials", "RH_USERNAME")
            # The pin in the old script was complex, involving an OTP call.
            # For now, let's assume pin/password is a single value or OTP is handled externally.
            pin = self.config.get("Credentials", "RH_PIN") 
            otp_command = self.config.get("Credentials", "ROL_OTP_COMMAND")
            return username, pin, otp_command
        elif environment == "rol-stage":
            username = self.config.get("Credentials", "GITHUB_USERNAME")
            password = self.config.get("Credentials", "GITHUB_PASSWORD")
            otp_command = self.config.get("Credentials", "GITHUB_OTP_COMMAND") 
            return username, password, otp_command
        elif environment == "china":
            # China environment in the old script had hardcoded credentials in the template itself
            # This should be moved to config
            username = self.config.get("Credentials", "CHINA_USERNAME")
            password = self.config.get("Credentials", "CHINA_PASSWORD")
            return username, password, None
        else:
            raise ValueError(f"Unknown environment for credentials: {environment}")

    def login(self, environment: str):
        self.logger(f"Login into '{environment}' environment")
        base_url = self.config.get_lab_base_url(environment)
        if not base_url:
            raise ValueError(f"Base URL for environment '{environment}' not configured.")

        # Navigate to a generic course page to trigger login if not already on the domain
        # The old script went to rh124-9.3, adjust if a more generic entry point is better
        self.selenium_driver.go_to_url(base_url + "rh124-9.3") # Placeholder course

        username, password_pin, otp_command = self._get_credentials(environment)

        if not username or not password_pin:
            raise ValueError(f"Username or password/pin not configured for environment '{environment}'.")

        try:
            if environment == "rol":
                # RH SSO Login Flow - matching old script behavior
                # Check cookies first (like old script)
                self.selenium_driver.accept_trustarc_cookies(timeout=5)
                
                self.wait.until(EC.element_to_be_clickable(
                    (By.XPATH, "/html/body/div[1]/main/div/div/div[1]/div[2]/div[2]/div/section[1]/form/div[1]/input")
                )).send_keys(f"{username}@redhat.com")
                self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="login-show-step2"]'))).click()
                
                # NOTE: rh-sso-flow button click is commented out in old script, so we skip it
                # self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="rh-sso-flow"]'))).click()
                
                # RH SSO - wait for username field (should appear automatically after clicking login-show-step2)
                self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="username"]'))).send_keys(username)
                
                otp_value = ""
                if otp_command:
                    try:
                        # This is a security risk and highly dependent on the local setup.
                        # Consider abstracting this to a function passed in, or user input.
                        print(f"Executing OTP command: {otp_command}")
                        otp_value = os.popen(otp_command).read().replace('\n', '')
                    except Exception as e:
                        print(f"Could not execute OTP command '{otp_command}': {e}")
                        # Potentially raise or ask user for OTP
                
                # Match old script: use replace('\n', '') instead of strip()
                full_password = str(password_pin).replace('\n', '') + str(otp_value)
                self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="password"]'))).send_keys(full_password)
                self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="submit"]'))).click()

            elif environment == "rol-stage":
                # GitHub Login Flow
                self.selenium_driver.accept_trustarc_cookies(timeout=1)
                self.wait.until(EC.element_to_be_clickable((By.XPATH, '/html/body/div/div[2]/div/div/div[2]/ul/a/span'))).click() # Assuming this is "Login with GitHub"
                self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="login_field"]'))).send_keys(username)
                self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="password"]'))).send_keys(password_pin)
                self.wait.until(EC.element_to_be_clickable((By.XPATH, '//input[@type="submit"]'))).click() # Match old script XPath

                if otp_command: # If 2FA is expected
                    otp_input_field = self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="app_totp"]')))
                    otp_input_field.click()
                    try:
                        print(f"Executing OTP command: {otp_command}")
                        otp_value = os.popen(otp_command).read().replace('\n', '')
                        otp_input_field.send_keys(otp_value)
                        #self.driver.find_element(By.XPATH, '/html/body/div[1]/div[3]/main/div/div[3]/div[2]/form/button').click() # Verify button
                    except Exception as e:
                        print(f"Could not execute OTP command '{otp_command}' for GitHub: {e}")
                        # Potentially raise or ask user for OTP

            elif environment == "china":
                # China local login - navigate to login page first (like old script)
                china_login_url = self.config.get_lab_base_url("china").replace("courses/", "login/local")
                self.selenium_driver.go_to_url(china_login_url)
                self.selenium_driver.accept_trustarc_cookies()
                self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="username"]'))).send_keys(username)
                self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="password"]'))).send_keys(password_pin)
                self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="login_button"]'))).click()
            
            self.wait_for_site_to_be_ready(environment)
        except Exception as e:
            pass

    def wait_for_site_to_be_ready(self, environment: str, timeout: int = 5):
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
            self.selenium_driver.accept_trustarc_cookies()
            # Re-attempt the original wait, could make this recursive with a depth limit
            if environment == "rol" or environment == "china":
                self.wait.until(EC.presence_of_element_located((By.XPATH, '/html/body/div[1]/div[1]/header/div[2]/div/nav[2]/button[4]')))
            elif environment == "rol-stage":
                self.wait.until(EC.presence_of_element_located((By.XPATH, '//*[@id="avatar"]')))
        except Exception as e:
            self.logger(f"Error waiting for site: {e}")
            raise

    def go_to_course(self, course_id: str, environment: str):
        self.logger(f"Navigating to course: {course_id} in {environment}")
        base_url = self.config.get_lab_base_url(environment)
        if not base_url:
            raise ValueError(f"Base URL for environment '{environment}' not configured.")
        self.selenium_driver.go_to_url(f"{base_url}{course_id}")
        self.wait_for_site_to_be_ready(environment) # Ensure page is loaded after navigation

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
            new_tab_xpath = f'//button[@role="tab" and .//span[contains(text(), "{tab_config["new"]}")]]'
            tab_element = WebDriverWait(self.driver, 5).until(EC.element_to_be_clickable((By.XPATH, new_tab_xpath)))
            tab_element.click()
            
            # Verify tab is selected in new interface
            WebDriverWait(self.driver, 10).until(
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
            
    def _get_lab_action_button(self, action_texts: list[str], timeout: int = 5):
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
        self.logger("Checking lab status...")
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
        self.go_to_course(course_id, environment) # Ensure on correct page
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
            # Wait until lab is in a state where adjustments can be made (e.g., running)
            # The original script checked for "CREATING" or "STARTING" states before clicking.
            # This implies we should wait until those are done.
            WebDriverWait(self.driver, 300).until(
                lambda d: self._get_lab_action_button(["Starting", "Stop"])[0] is not None or \
                            self._get_lab_action_button(["Bastion"])[0] is not None, # Assuming Workstation button means lab is ready
                message="Lab not in a state to adjust autostop/lifespan (e.g. not running)."
            )
            
            button_xpath = f'//*[@id="tab-course-lab-environment"]/div/table/tr[{button_xpath_part}]/td[2]/button'
            adj_button = self.wait.until(EC.element_to_be_clickable((By.XPATH, button_xpath)))
            for _ in range(times):
                adj_button.click()
                time.sleep(0.1) # Small pause between clicks
        except TimeoutException:
            logging.getLogger(__name__).error(f"Timeout finding {description} button for {course_id}. Lab might not be ready or button not found.")
            # self.selenium_driver.driver.save_screenshot(f"{description.lower().replace(' ','_')}_timeout_{course_id}.png")
            # Pass for now as in original script
        except Exception as e:
            logging.getLogger(__name__).error(f"Error during {description} for {course_id}: {e}")
            # Pass for now

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
                self.go_to_course(current_course_id, environment)
            self.select_lab_environment_tab("lab-environment") # Go to lab tab by default after impersonation

        except Exception as e:
            logging.getLogger(__name__).error(f"An exception occurred while impersonating {impersonate_username}: {e}")
            # self.selenium_driver.driver.save_screenshot(f"impersonate_error_{impersonate_username}.png")
            # Don't re-raise, allow script to continue if impersonation fails but is not critical path for *all* ops

    # --- Placeholder methods for QA and command execution ---
    # These are more complex and might need significant refactoring or external tools

    def open_workstation_console(self, course_id: str, setup_environment_style: str = None):
        self.logger(f"Opening workstation console for course: {course_id}")
        self.select_lab_environment_tab("lab-environment")
        
        # Wait for workstation button to be clickable
        # Original: //*[text()='workstation']/../td[3]/button
        workstation_button = self.wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//td[normalize-space(.)='workstation']/following-sibling::td/button[contains(@class, 'pf-m-primary') or contains(normalize-space(.), 'Open') or contains(normalize-space(.), 'Console')]")
        )) # More robust XPath
        workstation_button.click()
        
        # Wait for new window/tab and switch to it
        WebDriverWait(self.driver, 30).until(EC.number_of_windows_to_be(2))
        handles = self.driver.window_handles
        self.driver.switch_to.window(handles[1])

        # Open virtual keyboard in the console
        try:
            show_keyboard_button = self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="showKeyboard"]')))
            show_keyboard_button.click()
        except TimeoutException:
            logging.getLogger(__name__).error("Could not find 'Show Keyboard' button. Console might have changed.")
            # Proceeding as it might not be critical or UI might be different

        # Store send_text_option_button if needed, or handle text input directly
        # self.send_text_option_button = self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="showSendTextDialog"]')))
        
        if setup_environment_style == "rgdacosta":
            self._setup_environment_rgdacosta_style()
        elif setup_environment_style:
            logging.getLogger(__name__).error(f"Unknown environment setup style: {setup_environment_style}")

    def _setup_environment_rgdacosta_style(self):
        # This method is highly specific and involves many UI interactions.
        # It needs to be carefully translated, ensuring XPaths are robust.
        self.logger("Setting up lab environment 'rgdacosta' style!!!")
        # ... (Implementation would involve many introduce_command calls and waits)
        # Example: self.introduce_command_to_console("student", auto_enter=True)
        # For now, this is a placeholder.
        self.logger("Placeholder: rgdacosta style setup would run here.")
        pass

    def introduce_command_to_console(self, command: str, auto_enter: bool = True):
        self.logger(f"Introducing command to console: '{command[:50]}...' (Enter: {auto_enter})")
        if not command.strip():
            return

        try:
            # Assuming 'showSendTextDialog' button approach from original
            send_text_dialog_button = self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="showSendTextDialog"]')))
            send_text_dialog_button.click()

            text_input_area = self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="sendTextInput"]')))
            text_input_area.send_keys(command)

            send_text_button = self.wait.until(EC.element_to_be_clickable((By.XPATH, '//*[@id="sendTextButton"]')))
            send_text_button.click()
            
            # Wait proportional to command length (from original)
            time.sleep(len(command) * 0.05 + 0.5) # Adjusted multiplier

            if auto_enter:
                # This XPath for 'Enter' on virtual keyboard is highly specific
                # //*[@id="keyboard"]//div[text()="Enter"]
                enter_button_xpath = '//div[@id="keyboard"]//div[text()="Enter"]'
                try: # Try physical enter first via send_keys if possible, else virtual
                    # Attempting to send Enter to the active element if console allows direct input
                    # self.driver.switch_to.active_element.send_keys(Keys.RETURN)
                    # Or use virtual keyboard if that's the only way
                     enter_key_on_virtual_keyboard = self.wait.until(EC.element_to_be_clickable((By.XPATH, enter_button_xpath)))
                     enter_key_on_virtual_keyboard.click()
                except Exception as e_enter:
                    self.logger(f"Could not press Enter key via preferred method: {e_enter}. Ensure console is active or virtual keyboard XPath is correct.")


        except Exception as e:
            self.logger(f"Error introducing command '{command[:50]}...': {e}")
            # self.selenium_driver.driver.save_screenshot(f"command_error_{command[:10]}.png")


    def get_exercise_commands(self, course_id: str, chapter_section: str) -> list[str]:
        self.logger(f"Getting commands for {course_id} - {chapter_section}")
        # This method originally used local git repo and xq to parse XML.
        # This needs a new strategy:
        # 1. API endpoint if available.
        # 2. Pre-processed JSON/text files.
        # 3. User provides the commands.
        # 4. Re-implement parsing if XML files are locally accessible via config path.
        
        # For now, returns a placeholder or raises NotImplementedError
        self.logger("Placeholder: Command retrieval from guides not yet implemented.")
        # Example:
        # guide_content_path = self.config.get("Paths", "course_guides_dir")
        # if guide_content_path:
        #     # Logic to find and parse the correct guide file
        #     # return parsed_commands
        # else:
        #     print("Warning: Course guides directory not configured.")
        return [] 

    def filter_commands_list(self, commands: list[str]) -> list[str]:
        # This is adapted from the original filter_commands_list
        self.logger("Filtering command list...")
        filtered_commands = []
        composed_command = ''
        for command in commands:
            command_stripped = command.strip()
            if not command_stripped:
                continue
            
            if command_stripped.endswith('\\'):
                composed_command += command_stripped[:-1] # Add line, remove trailing slash
            else:
                full_command = composed_command + command_stripped
                if full_command: # Ensure not empty
                    filtered_commands.append(full_command)
                composed_command = ''
        
        # If the last command was a multiline pending, add it
        if composed_command:
             filtered_commands.append(composed_command)
             
        return filtered_commands

    def run_qa_on_exercise(self, course_id: str, chapter_section: str, commands: list[str]):
        self.logger(f"Starting QA for {course_id} - {chapter_section}")
        
        filtered_commands = self.filter_commands_list(commands)
        if not filtered_commands:
            self.logger("No commands to execute for QA.")
            return

        for i, command in enumerate(filtered_commands):
            self.logger(f"Introducing: {command}")
            if i + 1 < len(filtered_commands):
                self.logger(f"Next command: {filtered_commands[i+1]}")
            
            # The original 'manage_special_commands' had complex logic with user prompts.
            # This needs to be re-thought for an automated system.
            # For now, just execute directly or add simplified conditions.
            if self._is_special_command(command):
                self._handle_special_command(command)
            else:
                self.introduce_command_to_console(command, auto_enter=True)
            
            time.sleep(self.config.get("QA", "command_delay_seconds", 3)) # Configurable delay

        self.logger(f"Finished QA for {course_id} - {chapter_section}")

    def _is_special_command(self, command: str) -> bool:
        # Simplified version of manage_special_commands conditions
        # Add more patterns as needed
        special_patterns = [
            r"lab .*start", r"lab .*setup", r"lab .*grade", r"lab .*finish",
            r"ssh", r"ansible", r"vim ", r"vi ", r"less ",
            r"systemctl status", r"systemctl restart", r"daemon-reload",
            r"journalctl", r"yum install", r"dnf install",
            r"oc edit", r"oc create -f", r"oc apply -f",
            r"watch"
        ]
        return any(re.search(pattern, command) for pattern in special_patterns)

    def _handle_special_command(self, command: str):
        self.logger(f"Handling special command: {command[:60]}...")
        # This would contain the logic from manage_special_commands.
        # Many involve `input()` which is problematic for full automation.
        # These might need to become configurable actions or require manual steps.
        
        # Example: if "vim " in command or "vi " in command:
        # self.introduce_command_to_console(command, auto_enter=True)
        # self.logger("ACTION REQUIRED: File opened in editor. Resume script when done.")
        # input("Press Enter to continue after editing file...")
        
        # For now, just log and execute
        self.logger(f"Executing special command as regular command: {command}")
        self.introduce_command_to_console(command, auto_enter=True)
        # Add specific waits or interactions if a command is known to take time or change state
        if "yum install" in command or "dnf install" in command:
            time.sleep(self.config.get("QA", "install_command_delay_seconds", 30))


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
