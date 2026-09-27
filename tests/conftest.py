import sys
from pathlib import Path

import pytest

from citrus_agent.config import Config, Project
from citrus_agent.runtime import JobContext
from citrus_agent.state import State


@pytest.fixture
def job():
    return {
        "id": "J1",
        "agent_id": "A1",
        "owner_id": "user-1",
        "session_id": "S1",
        "project_id": "demo",
        "runtime": "codex",
        "prompt": "설명해줘",
        "lease_token": "lease-one",
        "lease_seconds": 60,
    }


@pytest.fixture
def config(tmp_path):
    project = tmp_path / "project with spaces 한글"
    project.mkdir()
    return Config(
        hub_url="http://127.0.0.1:8000",
        name="test-pc",
        projects={"demo": Project(str(project), policy="development")},
        approval_timeout_seconds=0.1,
    )


@pytest.fixture
def credentials():
    return {
        "agent_id": "A1",
        "owner_id": "user-1",
        "token": "device-token",
        "hub_url": "http://127.0.0.1:8000",
    }


@pytest.fixture
def state(tmp_path):
    instance = State(tmp_path / "state", ("device-token",))
    yield instance
    instance.close()


@pytest.fixture
def context(job, config, state):
    return JobContext(job, config.projects["demo"], config, state)


@pytest.fixture
def fake_codex():
    return [sys.executable, str(Path(__file__).parent / "fake_codex.py")]
