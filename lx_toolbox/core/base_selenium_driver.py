from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
import os
import time

class BaseSeleniumDriver:
    def __init__(self, browser_name: str = "firefox", is_headless: bool = False, config_manager=None):
        self.browser_name = browser_name.lower()
        self.is_headless = is_headless
        self.config_manager = config_manager
        self.driver = None
        self._initialize_driver()

    def _initialize_driver(self):
        if self.browser_name == "chrome":
            options = webdriver.ChromeOptions()
            options.add_argument('--ignore-certificate-errors')
            options.add_argument("--window-size=1600,1200")
            if self.is_headless:
                options.add_argument("--headless")
            # Add other Chrome-specific options if needed from old scripts (e.g., debuggerAddress)
            self.driver = webdriver.Chrome(options=options)
        elif self.browser_name == "firefox":
            options = webdriver.FirefoxOptions()
            if self.is_headless:
                options.add_argument("-headless") # Corrected from set_headless()
            self.driver = webdriver.Firefox(options=options)
            # Install the Copy On Select Firefox extension if present.
            # Adapted from playbook: driver.install_addon(os.path.expanduser('{{ playbook_dir }}/../copy_on_select-1.0-an+fx.xpi'), temporary=True)
            copy_on_select_xpi_path = os.path.expanduser(os.path.join(os.path.dirname(__file__), "../../copy_on_select-1.0-an+fx.xpi"))
            if os.path.exists(copy_on_select_xpi_path):
                self.driver.install_addon(copy_on_select_xpi_path, temporary=True)
            self.driver.maximize_window()
        else:
            raise ValueError(f"Unsupported browser: {self.browser_name}. Choose 'firefox' or 'chrome'.")
        
        self.wait = WebDriverWait(self.driver, 2) # Default explicit wait time

    def get_driver(self):
        return self.driver

    def close(self):
        if self.driver:
            self.driver.quit()

    def go_to_url(self, url: str):
        self.driver.get(url)
        time.sleep(2)

    def _preset_trustarc_cookie(self):
        """
        Inject the TrustArc opt-in cookie so the consent banner is never shown.

        Must be called after at least one ``driver.get()`` to a redhat.com
        domain so the browser has a domain to attach the cookie to.
        """
        try:
            self.driver.add_cookie({
                "name": "notice_behavior",
                "value": "expressed,eu",
                "domain": ".redhat.com",
                "path": "/",
                "secure": True,
            })
            self.driver.add_cookie({
                "name": "notice_gdpr_prefs",
                "value": "0|1|2:",
                "domain": ".redhat.com",
                "path": "/",
                "secure": True,
            })
            self.driver.add_cookie({
                "name": "cmapi_cookie_privacy",
                "value": "permit 1 2 3",
                "domain": ".redhat.com",
                "path": "/",
                "secure": True,
            })
        except Exception:
            pass

    def accept_trustarc_cookies(self, timeout: int = 2):
        """Handles TrustArc Cookie Consent Manager if present."""
        wait = WebDriverWait(self.driver, timeout)
        agree_xpath = "//a[@class='call'][normalize-space(text())='Agree and proceed with standard settings']"
        clicked = False
        used_legacy_iframe = False

        try:
            # Legacy TrustArc iframe (older ROL pages).
            wait.until(
                EC.frame_to_be_available_and_switch_to_it(
                    (By.XPATH, '//iframe[@title="TrustArc Cookie Consent Manager"]')
                ),
                message="TrustArc iframe not found",
            )
            agree_button = wait.until(
                EC.element_to_be_clickable((By.XPATH, agree_xpath)),
                message="TrustArc agree button not found",
            )
            agree_button.click()
            clicked = True
            used_legacy_iframe = True
        except Exception:
            self.driver.switch_to.default_content()
        else:
            self.driver.switch_to.default_content()

        if not clicked:
            try:
                # New TrustArc consent renders inside a shadow root on sso.redhat.com.
                agree_button = wait.until(
                    lambda driver: driver.execute_script(
                        """
                        const selectors = [
                          '[id^="pop-frame"]',
                          '[name="trustarc_cm"]',
                          '.trustarc_newcm_container',
                          '.truste_popframe',
                        ];
                        for (const selector of selectors) {
                          const host = document.querySelector(selector);
                          if (!host?.shadowRoot) continue;
                          const agree = host.shadowRoot.querySelector('a.call');
                          if (agree) return agree;
                        }
                        return null;
                        """
                    ),
                    message="TrustArc shadow-root agree button not found",
                )
                agree_button.click()
                clicked = True
            except Exception:
                pass

        self.driver.switch_to.default_content()

        if clicked and used_legacy_iframe:
            # Only the legacy iframe flow requires a page refresh to apply the cookie consent;
            # the shadow-root modal dismisses in place without reloading.
            self.driver.refresh()
            time.sleep(3)
        elif clicked:
            # Shadow-root overlay dismisses in place but the DIV lingers in the DOM and
            # can intercept clicks until it's fully removed.  Wait up to 10 s for it to go.
            overlay_selectors = (
                '.trustarc_newcm_container, .truste_popframe, '
                '[name="trustarc_cm"], [id^="pop-frame"]'
            )
            try:
                WebDriverWait(self.driver, 5).until(
                    EC.invisibility_of_element_located((By.CSS_SELECTOR, overlay_selectors))
                )
            except Exception:
                # If still present, force-remove it via JS so it doesn't block further clicks.
                try:
                    self.driver.execute_script(
                        """
                        document.querySelectorAll(
                          '.trustarc_newcm_container, .truste_popframe, [name="trustarc_cm"], [id^="pop-frame"]'
                        ).forEach(el => el.remove());
                        """
                    )
                except Exception:
                    pass

    def wait_for_element_clickable(self, by: By, value: str, timeout: int = 5):
        return WebDriverWait(self.driver, timeout).until(EC.element_to_be_clickable((by, value)))
    
    def wait_for_element_visible(self, by: By, value: str, timeout: int = 5):
        return WebDriverWait(self.driver, timeout).until(EC.presence_of_element_located((by, value)))
