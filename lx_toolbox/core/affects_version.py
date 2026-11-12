"""Choose Jira Affects version labels from dropdown options via LLM."""

from __future__ import annotations

import json
import logging
import re
from typing import Callable

log = logging.getLogger(__name__)

JSON_OUTPUT_RULES = (
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


def filter_released_affects_version_options(
    raw_options: list[dict],
    course_code: str,
) -> list[str]:
    """Keep Released dropdown rows for this Course Code; drop Unreleased / NEXT."""
    if not course_code:
        return []
    code = course_code.strip().upper()
    labels: list[str] = []
    seen: set[str] = set()
    for item in raw_options:
        text = (item.get("text") or "").strip()
        if not text or code not in text.upper():
            continue
        group = (item.get("group") or "").strip().lower()
        if "unreleased" in group:
            continue
        if text not in seen:
            seen.add(text)
            labels.append(text)
    return labels


def _format_options_block(options: list[str]) -> str:
    return "\n".join(f"{idx + 1}. {label}" for idx, label in enumerate(options))


def build_latest_released_prompt(course_code: str, options: list[str]) -> str:
    return f"""You choose the Affects version for a Jira Defect.

Course Code: {course_code.upper()}
These strings are the Released rows from the Jira "Affects versions" dropdown (exact labels):
{_format_options_block(options)}

Rules:
- Pick the newest / latest shipped production release among these options only.
- Labels vary by course (OCP vs RHOCP, en-N, date suffixes). Use semantic version and date cues; do not invent a label not in the list.
- Ignore any Unreleased or NEXT labels (they are not in this list).

Return JSON only:
{{"affects_version": "<exact string copied from the list>", "reason": "<short explanation>"}}
{JSON_OUTPUT_RULES}"""


def build_prefer_platform_prompt(
    course_code: str,
    platform_version: str,
    options: list[str],
) -> str:
    platform = (platform_version or "").strip()
    return f"""You choose the Affects version for a Jira Defect.

Course Code: {course_code.upper()}
Platform Course Version from the link check: {platform or "(unknown)"}
These strings are the Released rows from the Jira "Affects versions" dropdown (exact labels):
{_format_options_block(options)}

Rules:
- If one option clearly corresponds to platform version {platform or "above"}, pick that exact label.
- Otherwise pick the newest / latest shipped production release among these options.
- Do not invent a label not in the list.

Return JSON only:
{{"affects_version": "<exact string copied from the list>", "reason": "<short explanation>"}}
{JSON_OUTPUT_RULES}"""


def parse_affects_version_choice(response: str, allowed: list[str]) -> str | None:
    if not response or not allowed:
        return None
    allowed_set = set(allowed)
    text = response.strip()
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.debug("Affects version LLM response was not valid JSON: %s", response[:500])
        return None
    choice = data.get("affects_version")
    if not isinstance(choice, str):
        return None
    choice = choice.strip()
    if choice in allowed_set:
        return choice
    for label in allowed:
        if label.strip() == choice:
            return label
    log.debug(
        "Affects version LLM picked %r which is not in allowed options: %s",
        choice,
        allowed,
    )
    return None


def pick_affects_version_with_llm(
    ask_llm: Callable[[str], str],
    mode: str,
    course_code: str,
    released_options: list[str],
    *,
    platform_version: str | None = None,
) -> str | None:
    if not released_options:
        return None
    if mode == "prefer_platform":
        prompt = build_prefer_platform_prompt(
            course_code, platform_version or "", released_options
        )
    else:
        prompt = build_latest_released_prompt(course_code, released_options)
    response = ask_llm(prompt)
    if not response:
        log.debug("Empty LLM response when choosing Affects version for %s", course_code)
        return None
    return parse_affects_version_choice(response, released_options)


def standalone_ask_llm(prompt: str) -> str:
    """Run the configured LLM without starting a browser session."""
    from .snow_ai_processor import SnowAIProcessor

    return SnowAIProcessor.ask_llm_standalone(prompt)
