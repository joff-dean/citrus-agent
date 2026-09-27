import pytest

from citrus_agent.cli import enroll
from citrus_agent.secrets import CredentialStore


def test_enrollment_requires_local_owner_confirmation(tmp_path, config, monkeypatch):
    calls = []

    class Hub:
        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, path, body):
            calls.append((path, body))
            if path == "/v1/enrollments":
                return {
                    "id": "E1",
                    "device_code": "private",
                    "user_code": "ABCD",
                    "expires_in": 60,
                    "interval_seconds": 1,
                }
            if path.endswith("/poll"):
                return {"status": "matched", "owner": {"id": "user1", "display_name": "홍길동"}}
            if path.endswith("/confirm"):
                return {"agent_id": "A1", "owner_id": "user1", "token": "device-token"}
            return {}

    monkeypatch.setattr("citrus_agent.cli.HubClient", Hub)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    enroll(tmp_path, config)
    assert calls[-1][0].endswith("/cancel")
    assert not (tmp_path / "credentials.json").exists()
    monkeypatch.setattr("builtins.input", lambda _: "y")
    enroll(tmp_path, config)
    assert calls[-1][0].endswith("/confirm")
    assert CredentialStore(tmp_path).load()["owner_id"] == "user1"


def test_enrollment_owner_cannot_change_at_confirmation(tmp_path, config, monkeypatch):
    class Hub:
        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, path, body):
            if path == "/v1/enrollments":
                return {"id": "E1", "device_code": "private", "user_code": "ABCD", "expires_in": 60}
            if path.endswith("/poll"):
                return {"status": "matched", "owner": {"id": "user1", "display_name": "홍길동"}}
            return {"agent_id": "A1", "owner_id": "attacker", "token": "device-token"}

    monkeypatch.setattr("citrus_agent.cli.HubClient", Hub)
    monkeypatch.setattr("builtins.input", lambda _: "y")
    with pytest.raises(ValueError):
        enroll(tmp_path, config)
    assert not (tmp_path / "credentials.json").exists()
