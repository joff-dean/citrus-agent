import os

import pytest

from citrus_agent.cli import dispatch, parser
from citrus_agent.config import Config, Project, validate_hub, validate_id
from citrus_agent.locking import InstanceLock
from citrus_agent.secrets import CredentialStore
from citrus_agent.state import sanitize


@pytest.mark.parametrize(
    "url",
    [
        "http://internal.example",
        "https://u:p@hub.example",
        "https://hub.example?token=x",
        "file:///tmp/hub",
    ],
)
def test_bad_hub_urls(url):
    with pytest.raises(ValueError):
        validate_hub(url)


def test_allowed_urls():
    assert validate_hub("https://hub.example/api/") == "https://hub.example/api"
    assert validate_hub("http://127.0.0.1:1234")


@pytest.mark.parametrize("identifier", ["../x", "a/b", "", "x\ny", "a?token=x"])
def test_reject_path_ids(identifier):
    with pytest.raises(ValueError):
        validate_id(identifier)


def test_config_roundtrip(tmp_path, config):
    config.save(tmp_path)
    assert Config.load(tmp_path) == config


def test_project_validation(tmp_path):
    with pytest.raises(ValueError):
        Project(str(tmp_path), policy="unrestricted").validate()


def test_enrollment_secret_roundtrip(tmp_path, credentials):
    store = CredentialStore(tmp_path)
    store.save(credentials)
    assert store.load() == credentials
    if os.name == "nt":
        assert "device-token" not in store.path.read_text()
    else:
        assert store.path.stat().st_mode & 0o777 == 0o600
        store.path.chmod(0o644)
        with pytest.raises(ValueError):
            store.load()


def test_single_instance_lock(tmp_path):
    with InstanceLock(tmp_path):
        with pytest.raises(ValueError):
            with InstanceLock(tmp_path):
                pass
    with InstanceLock(tmp_path):
        pass


def test_durable_jobs_and_outbox(state, job):
    assert state.start(job)
    assert not state.start(job)
    state.finish(job["id"], "completed", {"text": "device-token"})
    event = state.pending()[0]
    assert event["data"]["text"] == "[REDACTED]"
    assert state.pending()[0]["id"] == event["id"]
    state.acknowledge([event["id"]])
    assert not state.pending()
    assert not state.start(job)


def test_restart_never_reexecutes_uncertain_job(state, job):
    state.start(job)
    state.recover()
    assert state.pending()[0]["type"] == "interrupted"
    assert not state.start(job)


def test_claim_request_id_stable_until_durable_receipt(state):
    before = state.claim_id()
    assert state.claim_id() == before
    state.clear_claim()
    assert state.claim_id() != before


def test_session_owner_project_runtime_binding(state):
    state.save_session("S", "u", "p", "codex", "provider")
    assert state.session("S", "u", "p", "codex") == "provider"
    for binding in [("other", "p", "codex"), ("u", "other", "codex"), ("u", "p", "claude")]:
        with pytest.raises(ValueError):
            state.session("S", *binding)


def test_redaction():
    result = sanitize({"api_key": "sensitive", "text": "Bearer abc123 sk-" + "a" * 20})
    assert result["api_key"] == "[REDACTED]"
    assert "abc123" not in result["text"]


def test_cli_initialization_and_project(tmp_path):
    home = tmp_path / "home"
    root = parser()
    assert (
        dispatch(root.parse_args(["--home", str(home), "init", "--hub", "https://hub.test"])) == 0
    )
    args = ["--home", str(home), "project", "add", "sample", str(tmp_path), "--runtime", "codex"]
    assert dispatch(root.parse_args(args)) == 0
    config = Config.load(home)
    assert config.projects["sample"].path == str(tmp_path.resolve())
    assert config.projects["sample"].policy == "read-only"


def test_enrolled_hub_cannot_change(tmp_path, config, credentials):
    config.save(tmp_path)
    CredentialStore(tmp_path).save(credentials)
    args = parser().parse_args(["--home", str(tmp_path), "init", "--hub", "https://other.test"])
    with pytest.raises(ValueError):
        dispatch(args)


def test_unknown_policy_is_not_loaded_as_valid(tmp_path):
    project = Project(str(tmp_path), policy="root")
    with pytest.raises(ValueError):
        project.validate()
