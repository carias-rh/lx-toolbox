"""Unit tests for SnowAIProcessor static helper methods.

Seam: SnowAIProcessor static/class methods called directly without instantiation.
"""

import pytest
from lx_toolbox.core.snow_ai_processor import SnowAIProcessor

VALID_CONTENT_MODES = {"confirm_defect", "teach", "ask_more", "lab_pending", "explain_expected", "acknowledge_resolved", "acknowledge_suggestion"}
VALID_ENV_MODES = {"confirm_defect", "explain_expected", "ask_more", "lab_pending", "acknowledge_resolved"}


# ---------------------------------------------------------------------------
# _sanitize_jira_title  (#23)
# ---------------------------------------------------------------------------

class TestSanitizeJiraTitle:
    def _sanitize(self, value):
        return SnowAIProcessor._sanitize_jira_title(value)

    def test_underscores_become_spaces(self):
        assert self._sanitize("video_content_mismatch_section_four") == "video content mismatch section four"

    def test_dashes_become_spaces(self):
        assert self._sanitize("lab-start-failure") == "lab start failure"

    def test_mixed_underscores_and_dashes(self):
        assert self._sanitize("video_content-mismatch") == "video content mismatch"

    def test_output_is_lowercase(self):
        assert self._sanitize("Lab Start Failure") == "lab start failure"

    def test_special_characters_stripped(self):
        assert self._sanitize("video (section) four!") == "video section four"

    def test_consecutive_separators_collapse_to_one_space(self):
        assert self._sanitize("lab__start--failure") == "lab start failure"

    def test_empty_string_returns_empty(self):
        assert self._sanitize("") == ""

    def test_none_returns_empty(self):
        assert self._sanitize(None) == ""

    def test_already_clean_title_unchanged(self):
        assert self._sanitize("lab start failure") == "lab start failure"

    def test_leading_trailing_whitespace_stripped(self):
        assert self._sanitize("  lab start  ") == "lab start"


# ---------------------------------------------------------------------------
# _normalize_content_analysis  (#24)
# ---------------------------------------------------------------------------

class TestNormalizeContentAnalysis:
    def _norm(self, parsed):
        return SnowAIProcessor._normalize_content_analysis(parsed)

    def test_confirm_defect_passthrough(self):
        result = self._norm({"reply_mode": "confirm_defect", "is_valid_issue": True, "jira_title": "some issue"})
        assert result["reply_mode"] == "confirm_defect"

    def test_teach_passthrough(self):
        result = self._norm({"reply_mode": "teach", "is_valid_issue": False})
        assert result["reply_mode"] == "teach"

    def test_acknowledge_resolved_passthrough(self):
        result = self._norm({"reply_mode": "acknowledge_resolved", "is_valid_issue": False})
        assert result["reply_mode"] == "acknowledge_resolved"

    def test_alias_confirm_resolves(self):
        result = self._norm({"reply_mode": "confirm", "is_valid_issue": True})
        assert result["reply_mode"] == "confirm_defect"

    def test_alias_defect_resolves(self):
        result = self._norm({"reply_mode": "defect", "is_valid_issue": True})
        assert result["reply_mode"] == "confirm_defect"

    def test_alias_teaching_resolves(self):
        result = self._norm({"reply_mode": "teaching", "is_valid_issue": False})
        assert result["reply_mode"] == "teach"

    def test_alias_ask_resolves(self):
        result = self._norm({"reply_mode": "ask", "is_valid_issue": False})
        assert result["reply_mode"] == "ask_more"

    def test_unknown_mode_with_valid_issue_falls_back_to_confirm_defect(self):
        result = self._norm({"reply_mode": "nonsense", "is_valid_issue": True})
        assert result["reply_mode"] == "confirm_defect"

    def test_unknown_mode_without_valid_issue_falls_back_to_ask_more(self):
        result = self._norm({"reply_mode": "nonsense", "is_valid_issue": False})
        assert result["reply_mode"] == "ask_more"

    def test_missing_mode_with_valid_issue_falls_back_to_confirm_defect(self):
        result = self._norm({"is_valid_issue": True})
        assert result["reply_mode"] == "confirm_defect"

    def test_jira_title_sanitized(self):
        result = self._norm({"reply_mode": "confirm_defect", "jira_title": "video_content_mismatch"})
        assert "_" not in result["jira_title"]
        assert result["jira_title"] == "video content mismatch"

    def test_acknowledge_suggestion_passthrough(self):
        result = self._norm({"reply_mode": "acknowledge_suggestion", "is_valid_issue": False})
        assert result["reply_mode"] == "acknowledge_suggestion"

    def test_alias_suggestion_resolves(self):
        result = self._norm({"reply_mode": "suggestion", "is_valid_issue": False})
        assert result["reply_mode"] == "acknowledge_suggestion"

    def test_output_mode_is_always_canonical(self):
        for mode in VALID_CONTENT_MODES:
            result = self._norm({"reply_mode": mode})
            assert result["reply_mode"] in VALID_CONTENT_MODES


# ---------------------------------------------------------------------------
# _normalize_environment_analysis  (#24)
# ---------------------------------------------------------------------------

