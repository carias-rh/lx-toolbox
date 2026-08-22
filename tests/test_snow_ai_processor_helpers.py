"""Unit tests for SnowAIProcessor static helper methods.

Seam: SnowAIProcessor static/class methods called directly without instantiation.
"""

import pytest
from lx_toolbox.core.snow_ai_processor import SnowAIProcessor

VALID_CONTENT_MODES = {"confirm_defect", "teach", "ask_more", "lab_pending", "explain_expected"}
VALID_ENV_MODES = {"confirm_defect", "explain_expected", "ask_more", "lab_pending"}


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
# jira_description cleared on non-defect modes  (#26)
# ---------------------------------------------------------------------------

class TestJiraDescriptionGating:
    """jira_description must be cleared by normalizers when reply_mode != confirm_defect."""

    def test_content_confirm_defect_keeps_jira_description(self):
        parsed = {
            "reply_mode": "confirm_defect",
            "is_valid_issue": True,
            "jira_description": "Step 3 command produces wrong output",
        }
        result = SnowAIProcessor._normalize_content_analysis(parsed)
        assert result.get("jira_description") == "Step 3 command produces wrong output"

    def test_content_teach_clears_jira_description(self):
        parsed = {
            "reply_mode": "teach",
            "is_valid_issue": False,
            "jira_description": "should be cleared",
        }
        result = SnowAIProcessor._normalize_content_analysis(parsed)
        assert result.get("jira_description", "") == ""

    def test_content_ask_more_clears_jira_description(self):
        parsed = {
            "reply_mode": "ask_more",
            "is_valid_issue": False,
            "jira_description": "should be cleared",
        }
        result = SnowAIProcessor._normalize_content_analysis(parsed)
        assert result.get("jira_description", "") == ""

    def test_env_confirm_defect_keeps_jira_description(self):
        parsed = {
            "reply_mode": "confirm_defect",
            "is_valid_issue": True,
            "jira_description": "Lab VM cannot reach registry",
        }
        result = SnowAIProcessor._normalize_environment_analysis(parsed)
        assert result.get("jira_description") == "Lab VM cannot reach registry"

    def test_env_explain_expected_clears_jira_description(self):
        parsed = {
            "reply_mode": "explain_expected",
            "is_valid_issue": False,
            "jira_description": "should be cleared",
        }
        result = SnowAIProcessor._normalize_environment_analysis(parsed)
        assert result.get("jira_description", "") == ""


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


