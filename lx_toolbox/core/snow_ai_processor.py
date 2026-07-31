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
        #self.OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "laguna-xs-2.1:latest")
        self.OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
        self.OLLAMA_TIMEOUT_SECONDS = int(os.environ.get("OLLAMA_TIMEOUT_SECONDS", "600"))
        self.OLLAMA_MAX_NUM_CTX = int(os.environ.get("OLLAMA_MAX_NUM_CTX", "32768"))

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
            "- ssh to the lab workstation VM is not working, ~/.ssh/rht_classroom.rsa, cloud-user@some-ip:22022, -J jump host, is not working\n"
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
        return self._ask_ollama(prompt, plain_text=plain_text)

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
                "- Treat lab startup, lab finish, lab grade, building, starting, stopping, VM access, and lab-environment behavior as environment issues.",
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

    def get_snow_info(self, snow_id: str) -> dict:
        """Open ticket page and parse key fields from description and form controls."""
        self.logger(f"Getting SNOW info for ticket {snow_id}")
        self.snow_handler.navigate_to_ticket(snow_id)

        description = self.driver.find_element(By.XPATH, '//*[@id="x_redha_rht_task.description"]').get_attribute('value')
        full_name = self.driver.find_element(By.XPATH, '//*[@id="x_redha_rht_task.contact_source"]').get_attribute('value')

        issue = re.search(r"Description:\s*(.*?)\s*Copyright", description, re.DOTALL).group(1).strip()
        course = re.findall("Course:.*", description)[0].split(":  ")[1].upper().split(" ")[0].strip()
        version = re.findall("Version:.*", description)[0].split(":  ")[1].strip()
        url = re.findall("URL:.*", description)[0].split(":  ")[1].strip()
        if "role.rhu.redhat.com/rol-rhu" in url:
            url = url.replace("role.rhu.redhat.com/rol-rhu", "rol.redhat.com/rol")
        elif "role.rhu.redhat.com/rol" in url:
            url = url.replace("role.rhu.redhat.com/rol", "rol.redhat.com/rol")
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

        raw_updates = self.snow_handler.get_customer_updates()
        customer_updates = self._filter_and_clean_updates(raw_updates)

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
            "customer_updates": customer_updates,
            "customer_updates_summary": "",
        }

        if customer_updates:
            self.logger("Summarizing customer follow-up messages")
            info["customer_updates_summary"] = self._summarize_customer_updates(info)
            logging.getLogger(__name__).info(
                f"Customer updates summary: {info['customer_updates_summary']}"
            )

        logging.getLogger(__name__).info(f"ServiceNow ticket info: {json.dumps({k: v for k, v in info.items() if k != 'customer_updates'}, indent=2)}")
        return info

    def get_ticket_ids_from_queue(self) -> list:
        """Get list of ticket IDs from the current queue view."""
        return self.snow_handler.get_ticket_ids_from_queue()

    # --------------------------
    # Ticket understanding
    # --------------------------
    def classify_ticket_llm(self, description: str, snow_info: dict | None = None) -> dict:
        self.logger("Classifying ticket using LLM")
        json_example = '{"student_feedback": "hay un error en el laboratorio", "language": "es", "summary": "The student is reporting an error in the lab", "is_content_issue_ticket": true, "is_environment_issue_ticket": false, "is_video_issue_ticket": false, "needs_lab_verification": true}'
        prompt = f"""
You are an expert classifier of Red Hat Training tickets.
{self._build_operational_context("classification", snow_info)}

Classify the user's feedback regarding a Red Hat Training course, there are three types of tickets:
- content_issue_ticket: a mismatch or inconsistency between the user's complaint and the text in the guide, a typo, a missing step, a missing command, a quiz problem, missing lab specification details, or a grading-script logic mismatch.
- environment_issue_ticket: if the feedback includes words such as 'lab start', 'lab finish', ' lab grade','SUCCESS', 'FAIL', 'stuck', or 'lab is taking to long to start', or the learner is reporting machine access, VM state, or lab-environment behavior, it's an environment issue.
- video_issue_ticket: if the feedback is about videos not being available, video not matching the section, subtitle issues, translation problems, bad video cuts, or any other video-related problem.

Examples of content issues:
{self.content_issues_examples}

Examples of environment issues:
{self.environment_issues_examples}

Examples of video issues:
{self.video_issues_examples}

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
- needs_lab_verification: (true/false) - whether we need to start a lab to verify the student's claim

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
            return {
                "student_feedback": description,
                "language": "en",
                "summary": "Error parsing LLM response",
                "is_content_issue_ticket": False,
                "is_environment_issue_ticket": False,
                "is_video_issue_ticket": False,
                "needs_lab_verification": False,
            }

        # Tolerate near-miss field names from some models (e.g. truncated keys).
        if "is_environment_issue_ticket" not in parsed:
            for key in list(parsed.keys()):
                if key.startswith("is_environment_issue"):
                    parsed["is_environment_issue_ticket"] = bool(parsed[key])
                    break

        return {
            "student_feedback": str(parsed.get("student_feedback", description)),
            "language": str(parsed.get("language", "en")).lower(),
            "summary": str(parsed.get("summary", "No summary provided")),
            "is_content_issue_ticket": bool(parsed.get("is_content_issue_ticket", False)),
            "is_environment_issue_ticket": bool(parsed.get("is_environment_issue_ticket", False)),
            "is_video_issue_ticket": bool(parsed.get("is_video_issue_ticket", False)),
            "needs_lab_verification": bool(parsed.get("needs_lab_verification", False)),
        }

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
            "\n        \"jira_title\": \"a short and precise title of the issue at hand without mentioning the exercise type, course section, or course name, all characters in lowercase separated by spaces, no dashes\"\n        }"
        )

        prompt_text = f"""
        You are an useful Red Hat Training expert who is able to understand the flow of the exercises and labs in the course guide.
        {self._build_operational_context("content", snow_info)}
        We have a student who reported an issue within the guide text. The student's feedback is:
        <student_feedback>
        {user_issue}
        </student_feedback>

        {guide_text_prompt}

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
        return parsed or {"is_valid_issue": False, "summary": "analysis parse error", "suggested_correction": "", "jira_title": ""}

    def analyze_environment_issue(self, user_issue: str, snow_info: dict | None = None) -> dict:
        self.logger("Analyzing environment issue using LLM")
        json_example = '{"analysis": "think in this value step by step, describe what the student is trying to communicate in it\'s feedback, and provide the steps needed to debug the issue knowing that the lab is composed of multiple RHEL virtual machines.", "is_valid_issue": true, "suggested_correction": "a brief suggestion for correction if applicable; otherwise an empty string", "summary": "a short summary of your analysis", "jira_title": "a short and precise title of the issue at hand without mentioning the exercise type, course section, or course name, all characters in lowercase separated by spaces, no dashes"}'
        prompt_text = f"""
        You are an expert in Red Hat Training lab environments.
        {self._build_operational_context("environment", snow_info)}
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
        return parsed or {"is_valid_issue": False, "summary": "analysis parse error", "suggested_correction": "", "jira_title": ""}

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
        
        json_example = '{"analysis": "detailed analysis of the video issue", "is_valid_issue": true, "needs_jira": true, "video_issue_type": "content_mismatch", "suggested_correction": "description of what needs to be fixed", "summary": "short summary of the issue", "jira_title": "a short and precise title of the issue at hand without mentioning the exercise type, course section, or course name, all characters in lowercase separated by spaces, no dashes"}'
        
        prompt_text = f"""
        You are an expert in Red Hat Training video content issues.
        {self._build_operational_context("video", snow_info, video_available=video_available)}
        
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
        - jira_title: a short and precise title of the issue at hand without mentioning the exercise type, course section, or course name, all characters in lowercase separated by spaces, no dashes
        
        Do not include any explanations, xml or markdown formatting outside the JSON object.
        {self._json_output_rules()}
        """
        response = self.ask_llm(prompt_text)
        logging.getLogger(__name__).info(f"LLM Video analysis response: {response}")
        parsed = self._parse_llm_json(response, context="LLM video analysis")
        return parsed or {
            "is_valid_issue": False,
            "needs_jira": False,
            "video_issue_type": "other",
            "summary": "analysis parse error",
            "suggested_correction": "",
            "jira_title": ""
        }

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

    def craft_llm_response(self, snow_info: dict, analysis_response_json: dict, classification_data: dict | None = None) -> dict:
        self.logger("LLM Crafting reply to student")
        student_name = snow_info.get("full_name", "").split(" ")[0]
        course = snow_info.get("Course", "")
        chapter = snow_info.get("Chapter", "")
        section = snow_info.get("Section", "")
        url = snow_info.get("URL", "")
        json_example = '{"response": "the response to the student"}'
        prompt_text = f"""
    You are a helpful Red Hat Training support representative responding to a student's feedback.
    {self._build_operational_context("reply", snow_info)}
    {self.communication_reply_notes}

    Student Information:
    - Name: {student_name}
    - Course: {course}
    - Chapter: {chapter}
    - Section: {section}

    Student's Original Feedback:
    {snow_info.get('Description', '')}

    Course guide URL:
    {url}

    Analysis Results:
    - Issue Summary: {analysis_response_json.get('summary', '')}
    - Is Valid Issue: {analysis_response_json.get('is_valid_issue', False)}
    - Suggested Correction: {self._normalize_suggested_correction(analysis_response_json.get('suggested_correction', ''))}
    - Analysis: {analysis_response_json.get('analysis', '')}
    - Video Issue Type: {analysis_response_json.get('video_issue_type', '')}
    - Classification Flags: {json.dumps(classification_data or {}, ensure_ascii=True)}

    Craft a professional, helpful response to the student based on the analysis results that:
    1. Do NOT include a greeting line such as 'Dear Name,' or 'Hi Name,' — the greeting is added separately.
    2. Start directly with thanking them and acknowledging the feedback shortly.
    3. If the analysis is not valid or the evidence is insufficient, ask for more information, a screenshot, and confirmation of the exact course section.
    4. Don't add a signature nor final salutation to the response.
    5. Do not mention Jira or internal tracking.
    6. NEVER use the words 'guide text', 'guide_text', or 'course guide text'. Use natural alternatives like 'course material', 'exercise instructions', 'course page', or 'course content'.
    7. Write the reply in short paragraphs separated by blank lines (two newlines). Each paragraph should cover one distinct idea: acknowledgement, explanation of the issue, what we are doing about it, and next steps for the learner. Do NOT write the entire reply as a single block of text.

    Keep the response concise but informative.

    Special cases:
    - If the issue looks like a doXXX first-boot delay, explain that the first boot can take about 30-40 minutes and mention ssh lab@utility plus ./wait.sh.
    - If videos for this version are not ready, explain that clearly and suggest using the previous version if video is important.
    - Only suggest deleting and recreating the lab when the analysis indicates that this is an appropriate recovery step.
    - If the learner seems to have used theory content as if it were a guided exercise or lab, clarify that politely.
    - IMPORTANT: If the analysis says lab verification is needed, or the issue involves files, directories, or scripts on the lab VM that we have not yet verified, do NOT tell the learner what the fix is. Instead, say something like: 'Thank you for your feedback. We are currently looking into this and verifying it in the lab environment. We will get back to you once we have confirmed the issue.' Keep it short and reassuring.
    - Never confirm an unverified filesystem claim as fact in the reply.

    Format the response as a JSON object with the following fields:
    - response: the response to the student

    {self._json_output_rules()}

    Example:
    {json_example}
    """
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
            # Add work note with summary of analysis
            summary = self._clean_llm_text(analysis_response_json.get('summary', 'No summary available'))
            analysis = self._clean_llm_text(analysis_response_json.get('analysis', ''))
            work_note = f"Summary:\n{summary}\n\nLLM Analysis:\n{analysis}\n"
            try:
                WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, WORK_NOTES_XPATH))).send_keys(work_note)
            except Exception:
                pass

            # Prepare and add student reply
            reply_text = ""
            if classification_data.get("is_content_issue_ticket", False):
                crafted = self.craft_llm_response(snow_info, analysis_response_json, classification_data)
                reply_text = crafted.get("response", "")
                default_jira_reply = (
                    f"\n\nDear {snow_info.get('full_name','').split(' ')[0]},\n\n"
                    f"We created a Jira ticket to fix it in the next release.\n\n"
                    f"Thanks again for your contributions to improving the course guide! \n\n"
                )
                try:
                    WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, COMMENTS_XPATH))).send_keys(reply_text + signature)
                    WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, WORK_NOTES_XPATH))).send_keys("\n\nDEFAULT RESPONSE:")
                    WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, WORK_NOTES_XPATH))).send_keys(default_jira_reply + signature)
                except Exception:
                    pass
            elif classification_data.get("is_video_issue_ticket", False):
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
                        f"If video is an important resource for you right now, I would recommend using "
                        f"the previous version of the course in the meantime, because the written content "
                        f"usually does not change much between versions.\n\n"
                        f"We appreciate your patience and understanding. Please check back later "
                        f"for video availability.\n\n"
                    )
                    try:
                        WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, COMMENTS_XPATH))).send_keys(reply_text + signature)
                        WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, WORK_NOTES_XPATH))).send_keys("\n\nVIDEO NOT READY - No Jira needed. Videos for this course version are still being produced.")
                    except Exception:
                        pass
                else:
                    # Video content issue that needs a Jira ticket
                    crafted = self.craft_llm_response(snow_info, analysis_response_json, classification_data)
                    reply_text = crafted.get("response", "")
                    default_jira_reply = (
                        f"\n\nDear {snow_info.get('full_name','').split(' ')[0]},\n\n"
                        f"We have created a Jira ticket to address this video issue.\n\n"
                        f"Thanks for helping us improve the video content!\n\n"
                    )
                    try:
                        WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, COMMENTS_XPATH))).send_keys(reply_text + signature)
                        WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, WORK_NOTES_XPATH))).send_keys("\n\nVIDEO ISSUE - Jira ticket created with 'Video Content' component.")
                        WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, WORK_NOTES_XPATH))).send_keys(default_jira_reply + signature)
                    except Exception:
                        pass
            elif classification_data.get("is_environment_issue_ticket", False) and self.is_long_boot_lab_first_start(snow_info, analysis_response_json):
                reply_text = (
                    f"\n\nDear {snow_info.get('full_name','').split(' ')[0]},\n\n"
                    f"Thank you for your feedback. Labs in this course can take about 30-40 minutes to finish the setup the first time they are booted up, so please give it some more time. Once everything is working, it should be much faster.\n\n"
                    f"You can monitor the status of the cluster by ssh lab@utility and running the ./wait.sh script. Once the script has finished the scripts are ready to be run.\n\n"
                    f"If by the time you read this message it is still not working fine, I would suggest deleting and creating a new lab environment, and then try to run the lab again.\n\n"
                    f"Please, let me know if the issue persists.\n\n"
                )
                try:
                    WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, COMMENTS_XPATH))).send_keys(reply_text + signature)
                except Exception:
                    pass
            else:
                crafted = self.craft_llm_response(snow_info, analysis_response_json, classification_data)
                reply_text = crafted.get("response", "")
                try:
                    WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, COMMENTS_XPATH))).send_keys(reply_text + signature)
                except Exception:
                    pass

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
            f"{self._json_output_rules()}"
        )
        llm_response = self.ask_llm(prompt)
        parsed = self._parse_llm_json(llm_response, context="LLM keyword extraction")
        keyword = parsed.get("keyword", "")
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
        self.driver.get("https://redhat.atlassian.net/jira/software/c/projects/PTL/issues")
        time.sleep(5)
        self.driver.execute_script("document.body.style.zoom = '0.8'")

        try:
            # Click Create button in the top nav bar
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
            time.sleep(8)
            logging.getLogger(__name__).info("Jira create dialog loaded")

            # Change Work type to "Bug" -- the input is obscured by the value
            # overlay so we JS-focus it, then type + Enter to select.
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
                time.sleep(2)
            except Exception as e:
                logging.getLogger(__name__).warning(f"Could not set Work type to Bug: {e}")

            # Fill in Summary
            summary_value = f"{snow_info.get('Course','')}: ch{snow_info.get('Chapter','')}s{snow_info.get('Section','')} - {analysis.get('jira_title', '')} - {snow_info.get('snow_id','')}"
            summary_field = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH,
                    '//input[@id="summary-field"]'
                ))
            )
            summary_field.send_keys(summary_value)

            time.sleep(5)
            # Fill the ProseMirror description editor.
            # The "Create Bug" template pre-fills a table with URL / Reporter RHNID /
            # Section Title rows plus an "Issue description" heading.
            # We use JS insertText for instant paste (send_keys types char-by-char).
            translated = classification.get("translated_student_feedback", snow_info.get("Description",""))

            desc_editor = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, 'ak-editor-textarea'))
            )

            def _insert_text_in_editor(text):
                """Insert text at current cursor position using execCommand (instant, not char-by-char)."""
                self.driver.execute_script(
                    "document.execCommand('insertText', false, arguments[0]);", text
                )

            # Click the <p> inside each <td> value cell to ensure cursor placement.
            # Empty cells have a tiny <p> with &nbsp; that's hard to click on the
            # <td> itself, so we target the inner paragraph.
            table_cells = desc_editor.find_elements(By.XPATH, './/td')
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
                ActionChains(self.driver).click(desc_editor).perform()
                time.sleep(0.3)

            # The template already has bold headings: "Issue description",
            # "Steps to reproduce:", "Workaround:", "Expected result:" with
            # empty paragraphs below each. We click into those empty <p> elements
            # and insert just the content.
            def _fill_section(heading_text, content):
                """Find the empty <p> after a bold heading and insert content there."""
                if not content or not content.strip():
                    return
                self.driver.execute_script("""
                    var editor = arguments[0];
                    var heading = arguments[1];
                    var text = arguments[2];
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
                """, desc_editor, heading_text, content)
                time.sleep(0.2)

            _fill_section("Issue description", translated)
            _fill_section("Workaround", self._normalize_suggested_correction(
                analysis.get('suggested_correction', '')))

            # Priority tab -> set priority to Minor
            try:
                #self._click_tab_by_text('Priority')
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
                #self._click_tab_by_text('Field Tab')
            except Exception as e:
                logging.getLogger(__name__).warning(f"Failed to set Priority: {e}")

            # Components combobox
            try:
                components_field = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH,
                        '//input[@id="components-field"]'
                    ))
                )
                self.driver.execute_script("arguments[0].focus(); arguments[0].click();", components_field)
                time.sleep(0.5)
                components_field.send_keys(snow_info.get("Course",""))
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
                logging.getLogger(__name__).warning(f"Failed to set Component: {e}")

            # Chapter number
            try:
                chapter_field = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH,
                        '//input[@id="customfield_10709-field"]'
                    ))
                )
                chapter_field.send_keys(f"{snow_info.get('Chapter','')}")
            except Exception as e:
                logging.getLogger(__name__).warning(f"Failed to set Chapter: {e}")

            # Affects versions combobox
            try:
                version_field = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH,
                        '//input[@id="versions-field"]'
                    ))
                )
                self.driver.execute_script("arguments[0].focus(); arguments[0].click();", version_field)
                time.sleep(0.5)
                version_field.send_keys(snow_info.get("Course",""))

            except Exception as e:
                logging.getLogger(__name__).warning(f"Failed to set Affects versions: {e}")
        except Exception as e:
            logging.getLogger(__name__).warning(f"Prefill Jira create failed: {e}")


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

                # Tab 1: ServiceNow ticket
                self.driver.get(f"{self.SNOW_BASE_URL}/api/redha/surl?n={snow_id}")
                time.sleep(3)
                tab_snow = self.driver.current_window_handle

                # Zoom in the ServiceNow ticket page
                self.driver.execute_script("document.body.style.zoom = '1.5'")

                # Parse info and run classification/analysis
                self.driver.switch_to.window(tab_snow)
                snow_info = self.get_snow_info(snow_id)
                full_description = self._build_full_description(snow_info)
                classification = self.classify_ticket_llm(full_description, snow_info)
                if classification.get("language") == "en":
                    translated = full_description
                else:
                    translated = self.translate_text(full_description, classification.get("language", "en"))
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
                    
                    # For video issues, just navigate and check video player availability
                    if is_video_issue:
                        self.logger("Video issue detected - navigating to course page without starting lab")
                        self.lab_mgr.go_to_course(course_id=course_id, chapter_section=chapter_section, environment=environment)
                        time.sleep(2)
                        video_player_available = self.lab_mgr.check_video_player_available()
                        analysis = self.analyze_video_issue(full_description, video_player_available, snow_info)
                    elif is_content_issue:                        
                        # Navigate to the course page
                        self.lab_mgr.go_to_course(course_id=course_id, chapter_section=chapter_section, environment=environment)
                        time.sleep(2)
                        
                        # Fetch guide text for "content guide" issue analysis
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

                        # Perform analysis based on issue type
                        analysis = self.analyze_content_issue(full_description, guide_text, snow_info)

                    elif is_environment_issue:
                        analysis = self.analyze_environment_issue(full_description, snow_info)

                    # If the issue requires lab verification, start the lab
                    if needs_lab:
                        self.logger("Lab verification needed - starting lab environment")
                        try:
                            self.start_lab_for_course(course_id=course_id, chapter_section=chapter_section, environment=environment)
                        except Exception as e:
                            logging.getLogger(__name__).warning(f"Failed to start lab: {e}")
                    else:
                        self.logger("No lab verification needed - navigating to course page only")
                        self.lab_mgr.go_to_course(course_id=course_id, chapter_section=chapter_section, environment=environment)
                        self.lab_mgr.select_lab_environment_tab("course")
                        if is_video_issue:
                            self.lab_mgr.toggle_video_player(state=True)
                        else:
                            self.lab_mgr.toggle_video_player(state=False)
                        time.sleep(2)

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
                    classification.get("is_video_issue_ticket", False) and 
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
