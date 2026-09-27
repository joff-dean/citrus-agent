from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import json
import logging
import logging.handlers
import platform
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import __version__
from .agent import Agent
from .config import Config, Project, data_dir, private_dir, validate_hub, validate_id
from .locking import InstanceLock
from .secrets import CredentialStore
from .service import manage_service
from .state import State, enrollment_state_dir
from .transport import HubClient, HubError
from .windows_job import protect_process_tree


def parser():
    root = argparse.ArgumentParser(description="사내 메신저와 연결하는 Ubuntu/Windows PC 에이전트")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--home", type=Path, default=data_dir(), help="설정·로컬 기록 디렉터리")
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="Hub 주소 설정")
    init.add_argument("--hub", required=True)
    init.add_argument("--name", default=socket.gethostname())
    init.add_argument("--ca-file", help="사내 CA PEM 파일")
    commands.add_parser("enroll", help="메신저로 PC 소유권 등록")
    commands.add_parser("unregister", help="Hub 장치 자격증명 폐기 후 로컬 등록 해제")
    project = commands.add_parser("project", help="작업 프로젝트 등록")
    projects = project.add_subparsers(dest="action", required=True)
    add = projects.add_parser("add")
    add.add_argument("id")
    add.add_argument("path", type=Path)
    add.add_argument("--runtime", action="append", choices=["codex", "claude"])
    add.add_argument("--policy", choices=["read-only", "development"], default="read-only")
    remove = projects.add_parser("remove")
    remove.add_argument("id")
    projects.add_parser("list")
    runtime = commands.add_parser("runtime", help="CLI 실행 파일 경로 설정")
    runtimes = runtime.add_subparsers(dest="action", required=True)
    configure = runtimes.add_parser("set")
    configure.add_argument("name", choices=["codex", "claude"])
    configure.add_argument("argv", nargs=argparse.REMAINDER)
    commands.add_parser("doctor", help="설정·CLI·Hub 연결 진단")
    commands.add_parser("status", help="로컬 작업 상태 조회")
    commands.add_parser("run", help="에이전트 실행")
    service = commands.add_parser("service", help="사용자 계정으로 백그라운드 실행")
    service.add_argument("action", choices=["install", "uninstall", "start", "stop", "status"])
    return root


def enroll(home: Path, config: Config):
    if (home / "credentials.json").exists():
        raise ValueError("이미 등록된 PC입니다. 변경하려면 unregister를 먼저 실행하세요.")
    hub = HubClient(config.hub_url, ca_file=config.ca_file)
    result = hub.request(
        "/v1/enrollments",
        {"name": config.name, "platform": platform.system(), "protocol_version": 1},
    )
    identifier = validate_id(result["id"])
    device_code = result["device_code"]
    interval = max(1, min(15, result.get("interval_seconds", 3)))
    deadline = time.monotonic() + min(result["expires_in"], 600)
    print(f"사내 봇과의 개인 대화에서 /PC등록 {result['user_code']} 를 보내세요.")
    while time.monotonic() < deadline:
        result = hub.request(f"/v1/enrollments/{identifier}/poll", {"device_code": device_code})
        if result["status"] == "matched":
            owner = result["owner"]
            print(f"연결할 사내 사용자: {owner['display_name']} ({owner['id']})")
            if input("이 사용자에게 이 PC를 연결할까요? [y/N] ").strip().lower() != "y":
                hub.request(f"/v1/enrollments/{identifier}/cancel", {"device_code": device_code})
                print("등록을 취소했습니다.")
                return
            credentials = hub.request(
                f"/v1/enrollments/{identifier}/confirm",
                {
                    "device_code": device_code,
                    "owner_id": owner["id"],
                },
            )
            validate_id(credentials["agent_id"])
            if credentials.get("owner_id") != owner["id"] or not credentials.get("token"):
                raise ValueError("Hub returned mismatched enrollment credentials")
            CredentialStore(home).save({**credentials, "hub_url": config.hub_url})
            print("등록 완료. project add → doctor → service install 순서로 진행하세요.")
            return
        if result["status"] in {"expired", "cancelled"}:
            raise ValueError("등록 코드가 만료 또는 취소되었습니다. enroll을 다시 실행하세요.")
        if result["status"] != "pending":
            raise ValueError("알 수 없는 등록 상태입니다.")
        time.sleep(interval)
    raise ValueError("등록 시간이 만료되었습니다.")


def credentials_for(home: Path, config: Config):
    credentials = CredentialStore(home).load()
    if credentials.get("hub_url") != config.hub_url:
        raise ValueError(
            "등록된 Hub와 설정이 다릅니다. 원래 Hub에서 unregister 후 다시 등록하세요."
        )
    return credentials