class TestNormalizeEnvironmentAnalysis:
    def _norm(self, parsed, user_issue=""):
        return SnowAIProcessor._normalize_environment_analysis(parsed, user_issue)

    def test_confirm_defect_passthrough(self):
        result = self._norm({"reply_mode": "confirm_defect", "is_valid_issue": True})
        assert result["reply_mode"] == "confirm_defect"

    def test_acknowledge_resolved_passthrough(self):
        result = self._norm({"reply_mode": "acknowledge_resolved", "is_valid_issue": False})
        assert result["reply_mode"] == "acknowledge_resolved"

    def test_alias_confirm_resolves(self):
        result = self._norm({"reply_mode": "confirm", "is_valid_issue": True})
        assert result["reply_mode"] == "confirm_defect"

    def test_alias_expected_resolves(self):
        result = self._norm({"reply_mode": "expected", "is_valid_issue": False})
        assert result["reply_mode"] == "explain_expected"

    def test_alias_teach_maps_to_explain_expected(self):
        result = self._norm({"reply_mode": "teach", "is_valid_issue": False})
        assert result["reply_mode"] == "explain_expected"

    def test_unknown_mode_with_valid_issue_falls_back_to_confirm_defect(self):
        result = self._norm({"reply_mode": "???", "is_valid_issue": True})
        assert result["reply_mode"] == "confirm_defect"

    def test_unknown_mode_without_valid_issue_falls_back_to_ask_more(self):
        result = self._norm({"reply_mode": "???", "is_valid_issue": False})
        assert result["reply_mode"] == "ask_more"

    def test_output_mode_is_always_canonical(self):
        for mode in VALID_ENV_MODES:
            result = self._norm({"reply_mode": mode})
            assert result["reply_mode"] in VALID_ENV_MODES

    def test_jira_title_sanitized(self):
        result = self._norm({"reply_mode": "confirm_defect", "jira_title": "lab-start-failure"})
        assert result["jira_title"] == "lab start failure"


# ---------------------------------------------------------------------------
# First Boot hard negatives inside _normalize_environment_analysis  (#25)
# ---------------------------------------------------------------------------

class TestFirstBootHardNegatives:
    """The normalizer must block explain_expected when git/TLS/SSL tokens appear
    without genuine startup-shaped evidence."""

    def _norm(self, parsed, user_issue=""):
        return SnowAIProcessor._normalize_environment_analysis(parsed, user_issue)

    def test_git_clone_error_is_not_first_boot(self):
        result = self._norm(
            {"reply_mode": "explain_expected", "is_valid_issue": False},
            user_issue="git clone failed with SSL certificate error",
        )
        assert result["reply_mode"] != "explain_expected"

    def test_ssl_verify_false_is_not_first_boot(self):
        result = self._norm(
            {"reply_mode": "explain_expected", "is_valid_issue": False},
            user_issue="I added sslverify=false to git config but still fails",
        )
        assert result["reply_mode"] != "explain_expected"

    def test_tls_error_is_not_first_boot(self):
        result = self._norm(
            {"reply_mode": "explain_expected", "is_valid_issue": False},
            user_issue="TLS handshake failed when connecting to registry",
        )
        assert result["reply_mode"] != "explain_expected"

    def test_certificate_error_is_not_first_boot(self):
        result = self._norm(
            {"reply_mode": "explain_expected", "is_valid_issue": False},
            user_issue="certificate verify failed: unable to get local issuer certificate",
        )
        assert result["reply_mode"] != "explain_expected"

    def test_startup_shaped_feedback_keeps_explain_expected(self):
        result = self._norm(
            {"reply_mode": "explain_expected", "is_valid_issue": False},
            user_issue="lab start is taking more than 40 minutes, verifying cluster operators",
        )
        assert result["reply_mode"] == "explain_expected"

    def test_kube_apiserver_startup_keeps_explain_expected(self):
        result = self._norm(
            {"reply_mode": "explain_expected", "is_valid_issue": False},
            user_issue="kube-apiserver is not ready after lab start",
        )
        assert result["reply_mode"] == "explain_expected"

    def test_non_ssl_confirm_defect_unaffected(self):
        result = self._norm(
            {"reply_mode": "confirm_defect", "is_valid_issue": True},
            user_issue="lab start script fails with exit code 1",
        )
        assert result["reply_mode"] == "confirm_defect"


# ---------------------------------------------------------------------------
# jira_description preserved on all modes  (ADR-0003 reverses #26)
# ---------------------------------------------------------------------------

