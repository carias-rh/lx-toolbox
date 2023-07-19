#!/usr/bin/python3
### Maintained by carias@redhat.com
import time, os.path, logging
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By

from selenium import webdriver


logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s',datefmt='%Y-%m-%d %H:%M:%S')

options = webdriver.ChromeOptions()
options.add_argument('--ignore-certificate-errors')
options.add_argument('--no-sandbox')
options.add_argument("--window-size=1600,1200")
options.add_argument('--disable-dev-shm-usage')

# Define the webdriver to use.
# Chrome webdriver
driver = webdriver.Chrome(options=options)

# Go to the website
def go_to_main_site():
    driver.get("https://app.intercom.com/a/inbox/jeuow7ss/inbox/admin/4643910?view=List")
    time.sleep(2)

def intercom_login():
    try:
        WebDriverWait(driver, 3).until(EC.element_to_be_clickable(
            (By.XPATH, '//*[@class="m__login__form"]//*[contains(text(), "Sign in with Google")]'))).click()
        try:
            WebDriverWait(driver, 3).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="identifierId"]'))).send_keys("carias" + "@redhat.com")
            WebDriverWait(driver, 3).until(EC.element_to_be_clickable(
                (By.XPATH, '//*[contains(text(), "Next")]'))).click()
        except:
            WebDriverWait(driver, 3).until(EC.element_to_be_clickable(
                (By.XPATH, '//*[@id="view_container"]/div/div/div[2]/div/div[1]/div/form/span/section/div/div/div/div/ul/li[1]/div/div[1]/div/div[2]/div[2]'))).click()

        # RH SSO
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="username"]'))).send_keys(
            "{{ username }}")
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="password"]'))).send_keys(
            str("{{ pin }}").replace('\n', '') + str(os.popen("curl -sL sso-rh-login:5000/get_otp").read()).replace(
                '\n', ''))
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="submit"]'))).click()
    except:
        logging.error("An exception occurred while accepting during login")


def skype_login():
    try:
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div/form[1]/div/div/div[2]/div[1]/div/div/div/div[2]/div[1]/div[3]/div/div/div/div[2]/div[2]/div/input[1]'))).send_keys(os.environ.get('SKYPE_USERNAME'))
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div/form[1]/div/div/div[2]/div[1]/div/div/div/div[2]/div[1]/div[3]/div/div/div/div[4]/div/div/div/div/input'))).click()
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div/form[1]/div/div/div[2]/div[1]/div/div/div/div[2]/div/div[3]/div/div[2]/div/div[3]/div/div[2]/input'))).send_keys(os.environ.get('SKYPE_PASSWORD'))
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div/form[1]/div/div/div[2]/div[1]/div/div/div/div[2]/div/div[3]/div/div[2]/div/div[4]/div[2]/div/div/div/div/input'))).click()
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div/form/div/div/div[2]/div[1]/div/div/div/div[2]/div/div[3]/div/div[2]/div/div[3]/div[1]/div/label/input'))).click()
        WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div/form/div/div/div[2]/div[1]/div/div/div/div[2]/div/div[3]/div/div[2]/div/div[3]/div[2]/div/div/div[2]/input'))).click()
        WebDriverWait(driver, 15).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div/div/div[1]/div[2]/div/div[2]/div/div[1]/div/div/div/div/div/div[3]/button/div'))).click()
        WebDriverWait(driver, 25).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div/div/div[1]/div[2]/div/div/div[1]/div/div[1]/div[3]/div[1]/div[1]/div[1]/button'))).click()
    except:
        logging.error("Skype login failed")

def skype_call():
    try:
        driver.execute_script("window.open('');")
        driver.switch_to.window(driver.window_handles[1])
        driver.get('https://web.skype.com/')
        skype_login()
        # Make the call
        logging.info("Calling")
        WebDriverWait(driver, 120).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div/div/div[1]/div[2]/div/div/div[1]/div/div[1]/div[3]/div[1]/div[3]/div[2]/div[1]/div/div[1]/div/div/div[3]/div[3]/div/div/div[2]'))).click()
        WebDriverWait(driver, 120).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div/div/div[1]/div[2]/div/div/div[1]/div/div[2]/div/div/div/div[1]/div[1]/div[1]/div[2]/div/button'))).click()
        # Do it without video or audio
        WebDriverWait(driver, 10).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div/div/div[2]/div/div/div/div/div/div/button'))).click()
        # Wait for the hang_up
        time.sleep(60)
        logging.info("Ended call")
        driver.close()
        driver.switch_to.window(driver.window_handles[0])
    except:
        driver.close()
        driver.switch_to.window(driver.window_handles[0])

def wait_for_expert_chat():
    try:
        tag = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div/div[1]/div[2]/div/div/div/div[3]/div/div[2]/div[1]/div[2]/div[1]//*[contains(text(), "Expert")]'))).text
        skype_call()
        # If the call was successful equal the customer_name to the one in the new chat to avoid more calls
        customer_name = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div/div[1]/div[2]/div/div/div/div[3]/div/div[1]/div/div[4]/ul/div/div/li/a/div[2]/div[1]/div[1]'))).text
    except:
        return ''
    return customer_name


# Main

go_to_main_site()
intercom_login()
customer_name = ''
new_customer_name = ''

while True:
    try:
        new_customer = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, '/html/body/div[1]/div/div[1]/div[2]/div/div/div/div[3]/div/div[1]/div/div[4]/ul/div/div/li/a/div[2]/div[1]/div[1]')))
        new_customer.click()
        new_customer_name = new_customer.text
    except:
        pass

    if customer_name != new_customer_name:
        logging.info("New chat from: " + new_customer_name)
        customer_name = wait_for_expert_chat()
    time.sleep(2)
