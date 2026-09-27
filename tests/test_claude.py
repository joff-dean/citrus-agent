import asyncio

import pytest

from citrus_agent.runtime import ClaudeRuntime


def test_claude_sdk_options_permissions_and_session(context, monkeypatch):
    sdk = pytest.importorskip("claude_agent_sdk")
    captured = []

    class FakeClient:
        def __init__(self, options):
            self.options = options
            captured.append(options)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def query(self, prompt):
            assert prompt == context.job["prompt"]

        async def receive_response(self):
            yield sdk.SystemMessage(subtype="init", data={"session_id": "claude-session"})
            yield sdk.AssistantMessage(content=[sdk.TextBlock(text="설명")], model="fake")
            yield sdk.ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="claude-session",
                result="완료",
            )

    monkeypatch.setattr(sdk, "ClaudeSDKClient", FakeClient)

    async def scenario():
        context.project.policy = "read-only"
        context.job["runtime"] = "claude"
        assert (await ClaudeRuntime().run(context))["text"] == "완료"
        assert context.provider_id == "claude-session"
        options = captured[0]
        assert "Bash" not in options.tools
        assert options.setting_sources == []
        assert options.strict_mcp_config
        hook = options.hooks["PreToolUse"][0].hooks[0]
        denied = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": "../secret"}}, None, {}
        )
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
        denied = await hook(
            {"tool_name": "Bash", "tool_input": {"command": "echo hello"}}, None, {}
        )
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
        allowed = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": "README.md"}}, None, {}
        )
        assert allowed == {}
        await ClaudeRuntime().run(context)
        assert captured[1].resume == "claude-session"

    asyncio.run(scenario())


def test_claude_development_requires_explicit_tool_approval(context, monkeypatch):
    sdk = pytest.importorskip("claude_agent_sdk")
    captured = []

    class FakeClient:
        def __init__(self, options):
            captured.append(options)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def query(self, prompt):
            pass

        async def receive_response(self):
            yield sdk.ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="claude-session",
            )

    monkeypatch.setattr(sdk, "ClaudeSDKClient", FakeClient)

    async def scenario():
        context.job["runtime"] = "claude"
        await ClaudeRuntime().run(context)
        options = captured[0]
        hook = options.hooks["PreToolUse"][0].hooks[0]
        denied = await hook(
            {"tool_name": "Bash", "tool_input": {"command": "rm -rf example"}}, None, {}
        )
        assert denied["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert any(e["type"] == "approval_required" for e in context.state.pending())
        result = await options.can_use_tool("AskUserQuestion", {"questions": []}, None)
        assert result.behavior == "deny"

    asyncio.run(scenario())