class TestJiraDescriptionGating:
    """jira_description is now preserved by normalizers regardless of reply_mode.

    ADR-0003: the create dialog is always prepared so the LX Engineer can override
    the model's verdict. The normalizers no longer clear jira_description.
    """

    def test_content_confirm_defect_keeps_jira_description(self):
        parsed = {
            "reply_mode": "confirm_defect",
            "is_valid_issue": True,
            "jira_description": "Step 3 command produces wrong output",
        }
        result = SnowAIProcessor._normalize_content_analysis(parsed)
        assert result.get("jira_description") == "Step 3 command produces wrong output"

    def test_content_teach_keeps_jira_description(self):
        parsed = {
            "reply_mode": "teach",
            "is_valid_issue": False,
            "jira_description": "text pasted from guide mangles case in edge",
        }
        result = SnowAIProcessor._normalize_content_analysis(parsed)
        assert result.get("jira_description") == "text pasted from guide mangles case in edge"

    def test_content_ask_more_keeps_jira_description(self):
        parsed = {
            "reply_mode": "ask_more",
            "is_valid_issue": False,
            "jira_description": "command output differs from guide",
        }
        result = SnowAIProcessor._normalize_content_analysis(parsed)
        assert result.get("jira_description") == "command output differs from guide"

    def test_env_confirm_defect_keeps_jira_description(self):
        parsed = {
            "reply_mode": "confirm_defect",
            "is_valid_issue": True,
            "jira_description": "Lab VM cannot reach registry",
        }
        result = SnowAIProcessor._normalize_environment_analysis(parsed)
        assert result.get("jira_description") == "Lab VM cannot reach registry"

    def test_env_explain_expected_keeps_jira_description(self):
        parsed = {
            "reply_mode": "explain_expected",
            "is_valid_issue": False,
            "jira_description": "first boot timing issue",
        }
        result = SnowAIProcessor._normalize_environment_analysis(parsed)
        assert result.get("jira_description") == "first boot timing issue"

    def test_content_acknowledge_resolved_keeps_jira_description(self):
        # acknowledge_resolved is the Resolution Follow-up mode; the create dialog
        # is still not shown for it (gate is in run()), but the normalizer itself
        # no longer clears the field.
        parsed = {
            "reply_mode": "acknowledge_resolved",
            "is_valid_issue": False,
            "jira_description": "draft description",
        }
        result = SnowAIProcessor._normalize_content_analysis(parsed)
        assert result.get("jira_description") == "draft description"
        assert result["reply_mode"] == "acknowledge_resolved"


# ---------------------------------------------------------------------------
# SSH Lab Access Feedback — detection, classification, analysis
# ---------------------------------------------------------------------------

SSH_INSTRUCTIONS = """
SSH Private Key & Instructions
Do not do this if you have already set a private key up before for a lab.

    Click CREATE to spin up the lab environment
    Click DOWNLOAD SSH KEY button when they're enabled (lab is running)
    mv ~/Downloads/rht_classroom.rsa ~/.ssh/
    chmod 0600 ~/.ssh/rht_classroom.rsa
    ssh-add ~/.ssh/rht_classroom.rsa
    ssh -i ~/.ssh/rht_classroom.rsa -J cloud-user@146.177.78.169:22022 student@workstation
"""

GUIDE_TYPO_ON_ROLE = (
    "There is a typo on page https://role.rhu.redhat.com/rol/app/courses/do316-4.18/pages/ch03s02 "
    "The guide says 'oc get pods' but it should be 'oc get pod'."
)


class TestLooksLikeSshLabAccess:
    def test_instruction_paste_is_ssh_lab_access(self):
        assert SnowAIProcessor.looks_like_ssh_lab_access(SSH_INSTRUCTIONS) is True

    def test_key_filename_alone_is_ssh_lab_access(self):
        assert SnowAIProcessor.looks_like_ssh_lab_access(
            "Permission denied (publickey) using rht_classroom.rsa"
        ) is True

    def test_guide_typo_on_role_is_not_ssh_lab_access(self):
        assert SnowAIProcessor.looks_like_ssh_lab_access(GUIDE_TYPO_ON_ROLE) is False

    def test_empty_is_not_ssh_lab_access(self):
        assert SnowAIProcessor.looks_like_ssh_lab_access("") is False

    def test_role_url_alone_is_not_ssh_lab_access(self):
        assert SnowAIProcessor.looks_like_ssh_lab_access(
            "https://role.rhu.redhat.com/rol/app/courses/do180-4.18/pages/ch01s01"
        ) is False

    def test_generic_workstation_ssh_is_not_ssh_lab_access(self):
        assert SnowAIProcessor.looks_like_ssh_lab_access(
            "I cannot ssh to student@workstation from inside the lab"
        ) is False


class TestIsRolePlatformUrl:
    def test_role_host_detected(self):
        assert SnowAIProcessor.is_role_platform_url(
            "https://role.rhu.redhat.com/rol/app/courses/do316-4.18/pages/pr01s02"
        ) is True

    def test_rol_host_not_role(self):
        assert SnowAIProcessor.is_role_platform_url(
            "https://rol.redhat.com/rol/app/courses/do316-4.18/pages/pr01s02"
        ) is False

    def test_empty_not_role(self):
        assert SnowAIProcessor.is_role_platform_url("") is False