def doctor(home: Path, config: Config) -> int:
    failures = 0
    print(f"citrus-agent {__version__} / Python {platform.python_version()} / {platform.system()}")
    print(f"데이터 디렉터리: {home}")
    if not config.projects:
        print("FAIL 프로젝트가 없습니다: project add <이름> <경로>")
        failures += 1
    runtimes = {r for p in config.projects.values() for r in p.runtimes}
    for name, project in config.projects.items():
        try:
            project.validate()
            print(f"OK 프로젝트 {name} ({project.policy})")
        except (ValueError, OSError) as exc:
            print(f"FAIL 프로젝트 {name}: {exc}")
            failures += 1
    for runtime in sorted(runtimes):
        try:
            if runtime == "claude" and not importlib.util.find_spec("claude_agent_sdk"):
                raise ValueError("citrus-agent[claude] 설치가 필요합니다")
            if runtime == "claude" and runtime not in config.commands:
                from claude_agent_sdk import ClaudeAgentOptions
                from claude_agent_sdk._internal.transport.subprocess_cli import (
                    SubprocessCLITransport,
                )

                executable = SubprocessCLITransport(
                    prompt="", options=ClaudeAgentOptions()
                )._cli_path
                command = [str(executable)]
            else:
                command = config.command(runtime)
            result = subprocess.run(
                [*command, "--version"], capture_output=True, text=True, timeout=15
            )
            if result.returncode:
                raise ValueError("CLI --version 실패")
            print(f"OK {runtime}: {result.stdout.strip()[:150]}")
        except (ValueError, OSError, subprocess.SubprocessError, ImportError) as exc:
            print(f"FAIL {runtime}: {exc}")
            failures += 1
    try:
        credentials = credentials_for(home, config)
        hub = HubClient(config.hub_url, credentials["token"], config.ca_file)
        hub.request(
            f"/v1/agents/{validate_id(credentials['agent_id'])}/check", {"protocol_version": 1}
        )
        print("OK Hub 인증·연결 (CLI 모델 계정 인증은 별도 확인 필요)")
    except (ValueError, HubError) as exc:
        print(f"FAIL Hub: {exc}")
        failures += 1
    return int(failures > 0)


async def run_agent(home, config):
    agent = Agent(home, config, credentials_for(home, config))
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, agent.stop)
    await agent.run()


def dispatch(args):
    home = args.home.expanduser().resolve()
    private_dir(home)
    config = Config.load(home)
    if args.command == "service":
        if args.action == "install":
            credentials_for(home, config)
            if not config.projects:
                raise ValueError("먼저 프로젝트를 등록하세요.")
            with InstanceLock(home):
                for runtime in {r for p in config.projects.values() for r in p.runtimes}:
                    if runtime == "codex" or runtime in config.commands:
                        config.commands[runtime] = config.command(runtime)
                config.save(home)
        manage_service(args.action, home)
        return 0
    if args.command == "doctor":
        return doctor(home, config)
    if args.command == "status":
        credentials = credentials_for(home, config)
        state = State(enrollment_state_dir(home, config.hub_url, credentials))
        try:
            print(
                json.dumps(
                    {"jobs": state.recent_jobs(), "pending_events": state.pending_count()},
                    ensure_ascii=False,
                    indent=2,
                )
            )
        finally:
            state.close()
        return 0
    with InstanceLock(home):
        if args.command == "init":
            if (home / "credentials.json").exists() and config.hub_url != validate_hub(args.hub):
                raise ValueError("다른 Hub로 변경하기 전에 unregister를 실행하세요.")
            config.hub_url, config.name = validate_hub(args.hub), args.name
            if args.ca_file:
                config.ca_file = str(Path(args.ca_file).expanduser().resolve(strict=True))
            config.save(home)
            print("Hub 설정을 저장했습니다. citrus-agent enroll로 PC를 등록하세요.")
        elif args.command == "enroll":
            enroll(home, config)
        elif args.command == "unregister":
            credentials = credentials_for(home, config)
            hub = HubClient(config.hub_url, credentials["token"], config.ca_file)
            hub.request(f"/v1/agents/{validate_id(credentials['agent_id'])}/revoke", {})
            CredentialStore(home).path.unlink()
            print("장치 토큰을 폐기했습니다. 로컬 작업 기록은 보존했습니다.")
        elif args.command == "project":
            if args.action == "add":
                identifier = validate_id(args.id)
                project = Project(str(args.path), args.runtime or ["codex", "claude"], args.policy)
                project.validate()
                config.projects[identifier] = project
                config.save(home)
            elif args.action == "remove":
                config.projects.pop(args.id, None)
                config.save(home)
            else:
                print(
                    json.dumps(
                        {k: vars(v) for k, v in config.projects.items()},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
        elif args.command == "runtime":
            argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
            if not argv:
                raise ValueError("실행 파일을 지정하세요: runtime set codex -- /path/to/codex")
            config.commands[args.name] = argv
            config.commands[args.name] = config.command(args.name)
            config.save(home)
        elif args.command == "run":
            protect_process_tree()
            handler = logging.handlers.RotatingFileHandler(
                home / "agent.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
            )
            logging.basicConfig(
                level=logging.INFO,
                handlers=[handler],
                format="%(asctime)s %(levelname)s %(message)s",
            )
            asyncio.run(run_agent(home, config))
    return 0


def main():
    # Windows pipes can default to cp1252 even when the interactive console supports
    # Unicode. The command-line interface and redirected output use UTF-8 consistently.
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        code = dispatch(parser().parse_args())
    except KeyboardInterrupt:
        code = 130
    except (ValueError, RuntimeError, HubError, OSError, subprocess.SubprocessError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)
