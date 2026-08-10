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
