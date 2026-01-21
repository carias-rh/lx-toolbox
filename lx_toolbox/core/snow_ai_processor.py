import os
import re
import json
import time
import subprocess
import logging
import traceback
from urllib.parse import quote

import requests
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.keys import Keys

from ..utils.config_manager import ConfigManager
from ..utils.helpers import step_logger, reset_step_counter
from .lab_manager import LabManager
from .jira_handler import JiraHandler
from .servicenow_handler import ServiceNowHandler


class SnowAIProcessor:
    """Port of snow-ai.py.j2 logic that relies on LabManager for ROL navigation."""

    def __init__(self, config: ConfigManager, browser_name: str = None, is_headless: bool = None):
        self.config = config
        self.logger = step_logger

        self.lab_mgr = LabManager(
            config=config,
            browser_name=browser_name or config.get("General", "default_selenium_driver", "firefox"),
            is_headless=is_headless if is_headless is not None else (config.get("General", "debug_mode", False) == False)
        )
        self.driver = self.lab_mgr.driver
        self.wait = self.lab_mgr.wait
        self._rol_logged_in = False
        
        # Initialize JiraHandler for Jira login
        self.jira_handler = JiraHandler(
            driver=self.driver,
            wait=self.wait,
            config=config,
            logger=self.logger
        )
        
        # Initialize ServiceNowHandler for ServiceNow operations
        self.snow_handler = ServiceNowHandler(
            driver=self.driver,
            wait=self.wait,
            config=config,
            logger=self.logger
        )

        # LLM provider configuration (matches j2 script semantics)
        self.LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama").strip().lower()
        self.OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "ministral-3:8b")
        self.OLLAMA_COMMAND = os.environ.get("OLLAMA_COMMAND", "/usr/local/bin/ollama")

        self.SIGNATURE_NAME = os.environ.get("SIGNATURE_NAME", "Carlos Arias")

        # ServiceNow URLs from handler
        self.SNOW_BASE_URL = self.snow_handler.base_url
        self.DEFAULT_SNOW_FEEDBACK_QUEUE_URL = self.snow_handler.feedback_queue_url

        # Rich prompt examples (ported from original template)
        self.content_issues_examples = (
            "Examples of content issues:\n"
            "- There is a typo\n"
            "- A paragraph or phrase is incorrect.\n"
            "- Missing information to complete the exercise\n"
            "- The exercise in the guide doesn't match with the video from the instructor\n"
            "- Outdated content of the guide\n"
        )

        self.environment_issues_examples = (
            "Examples of environment issues:\n"
            "- lab start script is failing\n"
            "- lab script is not available\n"
            "- user can't access visual block mode in vim, due to platform limitations. Solution is use the virtual keyboard and press ctrl + V\n"
            "- User can't complete boot troubleshooting because is not selecting the \"recovery\" entry in the boot menu\n"
        )

        self.manually_managed_issues_examples = (
            "Examples of types of issues to be manually managed:\n"
            "- Need to open the LAB in a new WINDOW... Tabs aren't cool, Need to see video side-by-side with lab\n"
            "- Content of the course not appearing\n"
            "- Labs are stuck in starting/stopping state\n"
            "- UI suggestions of improvement\n"
            "- Complaints / Praises on the learning platform or the courses.\n"
        )

        self.video_issues_examples = (
            "Examples of video issues:\n"
            "- Cannot find the video\n"
            "- Video is not available\n"
            "- Video doesn't match the section/chapter\n"
            "- Video subtitles are incorrect or missing\n"
            "- Video translation issues\n"
            "- Video has bad cuts or editing problems\n"
            "- Video audio is out of sync\n"
            "- Video player is not working\n"
            "- Where is the video for this course?\n"
        )

    # --------------------------
    # LLM helpers
    # --------------------------
    def _ask_ollama(self, prompt: str) -> str:
        try:
            if self.OLLAMA_MODEL.startswith(("qwen", "deepseek")):
                result = subprocess.run(
                    [self.OLLAMA_COMMAND, "run", self.OLLAMA_MODEL, prompt],
                    capture_output=True,
                    text=True
                )
            else:
                result = subprocess.run(
                    [self.OLLAMA_COMMAND, "run", self.OLLAMA_MODEL, prompt, "--format", "json"],
                    capture_output=True,
                    text=True
                )
            response = result.stdout or ""

            if self.OLLAMA_MODEL.startswith(("qwen", "deepseek", "gpt-oss")):
                response = re.sub(r'Thinking.*?done thinking\.', '', response, flags=re.DOTALL).strip()
                response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL).strip()
                response = re.sub(r'</think>', '', response).strip()
                response = re.sub(r'Thinking\.\.\.\s*', '', response)
                response = re.sub(r'\.\.\.done thinking\.\s*', '', response)
                response = re.sub(r'\.\.\.done thinking\.', '', response).strip()

            response = re.sub(r'```json\s*', '', response)
            response = re.sub(r'```\s*$', '', response)
            response = response.strip()
            logging.getLogger(__name__).debug(f"LLM[ollama:{self.OLLAMA_MODEL}] response: {response[:1000]}")
            return response
        except Exception as e:
            logging.getLogger(__name__).error(f"Could not run Ollama: {e}")
            return ""


    def ask_llm(self, prompt: str) -> str:
        """Ask the LLM a question and return the response using some LLM provider such as ollama."""
        provider = self.LLM_PROVIDER
        logging.getLogger(__name__).debug(f"LLM request via provider={provider}")
        return self._ask_ollama(prompt)

    @staticmethod
    def _normalize_suggested_correction(value) -> str:
        """Convert suggested_correction to string if LLM returns a dict instead of string."""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            parts = []
            if value.get("remove"):
                parts.append("Remove: " + ", ".join(value["remove"]))
            if value.get("add"):
                parts.append("Add: " + ", ".join(value["add"]))
            if value.get("remove_flag"):
                parts.append("Remove flag: " + ", ".join(value["remove_flag"]))
            return "\n".join(parts) if parts else str(value)
        return str(value)

    # --------------------------
    # ROL helpers (via LabManager)
    # --------------------------
    def ensure_logged_in_rol(self, environment: str = "rol"):
        self.lab_mgr.login(environment=environment)

    def get_section_info(self, course_url: str) -> str:
        """Navigate to course URL and fetch section title using framework tab selection."""
        self.logger("Extracting relevant information from section")
        self.lab_mgr.selenium_driver.go_to_url(course_url)
        time.sleep(2)
        self.lab_mgr.select_lab_environment_tab("course")
        try:
            relevant_section_info = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//h2"))
            ).text
            return relevant_section_info
        except Exception as e:
            logging.getLogger(__name__).error(f"Could not retrieve section title from {course_url}: {e}")
            return ""

    def fetch_guide_text_from_website(self) -> str:
        """Open course page, expand solutions, and return course content wrapper text."""
        self.logger("Fetching guide text from website")
        self.lab_mgr.select_lab_environment_tab("course")

        self.lab_mgr.disable_video_player()
        self.lab_mgr.dismiss_active_alerts()

        try:
            while True:
                try:
                    show_solution_button = WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[text()='Show Solution']"))
                    )
                    show_solution_button.click()
                    time.sleep(0.3)
                except Exception:
                    break
        except Exception:
            pass

        container = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//*[@class='course__content-wrapper']"))
        )
        return container.text

    # --------------------------
    # ServiceNow helpers (delegated to ServiceNowSeleniumHandler)
    # --------------------------
    def login_snow(self):
        """Login to ServiceNow using the ServiceNowSeleniumHandler."""
        self.snow_handler.login(use_session=True)

    def switch_to_iframe(self):
        """Switch to SNOW content iframe through macroponent shadow DOM."""
        self.snow_handler.switch_to_iframe()

    def get_snow_info(self, snow_id: str) -> dict:
        """Open ticket page and parse key fields from description and form controls."""
        self.logger(f"Getting SNOW info for ticket {snow_id}")
        self.snow_handler.navigate_to_ticket(snow_id)

        description = self.driver.find_element(By.XPATH, '//*[@id="sys_original.x_redha_red_hat_tr_x_red_hat_training.description"]').get_attribute('value')
        full_name = self.driver.find_element(By.XPATH, '//*[@id="x_redha_red_hat_tr_x_red_hat_training.contact_source"]').get_attribute('value')

        issue = re.search(r"Description:\s*(.*?)\s*Copyright", description, re.DOTALL).group(1).strip()
        course = re.findall("Course:.*", description)[0].split(":  ")[1].upper().split(" ")[0].strip()
        version = re.findall("Version:.*", description)[0].split(":  ")[1].strip()
        url = re.findall("URL:.*", description)[0].split(":  ")[1].strip()
        if "role.rhu.redhat.com/rol-rhu" in url:
            url = url.replace("role.rhu.redhat.com/rol-rhu", "rol.redhat.com/rol")

        try:
            chapter = re.findall("ch[0-9][0-9]", url)[0].split("ch")[1]
        except Exception:
            chapter = ""
        try:
            section = re.findall("s[0-9][0-9]", url)[0].split("s")[1]
        except Exception:
            section = ""
        title = re.findall("Section Title:.*", description)[0].split(":  ")[1]
        rhnid = re.findall("User Name:.*", description)[0].split(":  ")[1]

        self.driver.refresh()
        info = {
            "snow_id": snow_id,
            "full_name": full_name,
            "Description": issue,
            "Course": course,
            "Version": version,
            "URL": url,
            "Chapter": chapter,
            "Section": section,
            "Title": title,
            "RHNID": rhnid,
        }
        logging.getLogger(__name__).info(f"ServiceNow ticket info: {json.dumps(info, indent=2)}")
        return info

    def get_ticket_ids_from_queue(self) -> list:
        """Get list of ticket IDs from the current queue view."""
        return self.snow_handler.get_ticket_ids_from_queue()

    # --------------------------
    # Ticket understanding
    # --------------------------
    def classify_ticket_llm(self, description: str) -> dict:
        self.logger("Classifying ticket using LLM")
        json_example = '{"student_feedback": "hay un error en el laboratorio", "language": "es", "summary": "The student is reporting an error in the lab", "is_content_issue_ticket": true, "is_environment_issue": false, "is_video_issue": false}'
        prompt = f"""
You are an expert classifier of Red Hat Training tickets.
Classify the user's feedback regarding a Red Hat Training course, there are three types of tickets:
- content_issue_ticket: a mismatch or inconsistency between the user's complaint and the text in the guide, a typo, a missing step, a missing command, etc.
- environment_issue_ticket: if the feedback includes words such as 'lab start', 'lab finish', ' lab grade','SUCCESS', 'FAIL', 'stuck', or 'lab is taking to long to start', it's an environment issue.
- video_issue_ticket: if the feedback is about videos not being available, video not matching the section, subtitle issues, translation problems, bad video cuts, or any other video-related problem.

Examples of content issues:
{self.content_issues_examples}

Examples of environment issues:
{self.environment_issues_examples}

Examples of video issues:
{self.video_issues_examples}

Examples of types of issues to be manually managed:
{self.manually_managed_issues_examples}

Return JSON with the following fields:
- student_feedback: the user's feedback in english, as it is, without any changes. Substitute double quotes with single quotes.
- language: the language of the student's feedback. If the student's feedback is in english, the value of this key-value pair is 'en'.
- summary: in a short sentence, summarize the user's feedback
- is_content_issue_ticket: (true/false)
- is_environment_issue: (true/false)
- is_video_issue: (true/false)

This is the student's feedback:
<student_feedback>
\"\"\"{description}\"\"\"
</student_feedback>

Reply in JSON only, no extra text.
For example:
{json_example}
"""
        response = self.ask_llm(prompt)
        logging.getLogger(__name__).info(f"LLM Triaging response: {response}")
        try:
            return json.loads(response)
        except Exception:
            logging.getLogger(__name__).error(f"Could not parse JSON from LLM. Full response: {response}")
            return {
                "student_feedback": description,
                "language": "en",
                "summary": "Error parsing LLM response",
                "is_content_issue_ticket": False,
                "is_environment_issue": False,
                "is_video_issue": False,
            }

    def analyze_content_issue(self, user_issue: str, guide_text: str) -> dict:
        self.logger("Analyzing content issue using LLM")
        if guide_text.strip():
            guide_text_prompt = f"""
        Identify in the following guide_text the excerpt (text and commands) to which the student's feedback refers, include also the previous lines of the guide_text excerpt for expanded context:
        <guide_text>
        {guide_text}
        </guide_text>"""
            excerpt_spec = '"excerpt": "extract the text from the guide_text that the student\'s feedback refers to if it is related to the issue, otherwise return an empty string.",'
        else:
            guide_text_prompt = ""
            excerpt_spec = '"excerpt": ""'

        json_example = (
            '{' +
            f"\n        \"student_feedback\": \"{user_issue.replace('\n',' ').replace('\t',' ').replace('\r',' ').replace('\"', "'").strip()}\"," +
            f"\n        {excerpt_spec}" +
            "\n        \"analysis\": \"think step by step, first try to understand student_feedback, then explain what the student is trying to communicate, then comprehend the the guide_text excerpt, then compare to see if the student's claims are correct regarding the guide_text excerpt. Detail the analysis as much as possible. Substitute double quotes in this field with single quotes.\"," +
            "\n        \"is_valid_issue\": true," +
            "\n        \"suggested_correction\": \"If the issue is valid, indicate what words, lines, or commands that should be changed in the guide_text to fix the issue. If the issue is valid but there is not enough information it could be possible that a deeper investigation within the lab environment is required. Do not include any explanations or markdown formatting outside the JSON object.\"," +
            "\n        \"summary\": \"a short/medium summary of the 'analysis' field\"," +
            "\n        \"jira_title\": \"a short and precise title of the issue at hand\"\n        }"
        )

        prompt_text = f"""
        You are an useful Red Hat Training expert who is able to understand the flow of the exercises and labs in the course guide.
        We have a student who reported an issue within the guide text. The student's feedback is:
        <student_feedback>
        {user_issue}
        </student_feedback>

        {guide_text_prompt}

        Compare the student's feedback with the excerpt (if any), and provide a detailed analysis of the issue.
        Remove from the response in the JSON any reference to titles or headings, as I already have that information.

        Return your analysis in exactly the following JSON example format without any extra text. Note that the description of what to put in each field is in every value of the JSON example:
        <json_example>
        {json_example}
        </json_example>

        Do not include any explanations, xml or markdown formatting outside the JSON object. No dictionaries in the value fields
        Substitute double quote (") for single quote (') in all fields to avoid errors in the JSON object, and remove any special characters such as '\n', '\t', '\r', etc, as well as XML markers.
        """
        response = self.ask_llm(prompt_text)
        logging.getLogger(__name__).info(f"LLM Content analysis response: {response}")
        try:
            return json.loads(response)
        except Exception:
            return {"is_valid_issue": False, "summary": "analysis parse error", "suggested_correction": "", "jira_title": ""}

    def analyze_environment_issue(self, user_issue: str) -> dict:
        self.logger("Analyzing environment issue using LLM")
        json_example = '{"analysis": "think in this value step by step, describe what the student is trying to communicate in it\'s feedback, and provide the steps needed to debug the issue knowing that the lab is composed of multiple RHEL virtual machines.", "is_valid_issue": true, "suggested_correction": "a brief suggestion for correction if applicable; otherwise an empty string", "summary": "a short summary of your analysis", "jira_title": "a short title for the Jira ticket, all characters in lowercase separated by spaces, no dashes"}'
        prompt_text = f"""
        We have a student who reported an issue within the lab environment. The student's feedback is:
        <student_feedback>
        {user_issue}
        </student_feedback>

        Return your analysis in exactly the following JSON format without any extra text:
        {json_example}

        Do not include any explanations, xml or markdown formatting outside the JSON object. No dictionaries in the value fields
        Substitute double quote (") for single quote (') in all fields to avoid errors in the JSON object, and remove any special characters such as '\n', '\t', '\r', etc, as well as XML markers.
        """
        response = self.ask_llm(prompt_text)
        logging.getLogger(__name__).info(f"LLM Environment analysis response: {response}")
        try:
            return json.loads(response)
        except Exception:
            return {"is_valid_issue": False, "summary": "analysis parse error", "suggested_correction": "", "jira_title": ""}

    def analyze_video_issue(self, user_issue: str, video_available: bool) -> dict:
        """
        Analyze video-related issues reported by students.
        
        Args:
            user_issue: The student's feedback/complaint about video
            video_available: Whether the video player button is available on the page
        
        Returns:
            dict with analysis results including whether a Jira is needed
        """
        self.logger("Analyzing video issue using LLM")
        
        video_context = "The video player IS available on the page, so videos should be accessible." if video_available else "The video player button is NOT available on the page, which typically means videos for this course version are still being produced."
        
        json_example = '{"analysis": "detailed analysis of the video issue", "is_valid_issue": true, "needs_jira": true, "video_issue_type": "content_mismatch", "suggested_correction": "description of what needs to be fixed", "summary": "short summary of the issue", "jira_title": "video issue title for jira"}'
        
        prompt_text = f"""
        You are an expert in Red Hat Training video content issues.
        
        A student has reported a video-related issue. The student's feedback is:
        <student_feedback>
        {user_issue}
        </student_feedback>
        
        Video availability status: {video_context}
        
        Classify the video issue into one of these types:
        - "videos_not_ready": Videos for this course version are not yet available (typically for new course versions)
        - "content_mismatch": Video doesn't match the section/chapter content
        - "subtitle_issue": Problems with subtitles (missing, incorrect, translation issues)
        - "technical_issue": Video player problems, bad cuts, audio sync issues
        - "other": Other video-related issues
        
        Determine if a Jira ticket needs to be created:
        - If videos are not available (video player not present) AND the student is asking where videos are, this is "videos_not_ready" - NO Jira needed
        - If videos ARE available but there's a content, subtitle, or technical issue - Jira IS needed
        
        Return your analysis in exactly the following JSON format without any extra text:
        {json_example}
        
        Fields:
        - analysis: detailed step-by-step analysis of the issue
        - is_valid_issue: true if this is a legitimate video issue
        - needs_jira: true if a Jira ticket should be created, false if it's just videos not ready yet
        - video_issue_type: one of "videos_not_ready", "content_mismatch", "subtitle_issue", "technical_issue", "other"
        - suggested_correction: what needs to be fixed (empty if videos_not_ready)
        - summary: short summary of the analysis
        - jira_title: title for the Jira ticket (empty if no Jira needed)
        
        Do not include any explanations, xml or markdown formatting outside the JSON object.
        Substitute double quote (") for single quote (') in all fields to avoid errors in the JSON object.
        """
        response = self.ask_llm(prompt_text)
        logging.getLogger(__name__).info(f"LLM Video analysis response: {response}")
        try:
            return json.loads(response)
        except Exception:
            return {
                "is_valid_issue": False,
                "needs_jira": False,
                "video_issue_type": "other",
                "summary": "analysis parse error",
                "suggested_correction": "",
                "jira_title": ""
            }

    def is_openshift_lab_first_boot(self, snow_info: dict, analysis_response_json: dict) -> bool:
        course = snow_info.get("Course", "")
        if course in ["DO180", "DO280", "DO188", "DO288", "DO380", "DO480", "DO316", "DO322", "DO328", "DO370", "DO400"]:
            response = self.ask_llm(
                f"""
               You are an expert in Red Hat Training. We have a platform where students can run labs.
               Openshift labs take about 20-30 min to finish the setup the first time they are booted up. Once everything is working, it should be pretty fast.
               Determine from the students feedback if this could be the case that the lab is first boot or not.

               The analysis of the issue is:
               {json.dumps(analysis_response_json)}

               Your work is to determine if the lab is first boot or not based on the information provided.
               Return just True or False, no extra text.
              """
            )
            return str(response).strip().lower().startswith("true")
        return False

    def craft_llm_response(self, snow_info: dict, analysis_response_json: dict) -> dict:
        self.logger("LLM Crafting reply to student")
        student_name = snow_info.get("full_name", "").split(" ")[0]
        course = snow_info.get("Course", "")
        chapter = snow_info.get("Chapter", "")
        section = snow_info.get("Section", "")
        url = snow_info.get("URL", "")
        json_example = '{"response": "the response to the student"}'
        prompt_text = f"""
    You are a helpful Red Hat Training support representative responding to a student's feedback.

    Student Information:
    - Name: {student_name}

    Student's Original Feedback:
    {snow_info.get('Description', '')}

    Course guide URL:
    {url}

    Analysis Results:
    - Issue Summary: {analysis_response_json.get('summary', '')}
    - Is Valid Issue: {analysis_response_json.get('is_valid_issue', False)}
    - Suggested Correction: {self._normalize_suggested_correction(analysis_response_json.get('suggested_correction', ''))}
    - Analysis: {analysis_response_json.get('analysis', '')}

    Craft a professional, helpful response to the student based on the analysis results that:
    1. Addresses them by their first name
    2. Acknowledges their feedback if the analysis is valid, otherwise ask for more information.
    3. Don't add a signature nor final salutation to the response.


    Keep the response concise but informative. 

    Special cases:
    - If the issue is related to the lab environment Suggest to the student to delete the lab environment and create a new one .
    - If the feedback is vague, asks for more information.

    Format the response as a JSON object with the following fields:
    - response: the response to the student

    Reply in JSON only, no extra text, such as:
    {json_example}
    """
        response = self.ask_llm(prompt_text)
        logging.getLogger(__name__).info(f"LLM Student reply output: {response}")
        try:
            return json.loads(response)
        except Exception:
            return {"response": "Thank you for your feedback. We are investigating this and will follow up."}


    def reply_to_student_and_add_notes(self, snow_info: dict, classification_data: dict, analysis_response_json: dict):
        self.logger("Replying to student and adding summary notes")
        signature = f"\nBest Regards,\n{self.SIGNATURE_NAME}\nRed Hat Learner Experience Team"

        try:
            # Ensure we are inside the ticket iframe
            self.switch_to_iframe()

            # Add work note with summary of analysis
            work_note = f"""Summary:\n{analysis_response_json.get('summary', 'No summary available')}\n
LLM Analysis: {analysis_response_json.get('analysis', '')}\n"""
            try:
                WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="x_redha_red_hat_tr_x_red_hat_training.work_notes"]'))).send_keys(work_note)
            except Exception:
                pass

            # Prepare and add student reply
            reply_text = ""
            if classification_data.get("is_content_issue_ticket", False):
                crafted = self.craft_llm_response(snow_info, analysis_response_json)
                reply_text = crafted.get("response", "")
                default_jira_reply = (
                    f"\n\nDear {snow_info.get('full_name','').split(' ')[0]},\n\n"
                    f"We created a Jira ticket to fix it in the next release.\n\n"
                    f"Thanks again for your contributions to improving the course guide! \n\n"
                )
                try:
                    WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="x_redha_red_hat_tr_x_red_hat_training.comments"]'))).send_keys(reply_text + signature)
                    WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="x_redha_red_hat_tr_x_red_hat_training.work_notes"]'))).send_keys("\n\nDEFAULT RESPONSE:")
                    WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="x_redha_red_hat_tr_x_red_hat_training.work_notes"]'))).send_keys(default_jira_reply + signature)
                except Exception:
                    pass
            elif classification_data.get("is_video_issue", False):
                # Handle video issues
                video_issue_type = analysis_response_json.get("video_issue_type", "other")
                needs_jira = analysis_response_json.get("needs_jira", False)
                
                if video_issue_type == "videos_not_ready" or not needs_jira:
                    # Videos not yet available for this course version - no Jira needed
                    reply_text = (
                        f"\n\nDear {snow_info.get('full_name','').split(' ')[0]},\n\n"
                        f"Thank you for reaching out regarding the video content for this course.\n\n"
                        f"The videos for this course version are still being produced by our team. "
                        f"Once they become available, the \"Enable video player\" button will appear "
                        f"in the dock bar at the bottom of the learning platform.\n\n"
                        f"We appreciate your patience and understanding. Please check back later "
                        f"for video availability.\n\n"
                    )
                    try:
                        WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="x_redha_red_hat_tr_x_red_hat_training.comments"]'))).send_keys(reply_text + signature)
                        WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="x_redha_red_hat_tr_x_red_hat_training.work_notes"]'))).send_keys("\n\nVIDEO NOT READY - No Jira needed. Videos for this course version are still being produced.")
                    except Exception:
                        pass
                else:
                    # Video content issue that needs a Jira ticket
                    crafted = self.craft_llm_response(snow_info, analysis_response_json)
                    reply_text = crafted.get("response", "")
                    default_jira_reply = (
                        f"\n\nDear {snow_info.get('full_name','').split(' ')[0]},\n\n"
                        f"We have created a Jira ticket to address this video issue.\n\n"
                        f"Thanks for helping us improve the video content!\n\n"
                    )
                    try:
                        WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="x_redha_red_hat_tr_x_red_hat_training.comments"]'))).send_keys(reply_text + signature)
                        WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="x_redha_red_hat_tr_x_red_hat_training.work_notes"]'))).send_keys("\n\nVIDEO ISSUE - Jira ticket created with 'Video Content' component.")
                        WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="x_redha_red_hat_tr_x_red_hat_training.work_notes"]'))).send_keys(default_jira_reply + signature)
                    except Exception:
                        pass
            elif classification_data.get("is_environment_issue", False) and self.is_openshift_lab_first_boot(snow_info, analysis_response_json):
                reply_text = (
                    f"\n\nDear {snow_info.get('full_name','').split(' ')[0]},\n\n"
                    f"Labs take about 20-30 min to finish the setup the first time they are booted up, so please give it time. Once everything is working, it should be pretty fast.\n\n"
                    f"You can monitor the status of the cluster by ssh lab@utility and running the ./wait.sh script. Once the script has finished the scripts are ready to be run.\n\n"
                    f"If by the time you read this message it is still not working fine, I would suggest deleting and creating a new lab environment, and then try to run the lab again.\n\n"
                    f"Please, let me know if the issue persists.\n\n"
                )
                try:
                    WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="x_redha_red_hat_tr_x_red_hat_training.comments"]'))).send_keys(reply_text + signature)
                except Exception:
                    pass
            else:
                crafted = self.craft_llm_response(snow_info, analysis_response_json)
                reply_text = crafted.get("response", "")
                try:
                    WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="x_redha_red_hat_tr_x_red_hat_training.comments"]'))).send_keys(reply_text + signature)
                except Exception:
                    pass
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to add work note / reply for {snow_info.get('snow_id','')}: {e}")

    def translate_text(self, text: str, language: str) -> str:
        prompt_text = f"""
Translate the following text from {language} to english:
<text>
{text}
</text>
"""
        translated_text = self.ask_llm(prompt_text)
        logging.getLogger(__name__).info(f"LLM Feedback translation response: {translated_text}")
        if translated_text.strip().startswith('{') and translated_text.strip().endswith('}'):
            try:
                json_response = json.loads(translated_text)
                for key in ("text", "translation", "result"):
                    if key in json_response and isinstance(json_response[key], str) and json_response[key] != '/set parameter num_ctx 128000':
                        return json_response[key]
                for value in json_response.values():
                    if isinstance(value, str) and value != '/set parameter num_ctx 128000':
                        return value
            except Exception:
                pass
        return translated_text

    # --------------------------
    # High-level helpers
    # --------------------------
    def start_lab_for_course(self, course_id: str, chapter_section: str = "pr01", environment: str = "rol"):
        self.lab_mgr.go_to_course(course_id=course_id, chapter_section=chapter_section, environment=environment)
        primary_status, secondary_status = self.lab_mgr.check_lab_status()
        if primary_status == "CREATE":
            self.lab_mgr.create_lab(course_id=course_id)
            primary_status, secondary_status = self.lab_mgr.check_lab_status()
        if primary_status == "START" or secondary_status == "START":
            self.lab_mgr.start_lab(course_id=course_id)




    # --------------------------
    # Window/Tab Orchestration
    # --------------------------
    def login_jira(self):
        """
        Login to Jira using the JiraHandler.
        
        First tries session login (SSO may already be active from ServiceNow).
        If not logged in, attempts SSO login with available credentials.
        If credentials are not available, prompts for manual authentication.
        """
        self.jira_handler.login(use_session=True)

    def prelogin_all(self, environment: str = "rol"):
        # 1) ServiceNow queue (base window)
        self.driver.get(self.DEFAULT_SNOW_FEEDBACK_QUEUE_URL)
        try:
            WebDriverWait(self.driver, 3).until(EC.presence_of_element_located((By.XPATH, '//*[@id="username"]')))
            self.login_snow()
        except Exception:
            pass

        # Track base window and tabs for visual verification
        self.base_window_handle = self.driver.current_window_handle
        self.login_tab_handles = {}

        # 2) ROL login in new tab (within base window)
        self.driver.switch_to.new_window('tab')
        self.login_tab_handles['rol'] = self.driver.current_window_handle
        try:
            base_url = self.config.get_lab_base_url(environment) or "https://rol.redhat.com/rol/app/courses/"
            self.driver.get(base_url)
        except Exception:
            pass
        # Use LabManager for robust login
        try:
            self.lab_mgr.login(environment=environment)
            self._rol_logged_in = True
        except Exception as e:
            logging.getLogger(__name__).warning(f"ROL login issue: {e}")

        # 3) Jira login in new tab (within base window)
        self.driver.switch_to.new_window('tab')
        self.login_tab_handles['jira'] = self.driver.current_window_handle
        self.login_jira()

        # Return focus to ServiceNow tab in base window for visibility
        self.driver.switch_to.window(self.base_window_handle)

    def extract_jira_keyword(self, snow_info: dict) -> str:
        prompt = (
            "You are an expert technical keyword extractor. "
            "From the folowing feedback information, identify ONE single defining technical term that will be used to search into a database of tickets. "
            f"<feedback> {snow_info.get('Description','')} </feedback>"
            "Output JSON example: {\"keyword\": \"PosgreSQL\"}\n"
            "Reply in JSON only, no extra text."
        )
        llm_response = self.ask_llm(prompt)
        try:
            keyword = json.loads(llm_response).get("keyword", "")
            logging.getLogger(__name__).info(f"Extracted Jira keyword: {keyword if keyword else '[none]'}")
            return keyword
        except Exception:
            return ""

    def build_jira_search_url(self, snow_info: dict, keyword: str) -> str:
        course = snow_info.get("Course", "")
        if snow_info.get("Section"):
            chapter_and_section = f"ch{snow_info.get('Chapter','')}s{snow_info.get('Section','')}"
        else:
            chapter_and_section = f"ch{snow_info.get('Chapter','')}"
        if course == "RH199":
            component_clause = 'component in (RH134, RH199, RH124)'
        else:
            component_clause = f'component = "{course}"'
        term = (keyword or course).replace('"', '').replace("'", "")
        jql = f'project = PTL AND resolution = Unresolved AND description ~ {chapter_and_section} AND {component_clause} AND text ~ "{term}" ORDER BY priority DESC, updated DESC'
        return f"https://issues.redhat.com/issues/?jql={quote(jql)}"


    def open_jira_create_prefilled(self, snow_info: dict, analysis: dict, classification: dict):
        self.driver.get("https://issues.redhat.com/projects/PTL/issues")
        time.sleep(5)
        self.driver.execute_script("document.body.style.zoom = '0.6'")

        try:
            # Wait for tabs-placeholder to disappear (it obscures the Create button)
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.invisibility_of_element_located((By.CSS_SELECTOR, 'div.tabs-placeholder'))
                )
            except Exception:
                pass  # Continue even if placeholder doesn't exist or timeout

            # Click Create - use JavaScript click as fallback if element is obscured
            create_btn = WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.XPATH, '//*[@id="create_link"]')))
            try:
                create_btn.click()
            except Exception:
                # Fallback to JavaScript click if regular click fails
                self.driver.execute_script("arguments[0].click();", create_btn)
            time.sleep(2)

            # Select Text mode
            WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="description-wiki-edit"]/nav/div/div/ul/li[2]/button'))).click()

            # Fill in Summary
            summary_value = f"{snow_info.get('Course','')}: ch{snow_info.get('Chapter','')}s{snow_info.get('Section','')} - {analysis.get('jira_title', '')} - {snow_info.get('snow_id','')}"
            WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="summary"]'))).send_keys(summary_value)

            # Add Description
            translated = classification.get("translated_student_feedback", snow_info.get("Description",""))
            description = (
                f"\n|*URL:*|[ch{snow_info.get('Chapter','')}s{snow_info.get('Section','')} |{snow_info.get('URL','')}]|\n"
                f"|*Reporter RHNID:*| {snow_info.get('RHNID','')} |\n"
                f"|*Section title:*|{snow_info.get('Title','')}|\n"
                f"|*Language*:| English |\n\n"
                "*Issue description*\n\n" + translated + "\n\n"
                "*Steps to reproduce:*\n\n"
                "*Workaround:*\n" + self._normalize_suggested_correction(analysis.get('suggested_correction', '')) + "\n\n"
                "*Expected result:*"
            )

            # Add description
            WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="description"]'))).clear()
            WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="description"]'))).send_keys(description)

            # Select Visual mode back again
            WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="description-wiki-edit"]/nav/div/div/ul/li[1]/button'))).click()

            # Priority tab -> Minor
            try:
                self._click_tab_by_text('Priority')
                priority_dropdown = WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="priority-field"]')))
                priority_dropdown.send_keys(Keys.CONTROL + "a")
                priority_dropdown.send_keys(Keys.DELETE)
                priority_dropdown.send_keys("Minor")
                priority_dropdown.send_keys(Keys.TAB)    
                self._click_tab_by_text('Field Tab')
            except Exception as e:
                logging.getLogger(__name__).error(f"Failed to select Priority tab: {e}")
                pass

            # Select Component (course)
            try:
                components_field = WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="components-textarea"]')))
                components_field.send_keys(snow_info.get("Course",""))
                
                # Add "Video Content" component if this is a video issue
                if classification.get("is_video_issue", False):
                    time.sleep(0.5)  # Brief pause to allow first component to register
                    components_field.send_keys(Keys.TAB)
                    components_field = WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="components-textarea"]')))
                    components_field.send_keys("Video Content")
                    logging.getLogger(__name__).info("Added 'Video Content' component for video issue")
            except Exception as e:
                logging.getLogger(__name__).error(f"Failed to select Component: {e}")
                pass

            # Add chapter number (ch01s01 won't work)
            try:
                WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="customfield_12316549"]'))).send_keys(f"{snow_info.get('Chapter','')}")
            except Exception as e:
                logging.getLogger(__name__).error(f"Failed to add chapter number: {e}")
                pass

            # Select version (send HOME via unicode and type course name)
            try:
                version = WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//*[@id="versions-textarea"]')))
                version.send_keys(Keys.HOME)
                version.send_keys(snow_info.get("Course",""))
            except Exception:
                pass
        except Exception as e:
            logging.getLogger(__name__).warning(f"Prefill Jira create failed: {e}")


    def _click_tab_by_text(self, tab_text: str):
        try:
            WebDriverWait(self.driver, 20).until(EC.element_to_be_clickable((By.XPATH, f'//ul[@role="tablist"]//li[@class="menu-item first" or @class="menu-item "]//strong[text()="{tab_text}"]'))).click()
        except Exception:
            pass

    def run(self, tickets: list[str] | None = None, environment: str = "rol"):
        # Pre-login services in base window
        self.prelogin_all(environment=environment)

        # Collect tickets if none provided
        if not tickets:
            tickets = self.get_ticket_ids_from_queue()

        for snow_id in tickets:
            try:
                reset_step_counter()
                # Open new browser window for isolation
                self.driver.switch_to.new_window('window')
                ticket_window = self.driver.current_window_handle

                # Tab 1: ServiceNow ticket
                self.driver.get(f"{self.SNOW_BASE_URL}/surl.do?n={snow_id}")
                time.sleep(3)
                tab_snow = self.driver.current_window_handle

                # Zoom in the ServiceNow ticket page
                self.driver.execute_script("document.body.style.zoom = '1.5'")

                # Parse info and run classification/analysis
                self.driver.switch_to.window(tab_snow)
                snow_info = self.get_snow_info(snow_id)
                classification = self.classify_ticket_llm(snow_info["Description"])
                if classification.get("language") == "en":
                    translated = snow_info["Description"]
                else:
                    translated = self.translate_text(snow_info["Description"], classification.get("language", "en"))
                classification["translated_student_feedback"] = translated
                analysis = {"summary": "", "suggested_correction": "", "jira_title": "", "is_valid_issue": False}

                # Tab 2: ROL chapter/section
                self.driver.switch_to.window(ticket_window)
                self.driver.switch_to.new_window('tab')
                tab_rol = self.driver.current_window_handle
                video_player_available = False
                course_id = ""
                try:
                    self.driver.switch_to.window(tab_rol)
                    course_id = snow_info["Course"].lower() + "-" + snow_info["Version"]
                    if snow_info.get("Section"):
                        chapter_section=f"ch{ snow_info.get("Chapter", "01") }s{ snow_info.get("Section", "01") }"
                    else:
                        chapter_section=f"ch{ snow_info.get("Chapter", "01") }"
                    
                    # For video issues, just navigate to course page (don't start lab)
                    if classification.get("is_video_issue"):
                        self.logger("Video issue detected - navigating to course page without starting lab")
                        self.lab_mgr.go_to_course(course_id=course_id, chapter_section=chapter_section, environment=environment)
                        time.sleep(2)
                        
                        # Check if video player is available
                        video_player_available = self.lab_mgr.check_video_player_available()
                        
                        # Analyze video issue (no guide text needed)
                        analysis = self.analyze_video_issue(snow_info["Description"], video_player_available)
                    else:
                        try:
                            # For non-video issues, start lab as usual
                            self.start_lab_for_course(course_id=course_id, chapter_section=chapter_section, environment=environment)
                        except Exception:
                            pass
                            
                        try:
                            self.lab_mgr.select_lab_environment_tab("course")
                        except Exception:
                            pass
                        
                        # Fetch guide text from website for content analysis
                        guide_text = ""
                        try:
                            guide_text = self.fetch_guide_text_from_website()
                            self.logger(f"Fetched guide text length: {len(guide_text)}")
                        except Exception as e:
                            logging.getLogger(__name__).warning(f"Failed fetching guide text: {e}")

                        # Perform analysis using the guide_text (if content) or env analysis
                        if classification.get("is_content_issue_ticket"):
                            analysis = self.analyze_content_issue(snow_info["Description"], guide_text)
                        elif classification.get("is_environment_issue"):
                            analysis = self.analyze_environment_issue(snow_info["Description"]) 
                except Exception as e:
                    logging.getLogger(__name__).warning(f"ROL tab setup failed for {snow_id}: {e}\n{traceback.format_exc()}")

                # Add SNOW work notes and student reply in Tab 1 (after analysis)
                try:
                    self.driver.switch_to.window(tab_snow)
                    self.reply_to_student_and_add_notes(snow_info, classification, analysis)
                except Exception as e:
                    logging.getLogger(__name__).warning(f"Failed updating SNOW notes/reply for {snow_id}: {e}")

                # Determine if Jira ticket is needed
                # Skip Jira for video issues where videos are just not ready yet
                skip_jira = (
                    classification.get("is_video_issue", False) and 
                    (analysis.get("video_issue_type") == "videos_not_ready" or not analysis.get("needs_jira", True))
                )

                if skip_jira:
                    self.logger("Skipping Jira creation - videos not ready, no ticket needed")
                else:
                    # Tab 3: Jira search of similar tickets
                    self.driver.switch_to.window(ticket_window)
                    self.driver.switch_to.new_window('tab')
                    tab_jira_search = self.driver.current_window_handle
                    try:
                        self.driver.switch_to.window(tab_jira_search)
                        self.logger("Opening Jira search tab")
                        keyword = self.extract_jira_keyword(snow_info)
                        search_url = self.build_jira_search_url(snow_info, keyword)
                        self.driver.get(search_url)
                    except Exception as e:
                        logging.getLogger(__name__).warning(f"Jira search setup failed for {snow_id}: {e}")

                    # Tab 4: Jira prefilled new ticket
                    self.driver.switch_to.window(ticket_window)
                    self.driver.switch_to.new_window('tab')
                    tab_jira_create = self.driver.current_window_handle
                    try:
                        self.driver.switch_to.window(tab_jira_create)
                        self.logger("Opening Jira create dialog")
                        self.open_jira_create_prefilled(snow_info, analysis, classification)
                    except Exception as e:
                        logging.getLogger(__name__).warning(f"Jira create prefill failed for {snow_id}: {e}")


                # Increase autostop and lifespan (skip for video issues since no lab was started)
                if not classification.get("is_video_issue"):
                    self.driver.switch_to.window(tab_rol)
                    self.lab_mgr.select_lab_environment_tab("lab-environment")
                    self.lab_mgr.increase_autostop(course_id=course_id)
                    self.lab_mgr.increase_lifespan(course_id=course_id)
                    self.driver.switch_to.window(tab_snow)
                    self.logger(f"Autostop and lifespan increased for course {course_id}")
                else:
                    self.driver.switch_to.window(tab_snow)

            except Exception as e:
                logging.getLogger(__name__).error(f"Failed to orchestrate window for ticket {snow_id}: {e}")


