#!/usr/local/bin/python3
### Maintained by carias@redhat.com
import re
import time, os.path, logging, sys, traceback
from selenium import webdriver
from selenium.webdriver.common import actions
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver import ActionChains

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

options = webdriver.FirefoxOptions()
options.set_preference("permissions.default.microphone", True)
options.set_capability('browserName', 'firefox')
driver = webdriver.Firefox(options=options)
driver.get("https://app.intercom.com/a/inbox/jeuow7ss/inbox/admin/4643910?view=List")

def handle_exception(e):
    exc_type, exc_value, exc_traceback = sys.exc_info()
    traceback_details = traceback.extract_tb(exc_traceback)

    first_function_filename = traceback_details[0].filename
    first_function_line_number = traceback_details[0].lineno
    first_function_name = traceback_details[0].name

    print(
        f"Exception first caught in file {first_function_filename}, line {first_function_line_number}, in {first_function_name}")

    filename = traceback_details[-1].filename
    line_number = traceback_details[-1].lineno
    function_name = traceback_details[-1].name

    print(f"Exception occurred in file {filename}, line {line_number}, in {function_name}")
    print(f"Exception message: {str(e)}")


# Go to the website
def go_to_main_site():
    driver.get("https://app.intercom.com/a/inbox/jeuow7ss/inbox/admin/4643910")
    time.sleep(2)

def intercom_login():
    try:
        WebDriverWait(driver, 3).until(EC.element_to_be_clickable(
            (By.XPATH, '//*[@class="m__login__form"]//*[contains(text(), "Sign in with Google")]'))).click()
        try:
            WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="identifierId"]'))).send_keys(str(os.environ.get('USERNAME')) + "@redhat.com")
            WebDriverWait(driver, 3).until(EC.element_to_be_clickable(
                (By.XPATH, '//*[contains(text(), "Next")]'))).click()
        except:
            WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="view_container"]/div/div/div[2]/div/div[1]/div/form/span/section/div/div/div/div/ul/li[1]/div/div[1]/div/div[2]/div[2]'))).click()

        # RH SSO
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="username"]'))).send_keys(str(os.environ.get('USERNAME')))
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="password"]'))).send_keys(str(os.environ.get('PIN')).replace('\n', '') + str(os.popen("curl -sL https://sso-rh-login-lx-snow.apps.tools-na100.dev.ole.redhat.com/get_otp").read().replace('\n', '')))
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="submit"]'))).click()
        time.sleep(5)
    except Exception as e:
        print("An exception occurred while accepting during login")
        handle_exception(e)


def intercom_change_status(change_status_to):
    try:
        avatar = WebDriverWait(driver, 15).until(EC.element_to_be_clickable(
            (By.XPATH, '/html/body/div[1]/div/div[1]/div[2]/div/div/div/div[1]/div[5]/div/div/div')))
        avatar.click()
        status_raw = avatar.get_attribute(
            "class")
    except:
        avatar = WebDriverWait(driver, 15).until(EC.element_to_be_clickable(
            (By.XPATH, '/html/body/div[1]/div/div[1]/div[3]/div/div/div/div[1]/div[5]/div/div/div')))
        avatar.click()
        status_raw = avatar.get_attribute(
            "class")

        # Get attributes from the gravatar

    status_away = re.findall("o__away", status_raw)
    status_active = re.findall("o__active", status_raw)
    time.sleep(1)
    try:
        # Change status to Away if actual status is active
        if change_status_to == "Away" and status_active:
            WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div[2]/div/div/div/div/div/div[1]/div[2]/div/div/button/span'))).click()

            # Reason "Done for the day"
            time.sleep(1)
            avatar.click()
            reason = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div[2]/div/div/div/div/div/div[1]/div[4]/div/div[1]/div/div[1]')))
            actions.move_to_element(reason).perform()
            done_for_the_day = WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div[2]/div/div/div/div/div/div[1]/div[4]/div/div[2]/div/div/div/div[7]')))
            actions.move_to_element(done_for_the_day).click().perform()

            # Reassign replies
            time.sleep(1)
            avatar.click()
            WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div[2]/div/div/div/div/div/div[1]/div[3]/div/div/button'))).click()

        # Change status to Active if actual status is Away
        if change_status_to == "Active" and status_away:
            WebDriverWait(driver, 20).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div[2]/div/div/div/div/div/div[1]/div[2]/div/div/button/span'))).click()
    except:
        print("Failed to change intercom status")

# Main

go_to_main_site()
intercom_login()
intercom_change_status(os.environ.get('INTERCOM_STATUS'))
time.sleep(3)
driver.quit()
