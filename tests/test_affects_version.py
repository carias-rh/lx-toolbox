"""Unit tests for Jira Affects version LLM selection helpers."""

import json

from lx_toolbox.core.affects_version import (
    build_latest_released_prompt,
    build_prefer_platform_prompt,
    filter_released_affects_version_options,
    parse_affects_version_choice,
    pick_affects_version_with_llm,
)


class TestFilterReleasedAffectsVersionOptions:
    def test_drops_unreleased_group_and_keeps_released(self):
        raw = [
            {"text": "DO288 - RHOCP4.x NEXT", "group": "Unreleased"},
            {"text": "DO288 - RHOCP4.18-en-1-20250731", "group": "Released"},
            {"text": "DO288 - OCP4.14-en-1-20240529", "group": "Released"},
            {"text": "RH124 - 9.3", "group": "Released"},
        ]
        assert filter_released_affects_version_options(raw, "DO288") == [
            "DO288 - RHOCP4.18-en-1-20250731",
            "DO288 - OCP4.14-en-1-20240529",
        ]

    def test_empty_course_code_returns_empty(self):
        assert filter_released_affects_version_options([{"text": "X", "group": ""}], "") == []


class TestParseAffectsVersionChoice:
    def test_accepts_exact_label_from_list(self):
        allowed = ["DO288 - RHOCP4.18-en-1-20250731", "DO288 - OCP4.14-en-1-20240529"]
        response = json.dumps(
            {
                "affects_version": "DO288 - RHOCP4.18-en-1-20250731",
                "reason": "newest date",
            }
        )
        assert parse_affects_version_choice(response, allowed) == allowed[0]

    def test_rejects_unknown_label(self):
        allowed = ["DO288 - RHOCP4.18-en-1-20250731"]
        response = json.dumps({"affects_version": "DO288 - made up", "reason": "nope"})
        assert parse_affects_version_choice(response, allowed) is None


class TestPickAffectsVersionWithLlm:
    def test_delegates_to_parser(self):
        allowed = ["DO288 - RHOCP4.18-en-1-20250731"]
        payload = json.dumps({"affects_version": allowed[0], "reason": "latest"})

        def fake_llm(prompt: str) -> str:
            assert "DO288" in prompt
            assert allowed[0] in prompt
            return payload

        assert (
            pick_affects_version_with_llm(
                fake_llm, "latest_released", "do288", allowed
            )
            == allowed[0]
        )

    def test_prefer_platform_mode_uses_platform_in_prompt(self):
        allowed = ["DO288 - RHOCP4.18-en-1-20250731"]
        payload = json.dumps({"affects_version": allowed[0], "reason": "match"})

        def fake_llm(prompt: str) -> str:
            assert "4.18" in prompt
            assert "prefer" not in prompt.lower() or "platform" in prompt.lower()
            return payload

        pick_affects_version_with_llm(
            fake_llm,
            "prefer_platform",
            "DO288",
            allowed,
            platform_version="4.18",
        )


class TestPromptBuilders:
    def test_latest_prompt_lists_options(self):
        opts = ["DO288 - A", "DO288 - B"]
        prompt = build_latest_released_prompt("DO288", opts)
        assert "1. DO288 - A" in prompt
        assert "newest" in prompt.lower()

    def test_prefer_platform_prompt_includes_version(self):
        prompt = build_prefer_platform_prompt("DO288", "4.18", ["DO288 - A"])
        assert "4.18" in prompt
