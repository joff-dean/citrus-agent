from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


def data_dir() -> Path:
    override = os.environ.get("CITRUS_AGENT_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"]) / "CitrusAgent"
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "citrus-agent"


def private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)


def atomic_write(path: Path, content: str) -> None:
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def validate_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
        raise ValueError(
            "ID must contain 1–128 ASCII letters, numbers, dots, underscores or hyphens"
        )
    return value


def validate_hub(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Hub URL cannot contain credentials, query parameters or fragments")
    if not parsed.hostname:
        raise ValueError("Hub URL requires a hostname")
    local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not local_http:
        raise ValueError("Hub requires HTTPS (HTTP is allowed only on loopback for development)")
    return url.rstrip("/")


@dataclass
class Project:
    path: str
    runtimes: list[str] = field(default_factory=lambda: ["codex", "claude"])
    policy: str = "read-only"

    def validate(self) -> None:
        root = Path(self.path).expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Project must be a directory")
        if self.policy not in {"read-only", "development"}:
            raise ValueError("Unknown project policy")
        if not self.runtimes or set(self.runtimes) - {"codex", "claude"}:
            raise ValueError("Unsupported runtime")
        self.path = str(root)


@dataclass
class Config:
    hub_url: str = ""
    name: str = ""
    ca_file: str | None = None
    projects: dict[str, Project] = field(default_factory=dict)
    commands: dict[str, list[str]] = field(default_factory=dict)
    heartbeat_seconds: float = 5
    approval_timeout_seconds: float = 300
    job_timeout_seconds: float = 3600
    max_outbox_events: int = 10000

    @classmethod
    def load(cls, home: Path) -> Config:
        path = home / "config.json"
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["projects"] = {k: Project(**v) for k, v in raw.get("projects", {}).items()}
        config = cls(**raw)
        if config.hub_url:
            config.hub_url = validate_hub(config.hub_url)
        if not 1 <= config.heartbeat_seconds <= 15:
            raise ValueError("heartbeat_seconds must be between 1 and 15")
        if config.approval_timeout_seconds <= 0 or config.job_timeout_seconds <= 0:
            raise ValueError("Timeouts must be positive")
        if config.max_outbox_events < 100:
            raise ValueError("max_outbox_events must be at least 100")
        return config

    def save(self, home: Path) -> None:
        atomic_write(home / "config.json", json.dumps(asdict(self), ensure_ascii=False, indent=2))

    def command(self, runtime: str) -> list[str]:
        configured = self.commands.get(runtime)
        if configured:
            if not all(isinstance(arg, str) and arg for arg in configured):
                raise ValueError("Runtime command must be a nonempty argument array")
            executable = shutil.which(configured[0])
            if not executable:
                raise ValueError(f"Executable not found: {configured[0]}")
            command = [executable, *configured[1:]]
        else:
            executable = shutil.which(runtime)
            if not executable:
                raise ValueError(f"Install {runtime} or configure it with 'runtime set'")
            command = [executable]
        if sys.platform == "win32" and Path(command[0]).suffix.lower() in {".cmd", ".bat"}:
            # npm's Windows launcher is a shell script. Invoke its JS entry point directly.
            script = Path(command[0]).parent / "node_modules/@openai/codex/bin/codex.js"
            node = shutil.which("node.exe")
            if runtime == "codex" and len(command) == 1 and script.is_file() and node:
                return [node, str(script)]
            raise ValueError(
                "Use a native .exe or 'runtime set codex -- node.exe <codex.js>' on Windows"
            )
        return command
