import asyncio
import contextlib

import pytest

from citrus_agent.runtime import CodexRuntime


def test_codex_real_subprocess_and_resume(context, fake_codex):
    async def scenario():
        result = await CodexRuntime(fake_codex).run(context)
        assert result["text"] == "테스트 결과입니다"
        assert context.provider_id == "provider-1"
        assert (await CodexRuntime(fake_codex).run(context))["text"] == result["text"]

    asyncio.run(scenario())


@pytest.mark.parametrize("prompt", ["approval", "input"])
def test_codex_approval_and_input_roundtrip(context, fake_codex, prompt):
    async def scenario():
        context.config.approval_timeout_seconds = 5
        context.job["prompt"] = prompt
        task = asyncio.create_task(CodexRuntime(fake_codex).run(context))
        async with asyncio.timeout(5):
            while not context.requests:
                await asyncio.sleep(0.01)
            identifier, (digest, _) = next(iter(context.requests.items()))
            control = {
                "job_id": "J1",
                "owner_id": "user-1",
                "request_id": identifier,
                "digest": digest,
                "type": "approval" if prompt == "approval" else "input",
                "decision": "allow",
                "answers": {"q1": ["개발"]},
            }
            assert not context.control({**control, "owner_id": "intruder"})
            assert not context.control({**control, "digest": "bad"})
            assert not context.control(
                {**control, "type": "input" if prompt == "approval" else "approval"}
            )
            assert context.control(control)
            assert not context.control(control)
            assert (await task)["text"] == "테스트 결과입니다"
            assert not context.control(control)

    asyncio.run(scenario())


def test_approval_timeout_closes_request(context):
    async def scenario():
        assert (await context.ask("approval_required", {"command": "test"}))["decision"] == "deny"
        assert not context.requests
        assert context.state.pending()[-1]["type"] == "request_closed"

    asyncio.run(scenario())


def test_read_only_does_not_request_elevation(context, fake_codex):
    context.project.policy = "read-only"
    context.job["prompt"] = "approval"
    asyncio.run(CodexRuntime(fake_codex).run(context))
    assert "approval_required" not in [e["type"] for e in context.state.pending()]


def test_cancel_terminates_process(context, fake_codex):
    async def scenario():
        context.job["prompt"] = "hang"
        runtime = CodexRuntime(fake_codex)
        task = asyncio.create_task(runtime.run(context))
        async with asyncio.timeout(5):
            while not runtime.turn_id:
                await asyncio.sleep(0.01)
            context.cancel("test")
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            assert runtime.process.returncode is not None

    asyncio.run(scenario())


@pytest.mark.parametrize("prompt", ["exit", "malformed"])
def test_codex_protocol_failure_is_not_success(context, fake_codex, prompt):
    context.job["prompt"] = prompt
    with pytest.raises((RuntimeError, ValueError)):
        asyncio.run(CodexRuntime(fake_codex).run(context))
