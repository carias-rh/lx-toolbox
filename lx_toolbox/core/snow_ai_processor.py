import ast
import os
import re
import json
import time
import logging
import traceback
from urllib.parse import quote

import requests
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains

from ..utils.config_manager import ConfigManager
from ..utils.helpers import step_logger, reset_step_counter
from .lab_manager import LabManager
from .jira_handler import JiraHandler
from .servicenow_handler import ServiceNowHandler
from .servicenow_api import ServiceNowAPIClient, ServiceNowAPIError


class SnowAIProcessor:

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

        # API fast-path: attempt to create a REST client; fall back to DOM scraping
        # when credentials are absent.  See ADR-0001.
        _log = logging.getLogger(__name__)
        try:
            self._snow_api: ServiceNowAPIClient | None = ServiceNowAPIClient(config)
            _log.info("SNOW API available — using REST for ticket info")
        except ServiceNowAPIError:
            self._snow_api = None
            _log.info("SNOW API credentials not found — using browser scraping")

        # LLM provider configuration (matches j2 script semantics)
        self.LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "ollama").strip().lower()
        self.OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "ministral-3:8b")
        #self.OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "laguna-xs-2.1:latest")
        self.OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
        self.OLLAMA_TIMEOUT_SECONDS = int(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "600"))
        self.OLLAMA_MAX_NUM_CTX = int(os.environ.get("OLLAMA_MAX_NUM_CTX", "32768"))

        # OpenAI-compatible provider (e.g. granite-4.1-8b via Red Hat STC AI)
        self.OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "ibm-granite/granite-4.1-8b")
        self.OPENAI_BASE_URL = os.environ.get(
            "OPENAI_BASE_URL",
            "https://granite-4-1-8b--apicast-production.apps.int.stc.ai.prod.us-east-1.aws.paas.redhat.com/v1",
        ).rstrip("/")
        # USER_KEY is preferred: OPENAI_API_KEY is shadowed by the real OpenAI key in the shell env
        # and load_dotenv will not override an already-set env var.
        self.OPENAI_API_KEY = os.environ.get("USER_KEY", os.environ.get("OPENAI_API_KEY", ""))
        self.OPENAI_TIMEOUT_SECONDS = int(os.environ.get("OPENAI_TIMEOUT_SECONDS", "600"))
        self.OPENAI_MAX_TOKENS = int(os.environ.get("OPENAI_MAX_TOKENS", "4096"))

        self.SIGNATURE_NAME = os.environ.get("SIGNATURE_NAME", "Carlos Arias")

        # ServiceNow URLs from handler
        self.SNOW_BASE_URL = self.snow_handler.base_url
        self.DEFAULT_SNOW_FEEDBACK_QUEUE_URL = self.snow_handler.feedback_queue_url

        # Rich prompt examples (ported from original template)
        self.content_issues_examples = (
            "Examples of content issues:\n"
            "- There is a typo in the guide text\n"
            "- A paragraph or phrase is incorrect.\n"
            "- Missing information to complete the exercise\n"
            "- Outdated content of the guide\n"
            "- Some command is not working as expected in the lab environment\n"
            "- The solution is doing something that was not in the requirements of the exercise\n"
            "- The output of a command in the lab environment is different from the expected output in the guide\n"
            "- The grading script is not considering a particular solution from the student\n"       
        )

        self.environment_issues_examples = (
            "Examples of environment issues:\n"
            "- lab start script is failing\n"
            "- lab script is not available\n"
            "- lab is stuck in starting/stopping state\n"
            "- cluster is taking too long to start\n"
            "- ssh to the lab workstation VM from inside the lab is not working\n"
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
            "- The exercise in the guide doesn't match with the video from the instructor\n"
        )

        self.ssh_lab_access_examples = (
            "Examples of SSH Lab Access Feedback (Internal Learners on ROLE only):\n"
            "- Cannot connect with rht_classroom.rsa\n"
            "- DOWNLOAD SSH KEY is disabled or missing\n"
            "- Permission denied (publickey) when using the classroom SSH key\n"
            "- ssh -J cloud-user@<ip>:22022 student@workstation fails\n"
            "- chmod / ssh-add / jump-host instructions from the Lab Environment page\n"
        )

        self.manually_managed_issues_examples = (
            "Examples of types of issues to be manually managed:\n"
            "- I've lost many lab hours with an issue and I want hours back or a refund\n"
            "- How can I schedule an exam?\n"
            "- UI suggestions of improvement\n"
            "- Complaints / Praises on the learning platform or the courses.\n"
        )

        # Distilled operational rules from internal SNOW AI knowledge gathering.
        self.platform_operational_notes = (
            "Platform facts:\n"
            "- Some courses begin with a preface that explains the classroom or lab environment.\n"
            "- Learners can access ebook and PDF versions from the training bookshelf.\n"
            "- Learners can switch course version in platform settings; this can change written content, video availability, lab topology, and solution behavior.\n"
            "- Course pages have a Course view and a Lab Environment view.\n"
            "- The Lab Environment view includes SSH instructions, Classroom Webapp, and Lab Controls.\n"
            "- Labs create multiple VMs. Workstation is the main learner entrypoint. Learners usually connect from workstation to VMs such as servera, serverb, and utility.\n"
            "- Bastion, classroom, and registry machines are usually engineer-only systems and should not be suggested as normal learner entrypoints.\n"
            "- Guided exercises, labs, and comprehensive reviews always have Show Solution.\n"
            "- Quizzes always have Check and Show Solution.\n"
            "- Even-numbered sections are guided exercises. Odd-numbered sections are usually theory or labs.\n"
            "- Only guided exercises and labs should be assumed to have predictable runnable outcomes in the lab.\n"
            "- Theory sections may contain explanatory commands or examples that do not need to match lab output exactly.\n"
            "- When video and written course text disagree, the written course text is the source of truth.\n"
        )

        self.communication_reply_notes = (
            "Reply rules:\n"
            "- Always thank the learner and acknowledge that their feedback was received and considered.\n"
            "- Use a professional, warm, patient, and clear tone.\n"
            "- Do not mention Jira, internal tracking, or internal workflows in the learner-facing reply.\n"
            "- If the report is vague or cannot be confirmed, ask for a screenshot, more detail, and the exact course section.\n"
            "- If the issue cannot be reproduced, say that politely instead of sounding dismissive or overconfident.\n"
            "- Avoid generic filler wording.\n"
            "- NEVER use the words 'guide text', 'guide_text', or 'course guide text' in the reply. Use natural alternatives like 'course material', 'exercise instructions', 'course page', or 'course content' instead.\n"
            "- Structure the reply in short, clearly separated paragraphs. Each paragraph should cover one idea. Use a blank line (two newlines) between paragraphs.\n"
        )
    # --------------------------
    # LLM helpers
    # --------------------------
    _LLM_THINKING_LOG_MAX_CHARS = 24000
    _LLM_APOS_PLACEHOLDER = "__LX_APOS__"

    def _log_llm_thinking_if_present(self, raw_text: str) -> None:
        """Log chain-of-thought / thinking blocks from Ollama before they are stripped."""
        if not raw_text or not raw_text.strip():
            return
        logger = logging.getLogger(__name__)
        parts: list[str] = []
        seen: set[str] = set()

        def _add(fragment: str) -> None:
            s = (fragment or "").strip()
            if len(s) < 2:
                return
            if s in seen:
                return
            seen.add(s)
            parts.append(s)

        for m in re.finditer(r"Thinking.*?done thinking\.", raw_text, flags=re.DOTALL):
            _add(m.group(0))
        for m in re.finditer(
            r"<redacted_thinking>.*?</redacted_thinking>", raw_text, flags=re.DOTALL
        ):
            _add(m.group(0))

        if not parts:
            return

        combined = "\n\n---\n\n".join(parts)
        if len(combined) > self._LLM_THINKING_LOG_MAX_CHARS:
            combined = (
                combined[: self._LLM_THINKING_LOG_MAX_CHARS]
                + "\n... [thinking log truncated]"
            )
        logger.debug(
            "LLM thinking [%s] (%d chars):\n%s",
            self.OLLAMA_MODEL,
            len(combined),
            combined,
        )

    # Ollama defaults to a small runtime context window (commonly 4096
    # tokens) regardless of the model's trained maximum. Long guide_text
    # excerpts embedded in prompts can silently exceed that: Ollama then
    # truncates the *input* to fit, which can leave zero/one token of room
    # for the model to actually generate a response (observed in the wild
    # as a bare "{" reply that fails JSON parsing). We size num_ctx per
    # request based on the prompt length so this can't happen.
    _OLLAMA_MIN_NUM_CTX = 4096
    _OLLAMA_OUTPUT_RESERVE_TOKENS = 2048
    _OLLAMA_CHARS_PER_TOKEN_ESTIMATE = 3

    def _estimate_num_ctx(self, prompt: str) -> int:
        """Pick a context window large enough to hold prompt + response.

        Token count isn't known without calling the tokenizer, so we
        conservatively estimate ~3 chars/token (real English text is closer
        to ~4, but a smaller divisor overestimates tokens which is the safe
        direction here) and reserve headroom for the model's own output.
        """
        estimated_prompt_tokens = max(1, len(prompt) // self._OLLAMA_CHARS_PER_TOKEN_ESTIMATE)
        needed = estimated_prompt_tokens + self._OLLAMA_OUTPUT_RESERVE_TOKENS
        num_ctx = self._OLLAMA_MIN_NUM_CTX
        while num_ctx < needed and num_ctx < self.OLLAMA_MAX_NUM_CTX:
            num_ctx *= 2
        return min(num_ctx, self.OLLAMA_MAX_NUM_CTX)

    def _ask_ollama(self, prompt: str, plain_text: bool = False) -> str:
        logger = logging.getLogger(__name__)
        # Models that emit chain-of-thought (Thinking... / ...done thinking.)
        # don't reliably include that text when constrained to JSON output,
        # so we skip the format=json constraint for them, then strip the
        # thinking block and extract the JSON from the raw text ourselves.
        use_format_json = (not plain_text) and (not self.OLLAMA_MODEL.startswith(("qwen", "deepseek", "gemma")))
        num_ctx = self._estimate_num_ctx(prompt)
        payload = {
            "model": self.OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,
            "options": {"num_ctx": num_ctx},
        }
        if use_format_json:
            payload["format"] = "json"

        try:
            resp = requests.post(
                f"{self.OLLAMA_HOST}/api/generate",
                json=payload,
                timeout=self.OLLAMA_TIMEOUT_SECONDS,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as e:
            logger.error("Could not reach Ollama API at %s: %s", self.OLLAMA_HOST, e)
            return ""
        except ValueError as e:
            logger.error("Ollama API returned non-JSON payload: %s", e)
            return ""

        response = data.get("response") or ""
        prompt_eval_count = data.get("prompt_eval_count")

        if prompt_eval_count is not None and prompt_eval_count >= num_ctx - 8:
            logger.warning(
                "LLM[ollama:%s] prompt used %s/%s context tokens (nearly/fully filled "
                "num_ctx); response may have been truncated or empty. "
                "Consider shortening the prompt or raising OLLAMA_MAX_NUM_CTX.",
                self.OLLAMA_MODEL, prompt_eval_count, num_ctx,
            )

        if not response.strip():
            logger.warning(
                "LLM[ollama:%s] returned empty response (prompt_len=%d chars, "
                "num_ctx=%d, prompt_eval_count=%s, done_reason=%s)",
                self.OLLAMA_MODEL, len(prompt), num_ctx, prompt_eval_count,
                data.get("done_reason"),
            )

        self._log_llm_thinking_if_present(response)

        if self.OLLAMA_MODEL.startswith(("qwen", "deepseek", "gpt-oss", "glm", "gemma")):
            response = re.sub(r'Thinking.*?done thinking\.', '', response, flags=re.DOTALL).strip()
            response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL).strip()
            response = re.sub(r'</think>', '', response).strip()
            response = re.sub(r'Thinking\.\.\.\s*', '', response)
            response = re.sub(r'\.\.\.done thinking\.\s*', '', response)
            response = re.sub(r'\.\.\.done thinking\.', '', response).strip()
            # For glm and similar models that output thinking without clear end markers,
            # extract JSON by finding the first '{' if response doesn't start with it
            if not response.strip().startswith('{') and '{' in response:
                json_start = response.find('{')
                response = response[json_start:]

        response = re.sub(r'```json\s*', '', response)
        response = re.sub(r'```\s*$', '', response)
        response = response.strip()
        logger.debug(
            "LLM[ollama:%s] response (num_ctx=%d, prompt_eval_count=%s): %s",
            self.OLLAMA_MODEL, num_ctx, prompt_eval_count, response[:1000],
        )
        return response

    def _ask_openai(self, prompt: str, plain_text: bool = False) -> str:
        """Call an OpenAI-compatible chat completions endpoint (e.g. granite-4.1-8b)."""
        logger = logging.getLogger(__name__)
        messages = [{"role": "user", "content": prompt}]
        payload: dict = {
            "model": self.OPENAI_MODEL,
            "messages": messages,
            "max_tokens": self.OPENAI_MAX_TOKENS,
        }
        if not plain_text:
            payload["response_format"] = {"type": "json_object"}

        headers = {
            "Authorization": f"Bearer {self.OPENAI_API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            resp = requests.post(
                f"{self.OPENAI_BASE_URL}/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.OPENAI_TIMEOUT_SECONDS,
                verify=False,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.RequestException as e:
            logger.error("Could not reach OpenAI-compatible API at %s: %s", self.OPENAI_BASE_URL, e)
            return ""
        except ValueError as e:
            logger.error("OpenAI-compatible API returned non-JSON payload: %s", e)
            return ""

        try:
            response = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as e:
            logger.error("Unexpected response structure from OpenAI-compatible API: %s | data=%s", e, data)
            return ""

        response = re.sub(r'```json\s*', '', response)
        response = re.sub(r'```\s*$', '', response)
        response = response.strip()

        usage = data.get("usage", {})
        logger.debug(
            "LLM[openai:%s] response (prompt_tokens=%s, completion_tokens=%s): %s",
            self.OPENAI_MODEL,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            response[:1000],
        )
        return response

    def ask_llm(self, prompt: str, plain_text: bool = False) -> str:
        """Ask the LLM a question and return the response using some LLM provider such as ollama.

        Args:
            prompt: The prompt to send to the LLM.
            plain_text: If True, skip the format=json constraint so the model can
                        return unstructured plain text. Use for prompts that explicitly
                        ask for prose rather than JSON output.
        """
        provider = self.LLM_PROVIDER
        logging.getLogger(__name__).debug(f"LLM request via provider={provider}")
        if provider == "openai":
            return self._ask_openai(prompt, plain_text=plain_text)
        return self._ask_ollama(prompt, plain_text=plain_text)

    @staticmethod
    def _sanitize_jira_title(value) -> str:
        """Return a clean lowercase Jira title with words separated by single spaces.

        Replaces underscores and dashes with spaces, strips any remaining
        non-alphanumeric characters, collapses whitespace runs, and lowercases.
        Accepts None and returns an empty string in that case.
        """
        if not value:
            return ""
        text = str(value)
        text = re.sub(r"[_\-]+", " ", text)
        text = re.sub(r"[^\w\s]", "", text)
        text = re.sub(r"\s{2,}", " ", text)
        return text.lower().strip()

    # ------------------------------------------------------------------
    # Reply-mode normalizers (#24)
    # ------------------------------------------------------------------

    _CONTENT_MODE_ALIASES: dict = {
        "confirm": "confirm_defect",
        "defect": "confirm_defect",
        "teaching": "teach",
        "confusion": "teach",
        "ask": "ask_more",
        "need_more": "ask_more",
        "needs_more": "ask_more",
        "lab": "lab_pending",
        "pending": "lab_pending",
        "explain": "explain_expected",
        "expected": "explain_expected",
    }
    _CONTENT_VALID_MODES: frozenset = frozenset(
        {"confirm_defect", "teach", "ask_more", "lab_pending", "explain_expected", "acknowledge_resolved"}
    )

    _ENV_MODE_ALIASES: dict = {
        "confirm": "confirm_defect",
        "defect": "confirm_defect",
        "teach": "explain_expected",
        "teaching": "explain_expected",
        "first_boot": "explain_expected",
        "firstboot": "explain_expected",
        "explain": "explain_expected",
        "expected": "explain_expected",
        "ask": "ask_more",
        "need_more": "ask_more",
        "lab": "lab_pending",
        "pending": "lab_pending",
    }
    _ENV_VALID_MODES: frozenset = frozenset(
        {"confirm_defect", "explain_expected", "ask_more", "lab_pending", "acknowledge_resolved"}
    )

    # SSH Lab Access Feedback — Internal Learner stuck on ROLE SSH instructions.
    # A ROLE URL alone is not enough; Internal Learners also file ordinary Guide/Lab Feedback.
    _SSH_LAB_ACCESS_TOKENS: tuple = (
        "rht_classroom.rsa",
        "download ssh key",
        "ssh private key",
        "ssh-add",
        "cloud-user@",
        ":22022",
        "-j cloud-user",
    )

    @staticmethod
    def is_role_platform_url(url: str) -> bool:
        """True when the Feedback URL is on ROLE (role.rhu.redhat.com)."""
        return "role.rhu.redhat.com" in (url or "").lower()

    @classmethod
    def looks_like_ssh_lab_access(cls, text: str) -> bool:
        """True when Feedback text matches SSH Lab Access instruction markers."""
        if not text:
            return False
        lowered = text.lower()
        return any(token in lowered for token in cls._SSH_LAB_ACCESS_TOKENS)

    @classmethod
    def apply_ssh_lab_access_classification(cls, classification: dict, feedback_text: str) -> dict:
        """Force mutually exclusive SSH Lab Access flags when the LLM or heuristic says so."""
        is_ssh = bool(classification.get("is_ssh_lab_access_ticket")) or cls.looks_like_ssh_lab_access(
            feedback_text
        )
        classification["is_ssh_lab_access_ticket"] = is_ssh
        if is_ssh:
            classification["is_content_issue_ticket"] = False
            classification["is_environment_issue_ticket"] = False
            classification["is_video_issue_ticket"] = False
            classification["needs_lab_verification"] = False
        return classification

    @classmethod
    def _normalize_ssh_lab_access_analysis(cls, parsed: dict) -> dict:
        """SSH Lab Access is never a Defect. Default reply_mode is teach."""
        raw = str(parsed.get("reply_mode", "")).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {"ask": "ask_more", "need_more": "ask_more", "teaching": "teach", "confusion": "teach"}
        mode = aliases.get(raw, raw)
        if mode not in {"teach", "ask_more", "acknowledge_resolved"}:
            mode = "teach"
        parsed["reply_mode"] = mode
        parsed["is_valid_issue"] = False
        parsed["jira_description"] = ""
        if "jira_title" in parsed:
            parsed["jira_title"] = cls._sanitize_jira_title(parsed.get("jira_title") or "")
        return parsed

    @classmethod
    def _normalize_content_analysis(cls, parsed: dict) -> dict:
        """Canonicalise reply_mode, clear jira_description on non-defect modes, sanitize jira_title."""
        raw = str(parsed.get("reply_mode", "")).strip().lower().replace("-", "_").replace(" ", "_")
        mode = cls._CONTENT_MODE_ALIASES.get(raw, raw)
        if mode not in cls._CONTENT_VALID_MODES:
            mode = "confirm_defect" if parsed.get("is_valid_issue") else "ask_more"
        parsed["reply_mode"] = mode
        if mode != "confirm_defect":
            parsed["jira_description"] = ""
        if "jira_title" in parsed:
            parsed["jira_title"] = cls._sanitize_jira_title(parsed.get("jira_title") or "")
        return parsed

    # Tokens that indicate the feedback is NOT a First Boot / cluster warm-up issue.
    _NON_FIRST_BOOT_TOKENS: tuple = (
        "git clone", "sslverify", "ssl verify", "certificate", "cert issue",
        "tls", "http.ssl", "github.com", "x509", "ssl certificate",
    )
    # Tokens that confirm the feedback IS about cluster startup.
    _STARTUP_TOKENS: tuple = (
        "verifying cluster", "cluster state", "cluster readiness",
        "authentication timed out", "lab start", "operators",
        "kube-apiserver",
    )

    @classmethod
    def _normalize_environment_analysis(cls, parsed: dict, user_issue: str = "") -> dict:
        """Canonicalise reply_mode and sanitize jira_title after environment analysis.

        Applies First Boot hard negatives: if the feedback contains git/TLS/SSL
        tokens but lacks genuine startup-shaped evidence, force reply_mode away
        from explain_expected so git/cert errors are never treated as cluster warm-up.
        """
        raw = str(parsed.get("reply_mode", "")).strip().lower().replace("-", "_").replace(" ", "_")
        mode = cls._ENV_MODE_ALIASES.get(raw, raw)
        if mode not in cls._ENV_VALID_MODES:
            mode = "confirm_defect" if parsed.get("is_valid_issue") else "ask_more"

        # First Boot hard-negative guard (#25)
        if mode == "explain_expected":
            feedback_l = (user_issue or "").lower()
            has_non_first_boot = any(t in feedback_l for t in cls._NON_FIRST_BOOT_TOKENS)
            has_startup = any(t in feedback_l for t in cls._STARTUP_TOKENS)
            if has_non_first_boot and not has_startup:
                mode = "ask_more"

        parsed["reply_mode"] = mode
        if mode != "confirm_defect":
            parsed["jira_description"] = ""
        if "jira_title" in parsed:
            parsed["jira_title"] = cls._sanitize_jira_title(parsed.get("jira_title") or "")
        return parsed

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

    @staticmethod
    def _clean_llm_text(text: str) -> str:
        """Collapse line-wrapping newlines into spaces, preserve paragraph breaks."""
        if not text:
            return text
        text = text.replace('\r\n', '\n')
        # Protect intentional paragraph breaks (2+ consecutive newlines)
        text = re.sub(r'\n{2,}', '\x00PARA\x00', text)
        # Collapse remaining single newlines (wrapping artifacts) into spaces
        text = text.replace('\n', ' ')
        # Restore paragraph breaks
        text = text.replace('\x00PARA\x00', '\n\n')
        # Clean up runs of spaces
        text = re.sub(r' {2,}', ' ', text)
        return text.strip()

    @staticmethod
    def _format_reply_paragraphs(text: str) -> str:
        """Split a flat reply into paragraphs separated by blank lines.

        If the LLM already produced paragraph breaks (\\n\\n) those are kept.
        Otherwise the text is heuristically split: a sentence that ends a
        logical block (acknowledgement, explanation, action, next-steps) gets
        a blank line after it.
        """
        if not text:
            return text

        if "\n\n" in text:
            paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
            return "\n\n".join(paragraphs)

        sentences: list[str] = re.split(r'(?<=[.!?])\s+', text.strip())
        if len(sentences) <= 2:
            return text

        paragraphs: list[str] = []
        current: list[str] = []
        for sent in sentences:
            current.append(sent)
            if len(current) >= 2:
                paragraphs.append(" ".join(current))
                current = []
        if current:
            if paragraphs:
                paragraphs[-1] += " " + " ".join(current)
            else:
                paragraphs.append(" ".join(current))

        return "\n\n".join(paragraphs)

    @staticmethod
    def _scrub_guide_text_wording(text: str) -> str:
        """Replace robotic 'guide text' variants with natural alternatives."""
        text = re.sub(r'\bguide[_ ]text\b', 'course material', text, flags=re.IGNORECASE)
        text = re.sub(r'\bcourse guide text\b', 'course material', text, flags=re.IGNORECASE)
        return text

    @staticmethod
    def _json_output_rules() -> str:
        """Shared prompt instructions for strict JSON output."""
        return (
            "JSON formatting rules:\n"
            "- Return valid JSON only, with no extra text before or after the JSON object.\n"
            "- Use double quotes for every JSON key and every string value.\n"
            "- Do not use single quotes to delimit JSON keys or JSON string values.\n"
            "- If you need quote characters inside a string value, prefer single quotes inside the value so the outer JSON stays valid.\n"
            "- Use JSON booleans true/false and null, not Python True/False/None.\n"
            "- Do not return Python dict syntax.\n"
            "- Do not wrap the JSON in markdown fences.\n"
            "- Never use asterisks for bold formatting (e.g. **word**). Use plain text only.\n"
        )

    @classmethod
    def _normalize_llm_parsed_value(cls, value):
        """Normalize relaxed LLM parser output into Python-native values."""
        if isinstance(value, str):
            value = value.replace(cls._LLM_APOS_PLACEHOLDER, "'")
            lowered = value.strip().lower()
            if lowered == "true":
                return True
            if lowered == "false":
                return False
            if lowered in ("null", "none"):
                return None
            return value
        if isinstance(value, list):
            return [cls._normalize_llm_parsed_value(item) for item in value]
        if isinstance(value, dict):
            normalized = {}
            for key, item in value.items():
                norm_key = cls._normalize_llm_parsed_value(key) if isinstance(key, str) else key
                normalized[norm_key] = cls._normalize_llm_parsed_value(item)
            return normalized
        return value

    @staticmethod
    def _strip_email_quote_chain(text: str) -> str:
        """Strip the quoted email reply chain, keeping only the new content."""
        lines = text.split("\n")
        kept: list[str] = []
        for line in lines:
            stripped = line.strip()
            # Stop at the start of a quoted reply block
            if stripped.startswith(">"):
                break
            # Stop at "On <date> ... wrote:" patterns
            if re.match(r"^On .+ wrote:\s*$", stripped):
                break
            # Stop at common email thread markers
            if stripped.startswith("------") and len(stripped) > 10:
                break
            kept.append(line)
        # Remove trailing email signature boilerplate (generic patterns only)
        while kept and re.match(
            r"^\s*(:host|Thanks\s*&\s*Regards|Sr\.?\s*Consultant|"
            r"Email:\s|Mobile:\s|<https?://|Best Regards|"
            r"Red Hat Learner|reply from:|Regards,?\s*$|"
            r"\+\d[\d\s\-]{6,})\s*",
            kept[-1], re.IGNORECASE
        ):
            kept.pop()
        # Also strip trailing lines that look like a name-only sign-off
        # (single short line with only capitalized words, no punctuation)
        while kept and re.match(r"^\s*([A-Z][a-z]+\s*){1,4}\s*$", kept[-1]):
            kept.pop()
        result = "\n".join(kept).strip()
        # Remove shadow-root CSS artifacts
        result = re.sub(r":host\s+img\s*\{[^}]*\}", "", result).strip()
        return result

    def _filter_and_clean_updates(self, raw_updates: list[dict]) -> list[dict]:
        """Clean up each update's text and tag whether it came from the
        customer or from our own team (kept as context, e.g. a prior
        investigation reply, rather than dropped outright)."""
        agent_name = self.SIGNATURE_NAME
        cleaned: list[dict] = []
        seen_texts: set[str] = set()
        for update in raw_updates:
            author = update.get("author", "")
            is_agent = bool(author and agent_name and agent_name.lower() in author.lower())
            text = self._strip_email_quote_chain(update.get("text", ""))
            if not text or len(text) < 5:
                continue
            # De-duplicate near-identical messages (e.g. the same message
            # appearing as both "Additional comments" and "Email received")
            fingerprint = re.sub(r"\s+", " ", text[:200]).strip().lower()
            if fingerprint in seen_texts:
                continue
            seen_texts.add(fingerprint)
            cleaned.append({
                "timestamp": update.get("timestamp", ""),
                "author": author,
                "role": "agent" if is_agent else "customer",
                "text": text,
            })
        return cleaned

    def _summarize_customer_updates(self, snow_info: dict) -> str:
        """Use the LLM to distill customer follow-up updates into a concise,
        PII-free summary of any new facts, issues, or clarifications the
        customer provided beyond the original ticket description."""
        updates = snow_info.get("customer_updates")
        if not updates:
            return ""
        # Build a condensed input for the LLM (text only, no raw PII dump).
        # Each entry is labeled by role so the LLM can use our own prior
        # replies as context without mistaking them for new customer input.
        update_block = ""
        for u in updates:
            ts = u.get("timestamp", "")
            role = u.get("role", "customer")
            role_label = "OUR TEAM (previous reply)" if role == "agent" else "CUSTOMER"
            text = u.get("text", "").strip()
            if len(text) > 1500:
                text = text[:1500] + " …"
            update_block += f"[{ts}] ({role_label})\n{text}\n\n"

        prompt = f"""You are summarizing the follow-up activity on a Red Hat Training support ticket.

Original ticket description:
{snow_info.get("Description", "")}

Follow-up journal entries (newest first), each labeled as either CUSTOMER or OUR TEAM (a previous reply we already sent):
{update_block}

Instructions:
- First, if there are any OUR TEAM entries, summarize in 1-3 sentences the concrete findings or conclusions from our own prior investigation/reply (e.g. what was checked, what was found, what explanation or fix was already given). Preserve specific technical details (names, commands, paths, image/version names) rather than vague statements like "already addressed".
- Then, extract any NEW technical facts, clarifications, or additional issues the CUSTOMER raised beyond the original description AND beyond what our own prior reply already covered.
- Keep CUSTOMER statements that the problem is gone, resolved, was on their side (browser, cache, local setup), or was not a course issue — even if they also thank us. Those are Resolution Follow-up facts, not empty thank-yous.
- If the latest CUSTOMER entry both says the original issue is fixed AND raises a new problem, include both.
- Never present our own prior reply as if it were new information from the customer — clearly attribute investigation findings to "our team" / "we".
- Omit greetings, thank-yous, signatures, email addresses, phone numbers, and any personal information.
- Omit anything that simply repeats the original ticket description.
- If a follow-up mentions a different course page URL or section than the original, note that explicitly.
- If the customer provided a screenshot reference or image, mention that briefly.
- Only return exactly "No additional information." if there are NO OUR TEAM entries with findings to preserve AND the customer raised nothing new.
- Be concise: 3-6 sentences maximum.
- Return plain text only, no JSON.
- Never use asterisks for bold formatting (e.g. **word**). Use plain text only.
"""
        response = self.ask_llm(prompt, plain_text=True)
        _log = logging.getLogger(__name__)
        _log.debug("Customer updates sent to LLM:\n%s", update_block)
        _log.debug("LLM raw response for customer updates summary:\n%s", response)
        return self._clean_llm_text(response)

    def _build_full_description(self, snow_info: dict) -> str:
        """Combine the original description with a summary of ticket follow-up
        activity (customer replies and/or our own prior investigation)."""
        desc = snow_info.get("Description", "")
        summary = snow_info.get("customer_updates_summary", "")
        if not summary or summary == "No additional information.":
            return desc
        return f"{desc}\n\n--- Follow-up activity (customer replies and/or our prior investigation) ---\n{summary}"

    @staticmethod
    def _infer_course_family(course: str) -> str:
        match = re.match(r"([A-Za-z]+)", str(course or "").strip())
        return match.group(1).lower() if match else ""

    def _infer_section_kind(self, snow_info: dict | None) -> str:
        if not snow_info:
            return "unknown"

        title = str(snow_info.get("Title", "")).lower()
        section = str(snow_info.get("Section", "")).strip()

        if "quiz" in title:
            return "quiz"
        if "guided exercise" in title:
            return "guided exercise"
        if "comprehensive review" in title:
            return "comprehensive review"
        if title.startswith("lab") or "lab:" in title:
            return "lab"
        if section.isdigit():
            return "guided exercise" if int(section) % 2 == 0 else "theory or lab"
        return "unknown"

    def _format_ticket_context(self, snow_info: dict | None) -> str:
        if not snow_info:
            return "Ticket metadata:\n- Not available\n"

        course = snow_info.get("Course", "")
        version = snow_info.get("Version", "")
        chapter = snow_info.get("Chapter", "")
        section = snow_info.get("Section", "")
        title = snow_info.get("Title", "")
        section_kind = self._infer_section_kind(snow_info)

        context = (
            "Ticket metadata:\n"
            f"- Course: {course}\n"
            f"- Version: {version}\n"
            f"- Chapter: {chapter}\n"
            f"- Section: {section}\n"
            f"- Title: {title}\n"
            f"- Inferred section kind: {section_kind}\n"
        )

        summary = snow_info.get("customer_updates_summary", "")
        if summary and summary != "No additional information.":
            context += (
                f"\nFollow-up activity (customer replies and/or our prior investigation):\n"
                f"{summary}\n"
            )

        return context

    def _course_family_operational_notes(self, snow_info: dict | None) -> str:
        course = str((snow_info or {}).get("Course", "")).upper()
        family = self._infer_course_family(course)

        if family == "do":
            return (
                "Course-family guidance:\n"
                "- doXXX courses are OpenShift courses.\n"
                "- First boot commonly takes 30-40 minutes.\n"
                "- Long startup on first use is often expected behavior, especially in early setup sections.\n"
                "- ssh lab@utility and running ./wait.sh is valid guidance for these courses.\n"
            )
        if family == "rh":
            return (
                "Course-family guidance:\n"
                "- rhXXX courses are usually RHEL-focused courses.\n"
                "- Lab startup is usually under 10 minutes.\n"
            )
        if family == "au":
            return (
                "Course-family guidance:\n"
                "- auXXX courses are usually Ansible Automation Platform courses.\n"
                "- Lab startup is usually under 20 minutes.\n"
            )
        if family == "cl":
            return (
                "Course-family guidance:\n"
                "- clXXX courses are usually OpenStack courses.\n"
                "- Lab startup is usually under 20 minutes.\n"
            )
        if family == "ad":
            return (
                "Course-family guidance:\n"
                "- adXXX courses are usually advanced developer courses.\n"
                "- Lab startup is usually under 20 minutes.\n"
            )
        if family == "ai":
            return (
                "Course-family guidance:\n"
                "- aiXXX courses are usually artificial intelligence courses.\n"
                "- First boot commonly takes up to 40 minutes.\n"
                "- Long startup on first use is often expected behavior, similar to doXXX OpenShift courses.\n"
            )
        return (
            "Course-family guidance:\n"
            f"- No special family timing rule is encoded for {course or 'this course'}.\n"
        )

    def _build_operational_context(self, issue_type: str, snow_info: dict | None = None, video_available: bool | None = None) -> str:
        issue_specific_notes = []

        if issue_type == "classification":
            issue_specific_notes = [
                "Classification rules:",
                "- Treat quiz problems, missing instructions, missing details hidden only in Show Solution, and grading-script logic mismatches as content issues first.",
                "- SSH Lab Access Feedback is a separate type: Internal Learner stuck on ROLE SSH-from-laptop (rht_classroom.rsa, DOWNLOAD SSH KEY, cloud-user jump host). Never classify that as environment or content. A ROLE URL alone is not SSH Lab Access — Internal Learners also report ordinary Guide, Lab, and video Feedback.",
                "- Treat lab startup, lab finish, lab grade, building, starting, stopping, VM access *inside the lab*, and lab-environment behavior as environment issues. SSH from a laptop via jump host is not this type.",
                "- Treat account, exam, subscription, refund, and unrelated platform requests as manually managed issues.",
                "- If the learner ran commands from a theory section and expected lab-validated output, learner confusion is often more likely than an environment defect.",
                "- A doXXX or aiXXX course taking a long time to start on first use may be expected first-boot behavior and may not need lab verification if the report matches that pattern.",
                "- For doXXX and aiXXX courses: 'Authentication timed out' during 'Verifying cluster state', or 'FAIL Verifying cluster state' after a few minutes, are classic first-boot symptoms. The student likely did not wait 30-40 minutes. This is an environment issue but does NOT need lab verification.",
                "- Reports that can be validated by reading the course text alone do not need lab verification.",
                "- CRITICAL: If the learner claims that a file, directory, path, or script referenced in the guide does not exist, has a different name, or has different contents inside the lab VM, lab verification IS needed. The guide text alone cannot confirm or deny what is actually on the lab filesystem. The same applies to solution files prepared by the lab start command.",
            ]
        elif issue_type == "content":
            issue_specific_notes = [
                "Content-analysis rules:",
                "- Missing information in the lab specification that only appears in Show Solution is still a valid content defect.",
                "- If the learner is complaining about a quiz button or Show Solution behavior, analyze it as a content/platform issue rather than a lab-output issue.",
                "- If the complaint depends on command output, determine whether the learner is in a guided exercise or lab before assuming the guide is wrong.",
                "- If the complaint is about commands shown only in theory content, do not assume those commands must match a runnable lab outcome.",
                "- CRITICAL: If the learner claims that a file, directory, or path referenced in the guide does not exist or has a different name on the lab VM, you CANNOT confirm or deny this from the guide text alone. The guide may reference files that the lab start command creates dynamically. In your analysis, clearly state that this claim requires lab verification and that you cannot confirm the suggested correction without checking the actual lab filesystem.",
                "- When a claim involves lab filesystem state, set is_valid_issue to true but make the analysis and suggested_correction explicitly say that lab verification is needed to confirm.",
            ]
        elif issue_type == "environment":
            issue_specific_notes = [
                "Environment-analysis rules:",
                "- Distinguish expected warm-up from real failure using course-family timing norms.",
                "- A specific actionable error from a grading script often means learner error rather than platform failure.",
                "- A lab stuck in building, starting, or stopping for too long is more likely a platform or provisioning problem.",
                "- Workstation is the normal learner entrypoint. Bastion, classroom, and registry are not normal learner targets.",
                "- CRITICAL for doXXX and aiXXX courses: 'Authentication timed out' during 'Verifying cluster state' is a classic first-boot symptom, NOT a real authentication failure. The cluster is still initializing. The lab start script timeout expires before the cluster finishes first-time setup.",
                "- If a doXXX or aiXXX lab start fails within 10-15 minutes with cluster verification or authentication errors, this is almost always because the student did not wait the full 30-40 minutes for first boot.",
                "- Even if the student says 'tried N times' or 'tried for days', they may have been restarting too quickly each time without waiting long enough. Each restart resets the boot process.",
            ]
        elif issue_type == "video":
            issue_specific_notes = [
                "Video-analysis rules:",
                "- If the video toggle/button is missing, videos for that course version are usually not ready yet.",
                "- If videos are not ready yet, the learner can be advised to use the previous version if video is important.",
                "- If the video says something different from the course text, the written course text remains the source of truth and the video issue is a mismatch.",
            ]
            if video_available is not None:
                issue_specific_notes.append(
                    f"- Video player available on page: {'yes' if video_available else 'no'}."
                )
        elif issue_type == "reply":
            issue_specific_notes = [
                "Reply rules:",
                "- Keep the response concise but informative.",
                "- Do not mention Jira or internal tracking to the learner.",
                "- If the report is vague, ask for a screenshot, more details, and the exact course section.",
                "- CRITICAL: If the analysis mentions that lab verification is needed or the issue involves files, paths, or scripts on the lab VM that have not been checked yet, do NOT confirm the issue or suggest a specific fix to the learner. Instead, tell them that we are looking into it and will get back to them once we have verified it in the lab environment.",
                "- Never present an unverified claim about lab filesystem contents as a confirmed finding.",
            ]

        return (
            f"{self._format_ticket_context(snow_info)}\n"
            f"{self.platform_operational_notes}\n"
            f"{self._course_family_operational_notes(snow_info)}\n"
            + "\n".join(issue_specific_notes)
        ).strip()

    @staticmethod
    def _extract_first_json_object(text: str) -> str:
        """Extract the first top-level JSON object from arbitrary text."""
        if not text:
            return ""
        start = text.find("{")
        if start == -1:
            return text.strip()

        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue

            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start:idx + 1].strip()

        return text[start:].strip()

    def _parse_llm_json(self, response: str, context: str = "LLM") -> dict:
        """
        Parse LLM JSON output robustly.

        Handles markdown fences, extra pre/post text, control characters
        inside string values (literal newlines from line-wrapped model
        output), invalid escape sequences, and trailing commas.
        """
        raw = response or ""
        for old, new in [("\u201c", '"'), ("\u201d", '"'),
                         ("\u2018", "'"), ("\u2019", "'")]:
            raw = raw.replace(old, new)

        cleaned = re.sub(r"```json\s*", "", raw)
        cleaned = re.sub(r"```\s*$", "", cleaned).strip()

        if "{" not in cleaned:
            if not cleaned:
                logging.getLogger(__name__).error(
                    f"Empty response from {context} — LLM returned no output"
                )
            else:
                logging.getLogger(__name__).error(
                    f"No JSON object found in {context} response "
                    f"({len(cleaned)} chars): {response[:500]}"
                )
            return {}

        lenient = json.JSONDecoder(strict=False)
        start = cleaned.find("{")

        # Strategy 1: lenient decoder accepts literal control chars in strings
        try:
            parsed, _ = lenient.raw_decode(cleaned, idx=start)
            parsed = self._normalize_llm_parsed_value(parsed)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        # Strategy 1b: Python-dict-style output (single quotes, Python-ish booleans)
        # Some models return {'key': 'value', 'flag': true} instead of JSON.
        try:
            py_text = self._extract_first_json_object(cleaned)
            if py_text:
                # Python string literals cannot contain bare newlines, so normalize
                # line-wrapped model output before ast.literal_eval().
                py_text = re.sub(r"[\x00-\x1F]+", " ", py_text)
                py_text = re.sub(r",(\s*[}\]])", r"\1", py_text)
                py_text = re.sub(r"(:\s*)true(\s*[,}\]])", r"\1True\2", py_text)
                py_text = re.sub(r"(:\s*)false(\s*[,}\]])", r"\1False\2", py_text)
                py_text = re.sub(r"(:\s*)null(\s*[,}\]])", r"\1None\2", py_text)
                py_text = re.sub(
                    r"(?<=\w)'(?=\w)",
                    self._LLM_APOS_PLACEHOLDER,
                    py_text,
                )
                py_obj = ast.literal_eval(py_text)
                py_obj = self._normalize_llm_parsed_value(py_obj)
                if isinstance(py_obj, dict):
                    return py_obj
        except (ValueError, SyntaxError):
            pass

        # Strategy 2: extract first balanced {...}, then lenient parse
        extracted = self._extract_first_json_object(cleaned)
        if extracted:
            try:
                parsed = lenient.decode(extracted)
                parsed = self._normalize_llm_parsed_value(parsed)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        # Strategy 3: replace control chars with space, fix orphaned
        # backslashes (e.g. line wrap splitting \n into \ <space> n)
        sanitized = re.sub(r"[\x00-\x1F]", " ", extracted or cleaned)
        sanitized = re.sub(r'\\(?!["\\/bfnrtu])', "", sanitized)
        sanitized = re.sub(r",(\s*[}\]])", r"\1", sanitized)
        try:
            parsed = lenient.decode(sanitized)
            parsed = self._normalize_llm_parsed_value(parsed)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        # Strategy 4: collapse all whitespace + same fixes
        collapsed = re.sub(r"\s+", " ", extracted or cleaned)
        collapsed = re.sub(r'\\(?!["\\/bfnrtu])', "", collapsed)
        collapsed = re.sub(r",(\s*[}\]])", r"\1", collapsed)
        try:
            parsed = lenient.decode(collapsed)
            parsed = self._normalize_llm_parsed_value(parsed)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as e:
            logging.getLogger(__name__).debug(
                f"Final JSON parse attempt failed ({context}): {e}"
            )

        logging.getLogger(__name__).error(
            f"Could not parse JSON from {context}. Full response: {response}"
        )
        return {}

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
        self.lab_mgr.dismiss_pendo_overlay()
        self.lab_mgr.toggle_video_player(state=False)
        self.lab_mgr.dismiss_active_alerts()
        self.lab_mgr.dismiss_pendo_overlay()
        self.lab_mgr.select_lab_environment_tab("course")

        self.lab_mgr.click_on_show_solution_buttons()

        container = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//*[@class='course__content-wrapper']"))
        )
        # Use innerText via JS — more reliable than .text for dynamically expanded content
        parts = [self.driver.execute_script("return arguments[0].innerText", container) or ""]

        # Solution panel bodies may live outside course__content-wrapper in the DOM.
        # Collect each expanded panel's text separately and append it if not already present.
        for panel_body in self.driver.find_elements(By.CSS_SELECTOR, ".panel-collapse.in .panel-body"):
            panel_text = self.driver.execute_script("return arguments[0].innerText", panel_body) or ""
            if panel_text and panel_text.strip() not in parts[0]:
                parts.append(panel_text)

        return "\n\n".join(filter(None, (p.strip() for p in parts)))

    # --------------------------
    # ServiceNow helpers (delegated to ServiceNowSeleniumHandler)
    # --------------------------
    def login_snow(self):
        """Login to ServiceNow using the ServiceNowSeleniumHandler."""
        self.snow_handler.login(use_session=True)

    def switch_to_iframe(self):
        """Switch to SNOW content iframe through macroponent shadow DOM."""
        self.snow_handler.switch_to_iframe()

    # ------------------------------------------------------------------
    # Issue Section (ADR-0002)
    # ------------------------------------------------------------------

    _SECTION_SLUG_RE = re.compile(r"ch(\d{1,2})s(\d{1,2})", re.IGNORECASE)
    _CHAPTER_SECTION_WORDS_RE = re.compile(
        r"chapter\s+(\d{1,2})\s*,?\s*section\s+(\d{1,2})", re.IGNORECASE
    )
    _EXERCISE_NM_RE = re.compile(
        r"(?:演習|exercise|guided exercise|\bge\b|\blab\b)\s*(\d{1,2})\s*[.\s]\s*(\d{1,2})",
        re.IGNORECASE,
    )
    _BARE_NM_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\b")

    @classmethod
    def text_names_a_section(cls, text: str, course_version: str = "") -> bool:
        """Heuristic: does this Learner text look like it names a Section?"""
        raw = text or ""
        if cls._SECTION_SLUG_RE.search(raw):
            return True
        if cls._CHAPTER_SECTION_WORDS_RE.search(raw):
            return True
        if cls._EXERCISE_NM_RE.search(raw):
            return True
        version = (course_version or "").strip()
        for match in cls._BARE_NM_RE.finditer(raw):
            token = f"{match.group(1)}.{match.group(2)}"
            if version and token == version:
                continue
            return True
        return False

    @classmethod
    def parse_issue_section(cls, parsed: dict | None) -> tuple[str, str] | None:
        """Return zero-padded (chapter, section) or None if unchanged/invalid."""
        if not parsed:
            return None
        if parsed.get("unchanged") in (True, "true", "True"):
            return None
        slug = str(parsed.get("section_slug") or parsed.get("slug") or "").strip()
        slug_match = cls._SECTION_SLUG_RE.search(slug)
        if slug_match:
            return cls._pad_chapter_section(slug_match.group(1), slug_match.group(2))
        chapter = str(parsed.get("chapter") or "").strip()
        section = str(parsed.get("section") or "").strip()
        digits = re.compile(r"^\d{1,2}$")
        if digits.match(chapter) and digits.match(section):
            return cls._pad_chapter_section(chapter, section)
        return None

    @staticmethod
    def _pad_chapter_section(chapter: str, section: str) -> tuple[str, str]:
        return (chapter.zfill(2), section.zfill(2))

    @staticmethod
    def rewrite_page_url(url: str, chapter: str, section: str) -> str:
        """Replace /pages/<slug> keeping host, Course ID, and Platform."""
        slug = f"ch{chapter}s{section}"
        if re.search(r"/pages/[^/?#]+", url or ""):
            return re.sub(r"/pages/[^/?#]+", f"/pages/{slug}", url, count=1)
        return url

    @staticmethod
    def page_slug_from_url(url: str) -> str:
        match = re.search(r"/pages/([^/?#]+)", url or "")
        return match.group(1) if match else ""

    @classmethod
    def chapter_section_from_url(cls, url: str) -> tuple[str, str]:
        slug = cls.page_slug_from_url(url)
        match = re.match(r"ch(\d{2})s(\d{2})$", slug or "", re.IGNORECASE)
        if match:
            return match.group(1), match.group(2)
        return "", ""

    @staticmethod
    def issue_section_inference_text(snow_info: dict) -> str:
        """Description plus Learner Follow-ups; never the Feedback Title."""
        parts = [str(snow_info.get("Description") or "").strip()]
        for update in snow_info.get("customer_updates") or []:
            role = (update.get("role") or "customer").lower()
            if role in ("agent", "response"):
                continue
            text = (update.get("text") or "").strip()
            if text:
                parts.append(text)
        return "\n\n".join(p for p in parts if p)

    @classmethod
    def apply_issue_section(cls, snow_info: dict, chapter: str, section: str) -> dict:
        """Point Chapter, Section, and investigation URL at the Issue Section."""
        current_url = snow_info.get("URL") or ""
        if "CaptureURL" not in snow_info:
            snow_info["CaptureURL"] = current_url
        snow_info["Chapter"] = chapter
        snow_info["Section"] = section
        snow_info["URL"] = cls.rewrite_page_url(current_url, chapter, section)
        return snow_info

    @classmethod
    def restore_capture_location(cls, snow_info: dict) -> dict:
        """Revert investigation URL/Chapter/Section to the Capture URL page."""
        capture = snow_info.get("CaptureURL") or ""
        if not capture:
            return snow_info
        snow_info["URL"] = capture
        chapter, section = cls.chapter_section_from_url(capture)
        snow_info["Chapter"] = chapter
        snow_info["Section"] = section
        return snow_info

    def _resolve_issue_section(self, snow_info: dict) -> dict:
        """If Learner text names a Section, ask the LLM and update snow_info."""
        text = self.issue_section_inference_text(snow_info)
        version = str(snow_info.get("Version") or "")
        if not self.text_names_a_section(text, course_version=version):
            return snow_info
        course_id = (
            f"{str(snow_info.get('Course') or '').lower()}-{version}".strip("-")
        )
        capture_url = snow_info.get("URL") or ""
        self.logger("Inferring Issue Section from Feedback text")
        prompt = f"""You infer which course Section a Learner is complaining about.

Course ID (frozen — do not change it): {course_id}
Capture URL (page where they opened the Feedback form): {capture_url}

Learner text (original description, then any Learner Follow-ups).
A Follow-up that names a Section is the Issue Section:
{text}

Rules:
- Return the Issue Section as chapter and section numbers (chapter 8 section 8 is 8 and 8).
- "N.M", "chapter N section M", "chNNsMM", "Exercise N.M", "演習N.M" all mean chapter N section M.
- If a Follow-up names a Section, use that, not the original description.
- If the text does not clearly name one Section, or names two with no primary, return unchanged.
- Do not invent a different course.

JSON only.
Example when you can tell: {{"chapter": "8", "section": "8"}}
Example when you cannot: {{"unchanged": true}}
{self._json_output_rules()}
"""
        response = self.ask_llm(prompt)
        logging.getLogger(__name__).info("Issue Section inference: %s", response)
        parsed = self._parse_llm_json(response, context="Issue Section inference")
        pair = self.parse_issue_section(parsed)
        if not pair:
            return snow_info
        chapter, section = pair
        current_slug = self.page_slug_from_url(capture_url)
        new_slug = f"ch{chapter}s{section}"
        if current_slug == new_slug:
            return snow_info
        self.logger(f"Issue Section {new_slug} (Capture URL page was {current_slug or 'unknown'})")
        return self.apply_issue_section(snow_info, chapter, section)

    def get_snow_info(self, snow_id: str) -> dict:
        """
        Return key Feedback ticket fields.

        Uses the REST API fast-path when ``self._snow_api`` is set (ADR-0001);
        falls back to Selenium DOM scraping otherwise.  Both paths return a dict
        with the same keys: ``snow_id``, ``full_name``, ``Description``,
        ``Course``, ``Version``, ``URL``, ``Chapter``, ``Section``, ``Title``,
        ``RHNID``, ``customer_updates``, ``customer_updates_summary``.
        """
        self.logger(f"Getting SNOW info for ticket {snow_id}")

        if self._snow_api is not None:
            info = self._get_snow_info_via_api(snow_id)
        else:
            info = self._get_snow_info_via_dom(snow_id)

        if info["customer_updates"]:
            self.logger("Summarizing customer follow-up messages")
            info["customer_updates_summary"] = self._summarize_customer_updates(info)
            logging.getLogger(__name__).info(
                f"Customer updates summary: {info['customer_updates_summary']}"
            )

        info = self._resolve_issue_section(info)

        logging.getLogger(__name__).info(f"ServiceNow ticket info: {json.dumps({k: v for k, v in info.items() if k != 'customer_updates'}, indent=2)}")
        return info

    # ------------------------------------------------------------------
    # get_snow_info() path implementations (API and DOM)
    # ------------------------------------------------------------------

    def _parse_description_fields(self, description: str) -> dict:
        """Extract structured fields from the Feedback ticket description text."""
        issue = re.search(r"Description:\s*(.*?)\s*Copyright", description, re.DOTALL).group(1).strip()
        course = re.findall("Course:.*", description)[0].split(":  ")[1].upper().split(" ")[0].strip()
        version = re.findall("Version:.*", description)[0].split(":  ")[1].strip()
        url = re.findall("URL:.*", description)[0].split(":  ")[1].strip()
        # Keep the original host. ROLE URLs must stay on role.rhu.redhat.com for
        # SSH Lab Access Feedback; ordinary ROLE Guide/Lab Feedback still navigates
        # via the ROL base URL in _navigate_to_course_page, not this field.
        # ROL sometimes embeds course slugs like do180f-4.18; canonical path uses do180-4.18
        url = re.sub(r"([A-Za-z]{2}\d{3})f(?=-)", r"\1", url)

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
        return {
            "issue": issue,
            "course": course,
            "version": version,
            "url": url,
            "chapter": chapter,
            "section": section,
            "title": title,
            "rhnid": rhnid,
        }

    def _build_snow_info(
        self,
        snow_id: str,
        full_name: str,
        fields: dict,
        customer_updates: list,
    ) -> dict:
        """Assemble the canonical snow_info dict shared by both paths."""
        return {
            "snow_id": snow_id,
            "full_name": full_name,
            "Description": fields["issue"],
            "Course": fields["course"],
            "Version": fields["version"],
            "URL": fields["url"],
            "Chapter": fields["chapter"],
            "Section": fields["section"],
            "Title": fields["title"],
            "RHNID": fields["rhnid"],
            "customer_updates": customer_updates,
            "customer_updates_summary": "",
        }

    def _get_snow_info_via_api(self, snow_id: str) -> dict:
        """Fetch ticket info using the REST API fast-path (ADR-0001)."""
        record = self._snow_api.get_ticket(snow_id)
        fields = self._parse_description_fields(record.get("description", ""))
        raw_updates = self._snow_api.get_journal_entries(record.get("sys_id", ""))
        customer_updates = self._filter_and_clean_updates(raw_updates)
        return self._build_snow_info(snow_id, record.get("contact_source", ""), fields, customer_updates)

    def _get_snow_info_via_dom(self, snow_id: str) -> dict:
        """Fetch ticket info using the existing Selenium DOM-scraping path."""
        self.snow_handler.navigate_to_ticket(snow_id)

        description = self.driver.find_element(
            By.XPATH, '//*[@id="x_redha_rht_task.description"]'
        ).get_attribute("value")
        full_name = self.driver.find_element(
            By.XPATH, '//*[@id="x_redha_rht_task.contact_source"]'
        ).get_attribute("value")

        fields = self._parse_description_fields(description)
        raw_updates = self.snow_handler.get_customer_updates()
        customer_updates = self._filter_and_clean_updates(raw_updates)

        self.driver.refresh()
        return self._build_snow_info(snow_id, full_name, fields, customer_updates)

    def get_ticket_ids_from_queue(self) -> list:
        """Get list of ticket IDs from the current queue view."""
        return self.snow_handler.get_ticket_ids_from_queue()

    # --------------------------
    # Ticket understanding
    # --------------------------
    def classify_ticket_llm(self, description: str, snow_info: dict | None = None) -> dict:
        self.logger("Classifying ticket using LLM")
        json_example = '{"student_feedback": "hay un error en el laboratorio", "language": "es", "summary": "The student is reporting an error in the lab", "is_content_issue_ticket": true, "is_environment_issue_ticket": false, "is_video_issue_ticket": false, "is_ssh_lab_access_ticket": false, "needs_lab_verification": true}'
        role_note = ""
        if snow_info and self.is_role_platform_url(str(snow_info.get("URL") or "")):
            role_note = (
                "Supporting context: the Feedback URL is on ROLE (Internal Learner). "
                "That does not by itself mean SSH Lab Access Feedback — classify from the text. "
                "Ordinary Guide, Lab, and video Feedback from ROLE still use those types.\n"
            )
        prompt = f"""
You are an expert classifier of Red Hat Training tickets.
{self._build_operational_context("classification", snow_info)}
{role_note}
Classify the user's feedback regarding a Red Hat Training course. Types are mutually exclusive:
- content_issue_ticket: a mismatch or inconsistency between the user's complaint and the text in the guide, a typo, a missing step, a missing command, a quiz problem, missing lab specification details, or a grading-script logic mismatch.
- environment_issue_ticket: if the feedback includes words such as 'lab start', 'lab finish', ' lab grade','SUCCESS', 'FAIL', 'stuck', or 'lab is taking to long to start', or the learner is reporting machine access, VM state, or lab-environment behavior *inside* the lab VMs. This is NOT ROLE SSH-from-laptop access.
- video_issue_ticket: if the feedback is about videos not being available, video not matching the section, subtitle issues, translation problems, bad video cuts, or any other video-related problem.
- ssh_lab_access_ticket: the Internal Learner is stuck on SSH Lab Access from their local machine to a ROLE Lab — private key setup, DOWNLOAD SSH KEY, rht_classroom.rsa, cloud-user jump host, ssh -J, permission denied (publickey). A ROLE URL alone is NOT this type; Internal Learners also report ordinary Guide and Lab issues.

Examples of content issues:
{self.content_issues_examples}

Examples of environment issues:
{self.environment_issues_examples}

Examples of video issues:
{self.video_issues_examples}

Examples of SSH Lab Access Feedback:
{self.ssh_lab_access_examples}

Examples of types of issues to be manually managed:
{self.manually_managed_issues_examples}

IMPORTANT: Determine if lab verification is needed. Lab verification IS needed when:
- The student claims a command output is different from the guide AND the complaint is about a guided exercise or lab
- The student says a lab script (start/grade/finish) is not working
- The student says a particular solution doesn't work in the grading script
- The student reports specific behavior in the lab environment that needs confirmation
- The student claims that a file, directory, script, or path referenced in the guide does not exist, has a different name, or has different contents in the lab VM filesystem. These claims CANNOT be verified by reading the guide text alone; you must start the lab and check the actual filesystem.
- The student claims that a solution file prepared by the lab start command is missing or different from what the guide says
- The feedback contains a URL ending in *.example.com (e.g. https://api.ocp4.example.com:6443, https://console-openshift-console.apps.ocp4.example.com, etc.). These are lab-internal hostnames that only resolve inside the lab environment and must be verified there.

Lab verification is NOT needed when:
- There is a simple typo in the guide text (a spelling mistake visible in the guide itself, not involving lab files)
- Video issues (missing, not matching, subtitle problems)
- SSH Lab Access Feedback (connecting from a laptop via jump host)
- Manually managed issues (refunds, exam scheduling, UI suggestions)
- The issue can be determined just by reading the guide text WITHOUT needing to check anything on the lab VM
- The learner is complaining about theory-section example commands rather than guided exercise or lab steps
- The issue is a likely doXXX first-boot delay that matches expected startup behavior

Return JSON with the following fields:
- student_feedback: the user's feedback in english, as it is, without any changes. If the text itself contains double quotes, replace those inner quote characters with single quotes inside the string value.
- language: the language of the student's feedback. If the student's feedback is in english, the value of this key-value pair is 'en'.
- summary: in a short sentence, summarize the user's feedback
- is_content_issue_ticket: (true/false)
- is_environment_issue_ticket: (true/false)
- is_video_issue_ticket: (true/false)
- is_ssh_lab_access_ticket: (true/false)
- needs_lab_verification: (true/false) - whether we need to start a lab to verify the student's claim. Always false for ssh_lab_access_ticket.

This is the student's feedback:
<student_feedback>
\"\"\"{description}\"\"\"
</student_feedback>

{self._json_output_rules()}

For example:
{json_example}
"""
        response = self.ask_llm(prompt)
        logging.getLogger(__name__).info(f"LLM Triaging response:\n {response}")
        parsed = self._parse_llm_json(response, context="LLM triaging")
        if not parsed:
            parsed = {
                "student_feedback": description,
                "language": "en",
                "summary": "Error parsing LLM response",
                "is_content_issue_ticket": False,
                "is_environment_issue_ticket": False,
                "is_video_issue_ticket": False,
                "is_ssh_lab_access_ticket": False,
                "needs_lab_verification": False,
            }
        else:
            # Tolerate near-miss field names from some models (e.g. truncated keys).
            if "is_environment_issue_ticket" not in parsed:
                for key in list(parsed.keys()):
                    if key.startswith("is_environment_issue"):
                        parsed["is_environment_issue_ticket"] = bool(parsed[key])
                        break
            parsed = {
                "student_feedback": str(parsed.get("student_feedback", description)),
                "language": str(parsed.get("language", "en")).lower(),
                "summary": str(parsed.get("summary", "No summary provided")),
                "is_content_issue_ticket": bool(parsed.get("is_content_issue_ticket", False)),
                "is_environment_issue_ticket": bool(parsed.get("is_environment_issue_ticket", False)),
                "is_video_issue_ticket": bool(parsed.get("is_video_issue_ticket", False)),
                "is_ssh_lab_access_ticket": bool(parsed.get("is_ssh_lab_access_ticket", False)),
                "needs_lab_verification": bool(parsed.get("needs_lab_verification", False)),
            }

        return self.apply_ssh_lab_access_classification(parsed, description)

    def analyze_content_issue(self, user_issue: str, guide_text: str, snow_info: dict | None = None) -> dict:
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
            "\n        \"analysis\": \"think step by step, first try to understand student_feedback, then explain what the student is trying to communicate, then comprehend the the guide_text excerpt, then compare to see if the student's claims are correct regarding the guide_text excerpt. Detail the analysis as much as possible. If you need quote characters inside this string value, use single quotes inside the value so the outer JSON remains valid.\"," +
            "\n        \"is_valid_issue\": true," +
            "\n        \"suggested_correction\": \"If the issue is valid, indicate what words, lines, or commands that should be changed in the guide_text to fix the issue. If the issue is valid but there is not enough information it could be possible that a deeper investigation within the lab environment is required. Do not include any explanations or markdown formatting outside the JSON object.\"," +
            "\n        \"summary\": \"a short/medium summary of the 'analysis' field\"," +
            "\n        \"reply_mode\": \"one of: confirm_defect (real defect found), teach (learner confusion — explain the concept), ask_more (not enough info), lab_pending (lab still initialising), explain_expected (expected behaviour), acknowledge_resolved (latest Learner Follow-up is a Resolution Follow-up)\"," +
            "\n        \"jira_description\": \"cleaned technical problem statement for the Jira Defect — no raw learner wording, no PII, no suggested fix. Empty string when reply_mode is not confirm_defect.\"," +
            "\n        \"jira_title\": \"a short and precise title of the issue, words separated by spaces only — no underscores, dashes, or special characters, all lowercase\"\n        }"
        )

        prompt_text = f"""
        You are an useful Red Hat Training expert who is able to understand the flow of the exercises and labs in the course guide.
        {self._build_operational_context("content", snow_info)}
        We have a student who reported an issue within the guide text. The student's feedback is:
        <student_feedback>
        {user_issue}
        </student_feedback>

        {guide_text_prompt}

        {self._RESOLUTION_FOLLOW_UP_ANALYSIS_INSTRUCTIONS}
        Compare the student's feedback with the excerpt (if any), and provide a detailed analysis of the issue.
        Determine whether this is a real content defect, a platform/UI issue visible in the course page, or learner confusion caused by using theory content as if it were a guided exercise or lab.
        IMPORTANT: If the student's complaint involves files, directories, paths, or scripts that exist on the lab VM filesystem (not just in the guide text), you cannot confirm the issue from the guide alone. The lab start command may create or prepare files dynamically. In this case, explicitly state in your analysis that lab verification is required to confirm the claim, and do NOT present the student's suggested alternative as a confirmed fix.
        Remove from the response in the JSON any reference to titles or headings, as I already have that information.

        Return your analysis in exactly the following JSON example format without any extra text. Note that the description of what to put in each field is in every value of the JSON example:
        <json_example>
        {json_example}
        </json_example>

        Do not include any explanations, xml or markdown formatting outside the JSON object. No dictionaries in the value fields
        {self._json_output_rules()}
        Remove any special characters such as '\n', '\t', '\r', etc, as well as XML markers, from inside string values.
        """
        response = self.ask_llm(prompt_text)
        logging.getLogger(__name__).info(f"LLM Content analysis response: {response}")
        parsed = self._parse_llm_json(response, context="LLM content analysis")
        if not parsed:
            parsed = {"is_valid_issue": False, "summary": "analysis parse error", "suggested_correction": "", "jira_title": "", "reply_mode": "ask_more"}
        return self._normalize_content_analysis(parsed)

    def analyze_environment_issue(self, user_issue: str, snow_info: dict | None = None) -> dict:
        self.logger("Analyzing environment issue using LLM")
        json_example = '{"analysis": "think in this value step by step, describe what the student is trying to communicate in it\'s feedback, and provide the steps needed to debug the issue knowing that the lab is composed of multiple RHEL virtual machines.", "is_valid_issue": true, "suggested_correction": "a brief suggestion for correction if applicable; otherwise an empty string", "summary": "a short summary of your analysis", "reply_mode": "one of: confirm_defect, explain_expected, ask_more, lab_pending, acknowledge_resolved", "jira_description": "cleaned technical problem statement for Jira — no raw learner wording, no PII. Empty string when reply_mode is not confirm_defect.", "jira_title": "a short and precise title of the issue, words separated by spaces only — no underscores, dashes, or special characters, all lowercase"}'
        prompt_text = f"""
        You are an expert in Red Hat Training lab environments.
        {self._build_operational_context("environment", snow_info)}
        {self._RESOLUTION_FOLLOW_UP_ANALYSIS_INSTRUCTIONS}
        We have a student who reported an issue within the lab environment. The student's feedback is:
        <student_feedback>
        {user_issue}
        </student_feedback>

        Return your analysis in exactly the following JSON format without any extra text:
        {json_example}

        Do not include any explanations, xml or markdown formatting outside the JSON object. No dictionaries in the value fields
        {self._json_output_rules()}
        Remove any special characters such as '\n', '\t', '\r', etc, as well as XML markers, from inside string values.
        """
        response = self.ask_llm(prompt_text)
        logging.getLogger(__name__).info(f"LLM Environment analysis response:\n {response}")
        parsed = self._parse_llm_json(response, context="LLM environment analysis")
        if not parsed:
            parsed = {"is_valid_issue": False, "summary": "analysis parse error", "suggested_correction": "", "jira_title": "", "reply_mode": "ask_more"}
        return self._normalize_environment_analysis(parsed, user_issue=user_issue)

    def analyze_video_issue(self, user_issue: str, video_available: bool, snow_info: dict | None = None) -> dict:
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
        
        json_example = '{"analysis": "detailed analysis of the video issue", "is_valid_issue": true, "needs_jira": true, "video_issue_type": "content_mismatch", "suggested_correction": "description of what needs to be fixed", "summary": "short summary of the issue", "reply_mode": "one of: confirm_defect (real video defect), ask_more (not enough info), explain_expected (videos not yet produced), acknowledge_resolved (latest Learner Follow-up is a Resolution Follow-up)", "jira_description": "cleaned technical problem statement for Jira — no raw learner wording, no PII. Empty string when reply_mode is not confirm_defect.", "jira_title": "a short and precise title of the issue, words separated by spaces only — no underscores, dashes, or special characters, all lowercase"}'
        
        prompt_text = f"""
        You are an expert in Red Hat Training video content issues.
        {self._build_operational_context("video", snow_info, video_available=video_available)}
        
        {self._RESOLUTION_FOLLOW_UP_ANALYSIS_INSTRUCTIONS}
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
        - jira_title: a short and precise title of the issue, words separated by spaces only — no underscores, dashes, or special characters, all lowercase
        
        Do not include any explanations, xml or markdown formatting outside the JSON object.
        {self._json_output_rules()}
        """
        response = self.ask_llm(prompt_text)
        logging.getLogger(__name__).info(f"LLM Video analysis response: {response}")
        parsed = self._parse_llm_json(response, context="LLM video analysis")
        if not parsed:
            parsed = {
                "is_valid_issue": False,
                "needs_jira": False,
                "video_issue_type": "other",
                "summary": "analysis parse error",
                "suggested_correction": "",
                "jira_title": "",
                "reply_mode": "ask_more",
            }
        # Sanitize jira_title from LLM slippage
        if "jira_title" in parsed:
            parsed["jira_title"] = self._sanitize_jira_title(parsed.get("jira_title") or "")
        return parsed

    def analyze_ssh_lab_access(self, user_issue: str, snow_info: dict | None = None) -> dict:
        """Analyze SSH Lab Access Feedback. Never a Defect."""
        self.logger("Analyzing SSH Lab Access Feedback using LLM")
        json_example = (
            '{"analysis": "2-4 sentence diagnostic conclusion of which SSH Lab Access step the Internal Learner is stuck on",'
            ' "is_valid_issue": false,'
            ' "suggested_correction": "",'
            ' "summary": "one-line headline",'
            ' "reply_mode": "teach, ask_more, or acknowledge_resolved",'
            ' "jira_description": "",'
            ' "jira_title": ""}'
        )
        prompt_text = f"""
        You are an expert in ROLE SSH Lab Access for Internal Learners (Red Hat employees).

        SSH Lab Access is how Internal Learners reach a ROLE Lab from their local machine:
        download rht_classroom.rsa, chmod 0600, ssh-add, then jump via
        ssh -i ~/.ssh/rht_classroom.rsa -J cloud-user@<ip>:22022 student@workstation.
        The jump-host IP changes per Lab. This is never a Defect.

        The Internal Learner's Feedback is:
        <student_feedback>
        {user_issue}
        </student_feedback>

        {self._RESOLUTION_FOLLOW_UP_ANALYSIS_INSTRUCTIONS}
        Identify which instruction step they are stuck on (CREATE, DOWNLOAD SSH KEY, key install, ssh-add, jump host).
        All string fields must be in English.
        reply_mode is teach when you can explain the step; ask_more when there is no error text and no SSH-step clue; acknowledge_resolved when the latest Learner Follow-up is a Resolution Follow-up.
        Never use confirm_defect, lab_pending, or explain_expected.
        Never invent a jump-host IP.

        Return JSON:
        {json_example}
        {self._json_output_rules()}
        """
        response = self.ask_llm(prompt_text)
        logging.getLogger(__name__).info(f"LLM SSH Lab Access analysis response: {response}")
        parsed = self._parse_llm_json(response, context="LLM SSH Lab Access analysis")
        if not parsed:
            parsed = {
                "is_valid_issue": False,
                "summary": "analysis parse error",
                "suggested_correction": "",
                "jira_title": "",
                "reply_mode": "teach",
            }
        return self._normalize_ssh_lab_access_analysis(parsed)

    def is_long_boot_lab_first_start(self, snow_info: dict, analysis_response_json: dict) -> bool:
        course = snow_info.get("Course", "")
        family = self._infer_course_family(course)
        long_boot_courses = ["DO180", "DO280", "DO188", "DO288", "DO380", "DO480", "DO316", "DO322", "DO328", "DO370", "DO400"]
        is_long_boot = course in long_boot_courses or family == "ai"
        if is_long_boot:
            response = self.ask_llm(
                f"""
               You are an expert in Red Hat Training. We have a platform where students can run labs.
               {self._build_operational_context("environment", snow_info)}
               Labs in doXXX and aiXXX courses take about 30-40 min to finish the setup the first time they are booted up. Once everything is working, it should be pretty fast.

               CRITICAL first-boot symptoms to recognize:
               - "Authentication timed out" during "Verifying cluster state" is one of the MOST COMMON first-boot symptoms. The cluster is still initializing and the authentication layer is not ready yet. This is NOT a real authentication failure.
               - "FAIL Verifying cluster state" followed by a timeout is almost always caused by the cluster still starting up. The lab start script has a short timeout that expires before the cluster finishes its first-time initialization.
               - Even if the student says they tried multiple times over multiple days, they may have been impatient each time and not waited the full 30-40 minutes. Retrying too quickly restarts the timer but does not fix the underlying startup.
               - If the failure happens within 10-15 minutes of starting the lab, the student almost certainly did not wait long enough.
               - A student saying "it doesn't work at all" or "tried N times" does NOT mean it is a real failure. It means they did not wait long enough for the first boot to complete.

               Determine from the student's feedback if this looks like expected first-boot delay or an actual failure.

               The analysis of the issue is:
               {json.dumps(analysis_response_json)}

               Your work is to determine if this is likely a first-boot timing issue. Given the symptoms above, return True if there is any reasonable chance this is first-boot behavior. Only return False if the error is clearly unrelated to startup timing (e.g., a specific application error after the cluster is running).
               Return just True or False, no extra text.
              """
            )
            return str(response).strip().lower().startswith("true")
        return False

    _RESOLUTION_FOLLOW_UP_ANALYSIS_INSTRUCTIONS = (
        "Resolution Follow-up (conversation state, not issue type):\n"
        "- Judge only from the latest Learner Follow-up, not from earlier Follow-ups "
        "and not from our team's prior notes.\n"
        "- If that latest Follow-up states the reported problem is gone or was not a course issue, "
        "and it does not raise another problem that still needs help, set reply_mode to "
        "acknowledge_resolved. is_valid_issue must be false. jira_description must be empty.\n"
        "- An older 'it is fixed' does not override a newer report that the problem is back.\n"
        "- If the latest Follow-up both says the original issue is resolved AND raises a new problem, "
        "this is not a Resolution Follow-up — analyse the remaining claim with the usual modes.\n"
        "- Do not use explain_expected, teach, or confirm_defect when acknowledge_resolved applies."
    )

    # Per-mode reply instructions (#27)
    _REPLY_MODE_INSTRUCTIONS: dict = {
        "confirm_defect": (
            "A defect has been confirmed in the course content or lab.\n"
            "- Warmly acknowledge the Learner's report.\n"
            "- Let them know the team has identified the issue and a Defect report has been filed.\n"
            "- Do NOT describe the diagnosis, the analysis steps, or what exactly was wrong.\n"
            "- Do NOT mention Jira, internal ticket numbers, or internal workflows.\n"
            "- Do NOT suggest a workaround unless one is obvious and harmless."
        ),
        "teach": (
            "The Learner's question stems from confusion about expected course behaviour, not a defect.\n"
            "- Open with a validating phrase such as 'Good question' or 'That is a common point of confusion'.\n"
            "- Explain the relevant concept directly and factually in second person.\n"
            "- Do NOT imply that anything is broken or that a Defect has been filed.\n"
            "- Do NOT diagnose out loud or describe what the LLM analysis found.\n"
            "- Be specific: tell the Learner exactly what the expected behaviour is and why."
        ),
        "ask_more": (
            "There is not enough information to confirm or rule out a defect.\n"
            "- Politely thank the Learner and acknowledge the report.\n"
            "- Ask for the one or two specific pieces of information that are missing (screenshot, exact error text, steps taken, course page URL).\n"
            "- Do NOT speculate about the cause or imply a Defect has been found.\n"
            "- Keep the reply brief and the request concrete."
        ),
        "lab_pending": (
            "The Lab is still performing its First Boot (OpenShift Cluster Readiness Check).\n"
            "- Explain that do/ai-family Labs need about 30–40 minutes on first boot for the OpenShift cluster to be ready.\n"
            "- Suggest they wait and then run: ssh lab@utility followed by ./wait.sh to monitor progress.\n"
            "- Do NOT imply a Defect has been filed.\n"
            "- Be reassuring: this is expected behaviour, not a failure."
        ),
        "explain_expected": (
            "The behaviour the Learner reported is expected by design.\n"
            "- Acknowledge the Learner's concern warmly.\n"
            "- Explain clearly why the observed behaviour is expected, without implying anything is broken.\n"
            "- Do NOT imply a Defect has been filed.\n"
            "- If relevant, suggest a concrete next step (e.g. wait, use previous version, consult course page)."
        ),
        "acknowledge_resolved": (
            "The latest Learner Follow-up is a Resolution Follow-up: the Learner says the reported "
            "problem is gone or was not a course issue, and nothing else still needs help.\n"
            "- Thank them briefly and say we are glad it is resolved.\n"
            "- Invite them to reach out if anything else comes up.\n"
            "- Write 2-4 sentences in one short reply.\n"
            "- Do NOT recap their diagnosis, re-explain the cause, or give unsolicited advice.\n"
            "- Do NOT imply a Defect has been filed."
        ),
    }


    @classmethod
    def _build_student_reply_prompt(
        cls,
        *,
        reply_mode: str,
        student_name: str,
        url: str,
        analysis_response_json: dict,
        language_rule: str,
        ssh_rule: str,
        operational_context: str,
        communication_reply_notes: str,
        json_output_rules: str,
    ) -> str:
        """Build the Learner-facing Response prompt. No LLM call."""
        mode_instructions = cls._REPLY_MODE_INSTRUCTIONS.get(
            reply_mode, cls._REPLY_MODE_INSTRUCTIONS["ask_more"]
        )
        resolved = reply_mode == "acknowledge_resolved"
        notes_block = "" if resolved else communication_reply_notes
        ops_block = "" if resolved else operational_context
        analysis_block = ""
        if not resolved:
            analysis_block = (
                f"Analysis summary: {analysis_response_json.get('summary', '')}\n"
                f"Analysis conclusion: {analysis_response_json.get('analysis', '')}"
            )
        ssh_block = "" if resolved else ssh_rule
        json_example = '{"response": "the response to the student"}'
        return f"""
    You are a helpful Red Hat Training support representative responding to a Learner's Feedback.
    {ops_block}
    {notes_block}

    Learner name: {student_name}
    Course page: {url}

    {analysis_block}

    Mode: {reply_mode}
    Mode-specific instructions:
    {mode_instructions}

    Universal rules (apply on top of the mode instructions):
    - Address the Learner in second person by their first name. Never use third person.
    - Do NOT include a greeting ("Dear …" / "Hi …") — it is added automatically.
    - Do NOT add a closing salutation or signature.
    - Do NOT mention Jira, Defect IDs, internal tracking, or internal workflows.
    - Do NOT mention course codes, chapter numbers, section numbers, or "valid issue" status.
    - NEVER use the words 'guide text', 'guide_text', or 'course guide text'. Use 'course material', 'exercise instructions', or 'course content'.
    - Write in short paragraphs separated by blank lines. Each paragraph covers one idea.
    - {language_rule}
    {ssh_block}

    Format: JSON with one field.
    {json_output_rules}
    Example: {json_example}
    """

    def craft_llm_response(self, snow_info: dict, analysis_response_json: dict, classification_data: dict | None = None) -> dict:
        self.logger("LLM Crafting reply to student")
        student_name = snow_info.get("full_name", "").split(" ")[0]
        url = snow_info.get("URL", "")
        reply_mode = analysis_response_json.get("reply_mode", "ask_more")
        learner_language = (classification_data or {}).get("language", "en")
        if learner_language.startswith("en"):
            language_rule = "Write the reply in English."
        else:
            language_rule = f"Write the reply in the Learner's language: {learner_language}. Do NOT write in English."

        ssh_rule = ""
        if (classification_data or {}).get("is_ssh_lab_access_ticket"):
            ssh_rule = (
                "- This is SSH Lab Access Feedback. Teach the Internal Learner through the SSH key and jump-host steps.\n"
                "- Never invent or paste a jump-host IP address. Theirs is already in the Feedback and changes per Lab.\n"
                "- Do not mention ROLE, Factory, assignment groups, or internal handover."
            )

        prompt_text = self._build_student_reply_prompt(
            reply_mode=reply_mode,
            student_name=student_name,
            url=url,
            analysis_response_json=analysis_response_json,
            language_rule=language_rule,
            ssh_rule=ssh_rule,
            operational_context=self._build_operational_context("reply", snow_info),
            communication_reply_notes=self.communication_reply_notes,
            json_output_rules=self._json_output_rules(),
        )
        logging.getLogger(__name__).debug(f"LLM student reply prompt length: {len(prompt_text)} chars")
        response = self.ask_llm(prompt_text)
        logging.getLogger(__name__).info(f"LLM Student reply output ({len(response)} chars):\n {response}")
        parsed = self._parse_llm_json(response, context="LLM student reply")
        if not parsed:
            return {"response": "Thank you for your feedback. We are investigating this and will follow up."}
        if "response" in parsed:
            reply = self._clean_llm_text(parsed["response"])
            reply = self._format_reply_paragraphs(reply)
            reply = self._scrub_guide_text_wording(reply)
            reply = re.sub(r'^(Dear|Hi|Hello|Hey)\s+\S+[,.]?\s*\n*', '', reply, flags=re.IGNORECASE).lstrip()
            parsed["response"] = f"Dear {student_name},\n\n{reply}"
        return parsed


    def reply_to_student_and_add_notes(self, snow_info: dict, classification_data: dict, analysis_response_json: dict):
        self.logger("Replying to student and adding summary notes")
        signature = f"\n\nBest Regards,\n{self.SIGNATURE_NAME}\nRed Hat Learner Experience Team"

        # NOTE: hub.redhat.com's classic form ships a hidden legacy duplicate
        # at '//*[@id="x_redha_rht_task.work_notes"]' / '.comments' that is no
        # longer wired to anything - typing into it is silently discarded.
        # The real, visible journal inputs are the Angular activity-stream
        # textareas below, which must be submitted via the shared "Post"
        # button (button.activity-submit) to actually create the entry.
        WORK_NOTES_XPATH = '//*[@id="activity-stream-work_notes-textarea"]'
        COMMENTS_XPATH = '//*[@id="activity-stream-comments-textarea"]'

        try:
            # ------------------------------------------------------------------
            # Work notes — always in English (#28)
            # ------------------------------------------------------------------
            reply_mode = analysis_response_json.get("reply_mode", "ask_more")
            learner_language = classification_data.get("language", "en")
            summary = self._clean_llm_text(analysis_response_json.get("summary", "No summary available"))
            analysis_text = self._clean_llm_text(analysis_response_json.get("analysis", ""))
            # Only include translated feedback when the Learner wrote in a non-English language
            if learner_language.startswith("en"):
                work_note = (
                    f"Summary:\n{summary}\n\n"
                    f"LLM Analysis:\n{analysis_text}\n"
                )
            else:
                translated_feedback = classification_data.get("translated_student_feedback", snow_info.get("Description", ""))
                work_note = (
                    f"Translated feedback:\n{translated_feedback}\n\n"
                    f"Summary:\n{summary}\n\n"
                    f"LLM Analysis:\n{analysis_text}\n"
                )
            try:
                WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, WORK_NOTES_XPATH))).send_keys(work_note)
            except Exception:
                pass

            # ------------------------------------------------------------------
            # Learner-facing reply — driven by reply_mode (#27)
            # ------------------------------------------------------------------
            crafted = self.craft_llm_response(snow_info, analysis_response_json, classification_data)
            reply_text = crafted.get("response", "")

            try:
                WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, COMMENTS_XPATH))).send_keys(reply_text + signature)
            except Exception:
                pass

            # Annotate work notes with Jira gate decision
            jira_note = (
                f"\n\nreply_mode: {reply_mode}"
                if reply_mode != "confirm_defect"
                else f"\n\nreply_mode: {reply_mode} — Jira Defect to be filed."
            )
            try:
                WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, WORK_NOTES_XPATH))).send_keys(jira_note)
            except Exception:
                pass

            # English copy of the reply — only when the Learner is not writing in English (#28)
            if reply_text.strip() and not learner_language.startswith("en"):
                english_reply = self.translate_text(reply_text, learner_language)
                try:
                    WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, WORK_NOTES_XPATH))).send_keys(
                        f"\n\nLearner reply (English):\n{english_reply}\n"
                    )
                except Exception:
                    pass

            if classification_data.get("is_ssh_lab_access_ticket"):
                self._prefill_ssh_lab_access_handover()

            # Do NOT click the Post button automatically.
            # The journal fields are pre-filled for human review;
            # the agent operator is responsible for clicking Post manually.
        except Exception as e:
            logging.getLogger(__name__).warning(f"Failed to add work note / reply for {snow_info.get('snow_id','')}: {e}")

    def translate_text(self, text: str, language: str) -> str:
        prompt_text = f"""
Translate the following text from {language} to english.
Never use asterisks for bold formatting (e.g. **word**). Use plain text only.
<text>
{text}
</text>
"""
        translated_text = self.ask_llm(prompt_text)
        logging.getLogger(__name__).info(f"LLM Feedback translation response: {translated_text}")
        if '{' in translated_text:
            parsed = self._parse_llm_json(translated_text, context="LLM translation")
            if parsed:
                for key in ("text", "translation", "result"):
                    if key in parsed and isinstance(parsed[key], str) and parsed[key] != '/set parameter num_ctx 128000':
                        return parsed[key]
                for value in parsed.values():
                    if isinstance(value, str) and value != '/set parameter num_ctx 128000':
                        return value
        return translated_text

    # --------------------------
    # High-level helpers
    # --------------------------
    SSH_HANDOVER_ASSIGNMENT_GROUP = "RHT Learner Experience"
    SSH_HANDOVER_ASSIGNEE = "Yashashvi Singh"

    def _snow_typeahead_fill(self, field_id: str, value: str) -> None:
        """Prefill a ServiceNow reference/typeahead field. Human still Saves."""
        field = WebDriverWait(self.driver, 10).until(
            EC.element_to_be_clickable((By.ID, field_id))
        )
        field.click()
        field.send_keys(Keys.CONTROL, "a")
        field.send_keys(Keys.BACKSPACE)
        field.send_keys(value)
        time.sleep(0.8)
        field.send_keys(Keys.TAB)

    def _prefill_ssh_lab_access_handover(self) -> None:
        """Assignment group first (T1), then Assigned to Yashashvi Singh."""
        try:
            self._snow_typeahead_fill(
                "sys_display.x_redha_rht_task.assignment_group",
                self.SSH_HANDOVER_ASSIGNMENT_GROUP,
            )
            time.sleep(0.4)
            self._snow_typeahead_fill(
                "sys_display.x_redha_rht_task.assigned_to",
                self.SSH_HANDOVER_ASSIGNEE,
            )
            self.logger(
                f"Prefills: Assignment group={self.SSH_HANDOVER_ASSIGNMENT_GROUP}, "
                f"Assigned to={self.SSH_HANDOVER_ASSIGNEE}"
            )
        except Exception as e:
            logging.getLogger(__name__).warning(f"Could not prefill SSH Lab Access handover fields: {e}")

    def _open_role_ssh_lab_workspace(self, snow_info: dict, course_id: str) -> None:
        """Open the original ROLE URL, Lab Environment tab, expand SSH panel, CREATE without waiting."""
        url = snow_info.get("URL") or ""
        self.logger(f"Opening ROLE URL for SSH Lab Access: {url}")
        self.driver.get(url)
        WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".course__content-wrapper, #ssh-key-info--heading, [role='tab']")
            )
        )
        self.lab_mgr.select_lab_environment_tab("lab-environment")
        self.lab_mgr.expand_ssh_key_info()
        try:
            self.lab_mgr.create_lab(course_id=course_id, wait=False)
        except Exception as e:
            logging.getLogger(__name__).warning(f"CREATE Lab on ROLE did not complete: {e}")

    def _navigate_to_course_page(self, course_id: str, chapter_section: str, environment: str = "rol"):
        """
        Fast course navigation for the snowai per-ticket flow.

        Bypasses ``go_to_url()`` (2 s sleep) and ``wait_for_site_to_be_ready()``
        (5-15 s) because ``prelogin_all()`` already confirmed the ROL session.
        Uses ``driver.get()`` directly and waits for the course content wrapper,
        which is the correct readiness signal for this path.

        ``go_to_course()``, ``go_to_url()``, and ``wait_for_site_to_be_ready()``
        remain unchanged for QA and standalone lab callers.
        """
        base_url = self.config.get_lab_base_url(environment)
        if not base_url:
            raise ValueError(f"Base URL for environment '{environment}' not configured.")
        self.driver.get(f"{base_url}{course_id}/pages/{chapter_section}")
        WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".course__content-wrapper")
            )
        )

    def _goto_investigation_page(
        self,
        snow_info: dict,
        course_id: str,
        chapter_section: str,
        environment: str,
    ) -> str:
        """Open the Issue Section page; fall back to the Capture URL page on load failure."""
        try:
            self._navigate_to_course_page(
                course_id=course_id,
                chapter_section=chapter_section,
                environment=environment,
            )
            return chapter_section
        except Exception as exc:
            capture_slug = self.page_slug_from_url(
                snow_info.get("CaptureURL") or snow_info.get("URL") or ""
            )
            if not capture_slug or capture_slug == chapter_section:
                raise
            logging.getLogger(__name__).warning(
                f"Issue Section page {chapter_section} did not load ({exc}); "
                f"falling back to Capture URL page {capture_slug}"
            )
            self.restore_capture_location(snow_info)
            self._navigate_to_course_page(
                course_id=course_id,
                chapter_section=capture_slug,
                environment=environment,
            )
            return capture_slug

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
        # ── Phase 1: parallel tab loading.
        #
        # driver.get() blocks until the page finishes loading.
        # JavaScript window.open() is non-blocking: the tab starts loading in the
        # background and control returns immediately, so ROL and Jira load in
        # parallel while we do the SNOW session work.
        #
        # Sequence:
        #   1. First SNOW load (blocking) — establishes hub.redhat.com domain for
        #      TrustArc cookie pre-set.
        #   2. window.open(ROL) and window.open(Jira) — both start loading NOW.
        #   3. Second SNOW load (blocking, direct .do URL) — ROL and Jira keep
        #      loading in the background during this wait.
        #   4. Phase 2 login checks — by the time we reach ROL and Jira, they
        #      have had the full duration of step 3 to load.

        rol_base_url = (
            self.config.get_lab_base_url(environment)
            or "https://rol.redhat.com/rol/app/courses/"
        )

        # Step 1 — first SNOW load
        self.driver.get(self.DEFAULT_SNOW_FEEDBACK_QUEUE_URL)
        self.driver.get(self.DEFAULT_SNOW_FEEDBACK_QUEUE_URL)
        # Pre-set the TrustArc consent cookie now that we have a redhat.com domain context.
        self.lab_mgr.preset_trustarc_cookie()

        self.base_window_handle = self.driver.current_window_handle
        self.login_tab_handles = {}

        # Step 2 — fire ROL and Jira in background tabs (non-blocking)
        self.driver.execute_script("window.open(arguments[0], '_blank');", self.jira_handler.JIRA_DASHBOARD_URL)
        time.sleep(0.1)
        self.driver.execute_script("window.open(arguments[0], '_blank');", rol_base_url + "rh124-10.0/pages/pr01")

        # Capture handles (window.open is near-instant; wait briefly to be safe)
        WebDriverWait(self.driver, 5).until(lambda d: len(d.window_handles) >= 3)
        new_handles = [h for h in self.driver.window_handles if h != self.base_window_handle]
        self.login_tab_handles['rol'] = new_handles[0]
        self.login_tab_handles['jira'] = new_handles[1]


        # ── Phase 2: login round-robin — SNOW → ROL → Jira.
        try:
            WebDriverWait(self.driver, 2).until(
                EC.presence_of_element_located((By.XPATH, '//div[@class="navbar-header"]'))
            )
            self.logger("ServiceNow session already active")
        except Exception:
            self.login_snow()

        # ROL — page has been loading since Phase 1. Check if SSO session
        # is already active (header nav button visible) to avoid the costly
        # go_to_url + accept_trustarc + username-field-timeout sequence.
        self.driver.switch_to.window(self.login_tab_handles['rol'])

        try:
            avatar = WebDriverWait(self.driver, 5).until(EC.element_to_be_clickable((By.XPATH,
                '/html/body/div[1]/div[1]/header/div[2]/div/nav[2]/button[4]')))
            self.logger("ROL session already active")
        except:
            try:
                username = self.lab_mgr._get_credentials(environment)
                if username:
                    WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable(
                        (By.XPATH, "/html/body/div[1]/main/div/div/div[1]/div[2]/div[2]/div/section[1]/form/div[1]/input")
                    )).send_keys(f"{username}@redhat.com")
                    WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable(
                        (By.XPATH, '//*[@id="login-show-step2"]'))).click()
                #WebDriverWait(self.driver, 5).until(
                #    EC.presence_of_element_located((By.XPATH,
                #        '/html/body/div[1]/div[1]/header/div[2]/div/nav[2]/button[4]'))
                #)
                self.logger("ROL session already active")
                self._rol_logged_in = True
            except Exception:
                try:
                    self.lab_mgr.login(environment=environment)
                    self._rol_logged_in = True
                except Exception as e:
                    logging.getLogger(__name__).warning(f"ROL login issue: {e}")

        # Jira — page has been loading since Phase 1. Check logged-in state
        # directly instead of re-navigating (which reloads the page).
        self.driver.switch_to.window(self.login_tab_handles['jira'])
        if self.jira_handler._is_logged_in(timeout=1):
            self.jira_handler._logged_in = True
            self.logger("Jira session already active")
        else:
            try:
                WebDriverWait(self.driver, 5).until(EC.element_to_be_clickable(
                    (By.XPATH, "//*[@aria-labelledby='username-uid1-label']")
                )).send_keys(f"{username}@redhat.com")
                WebDriverWait(self.driver, 2).until(
                            EC.element_to_be_clickable((By.XPATH,
                                '//*[@id="login-submit"] | '
                                '//button[@type="submit"] | '
                                '//span[text()="Continue"]/parent::button'
                            ))).click()
                self.logger("Jira session already active")
            except Exception:
                self.login_jira()

        # Return focus to ServiceNow tab.
        self.driver.switch_to.window(self.base_window_handle)

    def extract_jira_keyword(self, snow_info: dict) -> str:
        prompt = (
            "You are an expert technical keyword extractor for course exercise feedback. "
            "From the learner's feedback below, extract a single search term to find an existing bug ticket. "
            "The ticket database is already filtered to the right course and chapter, "
            "so the term must be the most specific and unique identifier for this exercise's subject.\n\n"
            "Prefer in this strict order:\n"
            "1. Filenames or script names — e.g. 'run-app.sh', 'Containerfile', 'deploy.yaml'\n"
            "2. Fully-qualified image references — e.g. 'registry.redhat.io/ubi9/nodejs-18'\n"
            "3. Specific named resources — e.g. 'openshift-config' (namespace), 'frontend' (Deployment), 'db-secret' (Secret)\n"
            "4. A specific API kind only when it is the exercise's clear subject and no named instance is mentioned — e.g. 'PersistentVolumeClaim'\n"
            "5. A specific named Linux, POSIX, networking, or OpenShift technical concept — e.g. 'SIGTERM', 'SIGINT', 'cgroups', 'SELinux', 'iptables', 'inotify'\n\n"
            "Return null only if nothing from tiers 1–5 applies.\n"
            "NEVER return a bare Kubernetes/container infrastructure noun without a qualifying name: "
            "'pod', 'image', 'container', 'operator', 'deployment', 'service', 'namespace' alone are too generic. "
            "NEVER return a course code or course ID (e.g. 'DO188', 'rh124') or a section identifier (e.g. 'ch01s02'), nor general course words such as lab, comprehensive review, guided exercise, etc. — those are already in the search query.\n\n"
            f"<feedback>\n{snow_info.get('Description', '')}\n</feedback>\n\n"
            'Output JSON: {"keyword": "<value>"} or {"keyword": null}\n'
            f"{self._json_output_rules()}"
        )
        llm_response = self.ask_llm(prompt)
        parsed = self._parse_llm_json(llm_response, context="LLM keyword extraction")
        keyword = parsed.get("keyword") or ""
        logging.getLogger(__name__).info(f"Extracted Jira keyword: {keyword if keyword else '[none]'}")
        return keyword

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
        return f"https://redhat.atlassian.net/issues/?jql={quote(jql)}"


    def open_jira_create_prefilled(self, snow_info: dict, analysis: dict, classification: dict):
        _url = "https://redhat.atlassian.net/jira/software/c/projects/PTL/issues"
        log = logging.getLogger(__name__)
        for attempt in range(1, 3):
            try:
                self._prefill_jira_attempt(snow_info, analysis, classification, _url)
                return
            except Exception as e:
                log.warning(f"Jira prefill attempt {attempt}/2 failed: {e}")
                if attempt < 2:
                    log.info("Reloading Jira and retrying prefill…")
                    try:
                        discard = self.driver.find_elements(
                            By.XPATH, '//button[@aria-label="Discard changes"]'
                        )
                        if discard:
                            discard[0].click()
                            time.sleep(1)
                    except Exception:
                        pass
                    self.driver.get(_url)
                    time.sleep(2)
                else:
                    log.error("Jira prefill failed after 2 attempts")

    def _prefill_jira_attempt(self, snow_info: dict, analysis: dict, classification: dict, url: str):
        log = logging.getLogger(__name__)
        self.driver.get(url)
        self.driver.execute_script("document.body.style.zoom = '0.8'")

        # Click Create button in the top nav bar — raises on timeout to trigger retry
        create_btn = WebDriverWait(self.driver, 10).until(
            EC.element_to_be_clickable((By.XPATH,
                '//button[text()="Create"] | '
                '//button[contains(@data-testid, "create-button")]'
            ))
        )
        try:
            create_btn.click()
        except Exception:
            self.driver.execute_script("arguments[0].click();", create_btn)

        # Wait for the dialog to render — raises on timeout to trigger retry
        WebDriverWait(self.driver, 30).until(
            EC.presence_of_element_located((By.XPATH, '//input[@id="summary-field"]'))
        )
        log.info("Jira create dialog loaded")

        # Change Work type to "Bug" if not already set.
        # Use contains(., "Bug") instead of text()="Bug" — Jira renders the
        # label text inside a child <span>, so text()= never matches.
        already_bug = False
        try:
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.XPATH,
                    '//div[@data-testid="issue-field-select-base.ui.format-option-label.c-label"]'
                    '[contains(., "Bug")]'
                ))
            )
            already_bug = True
            log.info("Work type already set to Bug")
        except Exception:
            pass

        if not already_bug:
            try:
                work_type_input = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH,
                        '//input[starts-with(@id, "type-picker-")]'
                    ))
                )
                self.driver.execute_script("arguments[0].focus(); arguments[0].click();", work_type_input)
                time.sleep(0.5)
                work_type_input.send_keys("Bug")
                time.sleep(1)
                work_type_input.send_keys(Keys.RETURN)
                # The form re-renders after a type change; wait for the summary
                # field to become interactable (not just present) before proceeding.
                # Using element_to_be_clickable avoids ElementNotInteractableException
                # during the transition — this was the root cause of double-crash.
                WebDriverWait(self.driver, 20).until(
                    EC.element_to_be_clickable((By.XPATH, '//input[@id="summary-field"]'))
                )
                log.info("Work type changed to Bug, form re-render complete")
            except Exception as e:
                log.warning(f"Could not set Work type to Bug: {e}")

        # Fill in Summary — uses element_to_be_clickable to guard against a
        # still-rendering form when Bug type was just changed.
        raw_title = self._sanitize_jira_title(analysis.get("jira_title") or "")
        summary_value = f"{snow_info.get('Course','')}: ch{snow_info.get('Chapter','')}s{snow_info.get('Section','')} - {raw_title} - {snow_info.get('snow_id','')}"
        summary_field = WebDriverWait(self.driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, '//input[@id="summary-field"]'))
        )
        summary_field.send_keys(summary_value)

        # Fill the ProseMirror description editor.
        # The "Create Bug" template pre-fills a table with URL / Reporter RHNID /
        # Section Title rows plus an "Issue description" heading.
        # We use JS insertText for instant paste (send_keys types char-by-char).
        # Prefer the LLM-cleaned description; fall back to translated feedback as a safety net.
        jira_desc = (analysis.get("jira_description") or "").strip()
        translated = jira_desc or classification.get("translated_student_feedback", snow_info.get("Description", ""))

        # Wait for the editor to be present, then wait for the Bug template's
        # <td> cells to render.  We intentionally avoid caching the element
        # reference — Jira's React can re-render the editor DOM after the
        # template loads, turning any cached ref stale immediately.
        WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.ID, 'ak-editor-textarea'))
        )
        try:
            WebDriverWait(self.driver, 10).until(
                lambda d: d.find_elements(By.CSS_SELECTOR, '#ak-editor-textarea td')
            )
        except Exception:
            pass

        def _insert_text_in_editor(text):
            """Insert text at current cursor position using execCommand (instant, not char-by-char)."""
            self.driver.execute_script(
                "document.execCommand('insertText', false, arguments[0]);", text
            )

        # Click the <p> inside each <td> value cell to ensure cursor placement.
        # Empty cells have a tiny <p> with &nbsp; that's hard to click on the
        # <td> itself, so we target the inner paragraph.
        # Always fetch table_cells fresh here (no cached editor ref = no stale ref).
        table_cells = self.driver.find_elements(By.CSS_SELECTOR, '#ak-editor-textarea td')
        cell_values = [
            snow_info.get("URL", ""),
            snow_info.get("RHNID", ""),
            snow_info.get("Title", ""),
        ]
        if table_cells:
            for cell, value in zip(table_cells, cell_values):
                inner_p = cell.find_elements(By.TAG_NAME, 'p')
                target = inner_p[0] if inner_p else cell
                self.driver.execute_script(
                    "var el = arguments[0]; el.focus ? el.focus() : el.click();"
                    "var range = document.createRange(); range.selectNodeContents(el);"
                    "var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);"
                    "sel.collapseToStart();",
                    target
                )
                time.sleep(0.2)
                _insert_text_in_editor(value)
        else:
            # No table — click the editor by locating it fresh, then type inline.
            editor = self.driver.find_element(By.ID, 'ak-editor-textarea')
            ActionChains(self.driver).click(editor).perform()
            time.sleep(0.3)

        # The template already has bold headings: "Issue description",
        # "Steps to reproduce:", "Workaround:", "Expected result:" with
        # empty paragraphs below each. We click into those empty <p> elements
        # and insert just the content.
        # _fill_section finds the editor via document.getElementById inside JS
        # so it is never affected by a stale Python element reference.
        def _fill_section(heading_text, content):
            """Find the empty <p> after a bold heading and insert content there."""
            if not content or not content.strip():
                return
            self.driver.execute_script("""
                var editor = document.getElementById('ak-editor-textarea');
                var heading = arguments[0];
                var text = arguments[1];
                if (!editor) return;
                var paragraphs = editor.querySelectorAll('p');
                for (var i = 0; i < paragraphs.length; i++) {
                    var strong = paragraphs[i].querySelector('strong');
                    if (strong && strong.textContent.trim().toLowerCase().startsWith(heading.toLowerCase())) {
                        // Found the heading -- target the next <p> sibling
                        var next = paragraphs[i].nextElementSibling;
                        if (next && next.tagName === 'P') {
                            var range = document.createRange();
                            range.selectNodeContents(next);
                            var sel = window.getSelection();
                            sel.removeAllRanges();
                            sel.addRange(range);
                            sel.collapseToStart();
                            document.execCommand('insertText', false, text);
                            return;
                        }
                    }
                }
            """, heading_text, content)
            time.sleep(0.2)

        _fill_section("Issue description", translated)
        _fill_section("Workaround", self._normalize_suggested_correction(
            analysis.get('suggested_correction', '')))

        # Priority tab -> set priority to Minor
        try:
            time.sleep(1)
            priority_field = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH,
                    '//input[contains(@id, "priority")]'
                ))
            )
            self.driver.execute_script("arguments[0].focus(); arguments[0].click();", priority_field)
            time.sleep(0.5)
            priority_field.send_keys("Minor")
            time.sleep(1)
            priority_field.send_keys(Keys.RETURN)
        except Exception as e:
            log.warning(f"Failed to set Priority: {e}")

        # Components combobox
        try:
            components_field = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH,
                    '//input[@id="components-field"]'
                ))
            )
            self.driver.execute_script("arguments[0].focus(); arguments[0].click();", components_field)
            time.sleep(0.5)
            components_field.send_keys(snow_info.get("Course", ""))
            time.sleep(1)
            try:
                WebDriverWait(self.driver, 5).until(
                    EC.element_to_be_clickable((By.XPATH, '//*[@role="option"]'))
                ).click()
            except Exception:
                components_field.send_keys(Keys.RETURN)

            if classification.get("is_video_issue_ticket", False):
                time.sleep(0.5)
                components_field = self.driver.find_element(By.XPATH, '//input[@id="components-field"]')
                self.driver.execute_script("arguments[0].focus(); arguments[0].click();", components_field)
                components_field.send_keys("Video Content")
                time.sleep(1)
                try:
                    WebDriverWait(self.driver, 5).until(
                        EC.element_to_be_clickable((By.XPATH, '//*[@role="option"]'))
                    ).click()
                except Exception:
                    components_field.send_keys(Keys.RETURN)
        except Exception as e:
            log.warning(f"Failed to set Component: {e}")

        # Chapter number
        try:
            chapter_field = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH,
                    '//input[@id="customfield_10709-field"]'
                ))
            )
            chapter_field.send_keys(f"{snow_info.get('Chapter', '')}")
        except Exception as e:
            log.warning(f"Failed to set Chapter: {e}")

        # Affects versions combobox
        try:
            version_field = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH,
                    '//input[@id="versions-field"]'
                ))
            )
            self.driver.execute_script("arguments[0].focus(); arguments[0].click();", version_field)
            time.sleep(0.5)
            version_field.send_keys(snow_info.get("Course", ""))
        except Exception as e:
            log.warning(f"Failed to set Affects versions: {e}")


    def _click_tab_by_text(self, tab_text: str):
        try:
            WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.XPATH,
                    f'//*[@role="tab"]/span[text()="{tab_text}"]'
                ))
            ).click()
        except Exception:
            pass

    def run(self, tickets: list[str] | None = None, environment: str = "rol"):
        # Pre-login services in base window
        self.prelogin_all(environment=environment)

        # Collect tickets if none provided
        if not tickets:
            tickets = self.get_ticket_ids_from_queue()
            if not tickets:
                logging.getLogger(__name__).warning(
                    "No tickets found in the queue. "
                    "Check that the browser is on the correct SNOW queue URL and the iframe loaded successfully.\n"
                    f"  Expected queue URL: {self.DEFAULT_SNOW_FEEDBACK_QUEUE_URL}"
                )
                return
            logging.getLogger(__name__).info(f"Found {len(tickets)} ticket(s) in queue: {tickets}")

        for snow_id in tickets:
            try:
                reset_step_counter()
                # Open new browser window for isolation
                self.driver.switch_to.new_window('window')
                ticket_window = self.driver.current_window_handle

                # Tab 1: Feedback — open directly on the classic .do form
                self.snow_handler.navigate_to_ticket(snow_id)
                tab_snow = self.driver.current_window_handle

                # Zoom in the ServiceNow ticket page
                self.driver.execute_script("document.body.style.zoom = '1.5'")

                # Parse info and run classification/analysis
                self.driver.switch_to.window(tab_snow)
                snow_info = self.get_snow_info(snow_id)
                full_description = self._build_full_description(snow_info)
                classification = self.classify_ticket_llm(full_description, snow_info)
                if classification.get("language", "en").startswith("en"):
                    translated = full_description
                else:
                    translated = self.translate_text(full_description, classification.get("language", "en"))
                classification["translated_student_feedback"] = translated
                # Store English translation so analysis methods use it as primary evidence (#28)
                snow_info["Description_en"] = translated
                analysis = {"summary": "", "suggested_correction": "", "jira_title": "", "is_valid_issue": False, "reply_mode": "ask_more"}

                # Tab 2: ROL chapter/section
                self.driver.switch_to.window(ticket_window)
                self.driver.switch_to.new_window('tab')
                tab_rol = self.driver.current_window_handle
                video_player_available = False
                course_id = ""
                try:
                    self.driver.switch_to.window(tab_rol)
                    course_id = snow_info["Course"].lower() + "-" + snow_info["Version"]
                    chapter = snow_info.get("Chapter") or ""
                    section = snow_info.get("Section") or ""
                    if chapter and section:
                        chapter_section = f"ch{chapter}s{section}"
                    elif chapter:
                        chapter_section = f"ch{chapter}"
                    else:
                        # No chapter/section (e.g. preamble pages like pr01, ap01)
                        # Extract the page slug directly from the URL
                        url_page_match = re.search(r'/pages/([^/?#]+)', snow_info.get("URL", ""))
                        chapter_section = url_page_match.group(1) if url_page_match else "pr01"
                    
                    # Determine issue type from classification
                    needs_lab = classification.get("needs_lab_verification", False)
                    is_content_issue = classification.get("is_content_issue_ticket", False)
                    is_environment_issue = classification.get("is_environment_issue_ticket", False)
                    is_video_issue = classification.get("is_video_issue_ticket", False)
                    is_ssh_lab_access = classification.get("is_ssh_lab_access_ticket", False)
                    analysis_input = snow_info.get("Description_en") or translated or full_description

                    if is_ssh_lab_access:
                        self.logger("SSH Lab Access Feedback — ROLE Lab Environment, no Defect")
                        analysis = self.analyze_ssh_lab_access(analysis_input, snow_info)
                        self._open_role_ssh_lab_workspace(snow_info, course_id)
                    elif is_video_issue:
                        self.logger("Video issue detected - navigating to course page without starting lab")
                        chapter_section = self._goto_investigation_page(
                            snow_info, course_id, chapter_section, environment
                        )
                        video_player_available = self.lab_mgr.check_video_player_available()
                        analysis = self.analyze_video_issue(analysis_input, video_player_available, snow_info)
                    elif is_content_issue:
                        chapter_section = self._goto_investigation_page(
                            snow_info, course_id, chapter_section, environment
                        )
                        try:
                            self.lab_mgr.select_lab_environment_tab("course")
                        except Exception:
                            pass

                        guide_text = ""
                        try:
                            guide_text = self.fetch_guide_text_from_website()
                            self.logger(f"Fetched guide text length: {len(guide_text)}")
                        except Exception as e:
                            logging.getLogger(__name__).warning(f"Failed fetching guide text: {e}")

                        analysis = self.analyze_content_issue(analysis_input, guide_text, snow_info)

                    elif is_environment_issue:
                        analysis = self.analyze_environment_issue(analysis_input, snow_info)

                    # Lab verification on ROL — never for SSH Lab Access (ROLE path already ran)
                    if not is_ssh_lab_access:
                        if needs_lab:
                            self.logger("Lab verification needed - starting lab environment")
                            try:
                                chapter_section = self._goto_investigation_page(
                                    snow_info, course_id, chapter_section, environment
                                )
                                self.start_lab_for_course(course_id=course_id, chapter_section=chapter_section, environment=environment)
                            except Exception as e:
                                logging.getLogger(__name__).warning(f"Failed to start lab: {e}")
                        else:
                            self.logger("No lab verification needed - navigating to course page only")
                            chapter_section = self._goto_investigation_page(
                                snow_info, course_id, chapter_section, environment
                            )
                            self.lab_mgr.select_lab_environment_tab("course")
                            if is_video_issue:
                                self.lab_mgr.toggle_video_player(state=True)
                            else:
                                self.lab_mgr.toggle_video_player(state=False)

                except Exception as e:
                    logging.getLogger(__name__).warning(f"ROL tab setup failed for {snow_id}: {e}\n{traceback.format_exc()}")

                # Add SNOW work notes and student reply in Tab 1 (after analysis)
                try:
                    self.driver.switch_to.window(tab_snow)
                    self.reply_to_student_and_add_notes(snow_info, classification, analysis)
                except Exception as e:
                    logging.getLogger(__name__).warning(f"Failed updating SNOW notes/reply for {snow_id}: {e}")

                # SSH Lab Access Feedback is never a Defect. Otherwise only confirm_defect opens Jira.
                reply_mode = analysis.get("reply_mode", "confirm_defect")
                skip_jira = (
                    classification.get("is_ssh_lab_access_ticket", False)
                    or reply_mode != "confirm_defect"
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


                # Increase autostop and lifespan only if a lab is running for this course
                self.driver.switch_to.window(tab_rol)
                if self.lab_mgr.is_lab_running():
                    # Limit auto-stop to max 2 hours, always maximize lifespan
                    self.lab_mgr.increase_autostop(course_id=course_id, max_hours=2)
                    self.lab_mgr.increase_lifespan(course_id=course_id)
                    self.logger(f"Lab timers adjusted for course {course_id} (max 2h auto-stop, max lifespan)")
                    self.driver.switch_to.window(tab_rol)
                else:
                    self.logger(f"No running lab for {course_id} - skipping timer adjustments")
                self.driver.switch_to.window(tab_snow)

            except Exception as e:
                logging.getLogger(__name__).error(f"Failed to orchestrate window for ticket {snow_id}: {e}")