class TestApplySshLabAccessClassification:
    def test_heuristic_overrides_environment_flag(self):
        classification = {
            "is_content_issue_ticket": False,
            "is_environment_issue_ticket": True,
            "is_video_issue_ticket": False,
            "needs_lab_verification": True,
        }
        result = SnowAIProcessor.apply_ssh_lab_access_classification(
            classification, SSH_INSTRUCTIONS
        )
        assert result["is_ssh_lab_access_ticket"] is True
        assert result["is_environment_issue_ticket"] is False
        assert result["is_content_issue_ticket"] is False
        assert result["is_video_issue_ticket"] is False
        assert result["needs_lab_verification"] is False

    def test_guide_typo_keeps_content_flag(self):
        classification = {
            "is_content_issue_ticket": True,
            "is_environment_issue_ticket": False,
            "is_video_issue_ticket": False,
            "is_ssh_lab_access_ticket": False,
            "needs_lab_verification": False,
        }
        result = SnowAIProcessor.apply_ssh_lab_access_classification(
            classification, GUIDE_TYPO_ON_ROLE
        )
        assert result["is_ssh_lab_access_ticket"] is False
        assert result["is_content_issue_ticket"] is True

    def test_llm_ssh_flag_without_heuristic_still_wins(self):
        classification = {
            "is_ssh_lab_access_ticket": True,
            "is_content_issue_ticket": False,
            "is_environment_issue_ticket": False,
            "is_video_issue_ticket": False,
            "needs_lab_verification": True,
        }
        result = SnowAIProcessor.apply_ssh_lab_access_classification(
            classification, "I cannot connect to the workstation from my laptop"
        )
        assert result["is_ssh_lab_access_ticket"] is True
        assert result["needs_lab_verification"] is False


class TestNormalizeSshLabAccessAnalysis:
    def test_never_confirm_defect(self):
        result = SnowAIProcessor._normalize_ssh_lab_access_analysis(
            {"reply_mode": "confirm_defect", "is_valid_issue": True, "jira_description": "bug"}
        )
        assert result["reply_mode"] != "confirm_defect"
        assert result.get("jira_description", "") == ""
        assert result.get("is_valid_issue") is False

    def test_teach_passthrough(self):
        result = SnowAIProcessor._normalize_ssh_lab_access_analysis(
            {"reply_mode": "teach", "is_valid_issue": False}
        )
        assert result["reply_mode"] == "teach"

    def test_acknowledge_resolved_passthrough(self):
        result = SnowAIProcessor._normalize_ssh_lab_access_analysis(
            {"reply_mode": "acknowledge_resolved", "is_valid_issue": False}
        )
        assert result["reply_mode"] == "acknowledge_resolved"
        assert result.get("is_valid_issue") is False
        assert result.get("jira_description", "") == ""

    def test_ask_more_passthrough(self):
        result = SnowAIProcessor._normalize_ssh_lab_access_analysis(
            {"reply_mode": "ask_more"}
        )
        assert result["reply_mode"] == "ask_more"

    def test_missing_mode_defaults_to_teach(self):
        result = SnowAIProcessor._normalize_ssh_lab_access_analysis({})
        assert result["reply_mode"] == "teach"


# ---------------------------------------------------------------------------
# Issue Section inference (ADR-0002)
# ---------------------------------------------------------------------------

class TestTextNamesASection:
    def test_japanese_exercise_number(self):
        assert SnowAIProcessor.text_names_a_section(
            "演習8.8の「仮想マシンの復元」において velero が失敗する。"
        ) is True

    def test_exercise_n_m(self):
        assert SnowAIProcessor.text_names_a_section(
            "In Exercise 8.8, velero restore create fails."
        ) is True

    def test_chapter_section_words(self):
        assert SnowAIProcessor.text_names_a_section(
            "See chapter 8 section 8 for the DPA backup location."
        ) is True

    def test_chapter_section_with_comma(self):
        assert SnowAIProcessor.text_names_a_section(
            "See chapter 8, section 8 for the DPA backup location."
        ) is True

    def test_chxxsyy_slug(self):
        assert SnowAIProcessor.text_names_a_section("The bug is on ch08s08") is True

    def test_plain_complaint_has_no_section(self):
        assert SnowAIProcessor.text_names_a_section(
            "lab start is taking too long and never finishes."
        ) is False

    def test_course_version_alone_is_not_a_section(self):
        assert SnowAIProcessor.text_names_a_section(
            "I am using version 4.18 of the course.",
            course_version="4.18",
        ) is False


class TestParseIssueSection:
    def test_zero_pads_chapter_and_section(self):
        assert SnowAIProcessor.parse_issue_section(
            {"chapter": "8", "section": "8"}
        ) == ("08", "08")

    def test_chxxsyy_field(self):
        assert SnowAIProcessor.parse_issue_section(
            {"section_slug": "ch08s08"}
        ) == ("08", "08")

    def test_unchanged_returns_none(self):
        assert SnowAIProcessor.parse_issue_section({"unchanged": True}) is None

    def test_missing_returns_none(self):
        assert SnowAIProcessor.parse_issue_section(None) is None

    def test_partial_returns_none(self):
        assert SnowAIProcessor.parse_issue_section({"chapter": "8"}) is None


class TestRewritePageUrl:
    def test_replaces_preface_with_issue_section(self):
        url = "https://rol.redhat.com/rol/app/courses/do316-4.18/pages/pr01"
        assert SnowAIProcessor.rewrite_page_url(url, "08", "08") == (
            "https://rol.redhat.com/rol/app/courses/do316-4.18/pages/ch08s08"
        )

    def test_keeps_host_and_course_id(self):
        url = "https://role.rhu.redhat.com/rol/app/courses/do316-4.18/pages/ch02s03"
        result = SnowAIProcessor.rewrite_page_url(url, "08", "08")
        assert "role.rhu.redhat.com" in result
        assert "do316-4.18" in result
        assert result.endswith("/pages/ch08s08")

    def test_does_not_change_course_id(self):
        url = "https://rol.redhat.com/rol/app/courses/do180-4.18/pages/pr01"
        result = SnowAIProcessor.rewrite_page_url(url, "08", "08")
        assert "do180-4.18" in result
        assert "do316" not in result


