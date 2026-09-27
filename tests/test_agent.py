import asyncio
import time

import pytest

from citrus_agent.agent import Agent
from citrus_agent.transport import HubError


class FakeHub:
    def __init__(self, job):
        self.job = job
        self.delivered = []
        self.controls = []
        self.failure = None

    async def post(self, path, body, timeout=35):
        if self.failure:
            raise self.failure
        if path.endswith("/jobs/claim"):
            return {"job": self.job}
        if path.endswith("/events"):
            self.delivered.extend(body["events"])
            return {"acknowledged": [e["id"] for e in body["events"]]}
        if path.endswith("/heartbeat"):
            active = body.get("active_job")
            return {
                "lease": {"job_id": active["id"], "valid": True, "seconds": 60} if active else None,
                "controls": self.controls,
            }
        return {}


class Runtime:
    calls = 0

    async def run(self, ctx):
        self.calls += 1
        ctx.save_session("provider-1")
        return {"text": "done"}


def test_duplicate_claim_not_executed(tmp_path, config, credentials, job):
    async def scenario():
        hub, runtime = FakeHub(job), Runtime()
        agent = Agent(tmp_path, config, credentials, hub, lambda *_: runtime)
        try:
            await agent.claim_once()
            await agent.active_task
            await agent.claim_once()
            assert runtime.calls == 1
            await agent.flush_once()
            assert not agent.state.pending()
            assert "completed" in [e["type"] for e in hub.delivered]
        finally:
            agent.state.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        {"owner_id": "other"},
        {"project_id": "missing"},
        {"runtime": "shell"},
        {"agent_id": "other"},
        {"lease_seconds": 0},
    ],
)
def test_rejected_job_never_launches(tmp_path, config, credentials, job, change):
    async def scenario():
        runtime = Runtime()
        agent = Agent(tmp_path, config, credentials, FakeHub({**job, **change}), lambda *_: runtime)
        try:
            await agent.claim_once()
            assert runtime.calls == 0
            assert agent.state.pending()[0]["type"] == "rejected"
        finally:
            agent.state.close()

    asyncio.run(scenario())


def test_disconnected_outbox_retries_same_event_ids(tmp_path, config, credentials, job):
    async def scenario():
        hub = FakeHub(job)
        agent = Agent(tmp_path, config, credentials, hub)
        try:
            agent.state.emit("J1", "message", {"text": "result"})
            before = agent.state.pending()[0]["id"]
            hub.failure = HubError(503, "offline")
            with pytest.raises(HubError):
                await agent.flush_once()
            assert agent.state.pending()[0]["id"] == before
            hub.failure = None
            await agent.flush_once()
            assert hub.delivered[0]["id"] == before
        finally:
            agent.state.close()

    asyncio.run(scenario())


def test_revoked_auth_stops_agent(tmp_path, config, credentials, job):
    async def scenario():
        hub = FakeHub(job)
        hub.failure = HubError(401, "revoked")
        agent = Agent(tmp_path, config, credentials, hub)
        try:
            await agent.loop(agent.heartbeat_once, 1)
            assert agent.stopping.is_set()
        finally:
            agent.state.close()

    asyncio.run(scenario())


def test_lease_expiry_cancels_execution(tmp_path, config, credentials, job):
    class WaitingRuntime:
        async def run(self, ctx):
            await asyncio.Event().wait()

    async def scenario():
        agent = Agent(tmp_path, config, credentials, FakeHub(job), lambda *_: WaitingRuntime())
        try:
            await agent.claim_once()
            await asyncio.sleep(0)
            agent.lease_deadline = time.monotonic() - 1
            watchdog = asyncio.create_task(agent.watchdog())
            await asyncio.wait_for(agent.active_task, 3)
            agent.stop()
            await watchdog
            assert agent.state.pending()[-1]["data"]["reason"] == "lease_expired"
        finally:
            agent.state.close()

    asyncio.run(scenario())


def test_timeout_does_not_mark_success(tmp_path, config, credentials, job):
    class WaitingRuntime:
        async def run(self, ctx):
            await asyncio.Event().wait()

    async def scenario():
        config.job_timeout_seconds = 0.01
        agent = Agent(tmp_path, config, credentials, FakeHub(job), lambda *_: WaitingRuntime())
        try:
            await agent.claim_once()
            await agent.active_task
            assert agent.state.pending()[-1]["type"] == "cancelled"
        finally:
            agent.state.close()

    asyncio.run(scenario())
