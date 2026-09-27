import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from citrus_agent.agent import Agent
from citrus_agent.runtime import CodexRuntime
from citrus_agent.transport import HubClient, HubError


def test_full_http_hub_to_codex_and_durable_result(tmp_path, config, credentials, job, fake_codex):
    delivered = []
    claimed = False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            nonlocal claimed
            assert self.headers["Authorization"] == "Bearer device-token"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/jobs/claim"):
                response = {"job": None if claimed else job}
                claimed = True
            elif self.path.endswith("/events"):
                delivered.extend(body["events"])
                response = {"acknowledged": [e["id"] for e in body["events"]]}
            elif self.path.endswith("/heartbeat"):
                active = body["active_job"]
                response = {
                    "controls": [],
                    "lease": {"job_id": active["id"], "valid": True, "seconds": 60}
                    if active
                    else None,
                }
            else:
                response = {}
            payload = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config.hub_url = f"http://127.0.0.1:{server.server_port}"

    async def scenario():
        agent = Agent(
            tmp_path, config, credentials, runtime_factory=lambda *_: CodexRuntime(fake_codex)
        )
        task = asyncio.create_task(agent.run())
        try:
            async with asyncio.timeout(10):
                while not any(e["type"] == "completed" for e in delivered):
                    await asyncio.sleep(0.02)
            assert any(e["data"].get("text") == "테스트 결과입니다" for e in delivered)
        finally:
            agent.stop()
            await task

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_redirect_does_not_forward_device_token():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.send_response(307)
            self.send_header("Location", "https://attacker.invalid/")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        hub = HubClient(f"http://127.0.0.1:{server.server_port}", "token")
        with pytest.raises(HubError) as error:
            hub.request("/test", {})
        assert error.value.status == 307
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