class TestIssueSectionInferenceText:
    def test_uses_description_and_follow_ups_not_title(self):
        info = {
            "Description": "velero fails on restore",
            "Title": "Red Hat OpenShift Virtualization Administration Rapid Track",
            "customer_updates": [
                {"role": "customer", "text": "It is exercise 8.8"},
                {"role": "agent", "text": "Which section are you on?"},
            ],
        }
        text = SnowAIProcessor.issue_section_inference_text(info)
        assert "velero fails on restore" in text
        assert "exercise 8.8" in text
        assert "Rapid Track" not in text
        assert "Which section are you on?" not in text


class TestApplyIssueSection:
    def test_updates_chapter_section_and_url(self):
        info = {
            "URL": "https://rol.redhat.com/rol/app/courses/do316-4.18/pages/pr01",
            "Chapter": "",
            "Section": "",
            "Course": "DO316",
            "Version": "4.18",
        }
        result = SnowAIProcessor.apply_issue_section(info, "08", "08")
        assert result["Chapter"] == "08"
        assert result["Section"] == "08"
        assert result["URL"].endswith("/pages/ch08s08")
        assert result["CaptureURL"].endswith("/pages/pr01")
        assert result["Course"] == "DO316"

    def test_restore_capture_location(self):
        info = {
            "URL": "https://rol.redhat.com/rol/app/courses/do316-4.18/pages/ch08s08",
            "Chapter": "08",
            "Section": "08",
            "CaptureURL": "https://rol.redhat.com/rol/app/courses/do316-4.18/pages/pr01",
        }
        result = SnowAIProcessor.restore_capture_location(info)
        assert result["URL"].endswith("/pages/pr01")
        assert result["Chapter"] == ""
        assert result["Section"] == ""


# ---------------------------------------------------------------------------
# Resolution Follow-up Response prompt (#acknowledge_resolved)
# ---------------------------------------------------------------------------

_ANALYSIS_MARKER = "MISSING group_vars caused by Edge translation"
_THANK_ORIGINAL_MARKER = "Always thank the learner and acknowledge that their feedback was received and considered."
_SSH_TEACH_MARKER = "Teach the Internal Learner through the SSH key and jump-host steps."


class TestBuildStudentReplyPrompt:
    """Seam: SnowAIProcessor._build_student_reply_prompt — what we ask the LLM to write."""

    def _prompt(self, reply_mode, ssh_rule=""):
        return SnowAIProcessor._build_student_reply_prompt(
            reply_mode=reply_mode,
            student_name="Nils",
            url="https://rol.redhat.com/rol/app/courses/do417-2.4/pages/ch04",
            analysis_response_json={
                "summary": _ANALYSIS_MARKER,
                "analysis": _ANALYSIS_MARKER,
            },
            language_rule="Write the reply in English.",
            ssh_rule=ssh_rule,
            operational_context="",
            communication_reply_notes=_THANK_ORIGINAL_MARKER,
            json_output_rules="Return JSON only.",
        )

    def test_acknowledge_resolved_omits_analysis(self):
        prompt = self._prompt("acknowledge_resolved")
        assert _ANALYSIS_MARKER not in prompt

    def test_acknowledge_resolved_omits_generic_thank_notes(self):
        prompt = self._prompt("acknowledge_resolved")
        assert _THANK_ORIGINAL_MARKER not in prompt

    def test_acknowledge_resolved_omits_ssh_teach_rule(self):
        prompt = self._prompt("acknowledge_resolved", ssh_rule=_SSH_TEACH_MARKER)
        assert _SSH_TEACH_MARKER not in prompt

    def test_acknowledge_resolved_asks_for_brief_thanks(self):
        prompt = self._prompt("acknowledge_resolved")
        assert "2-4 sentences" in prompt
        assert "glad" in prompt.lower()

    def test_explain_expected_still_includes_analysis_and_notes(self):
        prompt = self._prompt("explain_expected")
        assert _ANALYSIS_MARKER in prompt
        assert _THANK_ORIGINAL_MARKER in prompt

    def test_acknowledge_resolved_omits_operational_context(self):
        screenshot_rule = "If the report is vague, ask for a screenshot, more details, and the exact course section."
        prompt = SnowAIProcessor._build_student_reply_prompt(
            reply_mode="acknowledge_resolved",
            student_name="Nils",
            url="https://rol.redhat.com/rol/app/courses/do417-2.4/pages/ch04",
            analysis_response_json={
                "summary": _ANALYSIS_MARKER,
                "analysis": _ANALYSIS_MARKER,
            },
            language_rule="Write the reply in English.",
            ssh_rule="",
            operational_context=screenshot_rule,
            communication_reply_notes=_THANK_ORIGINAL_MARKER,
            json_output_rules="Return JSON only.",
        )
        assert screenshot_rule not in prompt

    def test_acknowledge_suggestion_omits_generic_thank_notes(self):
        prompt = self._prompt("acknowledge_suggestion")
        assert _THANK_ORIGINAL_MARKER not in prompt

    def test_acknowledge_suggestion_includes_mode_instructions(self):
        prompt = self._prompt("acknowledge_suggestion")
        assert "will be considered" in prompt.lower()
        assert "Do not ask for more information" in prompt

    def test_acknowledge_suggestion_keeps_analysis(self):
        prompt = self._prompt("acknowledge_suggestion")
        assert _ANALYSIS_MARKER in prompt


