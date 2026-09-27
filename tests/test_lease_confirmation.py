import asyncio

from citrus_agent.agent import Agent


def test_expired_replayed_claim_cannot_start_runtime(tmp_path, config, credentials, job):
    class Hub:
        async def post(self, path, body, timeout=35):
            if path.endswith("/jobs/claim"):
                return {"job": job}
            return {"lease": {"job_id": job["id"], "valid": False}, "controls": []}

    def forbidden(*_):
        raise AssertionError("Runtime must never be constructed for an expired claim")

    async def scenario():
        agent = Agent(tmp_path, config, credentials, Hub(), forbidden)
        try:
            await agent.claim_once()
            assert agent.active is None
            assert agent.active_task is None
            assert agent.state.pending()[-1]["data"]["reason"] == "lease_rejected"
        finally:
            agent.state.close()

    asyncio.run(scenario())