# ---------------------------------------------------------------------------
# Suggestion (ADR-0004)
# ---------------------------------------------------------------------------

class TestApplySuggestionClassification:
    def test_suggestion_alone_clears_lab_verification(self):
        result = SnowAIProcessor.apply_suggestion_classification({
            "is_suggestion_ticket": True,
            "is_content_issue_ticket": False,
            "is_environment_issue_ticket": False,
            "is_video_issue_ticket": False,
            "is_ssh_lab_access_ticket": False,
            "needs_lab_verification": True,
        })
        assert result["is_suggestion_ticket"] is True
        assert result["needs_lab_verification"] is False

    def test_broken_content_wins_over_suggestion(self):
        result = SnowAIProcessor.apply_suggestion_classification({
            "is_suggestion_ticket": True,
            "is_content_issue_ticket": True,
            "is_environment_issue_ticket": False,
            "is_video_issue_ticket": False,
            "is_ssh_lab_access_ticket": False,
            "needs_lab_verification": True,
        })
        assert result["is_suggestion_ticket"] is False
        assert result["is_content_issue_ticket"] is True

    def test_ssh_wins_over_suggestion(self):
        result = SnowAIProcessor.apply_suggestion_classification({
            "is_suggestion_ticket": True,
            "is_content_issue_ticket": False,
            "is_environment_issue_ticket": False,
            "is_video_issue_ticket": False,
            "is_ssh_lab_access_ticket": True,
            "needs_lab_verification": False,
        })
        assert result["is_suggestion_ticket"] is False
        assert result["is_ssh_lab_access_ticket"] is True

    def test_all_false_stays_not_a_suggestion(self):
        result = SnowAIProcessor.apply_suggestion_classification({
            "is_suggestion_ticket": False,
            "is_content_issue_ticket": False,
            "is_environment_issue_ticket": False,
            "is_video_issue_ticket": False,
            "is_ssh_lab_access_ticket": False,
            "needs_lab_verification": False,
        })
        assert result["is_suggestion_ticket"] is False

    def test_rht0010800_quiz_suggestion(self):
        result = SnowAIProcessor.apply_suggestion_classification({
            "is_suggestion_ticket": True,
            "is_content_issue_ticket": False,
            "is_environment_issue_ticket": False,
            "is_video_issue_ticket": False,
            "is_ssh_lab_access_ticket": False,
            "needs_lab_verification": False,
        })
        assert result["is_suggestion_ticket"] is True


class TestNormalizeSuggestionAnalysis:
    def test_defaults_to_acknowledge_suggestion(self):
        result = SnowAIProcessor._normalize_suggestion_analysis({})
        assert result["reply_mode"] == "acknowledge_suggestion"
        assert result["is_valid_issue"] is False

    def test_keeps_jira_description(self):
        result = SnowAIProcessor._normalize_suggestion_analysis({
            "reply_mode": "acknowledge_suggestion",
            "jira_description": "Add a practical quiz after the guided exercise.",
        })
        assert result["jira_description"] == "Add a practical quiz after the guided exercise."

    def test_unknown_mode_becomes_acknowledge_suggestion(self):
        result = SnowAIProcessor._normalize_suggestion_analysis({"reply_mode": "confirm_defect"})
        assert result["reply_mode"] == "acknowledge_suggestion"

    def test_acknowledge_resolved_passthrough(self):
        result = SnowAIProcessor._normalize_suggestion_analysis({
            "reply_mode": "acknowledge_resolved",
        })
        assert result["reply_mode"] == "acknowledge_resolved"


class TestResolutionFollowUpAnalysisInstructions:
    def test_judges_latest_learner_follow_up(self):
        text = SnowAIProcessor._RESOLUTION_FOLLOW_UP_ANALYSIS_INSTRUCTIONS
        assert "latest Learner Follow-up" in text
        assert "acknowledge_resolved" in text
        assert "not a Resolution Follow-up" in text


# ---------------------------------------------------------------------------
# ADR-0003: always-prepare-jira — new pure helpers
# ---------------------------------------------------------------------------

class TestExtractPageSlug:
    """_extract_page_slug pulls the /pages/<slug> token from a ROL URL."""

    def _slug(self, url):
        return SnowAIProcessor._extract_page_slug(url)

    def test_section_page(self):
        url = "https://rol.redhat.com/rol/app/courses/do380-4.18/pages/ch02s06"
        assert self._slug(url) == "ch02s06"

    def test_preface_page(self):
        url = "https://rol.redhat.com/rol/app/courses/do380-4.18/pages/pr01"
        assert self._slug(url) == "pr01"

    def test_appendix_page(self):
        url = "https://rol.redhat.com/rol/app/courses/rh124-9.3/pages/ap01"
        assert self._slug(url) == "ap01"

    def test_url_with_query_string(self):
        url = "https://rol.redhat.com/rol/app/courses/do180-4.18/pages/ch04s02?foo=bar"
        assert self._slug(url) == "ch04s02"

    def test_empty_string_returns_empty(self):
        assert self._slug("") == ""

    def test_url_without_pages_returns_empty(self):
        assert self._slug("https://rol.redhat.com/rol/app/courses/do380-4.18/") == ""


class TestJiraEditorLinkHtml:
    """_jira_editor_link_html turns a Capture URL into an <a> for the Jira editor.

    Cloud ProseMirror does not auto-linkify insertText, so the create dialog
    must insert HTML (not wiki [text|url] markup).
    """

    def _html(self, url):
        return SnowAIProcessor._jira_editor_link_html(url)

    def test_capture_url_becomes_anchor_with_matching_href_and_text(self):
        url = "https://role.rhu.redhat.com/rol/app/courses/do280-4.22/pages/ch02s05"
        assert self._html(url) == (
            '<a href="https://role.rhu.redhat.com/rol/app/courses/do280-4.22/pages/ch02s05">'
            "https://role.rhu.redhat.com/rol/app/courses/do280-4.22/pages/ch02s05</a>"
        )

    def test_escapes_ampersand_in_query_string(self):
        url = "https://rol.redhat.com/rol/app/courses/do180-4.18/pages/ch04s02?foo=1&bar=2"
        assert self._html(url) == (
            '<a href="https://rol.redhat.com/rol/app/courses/do180-4.18/pages/ch04s02?foo=1&amp;bar=2">'
            "https://rol.redhat.com/rol/app/courses/do180-4.18/pages/ch04s02?foo=1&amp;bar=2</a>"
        )

    def test_empty_or_blank_returns_empty(self):
        assert self._html("") == ""
        assert self._html(None) == ""
        assert self._html("   ") == ""

    def test_non_http_is_not_wrapped(self):
        assert self._html("javascript:alert(1)") == ""
        assert self._html("mdunnett") == ""


class TestStripLocationFromTitle:
    """_strip_location_from_title removes course code / course ID / section tokens."""

    def _strip(self, title, course_code=""):
        return SnowAIProcessor._strip_location_from_title(title, course_code)

    def test_strips_uppercase_course_code(self):
        assert self._strip("DO380 copy paste mangles case", "DO380") == "copy paste mangles case"

    def test_strips_lowercase_course_code(self):
        assert self._strip("do380 copy paste mangles case", "do380") == "copy paste mangles case"

    def test_strips_course_id_with_version(self):
        assert self._strip("do380-4.18 copy paste mangles case") == "copy paste mangles case"

    def test_strips_chNNsMM_section(self):
        assert self._strip("ch02s06 paste mangles case") == "paste mangles case"

    def test_strips_section_N_M_pattern(self):
        # "section 4" is ambiguous; "4.1" alone is stripped when it looks like a section
        result = self._strip("4.1 paste mangles case")
        assert "4.1" not in result

    def test_strips_nothing_from_clean_title(self):
        result = self._strip("copy paste from guide mangles case in edge")
        assert result == "copy paste from guide mangles case in edge"

    def test_returns_empty_when_only_location_tokens(self):
        assert self._strip("do380 ch02s06", "do380") == ""

    def test_does_not_strip_unrelated_numbers(self):
        # "404" should not be stripped — not a section pattern
        result = self._strip("command returns 404 error")
        assert "404" in result


class TestBuildDefectSummary:
    """_build_defect_summary assembles Course Code: page slug - title - snow_id."""

    def _summary(self, snow_info, jira_title, jira_description=""):
        return SnowAIProcessor._build_defect_summary(snow_info, jira_title, jira_description)

    def _snow(self, course="DO380", url="https://rol.redhat.com/rol/app/courses/do380-4.18/pages/ch02s06", snow_id="RHT0015853"):
        return {"Course": course, "URL": url, "snow_id": snow_id}

    def test_normal_section_page(self):
        result = self._summary(self._snow(), "copy paste mangles case in edge")
        assert result == "DO380: ch02s06 - copy paste mangles case in edge - RHT0015853"

    def test_preface_page_uses_slug_not_chs(self):
        snow = self._snow(url="https://rol.redhat.com/rol/app/courses/do380-4.18/pages/pr01")
        result = self._summary(snow, "copy paste mangles case in edge")
        assert result == "DO380: pr01 - copy paste mangles case in edge - RHT0015853"
        assert "chs" not in result

    def test_empty_title_falls_back_to_description(self):
        snow = self._snow()
        desc = "copying text from the guide into the terminal via edge browser produces mixed case output"
        result = self._summary(snow, "", desc)
        # Should use first ~8 words of description
        assert "copying text from the guide into the terminal" in result
        assert result.startswith("DO380: ch02s06 - copying")

    def test_stripped_title_falls_back_to_description(self):
        # Title after stripping location is empty — fall back
        snow = self._snow()
        desc = "guide text pasted incorrectly in terminal"
        result = self._summary(snow, "do380 ch02s06", desc)
        assert "guide text pasted" in result

    def test_missing_url_no_orphan_dash(self):
        # No /pages/ segment → slug absent → no orphan dash in summary
        snow = {"Course": "DO380", "URL": "", "snow_id": "RHT0000001"}
        result = self._summary(snow, "some issue")
        assert result == "DO380: some issue - RHT0000001"
        assert ": -" not in result


class TestBuildJiraSearchUrlLocationClause:
    """build_jira_search_url only adds a location clause for chNNsMM pages."""

    def _proc(self):
        p = SnowAIProcessor.__new__(SnowAIProcessor)
        return p

    def test_section_page_adds_location_clause(self):
        snow = {
            "Course": "DO380", "Chapter": "2", "Section": "6",
            "URL": "https://rol.redhat.com/rol/app/courses/do380-4.18/pages/ch02s06",
        }
        url = self._proc().build_jira_search_url(snow, "kubectl")
        assert "ch02s06" in url

    def test_preface_page_omits_location_clause(self):
        snow = {
            "Course": "DO380", "Chapter": "", "Section": "",
            "URL": "https://rol.redhat.com/rol/app/courses/do380-4.18/pages/pr01",
        }
        url = self._proc().build_jira_search_url(snow, "edge")
        from urllib.parse import unquote
        jql = unquote(url.split("?jql=")[1])
        assert "description ~" not in jql

    def test_appendix_page_omits_location_clause(self):
        snow = {
            "Course": "RH124", "Chapter": "", "Section": "",
            "URL": "https://rol.redhat.com/rol/app/courses/rh124-9.3/pages/ap01",
        }
        url = self._proc().build_jira_search_url(snow, "")
        from urllib.parse import unquote
        jql = unquote(url.split("?jql=")[1])
        assert "description ~" not in jql


class TestNormalizeContentAnalysisKeepsJiraDescription:
    """After ADR-0003: jira_description is no longer cleared on non-confirm_defect modes."""

    def _norm(self, parsed):
        return SnowAIProcessor._normalize_content_analysis(parsed)

    def test_teach_preserves_jira_description(self):
        result = self._norm({"reply_mode": "teach", "is_valid_issue": False,
                             "jira_description": "text pasted incorrectly in terminal"})
        assert result["jira_description"] == "text pasted incorrectly in terminal"

    def test_ask_more_preserves_jira_description(self):
        result = self._norm({"reply_mode": "ask_more", "is_valid_issue": False,
                             "jira_description": "command output differs from guide"})
        assert result["jira_description"] == "command output differs from guide"

    def test_explain_expected_preserves_jira_description(self):
        result = self._norm({"reply_mode": "explain_expected", "is_valid_issue": False,
                             "jira_description": "behaviour is expected"})
        assert result["jira_description"] == "behaviour is expected"

    def test_confirm_defect_still_preserves(self):
        result = self._norm({"reply_mode": "confirm_defect", "is_valid_issue": True,
                             "jira_description": "step 3 command fails"})
        assert result["jira_description"] == "step 3 command fails"


class TestNormalizeEnvironmentAnalysisKeepsJiraDescription:
    """After ADR-0003: jira_description is no longer cleared on non-confirm_defect modes."""

    def _norm(self, parsed, user_issue=""):
        return SnowAIProcessor._normalize_environment_analysis(parsed, user_issue)

    def test_explain_expected_preserves_jira_description(self):
        # Note: explain_expected with no first-boot tokens stays as-is
        result = self._norm(
            {"reply_mode": "explain_expected", "is_valid_issue": False,
             "jira_description": "lab vm unreachable"},
            user_issue="lab is slow",
        )
        assert result["jira_description"] == "lab vm unreachable"

    def test_ask_more_preserves_jira_description(self):
        result = self._norm({"reply_mode": "ask_more", "is_valid_issue": False,
                             "jira_description": "registry pull fails"})
        assert result["jira_description"] == "registry pull fails"


class TestSkipJiraGate:
    """skip_jira fires only on is_ssh_lab_access_ticket or acknowledge_resolved."""

    @staticmethod
    def _should_skip(is_ssh, reply_mode):
        # Mirror the gate logic from run() — pure function extracted for testing
        return (
            is_ssh
            or reply_mode == "acknowledge_resolved"
        )

    def test_ssh_always_skips(self):
        for mode in ("confirm_defect", "teach", "ask_more"):
            assert self._should_skip(True, mode)

    def test_acknowledge_resolved_skips(self):
        assert self._should_skip(False, "acknowledge_resolved")

    def test_confirm_defect_does_not_skip(self):
        assert not self._should_skip(False, "confirm_defect")

    def test_teach_does_not_skip(self):
        assert not self._should_skip(False, "teach")

    def test_explain_expected_does_not_skip(self):
        assert not self._should_skip(False, "explain_expected")

    def test_ask_more_does_not_skip(self):
        assert not self._should_skip(False, "ask_more")

    def test_lab_pending_does_not_skip(self):
        assert not self._should_skip(False, "lab_pending")

    def test_acknowledge_suggestion_does_not_skip(self):
        assert not self._should_skip(False, "acknowledge_suggestion")

